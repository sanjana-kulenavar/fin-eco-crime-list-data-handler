"""Dutch national terrorism sanctions list, via OpenSanctions FollowTheMoney NDJSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from slo.models import ParseResult, RawCountry, RawDob, RawEntity, RawName
from slo.normalise.dates import parse_date

_TARGET_SCHEMAS = {
    "Person": "person",
    "Organization": "organisation",
    "LegalEntity": "organisation",
    "Company": "organisation",
}


def _first(props: dict[str, list[str]], key: str) -> str | None:
    values = props.get(key) or []
    return values[0].strip() if values and values[0].strip() else None


def _entity(rec: dict[str, Any], sanctions: dict[str, list[dict[str, Any]]]) -> RawEntity:
    props: dict[str, list[str]] = rec.get("properties", {})
    names = [
        RawName(n, "primary" if i == 0 else "alias", strength="strong")
        for i, n in enumerate(props.get("name", []))
    ]
    if names and len(props.get("firstName", [])) == 1 and len(props.get("lastName", [])) == 1:
        names[0].first_name = _first(props, "firstName")
        names[0].last_name = _first(props, "lastName")
    names += [RawName(a, "alias", strength="weak") for a in props.get("alias", [])]
    names += [RawName(a, "former", strength="weak") for a in props.get("previousName", [])]
    countries = [RawCountry("nationality", c) for c in props.get("nationality", [])]
    countries += [RawCountry("citizenship", c) for c in props.get("citizenship", [])]
    programmes: set[str] = set(props.get("programId", []))
    listed = []
    for s in sanctions.get(rec["id"], []):
        sp = s.get("properties", {})
        programmes.update(sp.get("programId", []))
        listed += [d for v in sp.get("listingDate", []) if (d := parse_date(v, "%Y-%m-%d"))]
    dobs = [RawDob(d) for d in props.get("birthDate", [])]
    return RawEntity(
        source_id=rec["id"],
        entity_type=_TARGET_SCHEMAS[rec["schema"]],
        primary_name=names[0].full_name if names else None,
        programmes=sorted(programmes),
        listed_on=min(listed) if listed else None,
        last_updated=parse_date(rec.get("last_change"), "%Y-%m-%d"),
        gender={"male": "M", "female": "F"}.get((_first(props, "gender") or "").lower()),
        remarks=_first(props, "notes"),
        names=names,
        dobs=dobs,
        countries=countries,
    )


def parse_nl(path: Path) -> ParseResult:
    records: list[dict[str, Any]] = []
    failures: list[str] = []
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                failures.append(f"line {lineno}: {exc!r}")
    sanctions: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        if rec.get("schema") == "Sanction":
            for target in rec.get("properties", {}).get("entity", []):
                sanctions.setdefault(target, []).append(rec)
    entities: list[RawEntity] = []
    count = 0
    last_seen = []
    for rec in records:
        if rec.get("schema") not in _TARGET_SCHEMAS:
            continue
        count += 1
        if d := parse_date(rec.get("last_seen"), "%Y-%m-%d"):
            last_seen.append(d)
        try:
            entities.append(_entity(rec, sanctions))
        except (KeyError, ValueError, TypeError, AttributeError) as exc:
            failures.append(f"{rec.get('id')}: {exc!r}")
    return ParseResult("nl", entities, count, None, max(last_seen) if last_seen else None, failures)
