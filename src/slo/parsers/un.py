"""UN Security Council Consolidated List XML parser."""

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
from slo.normalise.ids import map_un_id_type
from slo.parsers._xml import iter_elements, root_attr_and_text, text, texts

_QUALITY = {"good": "strong", "a.k.a.": "strong", "low": "weak", "f.k.a.": "weak"}


def _dob(elem: etree._Element) -> RawDob | None:
    kind = (text(elem, "TYPE_OF_DATE") or "").upper()
    circa = kind == "APPROXIMATELY"
    if kind == "BETWEEN" and text(elem, "FROM_YEAR") and text(elem, "TO_YEAR"):
        return RawDob(f"{text(elem, 'FROM_YEAR')} to {text(elem, 'TO_YEAR')}", circa)
    value = text(elem, "DATE") or text(elem, "YEAR")
    return RawDob(value, circa) if value else None


def _address(elem: etree._Element) -> RawAddress:
    line = ", ".join(
        p for p in (text(elem, "STREET"), text(elem, "STATE_PROVINCE"), text(elem, "NOTE")) if p
    )
    return RawAddress(
        line=line or None,
        city=text(elem, "CITY"),
        postal_code=text(elem, "ZIP_CODE"),
        country_raw=text(elem, "COUNTRY"),
    )


def _common(elem: etree._Element, entity_type: str, alias_tag: str, address_tag: str) -> RawEntity:
    parts = [text(elem, t) for t in ("FIRST_NAME", "SECOND_NAME", "THIRD_NAME", "FOURTH_NAME")]
    present = [p for p in parts if p]
    primary = " ".join(present)
    names = [
        RawName(
            full_name=primary,
            name_type="primary",
            first_name=present[0] if present else None,
            middle_name=" ".join(present[1:-1]) or None,
            last_name=present[-1] if len(present) > 1 else None,
            strength="strong",
        )
    ]
    for alias in elem.findall(alias_tag):
        quality = (text(alias, "QUALITY") or "").lower()
        names.append(
            RawName(
                # keep empty aliases: <ALIAS_NAME/> is a real quirk the DQ rules report
                full_name=(alias.findtext("ALIAS_NAME") or ""),
                name_type="former" if quality == "f.k.a." else "alias",
                strength=_QUALITY.get(quality),
            )
        )
    if original := text(elem, "NAME_ORIGINAL_SCRIPT"):
        names.append(RawName(full_name=original, name_type="non_latin", strength="strong"))
    updated = [d for v in texts(elem, "LAST_DAY_UPDATED/VALUE") if (d := parse_date(v, "%Y-%m-%d"))]
    return RawEntity(
        source_id=text(elem, "DATAID") or "",
        entity_type=entity_type,
        primary_name=primary or None,
        programmes=[p] if (p := text(elem, "UN_LIST_TYPE")) else [],
        listed_on=parse_date(text(elem, "LISTED_ON"), "%Y-%m-%d"),
        last_updated=max(updated) if updated else None,
        un_reference=text(elem, "REFERENCE_NUMBER"),
        remarks=text(elem, "COMMENTS1"),
        names=names,
        addresses=[_address(a) for a in elem.findall(address_tag)],
    )


def _individual(elem: etree._Element) -> RawEntity:
    ent = _common(elem, "person", "INDIVIDUAL_ALIAS", "INDIVIDUAL_ADDRESS")
    gender = (text(elem, "GENDER") or "").lower()
    ent.gender = {"male": "M", "female": "F"}.get(gender)
    ent.dobs = [d for e in elem.findall("INDIVIDUAL_DATE_OF_BIRTH") if (d := _dob(e))]
    ent.countries = [RawCountry("nationality", v) for v in texts(elem, "NATIONALITY/VALUE")]
    ent.countries += [
        RawCountry("birth", c)
        for p in elem.findall("INDIVIDUAL_PLACE_OF_BIRTH")
        if (c := text(p, "COUNTRY"))
    ]
    for doc in elem.findall("INDIVIDUAL_DOCUMENT"):
        number = doc.findtext("NUMBER")  # untrimmed on purpose: whitespace noise is a DQ finding
        if not number or not number.strip():
            continue
        ent.identifiers.append(
            RawIdentifier(
                id_type=map_un_id_type(doc.findtext("TYPE_OF_DOCUMENT")),
                number=number,
                country_raw=text(doc, "ISSUING_COUNTRY") or text(doc, "COUNTRY_OF_ISSUE"),
                notes=text(doc, "NOTE"),
            )
        )
    return ent


def _entity(elem: etree._Element) -> RawEntity:
    return _common(elem, "organisation", "ENTITY_ALIAS", "ENTITY_ADDRESS")


_GEN_DATE = re.compile(rb'dateGenerated="(\d{4}-\d{2}-\d{2})')


def parse_un(path: Path) -> ParseResult:
    entities: list[RawEntity] = []
    failures: list[str] = []
    count = 0
    for tag, fn in (("INDIVIDUAL", _individual), ("ENTITY", _entity)):
        for elem in iter_elements(path, tag):
            count += 1
            try:
                entities.append(fn(elem))
            except (ValueError, TypeError, AttributeError) as exc:
                failures.append(f"{elem.findtext('DATAID')}: {exc!r}")
    m = _GEN_DATE.search(root_attr_and_text(path))
    published = parse_date(m.group(1).decode(), "%Y-%m-%d") if m else None
    return ParseResult("un", entities, count, None, published, failures)
