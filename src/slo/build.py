"""Map parsed source records onto the canonical schema (the "normalise" step)."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any

import polars as pl

from slo.models import ParseResult, RawEntity
from slo.normalise.countries import to_iso2
from slo.normalise.dates import parse_dob
from slo.normalise.ids import normalise_id_number
from slo.normalise.names import detect_script, normalise_name, tokens_sorted

S = pl.String
D = pl.Date
B = pl.Boolean

SCHEMAS: dict[str, dict[str, pl.DataType]] = {
    "entities": {
        "source": S,
        "snapshot_date": D,
        "source_id": S,
        "entity_key": S,
        "entity_type": S,
        "primary_name": S,
        "programmes": pl.List(S),
        "listed_on": D,
        "last_updated": D,
        "un_reference": S,
        "gender": S,
        "remarks": S,
        "record_hash": S,
    },
    "names": {
        "source": S,
        "snapshot_date": D,
        "entity_key": S,
        "name_id": S,
        "full_name": S,
        "first_name": S,
        "middle_name": S,
        "last_name": S,
        "name_type": S,
        "strength": S,
        "script": S,
        "language": S,
        "norm_name": S,
        "norm_tokens_sorted": S,
        "generated": B,
    },
    "birth_dates": {
        "source": S,
        "snapshot_date": D,
        "entity_key": S,
        "raw": S,
        "date_from": D,
        "date_to": D,
        "precision": S,
        "circa": B,
    },
    "identifiers": {
        "source": S,
        "snapshot_date": D,
        "entity_key": S,
        "id_type": S,
        "raw_number": S,
        "norm_number": S,
        "country_iso2": S,
        "notes": S,
    },
    "countries": {
        "source": S,
        "snapshot_date": D,
        "entity_key": S,
        "role": S,
        "raw": S,
        "country_iso2": S,
    },
    "addresses": {
        "source": S,
        "snapshot_date": D,
        "entity_key": S,
        "line": S,
        "city": S,
        "postal_code": S,
        "country_iso2": S,
        "raw": S,
    },
}


def normalise_un_reference(ref: str | None) -> str | None:
    if not ref:
        return None
    value = "".join(ref.split()).upper().rstrip(".")
    if value in {"N/A", "NA", "NONE", "-", "NIL"}:
        return None
    # Zero-pad the serial so "LYI.28" (EU) and "LYi.028" (UN) compare equal.
    prefix, dot, serial = value.rpartition(".")
    if dot and serial.isdigit():
        value = f"{prefix}.{int(serial):03d}"
    return value or None


def name_row(
    source: str, snapshot_date: date, entity_key: str, idx: int, **kw: Any
) -> dict[str, Any]:
    full = kw.get("full_name") or ""
    is_org = kw.pop("is_org", False)
    norm = normalise_name(full, is_org=is_org)
    return {
        "source": source,
        "snapshot_date": snapshot_date,
        "entity_key": entity_key,
        "name_id": f"{entity_key}#n{idx}",
        "full_name": full,
        "first_name": kw.get("first_name"),
        "middle_name": kw.get("middle_name"),
        "last_name": kw.get("last_name"),
        "name_type": kw.get("name_type", "alias"),
        "strength": kw.get("strength"),
        "script": detect_script(full),
        "language": kw.get("language"),
        "norm_name": norm,
        "norm_tokens_sorted": tokens_sorted(norm),
        "generated": kw.get("generated", False),
    }


def _hash(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def entity_rows(
    source: str, snapshot_date: date, ent: RawEntity
) -> dict[str, list[dict[str, Any]]]:
    key = f"{source}:{ent.source_id}"
    is_org = ent.entity_type != "person"
    base = {"source": source, "snapshot_date": snapshot_date, "entity_key": key}
    names = [
        name_row(
            source,
            snapshot_date,
            key,
            i,
            full_name=n.full_name,
            first_name=n.first_name,
            middle_name=n.middle_name,
            last_name=n.last_name,
            name_type=n.name_type,
            strength=n.strength,
            language=n.language,
            is_org=is_org,
        )
        for i, n in enumerate(ent.names)
    ]
    dobs = []
    for d in ent.dobs:
        parsed = parse_dob(d.raw, d.circa)
        dobs.append(
            {
                **base,
                "raw": d.raw,
                "date_from": parsed.date_from,
                "date_to": parsed.date_to,
                "precision": parsed.precision,
                "circa": parsed.circa,
            }
        )
    ids = [
        {
            **base,
            "id_type": i.id_type,
            "raw_number": i.number,
            "norm_number": normalise_id_number(i.number),
            "country_iso2": to_iso2(i.country_raw),
            "notes": i.notes,
        }
        for i in ent.identifiers
    ]
    countries = [
        {**base, "role": c.role, "raw": c.raw, "country_iso2": to_iso2(c.raw)}
        for c in ent.countries
    ]
    addresses = [
        {
            **base,
            "line": a.line,
            "city": a.city,
            "postal_code": a.postal_code,
            "country_iso2": to_iso2(a.country_raw),
            "raw": " | ".join(p for p in (a.line, a.city, a.postal_code, a.country_raw) if p),
        }
        for a in ent.addresses
    ]
    entity = {
        **base,
        "source_id": ent.source_id,
        "entity_type": ent.entity_type,
        "primary_name": ent.primary_name,
        "programmes": sorted(ent.programmes),
        "listed_on": ent.listed_on,
        "last_updated": ent.last_updated,
        "un_reference": normalise_un_reference(ent.un_reference),
        "gender": ent.gender,
        "remarks": ent.remarks,
    }
    strip = ("source", "snapshot_date", "entity_key")
    hash_payload = {
        "entity": {k: v for k, v in entity.items() if k not in strip},
        "names": sorted((n["full_name"], n["name_type"], n["strength"] or "") for n in names),
        "dobs": sorted((d["raw"], d["circa"]) for d in dobs),
        "ids": sorted((i["id_type"], i["raw_number"]) for i in ids),
        "countries": sorted((c["role"], c["raw"]) for c in countries),
        "addresses": sorted(a["raw"] for a in addresses),
    }
    entity["record_hash"] = _hash(hash_payload)
    return {
        "entities": [entity],
        "names": names,
        "birth_dates": dobs,
        "identifiers": ids,
        "countries": countries,
        "addresses": addresses,
    }


def to_frames(rows: dict[str, list[dict[str, Any]]]) -> dict[str, pl.DataFrame]:
    return {t: pl.DataFrame(rows.get(t, []), schema=SCHEMAS[t], orient="row") for t in SCHEMAS}


def build_frames(result: ParseResult, snapshot_date: date) -> dict[str, pl.DataFrame]:
    rows: dict[str, list[dict[str, Any]]] = {t: [] for t in SCHEMAS}
    for ent in result.entities:
        for table, table_rows in entity_rows(result.source, snapshot_date, ent).items():
            rows[table].extend(table_rows)
    return to_frames(rows)
