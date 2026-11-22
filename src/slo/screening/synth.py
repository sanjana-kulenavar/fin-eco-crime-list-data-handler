"""Synthetic customer book with planted true hits drawn from the sanctions lists."""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from datetime import date, timedelta

import duckdb
import polars as pl
from anyascii import anyascii
from faker import Faker

from slo.normalise.names import collapse_ws, detect_script
from slo.store import query_frame

LOCALES: list[tuple[str, float, list[str]]] = [
    ("nl_NL", 0.50, ["NL"]),
    ("de_DE", 0.10, ["DE"]),
    ("en_GB", 0.10, ["GB"]),
    ("fr_FR", 0.05, ["FR"]),
    ("tr_TR", 0.05, ["TR"]),
    ("ar_AA", 0.05, ["EG", "JO", "MA", "SA", "AE", "IQ", "SY"]),
    ("ru_RU", 0.05, ["RU"]),
    ("pl_PL", 0.05, ["PL"]),
    ("es_ES", 0.05, ["ES"]),
]

PERTURBATIONS: dict[str, float] = {
    "exact": 0.20,
    "typo": 0.20,
    "token_swap": 0.15,
    "drop_middle": 0.15,
    "translit_variant": 0.15,
    "alias_used": 0.15,
}

# Spelling variants used to *perturb* planted names. The matcher never sees this table.
VARIANT_GROUPS = [
    ["mohammed", "muhammad", "mohamed", "mohammad", "muhammed", "mohamad"],
    ["ahmed", "ahmad"],
    ["yusuf", "youssef", "yousef", "yousuf"],
    ["hussein", "husain", "hussain", "husayn", "hossein"],
    ["hassan", "hasan"],
    ["omar", "umar"],
    ["osman", "othman", "uthman"],
    ["ibrahim", "ebrahim"],
    ["khalid", "khaled"],
    ["mahmoud", "mahmud"],
    ["mustafa", "mostafa"],
    ["abdullah", "abdallah"],
    ["abdul", "abdel"],
    ["ali", "aly"],
    ["said", "saeed", "sayed"],
    ["sergei", "sergey"],
    ["alexei", "alexey", "aleksei", "aleksey"],
    ["yuri", "yury", "iouri"],
    ["dmitri", "dmitry", "dmitriy"],
]
VARIANTS = {name: [v for v in group if v != name] for group in VARIANT_GROUPS for name in group}
_SUFFIXES = [("ov", "off"), ("ev", "eff"), ("iy", "y"), ("ii", "y")]


@dataclass
class ListPerson:
    entity_key: str
    source: str
    entity_type: str
    primary: str
    aliases: list[str]
    weak_aliases: list[str]
    dob: tuple[date, date, str] | None
    nationality: str | None


def load_list_targets(
    con: duckdb.DuckDBPyConnection, d: date, sources: list[str]
) -> list[ListPerson]:
    ents = query_frame(
        con,
        "SELECT entity_key, source, entity_type FROM entities WHERE snapshot_date = $d "
        "AND source IN (SELECT unnest($s)) AND entity_type IN ('person','organisation') ORDER BY entity_key",
        {"d": d, "s": sources},
    )
    names = query_frame(
        con,
        "SELECT entity_key, full_name, name_type, strength FROM names WHERE snapshot_date = $d "
        "AND source IN (SELECT unnest($s)) AND trim(full_name) <> '' ORDER BY name_id",
        {"d": d, "s": sources},
    )
    dobs = query_frame(
        con,
        "SELECT entity_key, date_from, date_to, precision FROM birth_dates WHERE snapshot_date = $d "
        "AND source IN (SELECT unnest($s)) AND date_from IS NOT NULL ORDER BY entity_key, date_from",
        {"d": d, "s": sources},
    )
    nats = query_frame(
        con,
        "SELECT entity_key, min(country_iso2) AS c FROM countries WHERE snapshot_date = $d "
        "AND source IN (SELECT unnest($s)) AND role IN ('nationality','citizenship') "
        "AND country_iso2 IS NOT NULL GROUP BY 1",
        {"d": d, "s": sources},
    )
    by_names: dict[str, list[tuple[str, str, str | None]]] = {}
    for key, full, ntype, strength in names.iter_rows():
        by_names.setdefault(key, []).append((full, ntype, strength))
    first_dob: dict[str, tuple[date, date, str]] = {}
    for key, lo, hi, prec in dobs.iter_rows():
        first_dob.setdefault(key, (lo, hi, prec))
    nat = dict(nats.iter_rows())
    out = []
    for key, source, etype in ents.iter_rows():
        rows = by_names.get(key, [])
        primary = next(
            (f for f, t, _ in rows if t == "primary" and detect_script(f) == "latin"), None
        )
        if not primary:
            continue
        # Customers carry real Latin spellings, so only Latin-script aliases are used.
        aliases = [
            collapse_ws(f) for f, t, _ in rows if t != "primary" and detect_script(f) == "latin"
        ]
        weak = [
            collapse_ws(f)
            for f, t, s in rows
            if t != "primary" and s == "weak" and detect_script(f) == "latin"
        ]
        out.append(
            ListPerson(
                key,
                source,
                etype,
                collapse_ws(primary),
                aliases,
                weak,
                first_dob.get(key),
                nat.get(key),
            )
        )
    return out


