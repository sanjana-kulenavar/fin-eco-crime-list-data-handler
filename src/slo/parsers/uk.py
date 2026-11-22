"""UK Sanctions List (FCDO) XML parser."""

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
from slo.parsers._xml import iter_elements, root_attr_and_text, text, texts

_TYPES = {"individual": "person", "entity": "organisation", "ship": "vessel"}
_STRENGTH = {"good quality a.k.a": "strong", "low quality a.k.a": "weak"}


def _name_type(raw: str | None) -> str:
    # UK spells the same value several ways: "Primary Name", "Primary name", "ALias", ...
    value = (raw or "").strip().lower()
    if value == "primary name":
        return "primary"
    return "alias"


def _designation(elem: etree._Element) -> RawEntity:
    names: list[RawName] = []
    for n in elem.iterfind("Names/Name"):
        given = [text(n, f"Name{i}") for i in range(1, 6)]
        surname = text(n, "Name6")
        full = " ".join(p for p in [*given, surname] if p)
        names.append(
            RawName(
                full,
                _name_type(text(n, "NameType")),
                first_name=given[0],
                middle_name=" ".join(p for p in given[1:] if p) or None,
                last_name=surname,
                strength=_STRENGTH.get((text(n, "AliasStrength") or "").lower()),
            )
        )
    # Exactly one primary: the first "Primary Name" row (or first row if none).
    primary_idx = next((i for i, n in enumerate(names) if n.name_type == "primary"), 0)
    for i, n in enumerate(names):
        n.name_type = "primary" if i == primary_idx else "alias"
        if i == primary_idx:
            n.strength = "strong"
    for nl in elem.iterfind("NonLatinNames/NonLatinName"):
        if value := text(nl, "NameNonLatinScript"):
            names.append(
                RawName(
                    value,
                    "non_latin",
                    strength="strong",
                    language=text(nl, "NonLatinScriptLanguage"),
                )
            )
    entity_type = _TYPES.get((text(elem, "IndividualEntityShip") or "").lower(), "unknown")
    ind = elem.find("IndividualDetails/Individual")
    dobs, countries, identifiers, gender = [], [], [], None
    if ind is not None:
        dobs = [RawDob(d) for d in texts(ind, "DOBs/DOB")]
        countries = [RawCountry("nationality", c) for c in texts(ind, "Nationalities/Nationality")]
        countries += [
            RawCountry("birth", c) for c in texts(ind, "BirthDetails/Location/CountryOfBirth")
        ]
        for p in ind.iterfind("PassportDetails/Passport"):
            if number := text(p, "PassportNumber"):
                identifiers.append(
                    RawIdentifier(
                        "passport", number, notes=text(p, "PassportAdditionalInformation")
                    )
                )
        for p in ind.iterfind("NationalIdentifierDetails/NationalIdentifier"):
            if number := text(p, "NationalIdentifierNumber"):
                identifiers.append(
                    RawIdentifier(
                        "national_id",
                        number,
                        notes=text(p, "NationalIdentifierAdditionalInformation"),
                    )
                )
        g = (text(ind, "Genders/Gender") or "").lower()
        gender = {"male": "M", "female": "F"}.get(g)
    for number in texts(
        elem, "EntityDetails/Entity/BusinessRegistrationNumbers/BusinessRegistrationNumber"
    ):
        identifiers.append(RawIdentifier("company_reg", number))
    for number in texts(elem, "ShipDetails/Ship/IMONumbers/IMONumber"):
        identifiers.append(RawIdentifier("imo", number))
    for flag in texts(
        elem, "ShipDetails/Ship/CurrentBelievedFlagOfShips/CurrentBelievedFlagOfShip"
    ):
        countries.append(RawCountry("registration", flag))
    addresses = []
    for a in elem.iterfind("Addresses/Address"):
        lines = [text(a, f"AddressLine{i}") for i in range(1, 6)]
        addresses.append(
            RawAddress(
                line=", ".join(p for p in lines if p) or None,
                city=text(a, "AddressLine6"),
                postal_code=text(a, "AddressPostalCode"),
                country_raw=text(a, "AddressCountry"),
            )
        )
    primary = names[primary_idx].full_name if names else None
    return RawEntity(
        source_id=text(elem, "UniqueID") or "",
        entity_type=entity_type,
        primary_name=primary,
        programmes=[r] if (r := text(elem, "RegimeName")) else [],
        listed_on=parse_date(text(elem, "DateDesignated"), "%d/%m/%Y"),
        last_updated=parse_date(text(elem, "LastUpdated"), "%d/%m/%Y"),
        un_reference=text(elem, "UNReferenceNumber"),
        gender=gender,
        remarks=text(elem, "OtherInformation"),
        names=names,
        dobs=dobs,
        identifiers=identifiers,
        countries=countries,
        addresses=addresses,
    )


_GEN_DATE = re.compile(rb"<DateGenerated>([^<]+)</DateGenerated>")


def parse_uk(path: Path) -> ParseResult:
    entities: list[RawEntity] = []
    failures: list[str] = []
    count = 0
    for elem in iter_elements(path, "Designation"):
        count += 1
        try:
            entities.append(_designation(elem))
        except (ValueError, TypeError, AttributeError) as exc:
            failures.append(f"{text(elem, 'UniqueID')}: {exc!r}")
    m = _GEN_DATE.search(root_attr_and_text(path))
    published = parse_date(m.group(1).decode(), "%d/%m/%Y") if m else None
    return ParseResult("uk", entities, count, None, published, failures)
