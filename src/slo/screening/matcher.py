"""A deliberately simple, explainable name screening matcher.

Mode ``raw`` screens the lists as published with no normalisation beyond
lower-casing (what a naive integration would do). Mode ``clean`` screens the
DQ-fixed tables with the shared normaliser (transliteration, punctuation,
honorific/particle variants, company suffixes).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import duckdb
import numpy as np
import polars as pl
from rapidfuzz import fuzz, process
from rapidfuzz.distance import JaroWinkler

from slo.normalise.names import naive_key, name_variants, normalise_name
from slo.requests_log import active_requests
from slo.store import query_frame

PAIR_SCHEMA = {
    "cust_id": pl.String,
    "entity_key": pl.String,
    "matched_name": pl.String,
    "name_score": pl.Float64,
    "final_score": pl.Float64,
}


@dataclass
class ListIndex:
    mode: str
    names: list[str] = field(default_factory=list)
    name_entity: list[str] = field(default_factory=list)
    entity_type: dict[str, str] = field(default_factory=dict)
    dobs: dict[str, list[tuple[date, date]]] = field(default_factory=dict)
    nationalities: dict[str, set[str]] = field(default_factory=dict)
    tokens: dict[str, list[int]] = field(default_factory=dict)
    prefixes: dict[str, list[int]] = field(default_factory=dict)
    suppressed: set[tuple[str, str]] = field(default_factory=set)


def _keys(text: str, mode: str, is_org: bool) -> list[str]:
    if mode == "raw":
        k = naive_key(text)
        return [k] if k else []
    return name_variants(normalise_name(text, is_org=is_org))


def _block_keys(key: str, min_len: int) -> tuple[list[str], str | None]:
    toks = [t for t in key.split() if len(t) >= min_len]
    longest = max(key.split(), key=len) if key.split() else ""
    return toks, (longest[:3] if len(longest) >= 3 else None)


def build_index(
    con: duckdb.DuckDBPyConnection,
    d: date,
    sources: list[str],
    mode: str,
    min_token_len: int = 3,
    requests_dir: Path | None = None,
) -> ListIndex:
    names_t, dobs_t = (
        ("names", "birth_dates") if mode == "raw" else ("names_clean", "birth_dates_clean")
    )
    p = {"d": d, "s": sources}
    idx = ListIndex(mode)
    ents = query_frame(
        con,
        "SELECT entity_key, entity_type FROM entities WHERE snapshot_date = $d "
        "AND source IN (SELECT unnest($s)) AND entity_type IN ('person','organisation')",
        p,
    )
    idx.entity_type = dict(ents.iter_rows())
    names = query_frame(
        con,
        f"SELECT entity_key, full_name FROM {names_t} WHERE snapshot_date = $d "
        "AND source IN (SELECT unnest($s)) ORDER BY name_id",
        p,
    )
    seen = set()
    for key, full in names.iter_rows():
        if key not in idx.entity_type:
            continue
        for k in _keys(full or "", mode, idx.entity_type[key] != "person"):
            if (key, k) in seen:
                continue
            seen.add((key, k))
            i = len(idx.names)
            idx.names.append(k)
            idx.name_entity.append(key)
            toks, pre = _block_keys(k, min_token_len)
            for t in set(toks):
                idx.tokens.setdefault(t, []).append(i)
            if pre:
                idx.prefixes.setdefault(pre, []).append(i)
    for key, lo, hi in query_frame(
        con,
        f"SELECT entity_key, date_from, date_to FROM {dobs_t} WHERE snapshot_date = $d "
        "AND source IN (SELECT unnest($s)) AND date_from IS NOT NULL",
        p,
    ).iter_rows():
        idx.dobs.setdefault(key, []).append((lo, hi))
    for key, c in query_frame(
        con,
        "SELECT DISTINCT entity_key, country_iso2 FROM countries WHERE snapshot_date = $d "
        "AND source IN (SELECT unnest($s)) AND role IN ('nationality','citizenship') "
        "AND country_iso2 IS NOT NULL",
        p,
    ).iter_rows():
        idx.nationalities.setdefault(key, set()).add(c)
    if mode == "clean" and requests_dir is not None:
        for r in active_requests(requests_dir, "false_positive_suppression"):
            name = r.payload.get("customer_name", "")
            idx.suppressed.add((normalise_name(name), r.target_entity_key))
    return idx


def adjust(
    base: float,
    cust_dob: date | None,
    cust_nat: str | None,
    cust_type: str,
    ent_type: str,
    dobs: list[tuple[date, date]],
    nats: set[str],
) -> float:
    score = base
    if cust_dob is not None and dobs:
        if any(lo <= cust_dob <= hi for lo, hi in dobs):
            score += 5
        else:
            gap = min(min(abs((cust_dob - lo).days), abs((cust_dob - hi).days)) for lo, hi in dobs)
            if gap > 730:
                score -= 15
    if cust_nat and nats:
        score += 3 if cust_nat in nats else -5
    if (cust_type == "person") != (ent_type == "person"):
        score -= 20
    return score


def screen(
    customers: pl.DataFrame,
    idx: ListIndex,
    *,
    min_token_len: int = 3,
    max_candidates: int = 200,
    keep_from: float = 70.0,
) -> pl.DataFrame:
    """Score every customer against blocked candidates; return pairs with final >= keep_from."""
    out: list[tuple] = []
    names_arr = np.array(idx.names, dtype=object)
    for cust_id, full, dob, nat, ctype in customers.select(
        "cust_id", "full_name", "dob", "nationality_iso2", "type"
    ).iter_rows():
        best: dict[str, tuple[float, str]] = {}
        for q in _keys(full or "", idx.mode, ctype != "person"):
            toks, pre = _block_keys(q, min_token_len)
            cand: set[int] = set()
            for t in toks:
                cand.update(idx.tokens.get(t, ()))
            if pre:
                cand.update(idx.prefixes.get(pre, ()))
            if not cand:
                continue
            cidx = np.fromiter(cand, dtype=np.int64)
            choices = names_arr[cidx].tolist()
            ts = process.cdist([q], choices, scorer=fuzz.token_sort_ratio, workers=1)[0]
            jw = (
                process.cdist([q], choices, scorer=JaroWinkler.normalized_similarity, workers=1)[0]
                * 100
            )
            scores = np.maximum(ts, jw)
            for i, s in zip(cidx.tolist(), scores.tolist(), strict=True):
                ent = idx.name_entity[i]
                if s > best.get(ent, (-1.0, ""))[0]:
                    best[ent] = (s, idx.names[i])
        if not best:
            continue
        top = sorted(best.items(), key=lambda kv: -kv[1][0])[:max_candidates]
        norm_cust = normalise_name(full or "")
        for ent, (s, matched) in top:
            if (norm_cust, ent) in idx.suppressed:
                continue
            final = adjust(
                s,
                dob,
                nat,
                ctype,
                idx.entity_type[ent],
                idx.dobs.get(ent, []),
                idx.nationalities.get(ent, set()),
            )
            if final >= keep_from:
                out.append((cust_id, ent, matched, round(float(s), 2), round(float(final), 2)))
    return pl.DataFrame(out, schema=PAIR_SCHEMA, orient="row")


def screen_names(names: list[str], idx: ListIndex) -> pl.DataFrame:
    """Convenience: screen bare names (person, no DOB/nationality)."""
    df = pl.DataFrame(
        {
            "cust_id": [f"Q{i}" for i in range(len(names))],
            "full_name": names,
            "dob": [None] * len(names),
            "nationality_iso2": [None] * len(names),
            "type": ["person"] * len(names),
        },
        schema={
            "cust_id": pl.String,
            "full_name": pl.String,
            "dob": pl.Date,
            "nationality_iso2": pl.String,
            "type": pl.String,
        },
    )
    return screen(df, idx, keep_from=0.0)