def _typo(name: str, rng: random.Random) -> str:
    tokens = name.split()
    idx = [i for i, t in enumerate(tokens) if len(t) >= 4]
    if not idx:
        return name
    for _ in range(rng.choice([1, 2])):
        i = rng.choice(idx)
        t = list(tokens[i])
        pos = rng.randrange(1, len(t) - 1)
        op = rng.choice(["sub", "del", "swap"])
        if op == "sub":
            t[pos] = rng.choice("aeiounrst")
        elif op == "del":
            del t[pos]
        else:
            j = pos + 1 if pos + 1 < len(t) else pos - 1
            t[pos], t[j] = t[j], t[pos]
        tokens[i] = "".join(t)
    return " ".join(tokens)


def _translit(name: str, rng: random.Random) -> str | None:
    tokens = name.split()
    options = []
    for i, tok in enumerate(tokens):
        low = tok.lower()
        if low in VARIANTS:
            options.append((i, rng.choice(VARIANTS[low])))
        for a, b in _SUFFIXES:
            if low.endswith(a) and len(low) > 4:
                options.append((i, low[: -len(a)] + b))
        if "ei" in low:
            options.append((i, low.replace("ei", "ey", 1)))
    if not options:
        return None
    i, new = rng.choice(options)
    tokens[i] = new
    return " ".join(tokens)


def perturb(target: ListPerson, kind: str, rng: random.Random) -> str | None:
    """Return the customer name for a planted hit, or None if ``kind`` does not apply."""
    tokens = target.primary.split()
    person = target.entity_type == "person"
    if kind in ("token_swap", "drop_middle", "translit_variant") and not person:
        return None  # name-order and given-name variants only make sense for people
    if kind == "exact":
        return target.primary if len(tokens) >= 2 else None
    if kind == "typo":
        return _typo(target.primary, rng) if len(tokens) >= 2 else None
    if kind == "token_swap":
        return " ".join([tokens[-1], *tokens[:-1]]) if len(tokens) >= 2 else None
    if kind == "drop_middle":
        return f"{tokens[0]} {tokens[-1]}" if len(tokens) >= 3 else None
    if kind == "translit_variant":
        return _translit(target.primary, rng) if len(tokens) >= 2 else None
    if kind == "alias_used":
        pool = [a for a in (target.weak_aliases or target.aliases) if len(a.split()) >= 2]
        return rng.choice(pool) if pool else None
    raise ValueError(kind)


def _title(name: str) -> str:
    # Core banking systems store names in title case; list names are often UPPER CASE.
    return " ".join(
        t[:1].upper() + t[1:].lower() if t.isupper() or t.islower() else t for t in name.split()
    )


