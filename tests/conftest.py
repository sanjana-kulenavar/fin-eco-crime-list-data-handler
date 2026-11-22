from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest

from slo.build import build_frames
from slo.config import PROJECT_ROOT, Workspace
from slo.models import ParseResult, RawCountry, RawDob, RawEntity, RawIdentifier, RawName
from slo.store import connect, replace_snapshot

D = date(2026, 11, 22)
FIXTURES = Path(__file__).parent / "fixtures"


def person(source_id: str, name: str, **kw) -> RawEntity:
    """A clean person record; override fields to inject a quirk."""
    defaults = dict(
        entity_type="person",
        primary_name=name,
        names=[RawName(name, "primary", strength="strong")],
        dobs=[RawDob("1970-05-17")],
        countries=[RawCountry("nationality", "NL")],
        identifiers=[RawIdentifier("passport", f"NP{source_id}X9", "NL")],
    )
    defaults.update(kw)
    return RawEntity(source_id=source_id, **defaults)


def org(source_id: str, name: str, **kw) -> RawEntity:
    defaults = dict(
        entity_type="organisation",
        primary_name=name,
        names=[RawName(name, "primary", strength="strong")],
    )
    defaults.update(kw)
    return RawEntity(source_id=source_id, **defaults)


def load(con, source: str, entities: list[RawEntity], d: date = D) -> None:
    result = ParseResult(source, entities, len(entities), None, d, [])
    replace_snapshot(con, source, d, build_frames(result, d))


@pytest.fixture
def con():
    c = connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def requests_dir(tmp_path: Path) -> Path:
    target = tmp_path / "requests"
    target.mkdir()
    for f in ("requests.csv", "internal_list.yaml"):
        shutil.copyfile(PROJECT_ROOT / "requests" / f, target / f)
    return target


@pytest.fixture(scope="session")
def demo(tmp_path_factory):
    """The full pipeline run once, offline, on the fixtures."""
    from slo.pipeline import run_all

    root = tmp_path_factory.mktemp("demo")
    req = root / "requests"
    req.mkdir()
    for f in ("requests.csv", "internal_list.yaml"):
        shutil.copyfile(PROJECT_ROOT / "requests" / f, req / f)
    ws = Workspace(data_dir=root / "data", requests_dir=req)
    summary = run_all(ws, D, offline=True, n_customers=400, n_hits=12, seed=7)
    return ws, summary
