import json
from datetime import date

from slo.delta import compute_delta, store_delta
from slo.models import RawDob, RawName
from slo.store import fetch_one
from tests.conftest import load, person

D1, D2 = date(2026, 11, 21), date(2026, 11, 22)


def test_initial_load(con, tmp_path):
    load(con, "tst", [person("1", "A B"), person("2", "C D")], d=D1)
    delta = compute_delta(con, "tst", D1)
    assert delta["initial_load"] and delta["from"] is None
    assert delta["counts"] == {"added": 2, "removed": 0, "amended": 0}
    path = store_delta(con, delta, tmp_path)
    assert json.loads(path.read_text())["changes_truncated"] is True


def test_add_remove_amend(con, tmp_path):
    load(
        con,
        "tst",
        [person("1", "Ali Hassan"), person("2", "Omar Saleh"), person("3", "Same Person")],
        d=D1,
    )
    load(
        con,
        "tst",
        [
            person(
                "1", "Ali Hassan", names=[RawName("Ali Hassan", "primary"), RawName("Aly Hasan")]
            ),  # alias added
            person("3", "Same Person"),  # unchanged
            person("4", "New Person"),  # added
            person("5", "Dob Change", dobs=[RawDob("1999")]),  # added
        ],
        d=D2,
    )
    delta = compute_delta(con, "tst", D2)
    assert delta["from"] == str(D1) and not delta["initial_load"]
    assert delta["counts"] == {"added": 2, "removed": 1, "amended": 1}
    amended = [c for c in delta["changes"] if c["change_type"] == "amended"]
    assert amended == [
        {
            "entity_key": "tst:1",
            "change_type": "amended",
            "changed_fields": ["names"],
            "primary_name": "Ali Hassan",
        }
    ]
    removed = [c["entity_key"] for c in delta["changes"] if c["change_type"] == "removed"]
    assert removed == ["tst:2"]
    path = store_delta(con, delta, tmp_path)
    assert path == tmp_path / "tst" / f"{D2}.json"
    assert json.loads(path.read_text())["counts"] == delta["counts"]
    assert fetch_one(con, "SELECT count(*) FROM deltas WHERE to_date = ?", [D2])[0] == 4
    store_delta(con, delta, tmp_path)  # idempotent
    assert fetch_one(con, "SELECT count(*) FROM deltas WHERE to_date = ?", [D2])[0] == 4


def test_entity_field_change(con):
    load(con, "tst", [person("1", "Ali Hassan", programmes=["A"])], d=D1)
    load(
        con, "tst", [person("1", "Ali Hassan", programmes=["A", "B"], dobs=[RawDob("1961")])], d=D2
    )
    change = compute_delta(con, "tst", D2)["changes"][0]
    assert change["change_type"] == "amended"
    assert change["changed_fields"] == ["programmes", "birth_dates"]
