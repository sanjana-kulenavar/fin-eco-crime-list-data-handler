"""Small helpers for memory-friendly XML parsing."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from lxml import etree


def iter_elements(path: Path, tag: str) -> Iterator[etree._Element]:
    """Yield each ``tag`` element and free it (and preceding siblings) afterwards."""
    for _, elem in etree.iterparse(str(path), events=("end",), tag=tag, huge_tree=True):
        yield elem
        elem.clear()
        parent = elem.getparent()
        while parent is not None and elem.getprevious() is not None:
            del parent[0]


def text(elem: etree._Element | None, path: str | None = None) -> str | None:
    """Stripped text of ``elem`` (or its child at ``path``); ``None`` if empty."""
    if elem is None:
        return None
    target = elem.find(path) if path else elem
    if target is None or target.text is None:
        return None
    value = target.text.strip()
    return value or None


def texts(elem: etree._Element, path: str) -> list[str]:
    out = []
    for child in elem.findall(path):
        if child.text and child.text.strip():
            out.append(child.text.strip())
    return out


def root_attr_and_text(path: Path, max_bytes: int = 4096) -> bytes:
    with path.open("rb") as fh:
        return fh.read(max_bytes)
