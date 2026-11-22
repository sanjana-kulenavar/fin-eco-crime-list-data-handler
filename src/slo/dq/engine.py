"""Run data-quality rules, persist issues, compute the scorecard and the export gate."""

from __future__ import annotations

from datetime import date

import duckdb
import polars as pl

from slo.config import load_dq_rules
from slo.crosslist import crosslist_issues
from slo.dq.rules import ISSUE_SCHEMA, RULES
from slo.store import fetch_one, insert_frame

CROSSLIST_RULES = ("DQ050", "DQ051", "DQ052")
GATE_RULES = ("DQ060", "DQ061")
SEVERITY_WEIGHTS = {"high": 10, "medium": 3, "low": 1}


def _decorate(df: pl.DataFrame, rule_id: str, source: str, d: date, run_id: str) -> pl.DataFrame:
    cfg = load_dq_rules()[rule_id]
    return df.with_columns(
        pl.lit(run_id).alias("run_id"),
        pl.lit(source).alias("source"),
        pl.lit(d).alias("snapshot_date"),
        pl.lit(rule_id).alias("rule_id"),
        pl.lit(cfg["severity"]).alias("severity"),
        pl.lit(bool(cfg["auto_fix"])).alias("auto_fixable"),
        pl.lit(False).alias("fixed"),
    )


def _store(
    con: duckdb.DuckDBPyConnection,
    source: str,
    d: date,
    rule_ids: list[str],
    frames: list[pl.DataFrame],
) -> pl.DataFrame:
    if rule_ids:
        placeholders = ",".join("?" for _ in rule_ids)
        con.execute(
            f"DELETE FROM dq_issues WHERE source = ? AND snapshot_date = ? AND rule_id IN ({placeholders})",
            [source, d, *rule_ids],
        )
    issues = pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()
    if issues.height:
        insert_frame(con, "dq_issues", issues)
    return issues


def run_rules(con: duckdb.DuckDBPyConnection, source: str, d: date, run_id: str) -> pl.DataFrame:
    """Run every enabled single-list rule for one snapshot and store the issues."""
    cfg = load_dq_rules()
    rule_ids = [r for r, c in cfg.items() if c.get("enabled", True) and r in RULES]
    frames = [_decorate(RULES[r](con, source, d), r, source, d, run_id) for r in rule_ids]
    return _store(con, source, d, rule_ids, frames)


def run_crosslist_rules(con: duckdb.DuckDBPyConnection, d: date, run_id: str) -> dict[str, int]:
    counts = {}
    cfg = load_dq_rules()
    rule_ids = [r for r in CROSSLIST_RULES if cfg[r].get("enabled", True)]
    for target in ("eu", "uk"):
        found = crosslist_issues(con, d, target)
        frames = [_decorate(found[r], r, target, d, run_id) for r in rule_ids]
        issues = _store(con, target, d, rule_ids, frames)
        counts[target] = issues.height
    return counts


def export_gate(con: duckdb.DuckDBPyConnection, source: str, d: date) -> tuple[bool, list[str]]:
    """A list release is blocked while any DQ060/DQ061 issue exists for the snapshot."""
    rows = con.execute(
        "SELECT rule_id, message FROM dq_issues WHERE source = ? AND snapshot_date = ? "
        f"AND rule_id IN {GATE_RULES}",
        [source, d],
    ).fetchall()
    status = fetch_one(
        con,
        "SELECT status FROM runs WHERE source = ? AND snapshot_date = ? ORDER BY started_at DESC LIMIT 1",
        [source, d],
    )
    reasons = [f"{r}: {m}" for r, m in rows]
    if status is not None and status[0] != "ok":
        reasons.append(f"run status is {status[0]}")
    return (not reasons, reasons)


def _pct(num: int, den: int) -> float | None:
    return round(100.0 * num / den, 1) if den else None


