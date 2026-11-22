"""Deterministic auto-fixes.

Fixes never modify the raw snapshot tables. They build ``names_clean``,
``birth_dates_clean`` and ``identifiers_clean`` for one (source, snapshot_date),
log every change to ``fix_log`` and mark the matching ``dq_issues`` as fixed.
Approved change requests (alias enrichment, data corrections) are applied here too.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import polars as pl
from anyascii import anyascii

from slo.build import SCHEMAS, name_row
from slo.normalise.dates import parse_dob
from slo.normalise.names import collapse_ws
from slo.requests_log import active_requests
from slo.store import delete_snapshot, insert_frame, query_frame

FIXED_RULES = ("DQ002", "DQ003", "DQ004", "DQ005", "DQ012", "DQ030", "DQ031")
_NON_LATIN = ("arabic", "cyrillic", "cjk", "other")


class _Log:
    def __init__(self, source: str, d: date) -> None:
        self.rows: list[dict] = []
        self.source, self.d = source, d
        self.at = datetime.now(UTC).replace(tzinfo=None)

    def add(self, key: str, rule: str, before: object, after: object) -> None:
        self.rows.append(
            {
                "source": self.source,
                "snapshot_date": self.d,
                "entity_key": key,
                "rule_id": rule,
                "before": None if before is None else str(before),
                "after": None if after is None else str(after),
                "applied_at": self.at,
            }
        )


def _read(con: duckdb.DuckDBPyConnection, table: str, source: str, d: date) -> pl.DataFrame:
    df = query_frame(
        con, f"SELECT * FROM {table} WHERE source = ? AND snapshot_date = ?", [source, d]
    )
    schema = SCHEMAS[table]
    return df.select(list(schema)).cast(schema) if df.height else pl.DataFrame(schema=schema)


def clean_names(
    names: pl.DataFrame,
    entity_types: dict[str, str],
    source: str,
    d: date,
    requests_dir: Path | None,
    log: _Log,
) -> pl.DataFrame:
    rows = []
    for r in names.iter_rows(named=True):
        if not collapse_ws(r["full_name"]):
            log.add(r["entity_key"], "DQ004", r["full_name"], None)  # drop empty alias
            continue
        fixed = collapse_ws(r["full_name"])
        if fixed != r["full_name"]:
            log.add(r["entity_key"], "DQ002", repr(r["full_name"]), fixed)
            r = {**r, "full_name": fixed}
        rows.append(r)
    # DQ003: entity with only non-Latin names -> add a generated transliterated alias.
    by_entity: dict[str, list[dict]] = {}
    for r in rows:
        by_entity.setdefault(r["entity_key"], []).append(r)
    extra = []
    for key, ents in by_entity.items():
        if any(r["script"] == "latin" for r in ents):
            continue
        for i, r in enumerate(e for e in ents if e["script"] in _NON_LATIN):
            translit = collapse_ws(anyascii(r["full_name"])).title()
            if not translit:
                continue
            new = name_row(
                source,
                d,
                key,
                9000 + i,
                full_name=translit,
                name_type="alias",
                strength="weak",
                generated=True,
                is_org=entity_types.get(key) != "person",
            )
            extra.append(new)
            log.add(key, "DQ003", r["full_name"], translit)
    # Approved alias-enrichment requests.
    for req in active_requests(requests_dir, "alias_enrichment") if requests_dir else []:
        key = req.target_entity_key
        if req.target_source != source or key not in entity_types:
            continue
        for i, alias in enumerate(req.payload.get("aliases", [])):
            extra.append(
                name_row(
                    source,
                    d,
                    key,
                    9500 + i,
                    full_name=alias,
                    name_type="alias",
                    strength="strong",
                    generated=True,
                    is_org=entity_types.get(key) != "person",
                )
            )
            log.add(key, f"REQ:{req.request_id}", None, alias)
    rows += extra

    # DQ005: de-duplicate by normalised key within a script (a Latin rendering of a
    # non-Latin name is kept even though both normalise to the same key);
    # keep primary, then strong, then original order.
    def rank(r: dict) -> tuple:
        return (
            r["name_type"] != "primary",
            r["strength"] != "strong",
            bool(r["generated"]),
            r["name_id"],
        )

    kept, seen = [], set()
    for r in sorted(rows, key=lambda r: (r["entity_key"], *rank(r))):
        k = (r["entity_key"], r["norm_name"], r["script"] == "latin")
        if k in seen:
            log.add(r["entity_key"], "DQ005", r["full_name"], None)
            continue
        seen.add(k)
        kept.append(r)
    return pl.DataFrame(kept, schema=SCHEMAS["names"], orient="row")


def clean_birth_dates(
    dobs: pl.DataFrame, source: str, d: date, requests_dir: Path | None, log: _Log
) -> pl.DataFrame:
    corrections = {
        r.target_entity_key: r
        for r in (active_requests(requests_dir, "data_correction") if requests_dir else [])
        if r.target_source == source and r.payload.get("field") == "dob"
    }
    out = []
    by_entity: dict[str, list[dict]] = {}
    for r in dobs.iter_rows(named=True):
        by_entity.setdefault(r["entity_key"], []).append(r)
    for key, rows in by_entity.items():
        if key in corrections:
            req = corrections[key]
            parsed = parse_dob(req.payload["value"])
            out.append(
                {
                    "source": source,
                    "snapshot_date": d,
                    "entity_key": key,
                    "raw": req.payload["value"],
                    "date_from": parsed.date_from,
                    "date_to": parsed.date_to,
                    "precision": parsed.precision,
                    "circa": parsed.circa,
                }
            )
            log.add(
                key, f"REQ:{req.request_id}", ";".join(r["raw"] for r in rows), req.payload["value"]
            )
            continue
        singles = sorted(
            {
                r["date_from"].year
                for r in rows
                if r["precision"] == "year" and not r["circa"] and r["date_from"]
            }
        )
        others = [
            r for r in rows if not (r["precision"] == "year" and not r["circa"] and r["date_from"])
        ]
        runs: list[list[int]] = []
        for y in singles:
            if runs and y == runs[-1][-1] + 1:
                runs[-1].append(y)
            else:
                runs.append([y])
        for run in runs:
            if len(run) == 1:
                out.append(
                    next(
                        r
                        for r in rows
                        if r["precision"] == "year"
                        and r["date_from"]
                        and r["date_from"].year == run[0]
                        and not r["circa"]
                    )
                )
                continue
            raw = f"{run[0]} to {run[-1]}"
            parsed = parse_dob(raw)
            out.append(
                {
                    "source": source,
                    "snapshot_date": d,
                    "entity_key": key,
                    "raw": raw,
                    "date_from": parsed.date_from,
                    "date_to": parsed.date_to,
                    "precision": "range",
                    "circa": False,
                }
            )
            log.add(key, "DQ012", ",".join(map(str, run)), raw)
        out.extend(others)
    return pl.DataFrame(out, schema=SCHEMAS["birth_dates"], orient="row")


def clean_identifiers(ids: pl.DataFrame, log: _Log) -> pl.DataFrame:
    out, seen = [], set()
    for r in ids.iter_rows(named=True):
        k = (r["entity_key"], r["id_type"], r["norm_number"])
        if k in seen:
            log.add(r["entity_key"], "DQ031", r["raw_number"], None)
            continue
        seen.add(k)
        if r["raw_number"] != r["norm_number"]:
            log.add(r["entity_key"], "DQ030", r["raw_number"], r["norm_number"])
        out.append({**r, "raw_number": r["norm_number"]})
    return pl.DataFrame(out, schema=SCHEMAS["identifiers"], orient="row")


def build_clean(
    con: duckdb.DuckDBPyConnection, source: str, d: date, requests_dir: Path | None = None
) -> dict[str, int]:
    """Rebuild the clean tables for one snapshot. Idempotent."""
    log = _Log(source, d)
    types = dict(
        con.execute(
            "SELECT entity_key, entity_type FROM entities WHERE source = ? AND snapshot_date = ?",
            [source, d],
        ).fetchall()
    )
    names = clean_names(_read(con, "names", source, d), types, source, d, requests_dir, log)
    dobs = clean_birth_dates(_read(con, "birth_dates", source, d), source, d, requests_dir, log)
    ids = clean_identifiers(_read(con, "identifiers", source, d), log)
    delete_snapshot(
        con, source, d, ("names_clean", "birth_dates_clean", "identifiers_clean", "fix_log")
    )
    insert_frame(con, "names_clean", names)
    insert_frame(con, "birth_dates_clean", dobs)
    insert_frame(con, "identifiers_clean", ids)
    fix_log = pl.DataFrame(
        log.rows,
        schema={
            "source": pl.String,
            "snapshot_date": pl.Date,
            "entity_key": pl.String,
            "rule_id": pl.String,
            "before": pl.String,
            "after": pl.String,
            "applied_at": pl.Datetime,
        },
    )
    insert_frame(con, "fix_log", fix_log)
    # Mark issues fixed where a fix for the same rule touched the same entity.
    con.execute(
        f"""UPDATE dq_issues SET fixed = TRUE
            WHERE source = $s AND snapshot_date = $d AND rule_id IN {FIXED_RULES}
              AND entity_key IN (SELECT entity_key FROM fix_log
                                 WHERE source = $s AND snapshot_date = $d
                                   AND fix_log.rule_id = dq_issues.rule_id)""",
        {"s": source, "d": d},
    )
    counts = {r: 0 for r in FIXED_RULES}
    for row in log.rows:
        counts[row["rule_id"]] = counts.get(row["rule_id"], 0) + 1
    return {
        "names_clean": names.height,
        "birth_dates_clean": dobs.height,
        "identifiers_clean": ids.height,
        "fixes": counts,
    }
