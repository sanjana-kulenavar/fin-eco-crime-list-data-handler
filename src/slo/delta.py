"""Snapshot-to-snapshot deltas: what a screening system has to load today."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import duckdb
import polars as pl

from slo.store import insert_frame, previous_snapshot, query_frame

CHILD_TABLES = {
    "names": "full_name || '|' || name_type || '|' || coalesce(strength, '')",
    "birth_dates": "raw || '|' || circa",
    "identifiers": "id_type || '|' || raw_number",
    "countries": "role || '|' || coalesce(raw, '')",
    "addresses": "raw",
}
ENTITY_FIELDS = (
    "primary_name",
    "entity_type",
    "programmes",
    "listed_on",
    "un_reference",
    "gender",
    "remarks",
)


def _child_sets(
    con: duckdb.DuckDBPyConnection, table: str, expr: str, source: str, d: date, keys: list[str]
) -> dict[str, set[str]]:
    if not keys:
        return {}
    df = query_frame(
        con,
        f"SELECT entity_key, {expr} AS v FROM {table} WHERE source = $s AND snapshot_date = $d "
        "AND entity_key IN (SELECT unnest($k))",
        {"s": source, "d": d, "k": keys},
    )
    out: dict[str, set[str]] = {}
    for key, v in df.iter_rows():
        out.setdefault(key, set()).add(v)
    return out


def compute_delta(con: duckdb.DuckDBPyConnection, source: str, to_date: date) -> dict:
    from_date = previous_snapshot(con, source, to_date)
    new = query_frame(
        con,
        "SELECT entity_key, primary_name, record_hash FROM entities WHERE source = ? AND snapshot_date = ?",
        [source, to_date],
    )
    if from_date is None:
        changes = [
            {"entity_key": k, "change_type": "added", "changed_fields": [], "primary_name": n}
            for k, n in zip(new["entity_key"], new["primary_name"], strict=True)
        ]
        return _result(source, None, to_date, changes, initial=True)
    old = query_frame(
        con,
        "SELECT entity_key, primary_name, record_hash FROM entities WHERE source = ? AND snapshot_date = ?",
        [source, from_date],
    )
    old_h = dict(zip(old["entity_key"], old["record_hash"], strict=True))
    new_h = dict(zip(new["entity_key"], new["record_hash"], strict=True))
    names = dict(zip(new["entity_key"], new["primary_name"], strict=True)) | {
        k: v for k, v in zip(old["entity_key"], old["primary_name"], strict=True) if k not in new_h
    }
    added = sorted(set(new_h) - set(old_h))
    removed = sorted(set(old_h) - set(new_h))
    amended = sorted(k for k in set(new_h) & set(old_h) if new_h[k] != old_h[k])
    changed: dict[str, list[str]] = {k: [] for k in amended}
    if amended:
        cols = ", ".join(ENTITY_FIELDS)
        q = f"SELECT entity_key, {cols} FROM entities WHERE source = $s AND snapshot_date = $d AND entity_key IN (SELECT unnest($k))"
        a = {
            r[0]: r[1:]
            for r in con.execute(q, {"s": source, "d": from_date, "k": amended}).fetchall()
        }
        b = {
            r[0]: r[1:]
            for r in con.execute(q, {"s": source, "d": to_date, "k": amended}).fetchall()
        }
        for k in amended:
            changed[k] += [f for f, x, y in zip(ENTITY_FIELDS, a[k], b[k], strict=True) if x != y]
        for table, expr in CHILD_TABLES.items():
            sa = _child_sets(con, table, expr, source, from_date, amended)
            sb = _child_sets(con, table, expr, source, to_date, amended)
            for k in amended:
                if sa.get(k, set()) != sb.get(k, set()):
                    changed[k].append(table)
    changes = (
        [
            {
                "entity_key": k,
                "change_type": "added",
                "changed_fields": [],
                "primary_name": names[k],
            }
            for k in added
        ]
        + [
            {
                "entity_key": k,
                "change_type": "removed",
                "changed_fields": [],
                "primary_name": names[k],
            }
            for k in removed
        ]
        + [
            {
                "entity_key": k,
                "change_type": "amended",
                "changed_fields": changed[k],
                "primary_name": names[k],
            }
            for k in amended
        ]
    )
    return _result(source, from_date, to_date, changes, initial=False)


def _result(
    source: str, from_date: date | None, to_date: date, changes: list[dict], *, initial: bool
) -> dict:
    counts = {
        t: sum(1 for c in changes if c["change_type"] == t) for t in ("added", "removed", "amended")
    }
    return {
        "source": source,
        "from": str(from_date) if from_date else None,
        "to": str(to_date),
        "initial_load": initial,
        "counts": counts,
        "changes": changes,
    }


def store_delta(con: duckdb.DuckDBPyConnection, delta: dict, deltas_dir: Path) -> Path:
    to_date = date.fromisoformat(delta["to"])
    con.execute("DELETE FROM deltas WHERE source = ? AND to_date = ?", [delta["source"], to_date])
    frame = pl.DataFrame(
        [
            {
                "source": delta["source"],
                "from_date": date.fromisoformat(delta["from"]) if delta["from"] else None,
                "to_date": to_date,
                "entity_key": c["entity_key"],
                "change_type": c["change_type"],
                "changed_fields": c["changed_fields"],
            }
            for c in delta["changes"]
        ],
        schema={
            "source": pl.String,
            "from_date": pl.Date,
            "to_date": pl.Date,
            "entity_key": pl.String,
            "change_type": pl.String,
            "changed_fields": pl.List(pl.String),
        },
    )
    insert_frame(con, "deltas", frame)
    path = deltas_dir / delta["source"] / f"{delta['to']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(delta)
    if delta["initial_load"]:
        # An initial load lists every entity; keep the committed file small.
        payload = {**delta, "changes": delta["changes"][:25], "changes_truncated": True}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
