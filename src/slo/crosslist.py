"""Cross-list linkage (UN <-> EU / UK / OFAC) and the cross-list DQ rules DQ050-DQ052."""

from __future__ import annotations

from datetime import date

import duckdb
import polars as pl

from slo.dq.rules import ISSUE_SCHEMA
from slo.store import fetch_one, insert_frame, query_frame


def _match_sql(a: str, b: str) -> str:
    """Entities of list ``a`` that share a >=2-token name key and type with list ``b``.

    Persons must also have overlapping DOBs when both sides publish one.
    """
    return f"""
    WITH na AS (
        SELECT DISTINCT n.entity_key, n.norm_tokens_sorted AS k, e.entity_type AS t
        FROM names n JOIN entities e USING (entity_key, snapshot_date)
        WHERE n.source = '{a}' AND n.snapshot_date = $da AND n.norm_tokens_sorted LIKE '% %'
          AND e.entity_type IN ('person','organisation')
    ),
    nb AS (
        SELECT DISTINCT n.entity_key, n.norm_tokens_sorted AS k, e.entity_type AS t
        FROM names n JOIN entities e USING (entity_key, snapshot_date)
        WHERE n.source = '{b}' AND n.snapshot_date = $db AND n.norm_tokens_sorted LIKE '% %'
          AND e.entity_type IN ('person','organisation')
    ),
    dob AS (
        SELECT entity_key, min(date_from) AS lo, max(date_to) AS hi FROM birth_dates
        WHERE ((source = '{a}' AND snapshot_date = $da) OR (source = '{b}' AND snapshot_date = $db))
          AND date_from IS NOT NULL GROUP BY entity_key
    )
    SELECT DISTINCT na.entity_key AS a_key, nb.entity_key AS b_key
    FROM na JOIN nb ON na.k = nb.k AND na.t = nb.t
    LEFT JOIN dob x ON x.entity_key = na.entity_key
    LEFT JOIN dob y ON y.entity_key = nb.entity_key
    WHERE na.t = 'organisation' OR x.lo IS NULL OR y.lo IS NULL
       OR (x.lo <= y.hi AND y.lo <= x.hi)
    """


def match_by_name(
    con: duckdb.DuckDBPyConnection, a: str, da: date, b: str, db: date
) -> pl.DataFrame:
    return query_frame(con, _match_sql(a, b), {"da": da, "db": db})


def _latest(con: duckdb.DuckDBPyConnection, source: str, on_or_before: date) -> date | None:
    row = fetch_one(
        con,
        "SELECT max(snapshot_date) FROM entities WHERE source = ? AND snapshot_date <= ?",
        [source, on_or_before],
    )
    return row[0] if row else None


def build_crosslinks(con: duckdb.DuckDBPyConnection, d: date) -> dict:
    """Link every UN entity to EU / UK / OFAC entities and compute overlap statistics."""
    dates = {s: _latest(con, s, d) for s in ("un", "eu", "uk", "ofac")}
    con.execute("DELETE FROM crosslinks WHERE snapshot_date = ?", [d])
    stats: dict = {"snapshot_dates": {k: str(v) if v else None for k, v in dates.items()}}
    if dates["un"] is None:
        return stats
    un = query_frame(
        con,
        "SELECT entity_key, un_reference, listed_on FROM entities WHERE source='un' AND snapshot_date=?",
        [dates["un"]],
    )
    links: dict[str, dict[str, tuple[str, str]]] = {k: {} for k in un["entity_key"]}
    ref_to_un = dict(zip(un["un_reference"], un["entity_key"], strict=True))
    for target in ("eu", "uk", "ofac"):
        if dates[target] is None:
            continue
        refs = con.execute(
            "SELECT entity_key, un_reference FROM entities WHERE source=? AND snapshot_date=? "
            "AND un_reference IS NOT NULL",
            [target, dates[target]],
        ).fetchall()
        for key, ref in refs:
            if ref in ref_to_un:
                links[ref_to_un[ref]][target] = (key, "un_ref")
        for row in match_by_name(con, "un", dates["un"], target, dates[target]).iter_rows():
            links[row[0]].setdefault(target, (row[1], "name_dob"))
    rows = []
    for un_key, ref in zip(un["entity_key"], un["un_reference"], strict=True):
        found = links[un_key]
        rows.append(
            {
                "snapshot_date": d,
                "un_reference": ref,
                "un_key": un_key,
                "eu_key": found.get("eu", (None,))[0],
                "uk_key": found.get("uk", (None,))[0],
                "ofac_key": found.get("ofac", (None,))[0],
                "link_method": ",".join(f"{t}:{m}" for t, (_, m) in sorted(found.items())) or None,
                "confidence": 1.0 if all(m == "un_ref" for _, m in found.values()) else 0.8,
            }
        )
    frame = pl.DataFrame(
        rows,
        schema={
            "snapshot_date": pl.Date,
            "un_reference": pl.String,
            "un_key": pl.String,
            "eu_key": pl.String,
            "uk_key": pl.String,
            "ofac_key": pl.String,
            "link_method": pl.String,
            "confidence": pl.Float64,
        },
    )
    insert_frame(con, "crosslinks", frame)
    n_un = frame.height
    stats.update(
        {
            "un_entities": n_un,
            "un_eu": frame["eu_key"].is_not_null().sum(),
            "un_uk": frame["uk_key"].is_not_null().sum(),
            "un_ofac": frame["ofac_key"].is_not_null().sum(),
            "un_eu_uk": (frame["eu_key"].is_not_null() & frame["uk_key"].is_not_null()).sum(),
            "linked_by_un_ref": {
                t: sum(1 for v in links.values() if v.get(t, ("", ""))[1] == "un_ref")
                for t in ("eu", "uk", "ofac")
            },
        }
    )
    pairs = {}
    for a, b in (("eu", "uk"), ("eu", "ofac"), ("uk", "ofac")):
        if dates[a] and dates[b]:
            m = match_by_name(con, a, dates[a], b, dates[b])
            pairs[f"{a}_{b}"] = m["a_key"].n_unique()
    stats["name_dob_overlap"] = pairs
    return stats


