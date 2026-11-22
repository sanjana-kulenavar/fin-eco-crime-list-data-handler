"""US OFAC SDN list XML parser."""

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
from slo.normalise.countries import to_iso2
from slo.normalise.dates import parse_date
from slo.normalise.ids import map_ofac_id_type
from slo.parsers._xml import iter_elements, root_attr_and_text

NS = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/XML"
Q = f"{{{NS}}}"

_TYPES = {
    "individual": "person",
    "entity": "organisation",
    "vessel": "vessel",
    "aircraft": "aircraft",
}


def _t(elem: etree._Element | None, tag: str) -> str | None:
    if elem is None:
        return None
    child = elem.find(f"{Q}{tag}")
    if child is None or child.text is None:
        return None
    return child.text.strip() or None


def _join(*parts: str | None) -> str:
    return " ".join(p for p in parts if p)


def _birth_country(place: str) -> str:
    """Country part of an OFAC place of birth ("Baghdad, Iraq", "Khartoum Sudan")."""
    tail = place.rsplit(",", 1)[-1].strip()
    if to_iso2(tail) is None and " " in tail and to_iso2(tail.rsplit(" ", 1)[-1]) is not None:
        return tail.rsplit(" ", 1)[-1]
    return tail


def _entry(elem: etree._Element) -> RawEntity:
    first, last = _t(elem, "firstName"), _t(elem, "lastName")
    primary = _join(first, last)
    names = [RawName(primary, "primary", first_name=first, last_name=last, strength="strong")]
    for aka in elem.iterfind(f"{Q}akaList/{Q}aka"):
        a_first, a_last = _t(aka, "firstName"), _t(aka, "lastName")
        names.append(
            RawName(
                _join(a_first, a_last),
                "former" if (_t(aka, "type") or "").lower() == "f.k.a." else "alias",
                first_name=a_first,
                last_name=a_last,
                strength=(_t(aka, "category") or "").lower() or None,
            )
        )
    identifiers = []
    for id_el in elem.iterfind(f"{Q}idList/{Q}id"):
        id_type = map_ofac_id_type(_t(id_el, "idType"))
        number = _t(id_el, "idNumber")
        if id_type is None or not number:
            continue  # OFAC stores metadata (gender, website, risk flags) in idList
        identifiers.append(
            RawIdentifier(id_type, number, _t(id_el, "idCountry"), notes=_t(id_el, "idType"))
        )
    countries = [
        RawCountry("nationality", c)
        for n in elem.iterfind(f"{Q}nationalityList/{Q}nationality")
        if (c := _t(n, "country"))
    ]
    countries += [
        RawCountry("citizenship", c)
        for n in elem.iterfind(f"{Q}citizenshipList/{Q}citizenship")
        if (c := _t(n, "country"))
    ]
    for pob in elem.iterfind(f"{Q}placeOfBirthList/{Q}placeOfBirthItem"):
        place = _t(pob, "placeOfBirth")
        if place:
            countries.append(RawCountry("birth", _birth_country(place)))
    if vessel_flag := _t(elem.find(f"{Q}vesselInfo"), "vesselFlag"):
        countries.append(RawCountry("registration", vessel_flag))
    addresses = [
        RawAddress(
            line=", ".join(
                p for p in (_t(a, "address1"), _t(a, "address2"), _t(a, "address3")) if p
            )
            or None,
            city=_t(a, "city"),
            postal_code=_t(a, "postalCode"),
            country_raw=_t(a, "country"),
        )
        for a in elem.iterfind(f"{Q}addressList/{Q}address")
    ]
    sdn_type = (_t(elem, "sdnType") or "").lower()
    return RawEntity(
        source_id=_t(elem, "uid") or "",
        entity_type=_TYPES.get(sdn_type, "unknown"),
        primary_name=primary or None,
        programmes=sorted(
            {p.text.strip() for p in elem.iterfind(f"{Q}programList/{Q}program") if p.text}
        ),
        remarks=_t(elem, "remarks"),
        names=names,
        dobs=[
            RawDob(d)
            for item in elem.iterfind(f"{Q}dateOfBirthList/{Q}dateOfBirthItem")
            if (d := _t(item, "dateOfBirth"))
        ],
        identifiers=identifiers,
        countries=countries,
        addresses=addresses,
    )


_PUBLISH = re.compile(rb"<Publish_Date>([^<]+)</Publish_Date>")
_COUNT = re.compile(rb"<Record_Count>(\d+)</Record_Count>")


def parse_ofac(path: Path) -> ParseResult:
    entities: list[RawEntity] = []
    failures: list[str] = []
    count = 0
    for elem in iter_elements(path, f"{Q}sdnEntry"):
        count += 1
        try:
            entities.append(_entry(elem))
        except (ValueError, TypeError, AttributeError) as exc:
            failures.append(f"{_t(elem, 'uid')}: {exc!r}")
    head = root_attr_and_text(path)
    pub = _PUBLISH.search(head)
    declared = _COUNT.search(head)
    return ParseResult(
        "ofac",
        entities,
        count,
        int(declared.group(1)) if declared else None,
        parse_date(pub.group(1).decode(), "%m/%d/%Y") if pub else None,
        failures,
    )
