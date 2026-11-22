"""EU Consolidated Financial Sanctions List (FSF) XML parser."""

from __future__ import annotations

import re
from pathlib import Path

from lxml import etree

from slo.models import (
    ParseResult,
    RawAddress,
    RawCountry,
    RawDob,
    RawEntity,
    RawIdentifier,
    RawName,
)
from slo.normalise.dates import parse_date
from slo.normalise.ids import map_eu_id_type
from slo.parsers._xml import iter_elements, root_attr_and_text

NS = "http://eu.europa.ec/fpi/fsd/export"
Q = f"{{{NS}}}"

_SUBJECT_TYPES = {"person": "person", "enterprise": "organisation"}


def _attr(elem: etree._Element, name: str) -> str | None:
    value = elem.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _dob(elem: etree._Element) -> RawDob | None:
    circa = (elem.get("circa") or "").lower() == "true"
    full = _attr(elem, "birthdate")
    if full:
        return RawDob(full, circa)
    year, month, day = _attr(elem, "year"), _attr(elem, "monthOfYear"), _attr(elem, "dayOfMonth")
    if year and month and day:
        return RawDob(f"{int(year):04d}-{int(month):02d}-{int(day):02d}", circa)
    if year and month:
        return RawDob(f"{int(year):04d}-{int(month):02d}", circa)
    if year:
        return RawDob(year, circa)
    return None


def _entity(elem: etree._Element) -> RawEntity:
    subject = elem.find(f"{Q}subjectType")
    entity_type = _SUBJECT_TYPES.get(
        subject.get("code", "") if subject is not None else "", "unknown"
    )
    programmes = sorted({p for r in elem.findall(f"{Q}regulation") if (p := _attr(r, "programme"))})
    listed = [
        d
        for r in elem.findall(f"{Q}regulation")
        if (d := parse_date(r.get("publicationDate"), "%Y-%m-%d"))
    ]
    names: list[RawName] = []
    gender = None
    for i, na in enumerate(elem.findall(f"{Q}nameAlias")):
        whole = na.get("wholeName") or " ".join(
            p for p in (na.get("firstName"), na.get("middleName"), na.get("lastName")) if p
        )
        names.append(
            RawName(
                full_name=whole,  # as published (whitespace untouched)
                name_type="primary" if i == 0 else "alias",
                first_name=_attr(na, "firstName"),
                middle_name=_attr(na, "middleName"),
                last_name=_attr(na, "lastName"),
                strength="strong" if (na.get("strong") or "").lower() == "true" else "weak",
                language=_attr(na, "nameLanguage"),
            )
        )
        gender = gender or _attr(na, "gender")
    dobs = [d for b in elem.findall(f"{Q}birthdate") if (d := _dob(b))]
    countries = [
        RawCountry("citizenship", c.get("countryIso2Code") or c.get("countryDescription") or "")
        for c in elem.findall(f"{Q}citizenship")
    ]
    countries += [
        RawCountry("birth", b.get("countryIso2Code") or "")
        for b in elem.findall(f"{Q}birthdate")
        if b.get("countryIso2Code")
    ]
    identifiers = [
        RawIdentifier(
            id_type=map_eu_id_type(i.get("identificationTypeCode")),
            number=i.get("number") or i.get("latinNumber") or "",
            country_raw=i.get("countryIso2Code"),
            notes=_attr(i, "identificationTypeDescription"),
        )
        for i in elem.findall(f"{Q}identification")
        if (i.get("number") or i.get("latinNumber"))
    ]
    addresses = [
        RawAddress(
            line=" ".join(p for p in (a.get("street"), a.get("poBox")) if p) or None,
            city=_attr(a, "city"),
            postal_code=_attr(a, "zipCode"),
            country_raw=a.get("countryIso2Code"),
        )
        for a in elem.findall(f"{Q}address")
    ]
    remark_el = elem.find(f"{Q}remark")
    remark = (remark_el.text or "").strip() if remark_el is not None else ""
    return RawEntity(
        source_id=elem.get("logicalId") or "",
        entity_type=entity_type,
        primary_name=names[0].full_name if names else None,
        programmes=programmes,
        listed_on=min(listed) if listed else None,
        un_reference=_attr(elem, "unitedNationId"),
        gender=gender,
        remarks=remark or None,
        names=names,
        dobs=dobs,
        identifiers=identifiers,
        countries=countries,
        addresses=addresses,
    )


_GEN_DATE = re.compile(rb'generationDate="(\d{4}-\d{2}-\d{2})')


def parse_eu(path: Path) -> ParseResult:
    entities: list[RawEntity] = []
    failures: list[str] = []
    count = 0
    for elem in iter_elements(path, f"{Q}sanctionEntity"):
        count += 1
        try:
            entities.append(_entity(elem))
        except (ValueError, TypeError, AttributeError) as exc:
            failures.append(f"{elem.get('logicalId')}: {exc!r}")
    m = _GEN_DATE.search(root_attr_and_text(path))
    published = parse_date(m.group(1).decode(), "%Y-%m-%d") if m else None
    return ParseResult("eu", entities, count, None, published, failures)
