"""Command-line interface: ``slo <command>``."""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import typer

from slo import __version__
from slo.config import (
    FIXTURES_DIR,
    LIST_SOURCES,
    PROJECT_ROOT,
    Workspace,
    default_workspace,
    load_screening_config,
)
from slo.store import connect, fetch_one

app = typer.Typer(
    name="slo",
    help="sanctions-list-ops: a mini sanctions-list management pipeline.",
    add_completion=False,
    no_args_is_help=True,
)
request_app = typer.Typer(help="List change requests (maker/checker).", no_args_is_help=True)
app.add_typer(request_app, name="request")

DateOpt = typer.Option(None, "--date", "-d", help="Snapshot date YYYY-MM-DD (default: today).")
SourceOpt = typer.Option("all", "--source", "-s", help="eu | un | ofac | uk | nl | all")
DataDirOpt = typer.Option(None, "--data-dir", help="Workspace data directory (default: ./data).")


def _ws(data_dir: Path | None) -> Workspace:
    if data_dir is None:
        return default_workspace()
    return Workspace(data_dir=data_dir)


def _date(value: str | None) -> date:
    return date.fromisoformat(value) if value else date.today()


def _sources(value: str, *, with_internal: bool = False) -> list[str]:
    if value == "all":
        return [*LIST_SOURCES, *(["internal"] if with_internal else [])]
    if value not in (*LIST_SOURCES, "internal"):
        raise typer.BadParameter(f"unknown source {value!r}")
    return [value]