def _random_customer(fakers: dict[str, Faker], rng: random.Random) -> dict:
    locale, _, nats = rng.choices(LOCALES, weights=[w for _, w, _ in LOCALES])[0]
    fake = fakers[locale]
    is_company = rng.random() < 0.10
    name = fake.company() if is_company else f"{fake.first_name()} {fake.last_name()}"
    if detect_script(name) not in ("latin", "unknown"):
        name = collapse_ws(anyascii(name))
    dob = None if is_company else fake.date_of_birth(minimum_age=18, maximum_age=90)
    return {
        "full_name": collapse_ws(name),
        "dob": dob,
        "nationality_iso2": rng.choice(nats),
        "type": "company" if is_company else "person",
        "locale": locale,
    }


def generate(
    targets: list[ListPerson], n_customers: int, n_hits: int, seed: int
) -> tuple[pl.DataFrame, pl.DataFrame]:
    rng = random.Random(seed)
    fakers = {}
    for i, (loc, _, _) in enumerate(LOCALES):
        f = Faker(loc)
        f.seed_instance(seed + i)
        fakers[loc] = f
    customers = [_random_customer(fakers, rng) for _ in range(n_customers)]
    by_source: dict[str, list[ListPerson]] = {}
    for t in targets:
        by_source.setdefault(t.source, []).append(t)
    sources = sorted(by_source)
    n_hits = min(n_hits, n_customers, len(targets))
    kinds = [k for k, w in PERTURBATIONS.items() for _ in range(round(w * n_hits))]
    while len(kinds) < n_hits:
        kinds.append("exact")
    kinds = kinds[:n_hits]
    rng.shuffle(kinds)
    hit_rows = rng.sample(range(n_customers), n_hits)
    used: set[str] = set()
    truth = []
    for i, (row, kind) in enumerate(zip(hit_rows, kinds, strict=True)):
        source = sources[i % len(sources)]
        pool = by_source[source]
        name = target = None
        for _ in range(500):
            cand = rng.choice(pool)
            if cand.entity_key in used:
                continue
            name = perturb(cand, kind, rng)
            if name:
                target = cand
                break
        if target is None:  # fall back to any unused target from any list
            for cand in rng.sample(targets, len(targets)):
                if cand.entity_key not in used and (name := perturb(cand, kind, rng)):
                    target = cand
                    break
        if target is None:  # no unused target supports this perturbation -> plant an exact hit
            kind = "exact"
            for cand in rng.sample(targets, len(targets)):
                if cand.entity_key not in used and (name := perturb(cand, kind, rng)):
                    target = cand
                    break
        if target is None or name is None:
            continue
        used.add(target.entity_key)
        cust = customers[row]
        cust["full_name"] = _title(name)
        cust["type"] = "person" if target.entity_type == "person" else "company"
        if target.dob:
            lo, hi, prec = target.dob
            dob = (
                lo
                if prec == "day"
                else lo + timedelta(days=rng.randrange(max(1, (hi - lo).days + 1)))
            )
            if rng.random() < 0.10:
                dob = dob.replace(year=dob.year + rng.choice([-1, 1]), day=min(dob.day, 28))
            cust["dob"] = dob if cust["type"] == "person" else None
        if target.nationality:
            cust["nationality_iso2"] = target.nationality
        truth.append(
            {
                "cust_row": row,
                "entity_key": target.entity_key,
                "source": target.source,
                "perturbation": kind,
                "listed_name": target.primary,
                "customer_name": cust["full_name"],
            }
        )
    cust_df = pl.DataFrame(
        [{"cust_id": f"C{i:06d}", **c} for i, c in enumerate(customers)],
        schema={
            "cust_id": pl.String,
            "full_name": pl.String,
            "dob": pl.Date,
            "nationality_iso2": pl.String,
            "type": pl.String,
            "locale": pl.String,
        },
    )
    truth_df = pl.DataFrame(
        [{"cust_id": f"C{t.pop('cust_row'):06d}", **t} for t in truth],
        schema={
            "cust_id": pl.String,
            "entity_key": pl.String,
            "source": pl.String,
            "perturbation": pl.String,
            "listed_name": pl.String,
            "customer_name": pl.String,
        },
    ).sort("cust_id")
    return cust_df, truth_df


def frame_hash(df: pl.DataFrame) -> str:
    return hashlib.sha256(df.write_csv().encode("utf-8")).hexdigest()