def scorecard(con: duckdb.DuckDBPyConnection, source: str, d: date) -> dict:
    p = {"s": source, "d": d}
    records, persons = fetch_one(
        con,
        "SELECT count(*), count(*) FILTER (WHERE entity_type = 'person') FROM entities "
        "WHERE source = $s AND snapshot_date = $d",
        p,
    )
    if records == 0:
        return {
            "source": source,
            "snapshot_date": str(d),
            "records": 0,
            "status": "failed",
            "dq_score": None,
        }
    issues = con.execute(
        "SELECT rule_id, severity, count(*) AS n, count(DISTINCT entity_key) AS entities, "
        "bool_and(fixed) AS fixed FROM dq_issues WHERE source = $s AND snapshot_date = $d "
        "GROUP BY 1, 2 ORDER BY 1",
        p,
    ).fetchall()
    by_sev = {"high": 0, "medium": 0, "low": 0}
    affected = {"high": set(), "medium": set(), "low": set()}
    for _rule_id, sev, n, _, _ in issues:
        by_sev[sev] += n
    for sev in by_sev:
        keys = con.execute(
            "SELECT DISTINCT coalesce(entity_key, '__list__') FROM dq_issues "
            "WHERE source = $s AND snapshot_date = $d AND severity = $sev AND NOT fixed",
            {**p, "sev": sev},
        ).fetchall()
        affected[sev] = {k[0] for k in keys}
    full_dob = fetch_one(
        con,
        "SELECT count(DISTINCT b.entity_key) FROM birth_dates b JOIN entities e USING (entity_key, snapshot_date) "
        "WHERE b.source = $s AND b.snapshot_date = $d AND e.entity_type = 'person' AND b.precision = 'day'",
        p,
    )[0]
    with_nat = fetch_one(
        con,
        "SELECT count(DISTINCT c.entity_key) FROM countries c JOIN entities e USING (entity_key, snapshot_date) "
        "WHERE c.source = $s AND c.snapshot_date = $d AND e.entity_type = 'person' "
        "AND c.role IN ('nationality','citizenship') AND c.country_iso2 IS NOT NULL",
        p,
    )[0]
    with_id = fetch_one(
        con,
        "SELECT count(DISTINCT entity_key) FROM identifiers WHERE source = $s AND snapshot_date = $d",
        p,
    )[0]
    nonlatin = fetch_one(
        con,
        """SELECT count(*) FILTER (WHERE nl > 0), count(*) FILTER (WHERE nl > 0 AND lat > 0) FROM (
             SELECT entity_key,
                    count(*) FILTER (WHERE script IN ('arabic','cyrillic','cjk','other')) AS nl,
                    count(*) FILTER (WHERE script = 'latin') AS lat
             FROM names WHERE source = $s AND snapshot_date = $d GROUP BY entity_key)""",
        p,
    )
    # Score = share of records free of open (unfixed) issues, weighted by severity:
    # 100 - 100 * (10*H + 3*M + 1*L) / (10 * records), where H/M/L count *affected
    # entities* (not issue rows), so one messy record cannot outweigh the list.
    weighted = sum(SEVERITY_WEIGHTS[s] * len(affected[s]) for s in affected)
    score = max(0.0, min(100.0, 100.0 - 100.0 * weighted / (10 * records)))
    passed, reasons = export_gate(con, source, d)
    return {
        "source": source,
        "snapshot_date": str(d),
        "status": "ok",
        "records": records,
        "persons": persons,
        "issues_total": sum(by_sev.values()),
        "issues_by_severity": by_sev,
        "open_entities_by_severity": {s: len(v) for s, v in affected.items()},
        "issues_by_rule": {
            r: {"severity": sev, "issues": n, "entities": ents, "fixed": bool(fx)}
            for r, sev, n, ents, fx in issues
        },
        "pct_persons_full_dob": _pct(full_dob, persons),
        "pct_persons_with_nationality": _pct(with_nat, persons),
        "pct_entities_with_identifier": _pct(with_id, records),
        "pct_nonlatin_with_latin": _pct(nonlatin[1], nonlatin[0]),
        "duplicate_candidates": next((n for r, _, n, _, _ in issues if r == "DQ040"), 0),
        "dq_score": round(score, 1),
        "export_gate_passed": passed,
        "export_gate_reasons": reasons,
    }


def empty_issues() -> pl.DataFrame:
    return pl.DataFrame(schema=ISSUE_SCHEMA)