def _fail(msg: str) -> None:
    typer.secho(msg, fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


@app.command()
def version() -> None:
    """Show the version."""
    typer.echo(f"sanctions-list-ops {__version__}")


@app.command()
def fetch(
    source: str = SourceOpt, snapshot: str | None = DateOpt, data_dir: Path | None = DataDirOpt
) -> None:
    """Download raw list files into data/raw/<source>/<date>.*"""
    from slo.pipeline import _display_path, fetch_step

    ws, d, failed = _ws(data_dir), _date(snapshot), []
    for s in _sources(source):
        try:
            r = fetch_step(ws, s, d)
            typer.echo(
                f"{s:<5} {r.bytes:>12,} bytes  sha256={r.sha256}  -> {_display_path(r.path)}"
            )
        except (RuntimeError, OSError) as exc:
            failed.append(s)
            typer.secho(f"{s:<5} FAILED: {exc}", fg=typer.colors.RED, err=True)
    if failed:
        raise typer.Exit(1)


@app.command()
def normalise(
    source: str = SourceOpt, snapshot: str | None = DateOpt, data_dir: Path | None = DataDirOpt
) -> None:
    """Parse already-fetched raw files and load the canonical tables."""
    import uuid

    from slo.pipeline import ingest_step

    ws, d = _ws(data_dir), _date(snapshot)
    con = connect(ws.db_path)
    run_id, failed = uuid.uuid4().hex[:12], False
    for s in _sources(source):
        r = ingest_step(con, ws, s, d, run_id, None)
        typer.echo(json.dumps(r, default=str))
        failed |= r["status"] != "ok"
    if failed:
        raise typer.Exit(1)


@app.command()
def dq(
    source: str = SourceOpt, snapshot: str | None = DateOpt, data_dir: Path | None = DataDirOpt
) -> None:
    """Run data-quality rules and print the scorecard."""
    from slo.dq.engine import run_rules, scorecard

    ws, d = _ws(data_dir), _date(snapshot)
    con = connect(ws.db_path)
    for s in _sources(source, with_internal=True):
        issues = run_rules(con, s, d, "cli")
        card = scorecard(con, s, d)
        typer.echo(
            f"{s:<8} issues={issues.height:<6} dq_score={card.get('dq_score')} gate_passed={card.get('export_gate_passed')}"
        )


@app.command()
def crosslist(snapshot: str | None = DateOpt, data_dir: Path | None = DataDirOpt) -> None:
    """Link UN entities to EU/UK/OFAC and run DQ050-DQ052."""
    from slo.crosslist import build_crosslinks
    from slo.dq.engine import run_crosslist_rules

    ws, d = _ws(data_dir), _date(snapshot)
    con = connect(ws.db_path)
    stats = build_crosslinks(con, d)
    stats["issues"] = run_crosslist_rules(con, d, "cli")
    typer.echo(json.dumps(stats, indent=2, default=str))


@app.command()
def fix(
    source: str = SourceOpt, snapshot: str | None = DateOpt, data_dir: Path | None = DataDirOpt
) -> None:
    """Build the *_clean tables (auto-fixes + approved requests) and log every change."""
    from slo.dq.fixes import build_clean

    ws, d = _ws(data_dir), _date(snapshot)
    con = connect(ws.db_path)
    for s in _sources(source, with_internal=True):
        typer.echo(f"{s:<8} {json.dumps(build_clean(con, s, d, ws.requests_dir))}")


@app.command()
def delta(
    source: str = SourceOpt, snapshot: str | None = DateOpt, data_dir: Path | None = DataDirOpt
) -> None:
    """Compute adds/removes/amendments against the previous snapshot."""
    from slo.delta import compute_delta, store_delta

    ws, d = _ws(data_dir), _date(snapshot)
    con = connect(ws.db_path)
    for s in _sources(source, with_internal=True):
        result = compute_delta(con, s, d)
        path = store_delta(con, result, ws.deltas_dir)
        typer.echo(f"{s:<8} from={result['from']} {result['counts']} -> {path}")


@app.command()
def export(
    system: str = typer.Option(
        "all", help="payments_screening | customer_screening | delta_feed | all"
    ),
    clean: bool = typer.Option(
        True, "--clean/--raw", help="Export the cleaned (default) or raw tables."
    ),
    snapshot: str | None = DateOpt,
    data_dir: Path | None = DataDirOpt,
) -> None:
    """Write screening-system exports. Sources failing the DQ gate are blocked (exit 1)."""
    from slo.export import SYSTEMS, export_all
    from slo.pipeline import loaded_sources

    ws, d = _ws(data_dir), _date(snapshot)
    con = connect(ws.db_path)
    systems = SYSTEMS if system == "all" else (system,)
    manifests = export_all(
        con, ws.exports_dir, d, loaded_sources(con, d), clean=clean, systems=systems
    )
    blocked = False
    for name, m in manifests.items():
        typer.echo(
            f"{name:<20} records={m['record_count']:<7} gate_passed={m['dq_gate_passed']} sha256={m['sha256'][:16]}"
        )
        for s, reasons in m["blocked_sources"].items():
            blocked = True
            typer.secho(f"  BLOCKED {s}: {'; '.join(reasons)}", fg=typer.colors.RED, err=True)
    if blocked:
        raise typer.Exit(1)


@app.command()
def screen(
    snapshot: str | None = DateOpt,
    n_customers: int | None = typer.Option(None, help="Synthetic customers (default from config)."),
    n_hits: int | None = typer.Option(None, help="Planted true hits (default from config)."),
    seed: int | None = typer.Option(None),
    data_dir: Path | None = DataDirOpt,
) -> None:
    """Run the raw-vs-clean screening experiment."""
    from slo.pipeline import screening_step

    cfg = load_screening_config()
    ws, d = _ws(data_dir), _date(snapshot)
    con = connect(ws.db_path)
    sources = [
        s
        for s in LIST_SOURCES
        if fetch_one(
            con, "SELECT 1 FROM entities WHERE source = ? AND snapshot_date = ? LIMIT 1", [s, d]
        )
    ]
    res = screening_step(
        con,
        ws,
        d,
        sources,
        n_customers or cfg["n_customers"],
        n_hits or cfg["n_hits"],
        seed if seed is not None else cfg["seed"],
    )
    for mode, ev in res["runs"].items():
        typer.echo(
            f"{mode:<6} {json.dumps(ev['at_default_threshold'])}  runtime={ev['runtime_seconds']}s"
        )


@app.command()
def report(
    snapshot: str | None = DateOpt,
    update_readme: bool = typer.Option(False, "--update-readme"),
    data_dir: Path | None = DataDirOpt,
) -> None:
    """Regenerate summary.json/md, charts and (optionally) the README findings block."""
    from slo.pipeline import report_step

    ws, d = _ws(data_dir), _date(snapshot)
    if not update_readme:
        ws = Workspace(data_dir=ws.data_dir, requests_dir=ws.requests_dir, readme_path=None)
    con = connect(ws.db_path)
    summary = report_step(con, ws, d)
    typer.echo(
        f"report written to {ws.report_dir(str(d))} (readme updated: {summary.get('readme_updated', False)})"
    )


@app.command("all")
def all_(
    source: str = SourceOpt, snapshot: str | None = DateOpt, data_dir: Path | None = DataDirOpt
) -> None:
    """Full live pipeline: fetch -> normalise -> dq -> crosslist -> fix -> delta -> export -> screen -> report."""
    from slo.pipeline import run_all

    ws = _ws(data_dir)
    summary = run_all(ws, _date(snapshot), sources=tuple(_sources(source)))
    failed = [s for s, c in summary["sources"].items() if c.get("status") != "ok"]
    missing = [s for s in _sources(source) if s not in summary["sources"]]
    if failed or missing:
        _fail(f"pipeline finished with failed sources: {failed + missing}")
    _fail_if_blocked(summary)


def _fail_if_blocked(summary: dict) -> None:
    blocked = {s for m in summary.get("exports", {}).values() for s in m.get("blocked_sources", {})}
    if blocked:
        _fail(f"export gate blocked: {sorted(blocked)} (see dq_issues DQ060/DQ061)")


@app.command("offline-demo")
def offline_demo(
    data_dir: Path = typer.Option(PROJECT_ROOT / "data" / "demo", "--data-dir"),
    fixtures_dir: Path = typer.Option(
        FIXTURES_DIR, "--fixtures-dir", help="Directory with eu.xml, un.xml, ..."
    ),
    keep: bool = typer.Option(False, help="Keep previous demo database."),
) -> None:
    """Run the whole pipeline on tests/fixtures with no network access."""
    from slo.pipeline import _display_path, run_all

    if data_dir.exists() and not keep:
        shutil.rmtree(data_dir)
    demo_requests = data_dir / "requests"
    demo_requests.mkdir(parents=True, exist_ok=True)
    for f in ("requests.csv", "internal_list.yaml"):
        shutil.copyfile(PROJECT_ROOT / "requests" / f, demo_requests / f)
    ws = Workspace(data_dir=data_dir, requests_dir=demo_requests)
    cfg = load_screening_config()["demo"]
    summary = run_all(
        ws,
        date.today(),
        offline=True,
        fixtures_dir=fixtures_dir,
        n_customers=cfg["n_customers"],
        n_hits=cfg["n_hits"],
    )
    failed = [s for s, c in summary["sources"].items() if c.get("status") != "ok"]
    if failed or len(summary["sources"]) < len(LIST_SOURCES) + 1:
        _fail(f"offline demo failed: {failed}")
    _fail_if_blocked(summary)
    typer.echo(
        f"offline demo OK -> {_display_path(ws.report_dir(summary['snapshot_date']) / 'summary.json')}"
    )


def _requests_path(data_dir: Path | None) -> Path:
    return _ws(data_dir).requests_dir / "requests.csv"


@request_app.command("list")
def request_list(data_dir: Path | None = DataDirOpt) -> None:
    from slo.requests_log import load_requests

    for r in load_requests(_requests_path(data_dir)):
        typer.echo(
            f"{r.request_id}  {r.status:<11} {r.request_type:<26} {r.target_entity_key:<28} maker={r.maker} checker={r.checker or '-'}"
        )


@request_app.command("add")
def request_add(
    request_type: str = typer.Option(..., "--type"),
    target_source: str = typer.Option(..., "--source"),
    target_entity_key: str = typer.Option("", "--entity"),
    payload: str = typer.Option("{}", help="JSON payload"),
    rationale: str = typer.Option(..., help="Why the change is needed"),
    maker: str = typer.Option(...),
    requested_by: str = typer.Option("FEC analyst"),
    evidence_ref: str = typer.Option(""),
    data_dir: Path | None = DataDirOpt,
) -> None:
    from slo.requests_log import RequestError, add_request

    try:
        r = add_request(
            _requests_path(data_dir),
            request_type=request_type,
            target_source=target_source,
            target_entity_key=target_entity_key,
            payload=json.loads(payload),
            rationale=rationale,
            maker=maker,
            requested_by=requested_by,
            evidence_ref=evidence_ref,
        )
    except (RequestError, json.JSONDecodeError) as exc:
        _fail(str(exc))
    typer.echo(f"added {r.request_id} (pending)")


@request_app.command("approve")
def request_approve(
    request_id: str, checker: str = typer.Option(...), data_dir: Path | None = DataDirOpt
) -> None:
    from slo.requests_log import RequestError, approve

    try:
        r = approve(_requests_path(data_dir), request_id, checker)
    except RequestError as exc:
        _fail(str(exc))
    typer.echo(f"{r.request_id} approved by {r.checker}")


@request_app.command("reject")
def request_reject(
    request_id: str, checker: str = typer.Option(...), data_dir: Path | None = DataDirOpt
) -> None:
    from slo.requests_log import RequestError, reject

    try:
        r = reject(_requests_path(data_dir), request_id, checker)
    except RequestError as exc:
        _fail(str(exc))
    typer.echo(f"{r.request_id} rejected by {r.checker}")


@request_app.command("apply")
def request_apply(data_dir: Path | None = DataDirOpt) -> None:
    from slo.requests_log import apply_requests

    done = apply_requests(_ws(data_dir).requests_dir)
    typer.echo(f"implemented: {done or 'nothing to do'}")


def main() -> None:  # pragma: no cover
    app()
