"""Evaluate raw vs clean screening runs against the planted truth."""

from __future__ import annotations

from datetime import date
from itertools import combinations

import duckdb
import polars as pl

from slo.crosslist import match_by_name


def equivalence_groups(
    con: duckdb.DuckDBPyConnection, d: date, sources: list[str]
) -> dict[str, set[str]]:
    """Entities that are the same real-world target on different lists.

    An alert on a planted customer counts as a true hit if it is on the planted
    entity *or* on one of its cross-list equivalents (UN reference or name+DOB link).
    """
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        parent[find(a)] = find(b)

    for row in con.execute(
        "SELECT un_key, eu_key, uk_key, ofac_key FROM crosslinks WHERE snapshot_date = ?", [d]
    ).fetchall():
        keys = [k for k in row if k]
        for k in keys[1:]:
            union(keys[0], k)
    for a, b in combinations(sorted(sources), 2):
        for x, y in match_by_name(con, a, d, b, d).iter_rows():
            union(x, y)
    groups: dict[str, set[str]] = {}
    for k in list(parent):
        groups.setdefault(find(k), set()).add(k)
    return {k: groups[find(k)] for k in parent}


def evaluate(
    pairs: pl.DataFrame,
    truth: pl.DataFrame,
    n_customers: int,
    equivalents: dict[str, set[str]],
    thresholds: list[int],
    default_threshold: int,
    target_recall: float = 0.95,
) -> dict:
    planted = {c: e for c, e in truth.select("cust_id", "entity_key").iter_rows()}
    kind = dict(truth.select("cust_id", "perturbation").iter_rows())
    n_hits = len(planted)

    def is_true(cust: str, ent: str) -> bool:
        target = planted.get(cust)
        return target is not None and (ent == target or ent in equivalents.get(target, set()))

    rows = pairs.select("cust_id", "entity_key", "final_score").iter_rows()
    scored = [(c, e, s, is_true(c, e)) for c, e, s in rows]
    sweep = []
    for t in thresholds:
        alerts = [(c, e, ok) for c, e, s, ok in scored if s >= t]
        found = {c for c, _, ok in alerts if ok}
        alerted = {c for c, _, _ in alerts}
        fp_customers = {c for c in alerted if c not in planted}
        fp_alerts = sum(1 for c, _, _ in alerts if c not in planted)
        sweep.append(
            {
                "threshold": t,
                "alerts": len(alerts),
                "alerted_customers": len(alerted),
                "true_hits_found": len(found),
                "recall": round(len(found) / n_hits, 4) if n_hits else None,
                "precision": round(len(found) / len(alerted), 4) if alerted else None,
                "false_positive_alerts": fp_alerts,
                "false_positive_customers": len(fp_customers),
                "fp_per_1k_customers": round(1000 * len(fp_customers) / n_customers, 2),
            }
        )
    at_default = next(s for s in sweep if s["threshold"] == default_threshold)
    ok = [s for s in sweep if s["recall"] is not None and s["recall"] >= target_recall]
    operating = max(ok, key=lambda s: s["threshold"]) if ok else None
    per_kind = {}
    for k in sorted(set(kind.values())):
        custs = [c for c, v in kind.items() if v == k]
        hit = {
            c for c, e, s, good in scored if good and s >= default_threshold and kind.get(c) == k
        }
        per_kind[k] = {
            "planted": len(custs),
            "found": len(hit),
            "recall": round(len(hit) / len(custs), 4),
        }
    return {
        "n_customers": n_customers,
        "n_planted_hits": n_hits,
        "default_threshold": default_threshold,
        "at_default_threshold": at_default,
        "operating_point_95_recall": operating,
        "recall_by_perturbation": per_kind,
        "sweep": sweep,
    }


def missed_hits(
    pairs: pl.DataFrame, truth: pl.DataFrame, equivalents: dict[str, set[str]], threshold: int
) -> pl.DataFrame:
    """Planted hits not alerted at ``threshold``, with the best score they did get."""
    planted = dict(truth.select("cust_id", "entity_key").iter_rows())
    true_best: dict[str, float] = {}
    for c, e, s in pairs.select("cust_id", "entity_key", "final_score").iter_rows():
        tgt = planted.get(c)
        if tgt and (e == tgt or e in equivalents.get(tgt, set())):
            true_best[c] = max(true_best.get(c, -1.0), s)
    return truth.with_columns(
        pl.col("cust_id")
        .replace_strict(true_best, default=None, return_dtype=pl.Float64)
        .alias("best_true_score")
    ).filter(pl.col("best_true_score").is_null() | (pl.col("best_true_score") < threshold))


def top_false_positives(
    pairs: pl.DataFrame,
    truth: pl.DataFrame,
    customers: pl.DataFrame,
    entity_names: dict[str, str],
    threshold: int,
    n: int = 30,
) -> pl.DataFrame:
    planted = set(truth["cust_id"])
    fp = pairs.filter(
        ~pl.col("cust_id").is_in(list(planted)) & (pl.col("final_score") >= threshold)
    )
    fp = fp.join(customers.select("cust_id", "full_name", "dob", "nationality_iso2"), on="cust_id")
    fp = fp.with_columns(
        pl.col("entity_key").replace_strict(entity_names, default=None).alias("list_primary_name")
    )
    return fp.sort(["final_score", "cust_id"], descending=[True, False]).head(n)
