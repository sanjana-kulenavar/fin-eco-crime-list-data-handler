"""Identifier normalisation and id-type mapping."""

from __future__ import annotations

import re

ID_TYPES = ("passport", "national_id", "tax_id", "company_reg", "imo", "swift_bic", "other")

_NOISE = re.compile(r"[\s\-./]")

EU_TYPE_MAP = {
    "passport": "passport",
    "id": "national_id",
    "regnumber": "company_reg",
    "fiscalcode": "tax_id",
    "taxid": "tax_id",
    "imo": "imo",
    "swiftbic": "swift_bic",
}

# OFAC publishes a lot of non-identifier metadata inside <idList> (Gender, Website,
# "Secondary sanctions risk:", ...). Only these prefixes are real identifiers.
_OFAC_RULES: tuple[tuple[str, str], ...] = (
    ("passport", "passport"),
    ("national id", "national_id"),
    ("cedula", "national_id"),
    ("identification number", "national_id"),
    ("personal id", "national_id"),
    ("tax id", "tax_id"),
    ("ruc", "tax_id"),
    ("nit", "tax_id"),
    ("rfc", "tax_id"),
    ("inn", "tax_id"),
    ("vat", "tax_id"),
    ("registration number", "company_reg"),
    ("business registration", "company_reg"),
    ("company number", "company_reg"),
    ("commercial registry", "company_reg"),
    ("government gazette", "company_reg"),
    ("legal entity number", "company_reg"),
    ("ogrn", "company_reg"),
    ("vessel registration identification", "imo"),
    ("imo", "imo"),
    ("swift/bic", "swift_bic"),
    ("swift", "swift_bic"),
    ("driver's license", "other"),
    ("birth certificate", "other"),
    ("residency number", "other"),
    ("diplomatic passport", "passport"),
)


def normalise_id_number(raw: str | None) -> str:
    return _NOISE.sub("", raw or "").upper()


def map_eu_id_type(code: str | None) -> str:
    return EU_TYPE_MAP.get((code or "").strip().lower(), "other")


def map_ofac_id_type(id_type: str | None) -> str | None:
    """Canonical id type, or ``None`` when the OFAC idList row is not an identifier."""
    text = (id_type or "").strip().lower()
    for prefix, canonical in _OFAC_RULES:
        if text.startswith(prefix):
            return canonical
    return None


def map_un_id_type(doc_type: str | None) -> str:
    text = " ".join((doc_type or "").split()).lower()
    if "passport" in text:
        return "passport"
    if "identification" in text or "id" in text.split():
        return "national_id"
    return "other"
