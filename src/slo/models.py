"""Intermediate (pre-normalisation) record types produced by the parsers.

Parsers only *extract* what a source publishes. All cleaning and mapping to the
canonical schema happens in :mod:`slo.build`, so every source is normalised by
the same code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class RawName:
    full_name: str
    name_type: str = "alias"  # primary | alias | former | non_latin
    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    strength: str | None = None  # strong | weak | None
    language: str | None = None


@dataclass
class RawDob:
    raw: str
    circa: bool = False


@dataclass
class RawIdentifier:
    id_type: str  # canonical id type (see slo.normalise.ids)
    number: str
    country_raw: str | None = None
    notes: str | None = None


@dataclass
class RawCountry:
    role: str  # nationality | citizenship | birth | registration
    raw: str


@dataclass
class RawAddress:
    line: str | None = None
    city: str | None = None
    postal_code: str | None = None
    country_raw: str | None = None


@dataclass
class RawEntity:
    source_id: str
    entity_type: str  # person | organisation | vessel | aircraft | unknown
    primary_name: str | None
    programmes: list[str] = field(default_factory=list)
    listed_on: date | None = None
    last_updated: date | None = None
    un_reference: str | None = None
    gender: str | None = None
    remarks: str | None = None
    names: list[RawName] = field(default_factory=list)
    dobs: list[RawDob] = field(default_factory=list)
    identifiers: list[RawIdentifier] = field(default_factory=list)
    countries: list[RawCountry] = field(default_factory=list)
    addresses: list[RawAddress] = field(default_factory=list)


@dataclass
class ParseResult:
    source: str
    entities: list[RawEntity]
    element_count: int  # entity elements seen in the file (independent count)
    declared_count: int | None = None  # count the publisher declares, if any
    published_date: date | None = None
    failures: list[str] = field(default_factory=list)  # "<source_id>: <error>"