def crosslist_issues(
    con: duckdb.DuckDBPyConnection, d: date, target: str
) -> dict[str, pl.DataFrame]:
    """DQ050/DQ051/DQ052 issues for list ``target`` (eu or uk) at snapshot ``d``."""
    col = f"{target}_key"
    empty = {r: pl.DataFrame(schema=ISSUE_SCHEMA) for r in ("DQ050", "DQ051", "DQ052")}
    un_date = _latest(con, "un", d)
    if un_date is None:
        return empty
    gap = query_frame(
        con,
        f"""SELECT c.un_key AS entity_key, 'un_reference' AS field, c.un_reference AS value,
                   'UN-listed ' || coalesce(e.primary_name, '?') || ' not found on {target.upper()} list; '
                   || coalesce(CAST(date_diff('day', e.listed_on, $d) AS VARCHAR), '?')
                   || ' days since UN listing' AS message
            FROM crosslinks c JOIN entities e ON e.entity_key = c.un_key AND e.snapshot_date = $ud
            WHERE c.snapshot_date = $d AND c.{col} IS NULL""",
        {"d": d, "ud": un_date},
    )
    disagree = query_frame(
        con,
        f"""WITH linked AS (
                SELECT c.un_key, c.{col} AS other, c.un_reference FROM crosslinks c
                WHERE c.snapshot_date = $d AND c.link_method LIKE '%{target}:un_ref%'),
            shared AS (
                SELECT l.un_key, count(*) FILTER (WHERE a.norm_tokens_sorted = b.norm_tokens_sorted) AS same
                FROM linked l JOIN names a ON a.entity_key = l.un_key AND a.snapshot_date = $ud
                JOIN names b ON b.entity_key = l.other AND b.snapshot_date = $d
                GROUP BY l.un_key)
            SELECT l.other AS entity_key, 'names' AS field, l.un_reference AS value,
                   'shares UN reference with ' || l.un_key || ' but no name matches' AS message
            FROM linked l JOIN shared s USING (un_key) WHERE s.same = 0""",
        {"d": d, "ud": un_date},
    )
    stale = query_frame(
        con,
        f"""SELECT e.entity_key, 'un_reference' AS field, e.un_reference AS value,
                   'cites UN reference not on current UN list' AS message
            FROM entities e WHERE e.source = '{target}' AND e.snapshot_date = $d
              AND e.un_reference IS NOT NULL
              AND e.un_reference NOT IN (SELECT un_reference FROM entities
                                          WHERE source='un' AND snapshot_date=$ud
                                          AND un_reference IS NOT NULL)""",
        {"d": d, "ud": un_date},
    )

    def cast(df: pl.DataFrame) -> pl.DataFrame:
        if df.height == 0:
            return pl.DataFrame(schema=ISSUE_SCHEMA)
        return df.select([pl.col(c).cast(pl.String) for c in ISSUE_SCHEMA])

    return {"DQ050": cast(gap), "DQ051": cast(disagree), "DQ052": cast(stale)}
