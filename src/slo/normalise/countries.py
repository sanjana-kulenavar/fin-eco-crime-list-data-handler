"""Country normalisation: ISO2 codes, country names and sanctions-list spellings -> ISO2."""

from __future__ import annotations

import re
from functools import cache

import pycountry

UNKNOWN_CODES = frozenset({"00", "XX", "ZZ", "UNKNOWN", "N/A", "NA", "-"})
EXTRA_CODES = frozenset({"XK"})  # Kosovo is not in ISO 3166 but used by every list

OVERRIDES = {
    "russia": "RU",
    "russian federation": "RU",
    "iran": "IR",
    "iran (islamic republic of)": "IR",
    "islamic republic of iran": "IR",
    "syria": "SY",
    "syrian arab republic": "SY",
    "north korea": "KP",
    "korea, north": "KP",
    "democratic people's republic of korea": "KP",
    "korea, democratic people's republic of": "KP",
    "dprk": "KP",
    "south korea": "KR",
    "korea, south": "KR",
    "republic of korea": "KR",
    "korea, republic of": "KR",
    "democratic republic of the congo": "CD",
    "congo, democratic republic of the": "CD",
    "congo (democratic republic)": "CD",
    "democratic republic of congo": "CD",
    "drc": "CD",
    "congo": "CG",
    "republic of the congo": "CG",
    "congo, republic of the": "CG",
    "united republic of tanzania": "TZ",
    "tanzania": "TZ",
    "türkiye": "TR",
    "turkiye": "TR",
    "turkey": "TR",
    "palestinian": "PS",
    "palestine": "PS",
    "occupied palestinian territories": "PS",
    "palestinian territories": "PS",
    "west bank": "PS",
    "gaza": "PS",
    "gaza strip": "PS",
    "kosovo": "XK",
    "burma": "MM",
    "myanmar": "MM",
    "ivory coast": "CI",
    "cote d'ivoire": "CI",
    "côte d'ivoire": "CI",
    "laos": "LA",
    "lao people's democratic republic": "LA",
    "vietnam": "VN",
    "viet nam": "VN",
    "bolivia": "BO",
    "venezuela": "VE",
    "moldova": "MD",
    "netherlands": "NL",
    "the netherlands": "NL",
    "uk": "GB",
    "united kingdom": "GB",
    "great britain": "GB",
    "usa": "US",
    "united states": "US",
    "united states of america": "US",
    "uae": "AE",
    "united arab emirates": "AE",
    "czech republic": "CZ",
    "macedonia": "MK",
    "north macedonia": "MK",
    "swaziland": "SZ",
    "cape verde": "CV",
    "brunei": "BN",
    "taiwan": "TW",
    "hong kong": "HK",
    "macau": "MO",
    "macao": "MO",
    "vatican": "VA",
    "holy see": "VA",
    "crimea": "UA",
    "gambia, the": "GM",
    "the gambia": "GM",
    "bahamas, the": "BS",
    "the bahamas": "BS",
    "sudan": "SD",
    "south sudan": "SS",
    "libya": "LY",
    "bosnia-herzegovina": "BA",
    "bosnia": "BA",
    "st kitts and nevis": "KN",
    "st. kitts and nevis": "KN",
    "st. kitts & nevis": "KN",
    "saint kitts and nevis": "KN",
    "st vincent and the grenadines": "VC",
    "st. vincent and the grenadines": "VC",
    "st lucia": "LC",
    "st. lucia": "LC",
    "comoros islands": "KM",
    "sint maarten": "SX",
    "curacao": "CW",
    "curaçao": "CW",
    "micronesia": "FM",
    "micronesia, federated states of": "FM",
}

# Historical states that no longer exist map to None on purpose (DQ020 reports them).
HISTORICAL = frozenset(
    {"ussr", "soviet union", "serbia and montenegro", "yugoslavia", "czechoslovakia"}
)

_PAREN_NOTE = re.compile(r"\s*\((?:as at|formerly|reportedly|possibly)[^)]*\)\s*$", re.I)
_NOW = re.compile(r"\((?:now|currently)\s+([^)]+)\)|\b(?:now|currently)\s+(.+)$", re.I)
_ANY_PAREN = re.compile(r"\s*\([^)]*\)\s*")


def is_valid_iso2(code: str) -> bool:
    return code in EXTRA_CODES or pycountry.countries.get(alpha_2=code) is not None


@cache
def to_iso2(raw: str | None) -> str | None:
    """Map a country code or name to ISO 3166-1 alpha-2, or ``None`` if unknown.

    Never returns the input string unchanged unless it is itself a valid code.
    """
    text = (raw or "").strip()
    if not text or text.upper() in UNKNOWN_CODES:
        return None
    if len(text) == 2 and text.isalpha():
        code = text.upper()
        return code if is_valid_iso2(code) else None
    if len(text) == 3 and text.isalpha() and text.isupper():
        c = pycountry.countries.get(alpha_3=text)
        if c is not None:
            return c.alpha_2
    if m := _NOW.search(text):  # "Ukrainian SSR (now Ukraine)", "Former USSR Currently Belarus"
        return to_iso2((m.group(1) or m.group(2)).strip())
    key = _PAREN_NOTE.sub("", text).strip().lower()
    if key in OVERRIDES:
        return OVERRIDES[key]
    if key in HISTORICAL:
        return None
    if "(" in key:  # "Russia (USSR)" -> "russia"
        return to_iso2(_ANY_PAREN.sub(" ", key).strip())
    try:
        return pycountry.countries.lookup(key).alpha_2
    except LookupError:
        pass
    try:
        matches = pycountry.countries.search_fuzzy(key)
    except LookupError:
        return None
    return matches[0].alpha_2 if len(matches) == 1 else None
