"""Name normalisation for matching keys.

``full_name`` keeps what the list published; ``normalise_name`` builds the
comparison key used for blocking, de-duplication and screening.
"""

from __future__ import annotations

import re
import unicodedata

from anyascii import anyascii

HONORIFICS = frozenset(
    {"mr", "mrs", "ms", "dr", "sheikh", "shaykh", "haji", "hajji", "mullah", "maulavi", "mawlawi"}
)
PARTICLES = frozenset({"al", "el", "bin", "ibn", "abu", "abd", "bint", "ould"})

COMPANY_SUFFIXES = {
    "ltd": "ltd",
    "limited": "ltd",
    "llc": "llc",
    "ooo": "llc",
    "tov": "llc",
    "co": "co",
    "company": "co",
    "inc": "inc",
    "incorporated": "inc",
    "corp": "corp",
    "corporation": "corp",
    "gmbh": "gmbh",
    "bv": "bv",
    "nv": "nv",
    "sa": "sa",
    "jsc": "jsc",
    "ojsc": "jsc",
    "pjsc": "jsc",
    "cjsc": "jsc",
    "ao": "jsc",
    "oao": "jsc",
    "pao": "jsc",
    "zao": "jsc",
}

_WS = re.compile(r"\s+")
_APOSTROPHES = re.compile(r"['’`ʻʼ]")
_DOTS = re.compile(r"\.")
_NON_ALNUM = re.compile(r"[^a-z0-9 ]+")


def collapse_ws(value: str | None) -> str:
    return _WS.sub(" ", value or "").strip()


def detect_script(value: str | None) -> str:
    """Return the dominant script of the letters in ``value``."""
    counts: dict[str, int] = {}
    for ch in value or "":
        if not ch.isalpha():
            continue
        try:
            uname = unicodedata.name(ch)
        except ValueError:
            continue
        if uname.startswith("LATIN"):
            script = "latin"
        elif uname.startswith("ARABIC"):
            script = "arabic"
        elif uname.startswith("CYRILLIC"):
            script = "cyrillic"
        elif uname.startswith(("CJK", "HANGUL", "HIRAGANA", "KATAKANA")):
            script = "cjk"
        else:
            script = "other"
        counts[script] = counts.get(script, 0) + 1
    if not counts:
        return "unknown"
    return max(counts.items(), key=lambda kv: kv[1])[0]


def normalise_name(value: str | None, *, is_org: bool = False) -> str:
    """NFKC -> transliterate -> lowercase -> strip punctuation -> canonical suffixes."""
    text = unicodedata.normalize("NFKC", value or "")
    text = anyascii(text).lower()
    text = _APOSTROPHES.sub("", text)
    text = _DOTS.sub("", text)  # "b.v." -> "bv", "co., ltd." -> "co, ltd"
    text = _NON_ALNUM.sub(" ", text)
    tokens = text.split()
    if is_org:
        tokens = [COMPANY_SUFFIXES.get(t, t) for t in tokens]
    return " ".join(tokens)


def strip_honorifics(norm: str) -> str:
    """Variant of a normalised name without honorifics and name particles."""
    return " ".join(t for t in norm.split() if t not in HONORIFICS and t not in PARTICLES)


def name_variants(norm: str) -> list[str]:
    """The normalised name plus (if different and non-empty) its stripped variant."""
    variants = [norm] if norm else []
    stripped = strip_honorifics(norm)
    if stripped and stripped != norm:
        variants.append(stripped)
    return variants


def tokens_sorted(norm: str) -> str:
    return " ".join(sorted(norm.split()))


def naive_key(value: str | None) -> str:
    """What a system that does *no* normalisation compares: lowercase only."""
    return collapse_ws(value).lower()
