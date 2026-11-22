"""Data-quality rules.

Every rule takes ``(con, source, snapshot_date)`` and returns issue rows with
``entity_key, field, value, message``. Rules read the *raw* snapshot tables.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from itertools import pairwise

import duckdb
import polars as pl

from slo.store import fetch_one, query_frame

ISSUE_SCHEMA = {
    "entity_key": pl.String,
    "field": pl.String,
    "value": pl.String,
    "message": pl.String,
}

Rule = Callable[[duckdb.DuckDBPyConnection, str, date], pl.DataFrame]
RULES: dict[str, Rule] = {}


def rule(rule_id: str) -> Callable[[Rule], Rule]:
    def register(fn: Rule) -> Rule:
        RULES[rule_id] = fn
        return fn

    return register


def _sql(con: duckdb.DuckDBPyConnection, sql: str, source: str, d: date) -> pl.DataFrame:
    df = query_frame(con, sql, {"src": source, "d": d})
    if df.height == 0:
        return pl.DataFrame(schema=ISSUE_SCHEMA)
    return df.select(
        pl.col("entity_key").cast(pl.String),
        pl.col("field").cast(pl.String),
        pl.col("value").cast(pl.String),
        pl.col("message").cast(pl.String),
    )


SNAP = "source = $src AND snapshot_date = $d"


@rule("DQ001")
def no_primary_name(con, source, d):
    return _sql(
        con,
        f"""
        SELECT entity_key, 'primary_name' AS field, primary_name AS value,
               'entity has no primary name' AS message
        FROM entities WHERE {SNAP} AND coalesce(trim(primary_name), '') = ''
    """,
        source,
        d,
    )


@rule("DQ002")
def whitespace_noise(con, source, d):
    return _sql(
        con,
        f"""
        SELECT entity_key, 'full_name' AS field, full_name AS value,
               'leading/trailing/double whitespace in name' AS message
        FROM names WHERE {SNAP} AND trim(full_name) <> ''
          AND full_name <> regexp_replace(trim(full_name), '\\s+', ' ', 'g')
    """,
        source,
        d,
    )


@rule("DQ003")
def non_latin_without_latin(con, source, d):
    return _sql(
        con,
        f"""
        SELECT entity_key, 'names' AS field, any_value(full_name) AS value,
               'non-Latin name(s) but no Latin-script name' AS message
        FROM names WHERE {SNAP} AND trim(full_name) <> ''
        GROUP BY entity_key
        HAVING count(*) FILTER (WHERE script IN ('arabic','cyrillic','cjk','other')) > 0
           AND count(*) FILTER (WHERE script = 'latin') = 0
    """,
        source,
        d,
    )


@rule("DQ004")
def empty_alias(con, source, d):
    return _sql(
        con,
        f"""
        SELECT entity_key, 'full_name' AS field, full_name AS value,
               'empty ' || name_type || ' element' AS message
        FROM names WHERE {SNAP} AND trim(coalesce(full_name, '')) = ''
    """,
        source,
        d,
    )


@rule("DQ005")
def duplicate_names(con, source, d):
    return _sql(
        con,
        f"""
        SELECT entity_key, 'norm_name' AS field, norm_name AS value,
               count(*) || ' names normalise to the same key' AS message
        FROM names WHERE {SNAP} AND norm_name <> ''
        GROUP BY entity_key, norm_name, script = 'latin' HAVING count(*) > 1
    """,
        source,
        d,
    )


@rule("DQ010")
def person_no_dob(con, source, d):
    return _sql(
        con,
        """
        SELECT e.entity_key, 'birth_dates' AS field, NULL AS value,
               'person has no date of birth' AS message
        FROM entities e
        WHERE e.source = $src AND e.snapshot_date = $d AND e.entity_type = 'person'
          AND NOT EXISTS (SELECT 1 FROM birth_dates b WHERE b.entity_key = e.entity_key
                          AND b.snapshot_date = e.snapshot_date)
    """,
        source,
        d,
    )


@rule("DQ011")
def person_no_full_dob(con, source, d):
    return _sql(
        con,
        """
        SELECT b.entity_key, 'precision' AS field, string_agg(DISTINCT b.precision, ',') AS value,
               'no day-precision DOB (only ' || string_agg(DISTINCT b.precision, ',') || ')' AS message
        FROM birth_dates b JOIN entities e USING (entity_key, snapshot_date)
        WHERE b.source = $src AND b.snapshot_date = $d AND e.entity_type = 'person'
        GROUP BY b.entity_key
        HAVING count(*) FILTER (WHERE b.precision = 'day') = 0
           AND count(*) FILTER (WHERE b.precision IN ('year','month','range')) > 0
    """,
        source,
        d,
    )


@rule("DQ012")
def consecutive_year_dobs(con, source, d):
    df = query_frame(
        con,
        f"SELECT entity_key, year(date_from) AS y FROM birth_dates "
        f"WHERE {SNAP} AND precision = 'year' AND NOT circa",
        {"src": source, "d": d},
    )
    rows = []
    for key, grp in df.group_by("entity_key"):
        years = sorted(set(grp["y"].to_list()))
        if len(years) >= 2 and any(b - a == 1 for a, b in pairwise(years)):
            rows.append(
                {
                    "entity_key": key[0],
                    "field": "birth_dates",
                    "value": ",".join(map(str, years)),
                    "message": f"{len(years)} single-year DOBs, consecutive years -> express as a range",
                }
            )
    return pl.DataFrame(rows, schema=ISSUE_SCHEMA)


@rule("DQ013")
def unparseable_dob(con, source, d):
    return _sql(
        con,
        f"""
        SELECT entity_key, 'raw' AS field, raw AS value, 'DOB cannot be parsed' AS message
        FROM birth_dates WHERE {SNAP} AND precision = 'unparseable'
    """,
        source,
        d,
    )


@rule("DQ014")
def invalid_dob(con, source, d):
    return _sql(
        con,
        f"""
        SELECT entity_key, 'raw' AS field, raw AS value,
               'DOB before 1900 or in the future' AS message
        FROM birth_dates WHERE {SNAP} AND precision = 'invalid'
    """,
        source,
        d,
    )


@rule("DQ020")
def unmapped_country(con, source, d):
    return _sql(
        con,
        f"""
        SELECT entity_key, role AS field, raw AS value,
               'country not mappable to ISO 3166-1' AS message
        FROM countries WHERE {SNAP} AND country_iso2 IS NULL AND trim(coalesce(raw, '')) <> ''
    """,
        source,
        d,
    )


@rule("DQ021")
def person_no_nationality(con, source, d):
    return _sql(
        con,
        """
        SELECT e.entity_key, 'countries' AS field, NULL AS value,
               'person has no nationality/citizenship' AS message
        FROM entities e WHERE e.source = $src AND e.snapshot_date = $d AND e.entity_type = 'person'
          AND NOT EXISTS (SELECT 1 FROM countries c WHERE c.entity_key = e.entity_key
                          AND c.snapshot_date = e.snapshot_date
                          AND c.role IN ('nationality','citizenship') AND c.country_iso2 IS NOT NULL)
    """,
        source,
        d,
    )


@rule("DQ030")
def id_noise(con, source, d):
    return _sql(
        con,
        f"""
        SELECT entity_key, id_type AS field, raw_number AS value,
               'identifier contains whitespace/punctuation' AS message
        FROM identifiers WHERE {SNAP} AND regexp_matches(raw_number, '[\\s./-]')
    """,
        source,
        d,
    )


@rule("DQ031")
def id_repeated_within_entity(con, source, d):
    return _sql(
        con,
        f"""
        SELECT entity_key, id_type AS field, norm_number AS value,
               'identifier listed ' || count(*) || ' times on the same entity' AS message
        FROM identifiers WHERE {SNAP} AND norm_number <> ''
        GROUP BY entity_key, id_type, norm_number HAVING count(*) > 1
    """,
        source,
        d,
    )


@rule("DQ032")
def id_shared_across_entities(con, source, d):
    return _sql(
        con,
        f"""
        WITH ids AS (
            SELECT DISTINCT entity_key, id_type, norm_number, coalesce(country_iso2, '') AS c
            FROM identifiers WHERE {SNAP} AND length(norm_number) >= 5
        )
        SELECT a.entity_key, a.id_type AS field, a.norm_number AS value,
               'same identifier also on ' || string_agg(DISTINCT b.entity_key, ', ') AS message
        FROM ids a JOIN ids b ON a.id_type = b.id_type AND a.norm_number = b.norm_number
             AND a.c = b.c AND a.entity_key <> b.entity_key
        GROUP BY a.entity_key, a.id_type, a.norm_number
    """,
        source,
        d,
    )


@rule("DQ033")
def id_without_country(con, source, d):
    return _sql(
        con,
        f"""
        SELECT entity_key, id_type AS field, raw_number AS value,
               id_type || ' without issuing country' AS message
        FROM identifiers WHERE {SNAP} AND id_type IN ('passport','national_id') AND country_iso2 IS NULL
    """,
        source,
        d,
    )


@rule("DQ040")
def duplicate_entities(con, source, d):
    return _sql(
        con,
        f"""
        WITH p AS (
            SELECT n.entity_key, n.norm_tokens_sorted AS k, e.entity_type
            FROM names n JOIN entities e USING (entity_key, snapshot_date)
            WHERE n.source = $src AND n.snapshot_date = $d AND n.name_type = 'primary'
              AND n.norm_tokens_sorted <> '' AND e.entity_type IN ('person','organisation')
        ),
        dob AS (
            SELECT entity_key, min(date_from) AS lo, max(date_to) AS hi
            FROM birth_dates WHERE {SNAP} AND date_from IS NOT NULL GROUP BY entity_key
        ),
        pairs AS (
            SELECT a.entity_key, b.entity_key AS other, a.k
            FROM p a JOIN p b ON a.k = b.k AND a.entity_type = b.entity_type
                 AND a.entity_key <> b.entity_key
            LEFT JOIN dob da ON da.entity_key = a.entity_key
            LEFT JOIN dob db ON db.entity_key = b.entity_key
            WHERE a.entity_type = 'organisation'
               OR (da.lo IS NOT NULL AND db.lo IS NOT NULL AND da.lo <= db.hi AND db.lo <= da.hi)
        )
        SELECT entity_key, 'primary_name' AS field, any_value(k) AS value,
               'possible duplicate of ' || string_agg(DISTINCT other, ', ') AS message
        FROM pairs GROUP BY entity_key
    """,
        source,
        d,
    )


@rule("DQ060")
def release_counts(con, source, d):
    run = fetch_one(
        con,
        "SELECT record_count_source, record_count_parsed, error FROM runs "
        "WHERE source = $src AND snapshot_date = $d ORDER BY started_at DESC LIMIT 1",
        {"src": source, "d": d},
    )
    rows = []
    if run is not None:
        declared, parsed, error = run
        if declared is not None and parsed != declared:
            rows.append(
                {
                    "entity_key": None,
                    "field": "record_count",
                    "value": f"{parsed}/{declared}",
                    "message": f"parsed {parsed} records but source declares/contains {declared}",
                }
            )
        if error:
            rows.append(
                {
                    "entity_key": None,
                    "field": "parse",
                    "value": error[:200],
                    "message": "parse failures during load",
                }
            )
    prev = fetch_one(
        con,
        "SELECT snapshot_date, count(*) FROM entities WHERE source = $src AND snapshot_date < $d "
        "GROUP BY 1 ORDER BY 1 DESC LIMIT 1",
        {"src": source, "d": d},
    )
    cur = fetch_one(con, f"SELECT count(*) FROM entities WHERE {SNAP}", {"src": source, "d": d})
    if prev and cur and prev[1] and abs(cur[0] - prev[1]) / prev[1] > 0.05:
        rows.append(
            {
                "entity_key": None,
                "field": "record_count",
                "value": f"{prev[1]}->{cur[0]}",
                "message": f"entity count changed by more than 5% since {prev[0]}",
            }
        )
    return pl.DataFrame(rows, schema=ISSUE_SCHEMA)


@rule("DQ061")
def release_fingerprint(con, source, d):
    runs = con.execute(
        "SELECT snapshot_date, raw_sha256, published_date FROM runs WHERE source = $src "
        "AND snapshot_date <= $d AND status = 'ok' ORDER BY snapshot_date DESC, started_at DESC",
        {"src": source, "d": d},
    ).fetchall()
    by_date: dict[date, tuple[str, date | None]] = {}
    for snap, sha, pub in runs:
        by_date.setdefault(snap, (sha, pub))
    dates = sorted(by_date, reverse=True)
    if len(dates) < 2 or dates[0] != d:
        return pl.DataFrame(schema=ISSUE_SCHEMA)
    (sha_now, pub_now), (sha_prev, pub_prev) = by_date[dates[0]], by_date[dates[1]]
    rows = []
    if pub_now is None or pub_prev is None:
        return pl.DataFrame(schema=ISSUE_SCHEMA)
    if sha_now == sha_prev and pub_now != pub_prev:
        rows.append(
            {
                "entity_key": None,
                "field": "published_date",
                "value": f"{pub_prev}->{pub_now}",
                "message": "file content unchanged but publish date changed",
            }
        )
    if sha_now != sha_prev and pub_now == pub_prev:
        rows.append(
            {
                "entity_key": None,
                "field": "raw_sha256",
                "value": f"{sha_prev[:12]}->{sha_now[:12]}",
                "message": "file content changed but publish date did not",
            }
        )
    return pl.DataFrame(rows, schema=ISSUE_SCHEMA)
