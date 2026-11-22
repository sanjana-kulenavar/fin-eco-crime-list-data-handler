from datetime import date

import polars as pl
import pytest

from slo.build import build_frames, entity_rows, normalise_un_reference
from slo.models import ParseResult, RawName
from slo.store import (
    connect,
    fetch_one,
    insert_frame,
    previous_snapshot,
    query_frame,
    replace_snapshot,
    snapshot_dates,
)
from tests.conftest import D, load, person


def test_record_hash_is_stable_and_order_independent():
    a = person("1", "Jan Jansen", names=[RawName("Jan Jansen", "primary"), RawName("J Jansen")])
    b = person("1", "Jan Jansen", names=[RawName("J Jansen"), RawName("Jan Jansen", "primary")])
    ha = entity_rows("x", D, a)["entities"][0]["record_hash"]
    hb = entity_rows("x", D, b)["entities"][0]["record_hash"]
    hc = entity_rows("x", date(2027, 1, 1), a)["entities"][0]["record_hash"]
    assert ha == hb == hc
    c = person(
        "1", "Jan Jansen", names=[RawName("Jan Jansen", "primary"), RawName("Johannes Jansen")]
    )
    assert entity_rows("x", D, c)["entities"][0]["record_hash"] != ha


def test_un_reference_normalised():
    assert normalise_un_reference(" QDi.1. ") == "QDI.001"
    assert normalise_un_reference("") is None
    assert normalise_un_reference("N/A") is None


def test_replace_snapshot_is_idempotent(con):
    load(con, "tst", [person("1", "A B"), person("2", "C D")])
    load(con, "tst", [person("1", "A B"), person("2", "C D")])
    assert fetch_one(con, "SELECT count(*) FROM entities")[0] == 2
    assert fetch_one(con, "SELECT count(*) FROM names")[0] == 2
    load(con, "tst", [person("1", "A B")], d=date(2026, 11, 23))
    assert snapshot_dates(con, "tst") == [D, date(2026, 11, 23)]
    assert previous_snapshot(con, "tst", date(2026, 11, 23)) == D
    assert previous_snapshot(con, "tst", D) is None


def test_insert_frame_rejects_unknown_columns(con):
    with pytest.raises(ValueError, match="unknown columns"):
        insert_frame(con, "entities", pl.DataFrame({"nope": [1]}))
    assert insert_frame(con, "entities", pl.DataFrame()) == 0


def test_replace_snapshot_rolls_back_on_error(con):
    load(con, "tst", [person("1", "A B")])
    frames = build_frames(ParseResult("tst", [person("2", "X Y")], 1), D)
    frames["names"] = frames["names"].with_columns(pl.lit(1).alias("bogus"))
    with pytest.raises(ValueError):
        replace_snapshot(con, "tst", D, frames)
    assert query_frame(con, "SELECT entity_key FROM entities")["entity_key"].to_list() == ["tst:1"]


def test_connect_file(tmp_path):
    c = connect(tmp_path / "sub" / "x.duckdb")
    assert fetch_one(c, "SELECT count(*) FROM runs")[0] == 0
    assert fetch_one(c, "SELECT 1 WHERE false") is None
    c.close()


def test_un_reference_zero_padding():
    assert normalise_un_reference("LYI.28") == normalise_un_reference("LYi.028") == "LYI.028"
    assert normalise_un_reference("CDi.001") == "CDI.001"


def test_ofac_birth_country():
    from slo.parsers.ofac import _birth_country

    assert _birth_country("Baghdad, Iraq") == "Iraq"
    assert _birth_country("Khartoum Sudan") == "Sudan"
    assert _birth_country("Indiana USA") == "USA"
    assert _birth_country("Tripoli") == "Tripoli"
