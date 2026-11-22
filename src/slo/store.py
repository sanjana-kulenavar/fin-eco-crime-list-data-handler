"""DuckDB storage: schema, idempotent snapshot loads and read helpers."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import polars as pl

SNAPSHOT_TABLES = ("entities", "names", "birth_dates", "identifiers", "countries", "addresses")
CLEAN_TABLES = ("names_clean", "birth_dates_clean", "identifiers_clean")

_NAME_COLS = """
    source VARCHAR, snapshot_date DATE, entity_key VARCHAR, name_id VARCHAR,
    full_name VARCHAR, first_name VARCHAR, middle_name VARCHAR, last_name VARCHAR,
    name_type VARCHAR, strength VARCHAR, script VARCHAR, language VARCHAR,
    norm_name VARCHAR, norm_tokens_sorted VARCHAR, generated BOOLEAN
"""
_DOB_COLS = """
    source VARCHAR, snapshot_date DATE, entity_key VARCHAR, raw VARCHAR,
    date_from DATE, date_to DATE, precision VARCHAR, circa BOOLEAN
"""
_ID_COLS = """
    source VARCHAR, snapshot_date DATE, entity_key VARCHAR, id_type VARCHAR,
    raw_number VARCHAR, norm_number VARCHAR, country_iso2 VARCHAR, notes VARCHAR
"""

SCHEMA_DDL = f"""
CREATE TABLE IF NOT EXISTS entities (
    source VARCHAR, snapshot_date DATE, source_id VARCHAR, entity_key VARCHAR,
    entity_type VARCHAR, primary_name VARCHAR, programmes VARCHAR[], listed_on DATE,
    last_updated DATE, un_reference VARCHAR, gender VARCHAR, remarks VARCHAR,
    record_hash VARCHAR
);
CREATE TABLE IF NOT EXISTS names ({_NAME_COLS});
CREATE TABLE IF NOT EXISTS birth_dates ({_DOB_COLS});
CREATE TABLE IF NOT EXISTS identifiers ({_ID_COLS});
CREATE TABLE IF NOT EXISTS countries (
    source VARCHAR, snapshot_date DATE, entity_key VARCHAR, role VARCHAR,
    raw VARCHAR, country_iso2 VARCHAR
);
CREATE TABLE IF NOT EXISTS addresses (
    source VARCHAR, snapshot_date DATE, entity_key VARCHAR, line VARCHAR, city VARCHAR,
    postal_code VARCHAR, country_iso2 VARCHAR, raw VARCHAR
);
CREATE TABLE IF NOT EXISTS names_clean ({_NAME_COLS});
CREATE TABLE IF NOT EXISTS birth_dates_clean ({_DOB_COLS});
CREATE TABLE IF NOT EXISTS identifiers_clean ({_ID_COLS});
CREATE TABLE IF NOT EXISTS runs (
    run_id VARCHAR, started_at TIMESTAMP, finished_at TIMESTAMP, source VARCHAR,
    snapshot_date DATE, raw_path VARCHAR, raw_sha256 VARCHAR, raw_bytes BIGINT,
    published_date DATE, record_count_source INTEGER, record_count_parsed INTEGER,
    status VARCHAR, error VARCHAR
);
CREATE TABLE IF NOT EXISTS dq_issues (
    run_id VARCHAR, source VARCHAR, snapshot_date DATE, entity_key VARCHAR, rule_id VARCHAR,
    severity VARCHAR, field VARCHAR, value VARCHAR, message VARCHAR,
    auto_fixable BOOLEAN, fixed BOOLEAN
);
CREATE TABLE IF NOT EXISTS fix_log (
    source VARCHAR, snapshot_date DATE, entity_key VARCHAR, rule_id VARCHAR,
    before VARCHAR, after VARCHAR, applied_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS deltas (
    source VARCHAR, from_date DATE, to_date DATE, entity_key VARCHAR,
    change_type VARCHAR, changed_fields VARCHAR[]
);
CREATE TABLE IF NOT EXISTS crosslinks (
    snapshot_date DATE, un_reference VARCHAR, un_key VARCHAR, eu_key VARCHAR,
    uk_key VARCHAR, ofac_key VARCHAR, link_method VARCHAR, confidence DOUBLE
);
"""


def connect(path: str | Path = ":memory:") -> duckdb.DuckDBPyConnection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(SCHEMA_DDL)
    return con


def table_columns(con: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    return [r[0] for r in con.execute(f"DESCRIBE {table}").fetchall()]


def insert_frame(con: duckdb.DuckDBPyConnection, table: str, frame: pl.DataFrame) -> int:
    """Insert ``frame`` by column name; missing columns become NULL."""
    if frame.height == 0:
        return 0
    cols = table_columns(con, table)
    unknown = set(frame.columns) - set(cols)
    if unknown:
        raise ValueError(f"{table}: unknown columns {sorted(unknown)}")
    con.register("_frame", frame.to_arrow())
    try:
        con.execute(f"INSERT INTO {table} BY NAME SELECT * FROM _frame")
    finally:
        con.unregister("_frame")
    return frame.height


def delete_snapshot(
    con: duckdb.DuckDBPyConnection, source: str, snapshot_date: date, tables: tuple[str, ...]
) -> None:
    for table in tables:
        con.execute(
            f"DELETE FROM {table} WHERE source = ? AND snapshot_date = ?", [source, snapshot_date]
        )


def replace_snapshot(
    con: duckdb.DuckDBPyConnection,
    source: str,
    snapshot_date: date,
    frames: dict[str, pl.DataFrame],
) -> dict[str, int]:
    """Idempotently replace one (source, snapshot_date) across the snapshot tables."""
    con.execute("BEGIN")
    try:
        delete_snapshot(con, source, snapshot_date, SNAPSHOT_TABLES)
        counts = {t: insert_frame(con, t, frames[t]) for t in SNAPSHOT_TABLES if t in frames}
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    return counts


def snapshot_dates(con: duckdb.DuckDBPyConnection, source: str) -> list[date]:
    rows = con.execute(
        "SELECT DISTINCT snapshot_date FROM entities WHERE source = ? ORDER BY 1", [source]
    ).fetchall()
    return [r[0] for r in rows]


def previous_snapshot(con: duckdb.DuckDBPyConnection, source: str, before: date) -> date | None:
    earlier = [d for d in snapshot_dates(con, source) if d < before]
    return earlier[-1] if earlier else None


def query_frame(
    con: duckdb.DuckDBPyConnection, sql: str, params: list | dict | None = None
) -> pl.DataFrame:
    """Run a query and return a polars DataFrame (via Arrow)."""
    rel = con.sql(sql, params=params) if params else con.sql(sql)
    return pl.from_arrow(rel.to_arrow_table())  # type: ignore[return-value]


def fetch_one(
    con: duckdb.DuckDBPyConnection, sql: str, params: list | dict | None = None
) -> tuple | None:
    """First row of a query (reads the result to completion, as DuckDB 2 requires)."""
    rows = con.execute(sql, params or []).fetchall()
    return rows[0] if rows else None
