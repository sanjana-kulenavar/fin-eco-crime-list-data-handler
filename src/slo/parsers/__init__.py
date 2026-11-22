"""Source parsers. Each returns a :class:`slo.models.ParseResult`."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from slo.models import ParseResult
from slo.parsers.eu import parse_eu
from slo.parsers.nl_ftm import parse_nl
from slo.parsers.ofac import parse_ofac
from slo.parsers.uk import parse_uk
from slo.parsers.un import parse_un

PARSERS: dict[str, Callable[[Path], ParseResult]] = {
    "eu": parse_eu,
    "un": parse_un,
    "ofac": parse_ofac,
    "uk": parse_uk,
    "nl": parse_nl,
}

__all__ = ["PARSERS", "parse_eu", "parse_nl", "parse_ofac", "parse_uk", "parse_un"]
