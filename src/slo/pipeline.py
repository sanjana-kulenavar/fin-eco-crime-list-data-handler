"""Pipeline steps and orchestration (used by the CLI)."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import polars as pl

from slo.build import build_frames
from slo.config import (
    FIXTURES_DIR,
    LIST_SOURCES,
    PROJECT_ROOT,
    Workspace,
    load_screening_config,
    load_sources,
)
from slo.crosslist import build_crosslinks
from slo.delta import compute_delta, store_delta
from slo.dq.engine import run_crosslist_rules, run_rules, scorecard
from slo.dq.fixes import build_clean
from slo.export import export_all
from slo.fetch import FetchResult, copy_local, fetch_source, raw_path
from slo.parsers import PARSERS
from slo.requests_log import apply_requests, parse_internal
from slo.screening.evaluate import equivalence_groups, evaluate, missed_hits, top_false_positives
from slo.screening.matcher import build_index, screen
from slo.screening.synth import frame_hash, generate, load_list_targets
from slo.store import connect, fetch_one, insert_frame, replace_snapshot

FIXTURE_FILES = {
    "eu": "eu.xml",
    "un": "un.xml",
    "ofac": "ofac.xml",
    "uk": "uk.xml",
    "nl": "nl.ftm.json",
}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _display_path(path: Path) -> str:
    """Path relative to the project root when possible (no machine-specific prefixes)."""
    try:
        return str(Path(path).resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return Path(path).name


@dataclass
class StepLog:
    lines: list[str] = field(default_factory=list)

    def __call__(self, msg: str) -> None:
        self.lines.append(msg)
        print(msg, flush=True)


def record_run(con: duckdb.DuckDBPyConnection, **row) -> None:
    con.execute(
        "DELETE FROM runs WHERE source = ? AND snapshot_date = ? AND run_id = ?",
        [row["source"], row["snapshot_date"], row["run_id"]],
    )
    insert_frame(
        con,
        "runs",
        pl.DataFrame(
            [row],
            schema={
                "run_id": pl.String,
                "started_at": pl.Datetime,
                "finished_at": pl.Datetime,
                "source": pl.String,
                "snapshot_date": pl.Date,
                "raw_path": pl.String,
                "raw_sha256": pl.String,
                "raw_bytes": pl.Int64,
                "published_date": pl.Date,
                "record_count_source": pl.Int32,
                "record_count_parsed": pl.Int32,
                "status": pl.String,
                "error": pl.String,
            },
        ),
    )


def fetch_step(
    ws: Workspace, source: str, d: date, *, offline: bool = False, fixtures_dir: Path = FIXTURES_DIR
) -> FetchResult:
    cfg = load_sources()[source]
    if offline:
        return copy_local(
            source, fixtures_dir / FIXTURE_FILES[source], cfg["format"], ws.raw_dir, str(d)
        )
    return fetch_source(source, cfg, ws.raw_dir, str(d))


def ingest_step(
    con: duckdb.DuckDBPyConnection,
    ws: Workspace,
    source: str,
    d: date,
    run_id: str,
    fetched: FetchResult | None,
) -> dict:
    """Parse + normalise + load one source. Records a ``runs`` row whatever happens."""
    started = _now()
    path = (
        fetched.path
        if fetched
        else raw_path(ws.raw_dir, source, str(d), load_sources()[source]["format"])
    )
    row = {
        "run_id": run_id,
        "started_at": started,
        "source": source,
        "snapshot_date": d,
        "raw_path": _display_path(path),
        "raw_sha256": fetched.sha256 if fetched else None,
        "raw_bytes": fetched.bytes if fetched else None,
    }
    try:
        if not path.exists():
            raise FileNotFoundError(f"no raw file for {source} on {d}: {path}")
        if row["raw_sha256"] is None:
            from slo.fetch import sha256_file

            row["raw_sha256"], row["raw_bytes"] = sha256_file(path), path.stat().st_size
        result = PARSERS[source](path)
        counts = replace_snapshot(con, source, d, build_frames(result, d))
        status = "ok" if not result.failures else "failed"
        record_run(
            con,
            **row,
            finished_at=_now(),
            published_date=result.published_date,
            record_count_source=result.declared_count
            if result.declared_count is not None
            else result.element_count,
            record_count_parsed=len(result.entities),
            status=status,
            error="; ".join(result.failures[:20]) or None,
        )
        return {
            "source": source,
            "status": status,
            "published": str(result.published_date),
            "declared": result.declared_count,
            "elements": result.element_count,
            "parsed": len(result.entities),
            "failures": len(result.failures),
            **counts,
        }
    except Exception as exc:
        record_run(
            con,
            **row,
            finished_at=_now(),
            published_date=None,
            record_count_source=None,
            record_count_parsed=0,
            status="failed",
            error=repr(exc)[:500],
        )
        return {"source": source, "status": "failed", "error": repr(exc)}


def ingest_internal(con: duckdb.DuckDBPyConnection, ws: Workspace, d: date, run_id: str) -> dict:
    implemented = apply_requests(ws.requests_dir)
    path = ws.requests_dir / "internal_list.yaml"
    result = parse_internal(path)
    counts = replace_snapshot(con, "internal", d, build_frames(result, d))
    from slo.fetch import sha256_file

    record_run(
        con,
        run_id=run_id,
        started_at=_now(),
        finished_at=_now(),
        source="internal",
        snapshot_date=d,
        raw_path=_display_path(path),
        raw_sha256=sha256_file(path) if path.exists() else None,
        raw_bytes=path.stat().st_size if path.exists() else 0,
        published_date=None,
        record_count_source=result.element_count,
        record_count_parsed=len(result.entities),
        status="ok",
        error=None,
    )
    return {
        "source": "internal",
        "status": "ok",
        "parsed": len(result.entities),
        "requests_implemented": implemented,
        **counts,
    }


def screening_step(
    con: duckdb.DuckDBPyConnection,
    ws: Workspace,
    d: date,
    sources: list[str],
    n_customers: int,
    n_hits: int,
    seed: int,
) -> dict:
    cfg = load_screening_config()
    threshold = int(cfg["threshold"])
    targets = load_list_targets(con, d, sources)
    customers, truth = generate(targets, n_customers, n_hits, seed)
    ws.synth_dir.mkdir(parents=True, exist_ok=True)
    customers.write_parquet(ws.synth_dir / "customers.parquet")
    truth.write_parquet(ws.synth_dir / "truth.parquet")
    meta = {
        "seed": seed,
        "n_customers": customers.height,
        "n_hits": truth.height,
        "customers_sha256": frame_hash(customers),
        "truth_sha256": frame_hash(truth),
    }
    (ws.synth_dir / "metadata.json").write_text(json.dumps(meta, indent=2) + "\n")
    equivalents = equivalence_groups(con, d, sources)
    thresholds = list(range(int(cfg["sweep"]["min"]), int(cfg["sweep"]["max"]) + 1))
    out_dir = ws.report_dir(str(d)) / "screening"
    out_dir.mkdir(parents=True, exist_ok=True)
    names = dict(
        con.execute(
            "SELECT entity_key, primary_name FROM entities WHERE snapshot_date = ?", [d]
        ).fetchall()
    )
    results: dict = {"synthetic_data": meta, "runs": {}}
    for mode in ("raw", "clean"):
        t0 = time.perf_counter()
        idx = build_index(
            con, d, sources, mode, int(cfg["blocking"]["min_token_length"]), ws.requests_dir
        )
        pairs = screen(
            customers,
            idx,
            min_token_len=int(cfg["blocking"]["min_token_length"]),
            max_candidates=int(cfg["blocking"]["max_candidates"]),
            keep_from=float(min(thresholds)),
        )
        elapsed = time.perf_counter() - t0
        ev = evaluate(pairs, truth, customers.height, equivalents, thresholds, threshold)
        ev["runtime_seconds"] = round(elapsed, 1)
        ev["list_names_indexed"] = len(idx.names)
        results["runs"][mode] = ev
        missed_hits(pairs, truth, equivalents, threshold).write_csv(
            out_dir / f"missed_hits_{mode}.csv"
        )
        top_false_positives(pairs, truth, customers, names, threshold).write_csv(
            out_dir / f"top_false_positives_{mode}.csv"
        )
    (out_dir / "metrics.json").write_text(json.dumps(results, indent=2, default=str) + "\n")
    return results


def run_all(
    ws: Workspace,
    d: date,
    *,
    sources: tuple[str, ...] = LIST_SOURCES,
    offline: bool = False,
    fixtures_dir: Path = FIXTURES_DIR,
    n_customers: int | None = None,
    n_hits: int | None = None,
    seed: int | None = None,
    log: StepLog | None = None,
) -> dict:
    """fetch -> normalise -> dq -> crosslist -> fix -> delta -> export -> screen -> report."""
    from slo.report import write_report

    log = log or StepLog()
    cfg = load_screening_config()
    run_id = uuid.uuid4().hex[:12]
    con = connect(ws.db_path)
    t0 = time.perf_counter()
    ingest = {}
    for s in sources:
        try:
            fetched = fetch_step(ws, s, d, offline=offline, fixtures_dir=fixtures_dir)
            log(f"fetch     {s:<8} {fetched.bytes:>12,} bytes  sha256={fetched.sha256[:12]}")
        except (RuntimeError, OSError) as exc:
            log(f"fetch     {s:<8} FAILED {exc}")
            fetched = None
        ingest[s] = ingest_step(con, ws, s, d, run_id, fetched)
        i = ingest[s]
        log(
            f"normalise {s:<8} status={i['status']} parsed={i.get('parsed', 0)} "
            f"declared/elements={i.get('declared') or i.get('elements')} names={i.get('names', 0)}"
        )
    ingest["internal"] = ingest_internal(con, ws, d, run_id)
    loaded = [s for s, i in ingest.items() if i.get("parsed")]
    for s in loaded:
        issues = run_rules(con, s, d, run_id)
        log(f"dq        {s:<8} {issues.height:>7,} issues")
    cross = build_crosslinks(con, d)
    run_crosslist_rules(con, d, run_id)
    log(
        f"crosslist UN->EU {cross.get('un_eu')}/{cross.get('un_entities')}, UN->UK {cross.get('un_uk')}/{cross.get('un_entities')}"
    )
    for s in loaded:
        fixed = build_clean(con, s, d, ws.requests_dir)
        log(f"fix       {s:<8} {sum(fixed['fixes'].values()):>7,} fixes")
    deltas = {}
    for s in loaded:
        delta = compute_delta(con, s, d)
        store_delta(con, delta, ws.deltas_dir)
        deltas[s] = {
            **delta["counts"],
            "from": delta["from"],
            "initial_load": delta["initial_load"],
        }
        log(
            f"delta     {s:<8} {delta['counts']} {'(initial load)' if delta['initial_load'] else ''}"
        )
    manifests = export_all(con, ws.exports_dir, d, loaded)
    for system, m in manifests.items():
        log(
            f"export    {system:<20} {m['record_count']:>7,} records  gate_passed={m['dq_gate_passed']}  blocked={list(m['blocked_sources'])}"
        )
    list_sources = [s for s in loaded if s != "internal"]
    screening = screening_step(
        con,
        ws,
        d,
        list_sources,
        n_customers or int(cfg["n_customers"]),
        n_hits or int(cfg["n_hits"]),
        seed if seed is not None else int(cfg["seed"]),
    )
    for mode, ev in screening["runs"].items():
        a = ev["at_default_threshold"]
        log(
            f"screen    {mode:<6} recall={a['recall']} fp/1k={a['fp_per_1k_customers']} ({ev['runtime_seconds']}s)"
        )
    scorecards = {s: scorecard(con, s, d) for s in loaded}
    summary = write_report(
        con,
        ws,
        d,
        ingest=ingest,
        scorecards=scorecards,
        crosslist=cross,
        deltas=deltas,
        manifests=manifests,
        screening=screening,
    )
    summary["elapsed_seconds"] = round(time.perf_counter() - t0, 1)
    log(f"report    {_display_path(ws.report_dir(str(d)))}  ({summary['elapsed_seconds']}s total)")
    con.close()
    return summary


def loaded_sources(con: duckdb.DuckDBPyConnection, d: date) -> list[str]:
    rows = con.execute(
        "SELECT DISTINCT source FROM entities WHERE snapshot_date = ? ORDER BY 1", [d]
    ).fetchall()
    order = [*LIST_SOURCES, "internal"]
    return sorted((r[0] for r in rows), key=order.index)


def report_step(con: duckdb.DuckDBPyConnection, ws: Workspace, d: date) -> dict:
    """Rebuild the report for an existing snapshot from the database and artefacts."""
    from slo.report import write_report

    loaded = loaded_sources(con, d)
    ingest = {
        s: {"status": st, "parsed": n}
        for s, st, n in con.execute(
            "SELECT source, arg_max(status, started_at), arg_max(record_count_parsed, started_at) "
            "FROM runs WHERE snapshot_date = ? GROUP BY 1",
            [d],
        ).fetchall()
    }
    deltas = {}
    for s in loaded:
        rows = dict(
            con.execute(
                "SELECT change_type, count(*) FROM deltas WHERE source = ? AND to_date = ? GROUP BY 1",
                [s, d],
            ).fetchall()
        )
        frm = fetch_one(
            con, "SELECT max(from_date) FROM deltas WHERE source = ? AND to_date = ?", [s, d]
        )[0]
        deltas[s] = {t: rows.get(t, 0) for t in ("added", "removed", "amended")} | {
            "from": str(frm) if frm else None,
            "initial_load": frm is None,
        }
    manifests = {}
    for system in ("payments_screening", "customer_screening", "delta_feed"):
        p = ws.exports_dir / system / f"{d}.manifest.json"
        if p.exists():
            manifests[system] = json.loads(p.read_text())
    metrics = ws.report_dir(str(d)) / "screening" / "metrics.json"
    screening = json.loads(metrics.read_text()) if metrics.exists() else None
    return write_report(
        con,
        ws,
        d,
        ingest=ingest,
        scorecards={s: scorecard(con, s, d) for s in loaded},
        crosslist=build_crosslinks(con, d),
        deltas=deltas,
        manifests=manifests,
        screening=screening,
    )
