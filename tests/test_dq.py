"""Every DQ rule: fires on its quirk (positive) and stays silent on a clean record (negative)."""

from datetime import UTC, date, datetime

import polars as pl
import pytest

from slo.config import load_dq_rules
from slo.crosslist import build_crosslinks, crosslist_issues
from slo.dq.engine import export_gate, run_crosslist_rules, run_rules, scorecard
from slo.dq.rules import RULES
from slo.models import RawCountry, RawDob, RawIdentifier, RawName
from slo.store import fetch_one, insert_frame
from tests.conftest import D, load, org, person

CLEAN = person("ok", "Clean Person")


def keys(con, rule_id, source="tst", d=D):
    return set(RULES[rule_id](con, source, d)["entity_key"].to_list())


def check(con, rule_id, bad, *, extra=()):
    load(con, "tst", [CLEAN, bad, *extra])
    found = keys(con, rule_id)
    assert f"tst:{bad.source_id}" in found, f"{rule_id} did not fire"
    assert "tst:ok" not in found, f"{rule_id} fired on a clean record"


def test_every_configured_rule_is_implemented():
    cfg = load_dq_rules()
    implemented = set(RULES) | {"DQ050", "DQ051", "DQ052"}
    assert set(cfg) == implemented


def test_dq001_no_primary_name(con):
    check(con, "DQ001", person("x", "", primary_name=None, names=[]))


def test_dq002_whitespace(con):
    check(con, "DQ002", person("x", "Ali  Hassan ", names=[RawName("Ali  Hassan ", "primary")]))


def test_dq003_non_latin_only(con):
    check(con, "DQ003", person("x", "Сергей Иванов", names=[RawName("Сергей Иванов", "primary")]))


def test_dq004_empty_alias(con):
    check(
        con,
        "DQ004",
        person("x", "Ali Hassan", names=[RawName("Ali Hassan", "primary"), RawName("", "alias")]),
    )


def test_dq005_duplicate_names(con):
    check(
        con,
        "DQ005",
        person("x", "Ali Hassan", names=[RawName("Ali Hassan", "primary"), RawName("ALI  HASSAN")]),
    )


def test_dq010_no_dob(con):
    check(con, "DQ010", person("x", "Ali Hassan", dobs=[]))
    load(con, "tst", [org("o", "Some Org")])
    assert "tst:o" not in keys(con, "DQ010")  # organisations are exempt


def test_dq011_partial_dob(con):
    check(con, "DQ011", person("x", "Ali Hassan", dobs=[RawDob("dd/mm/1960")]))


def test_dq012_consecutive_years(con):
    bad = person("x", "Ali Hassan", dobs=[RawDob("1960"), RawDob("1961"), RawDob("1962")])
    gap = person("g", "Gap Years", dobs=[RawDob("1960"), RawDob("1965")])
    check(con, "DQ012", bad, extra=[gap])
    assert "tst:g" not in keys(con, "DQ012")


def test_dq013_unparseable(con):
    check(con, "DQ013", person("x", "Ali Hassan", dobs=[RawDob("15/08/19yy")]))


def test_dq014_invalid(con):
    check(con, "DQ014", person("x", "Ali Hassan", dobs=[RawDob("1340")]))


def test_dq020_unmapped_country(con):
    check(con, "DQ020", person("x", "Ali Hassan", countries=[RawCountry("nationality", "00")]))


def test_dq021_no_nationality(con):
    check(con, "DQ021", person("x", "Ali Hassan", countries=[RawCountry("birth", "IQ")]))


def test_dq030_id_noise(con):
    check(
        con,
        "DQ030",
        person("x", "Ali Hassan", identifiers=[RawIdentifier("passport", "AB 1234-56", "IQ")]),
    )


def test_dq031_repeated_id(con):
    ids = [RawIdentifier("passport", "P123456", "IQ"), RawIdentifier("passport", "P-123456", "IQ")]
    check(con, "DQ031", person("x", "Ali Hassan", identifiers=ids))


def test_dq032_shared_id(con):
    a = person("x", "Ali Hassan", identifiers=[RawIdentifier("passport", "P999888", "IQ")])
    b = person("y", "Omar Saleh", identifiers=[RawIdentifier("passport", "P999888", "IQ")])
    check(con, "DQ032", a, extra=[b])
    assert "tst:y" in keys(con, "DQ032")


def test_dq033_id_without_country(con):
    check(
        con,
        "DQ033",
        person("x", "Ali Hassan", identifiers=[RawIdentifier("passport", "P777666", None)]),
    )


def test_dq040_duplicate_entity(con):
    a = person("x", "Ali Hassan Omar", dobs=[RawDob("1960")])
    b = person("y", "Omar Ali Hassan", dobs=[RawDob("1960-05-01")])
    other_dob = person("z", "Ali Hassan Omar", dobs=[RawDob("1990-01-01")])
    check(con, "DQ040", a, extra=[b, other_dob])
    assert "tst:z" not in keys(con, "DQ040")  # same name, different person (disjoint DOB)


def _run(con, source, declared, parsed, sha="s1", pub=D, d=D, error=None):
    now = datetime.now(UTC).replace(tzinfo=None)
    insert_frame(
        con,
        "runs",
        pl.DataFrame(
            [
                {
                    "run_id": f"r{d}",
                    "started_at": now,
                    "finished_at": now,
                    "source": source,
                    "snapshot_date": d,
                    "raw_sha256": sha,
                    "published_date": pub,
                    "record_count_source": declared,
                    "record_count_parsed": parsed,
                    "status": "ok",
                    "error": error,
                }
            ]
        ),
    )


def test_dq060_count_mismatch_and_gate(con):
    load(con, "tst", [CLEAN])
    _run(con, "tst", declared=1, parsed=1)
    assert RULES["DQ060"](con, "tst", D).height == 0
    load(con, "bad", [CLEAN])
    _run(con, "bad", declared=2, parsed=1, error="1: boom")
    issues = RULES["DQ060"](con, "bad", D)
    assert set(issues["field"]) == {"record_count", "parse"}
    run_rules(con, "bad", D, "t")
    run_rules(con, "tst", D, "t")
    assert export_gate(con, "tst", D) == (True, [])
    ok, reasons = export_gate(con, "bad", D)
    assert not ok and any("DQ060" in r for r in reasons)


def test_dq060_swing_vs_previous_snapshot(con):
    prev = date(2026, 11, 21)
    load(con, "tst", [person(str(i), f"P{i} X") for i in range(20)], d=prev)
    load(con, "tst", [person(str(i), f"P{i} X") for i in range(10)])
    assert "more than 5%" in RULES["DQ060"](con, "tst", D)["message"][0]


def test_dq061_fingerprint(con):
    prev = date(2026, 11, 21)
    for src in ("same", "diff", "fine"):
        load(con, src, [CLEAN], d=prev)
        load(con, src, [CLEAN])
    _run(con, "same", 1, 1, sha="a", pub=date(2026, 11, 20), d=prev)
    _run(con, "same", 1, 1, sha="a", pub=date(2026, 11, 21))
    _run(con, "diff", 1, 1, sha="a", pub=date(2026, 11, 20), d=prev)
    _run(con, "diff", 1, 1, sha="b", pub=date(2026, 11, 20))
    _run(con, "fine", 1, 1, sha="a", pub=date(2026, 11, 20), d=prev)
    _run(con, "fine", 1, 1, sha="b", pub=date(2026, 11, 21))
    assert "unchanged" in RULES["DQ061"](con, "same", D)["message"][0]
    assert "did not" in RULES["DQ061"](con, "diff", D)["message"][0]
    assert RULES["DQ061"](con, "fine", D).height == 0


@pytest.fixture
def cross(con):
    un = [
        person("1", "Ali Hassan Omar", un_reference="QDi.001", dobs=[RawDob("1960")]),
        person("2", "Missing From Eu", un_reference="QDi.002", listed_on=date(2026, 1, 1)),
        person("3", "Shared Ref Person", un_reference="QDi.003"),
    ]
    eu = [
        person("a", "Ali Hassan Omar", dobs=[RawDob("1960-03-03")]),  # linked by name + DOB
        person("b", "Totally Different Name", un_reference="QDi.003"),  # same ref, no name match
        person("c", "Stale Reference", un_reference="QDi.999"),
    ]
    load(con, "un", un)
    load(con, "eu", eu)
    load(con, "uk", [person("k", "Ali Hassan Omar", un_reference="QDi.001")])
    return build_crosslinks(con, D)


def test_crosslinks(con, cross):
    assert cross["un_entities"] == 3 and cross["un_eu"] == 2 and cross["un_uk"] == 1
    assert cross["linked_by_un_ref"]["uk"] == 1 and cross["linked_by_un_ref"]["eu"] == 1
    row = fetch_one(con, "SELECT eu_key, uk_key, link_method FROM crosslinks WHERE un_key = 'un:1'")
    assert row == ("eu:a", "uk:k", "eu:name_dob,uk:un_ref")


def test_dq050_051_052(con, cross):
    eu = crosslist_issues(con, D, "eu")
    assert eu["DQ050"]["entity_key"].to_list() == ["un:2"]
    assert "days since UN listing" in eu["DQ050"]["message"][0]
    assert eu["DQ051"]["entity_key"].to_list() == ["eu:b"]
    assert eu["DQ052"]["entity_key"].to_list() == ["eu:c"]
    uk = crosslist_issues(con, D, "uk")
    assert set(uk["DQ050"]["entity_key"]) == {"un:2", "un:3"}
    assert uk["DQ051"].height == 0 and uk["DQ052"].height == 0
    counts = run_crosslist_rules(con, D, "t")
    assert counts == {"eu": 3, "uk": 2}


def test_crosslist_without_un_snapshot(con):
    load(con, "eu", [CLEAN])
    assert build_crosslinks(con, D)["snapshot_dates"]["un"] is None
    assert all(df.height == 0 for df in crosslist_issues(con, D, "eu").values())


def test_run_rules_and_scorecard(con):
    load(
        con,
        "tst",
        [CLEAN, person("x", "Ali  Hassan", dobs=[], names=[RawName("Ali  Hassan", "primary")])],
    )
    _run(con, "tst", 2, 2)
    issues = run_rules(con, "tst", D, "run1")
    assert {"DQ002", "DQ010"} <= set(issues["rule_id"])
    assert set(issues["severity"]) <= {"high", "medium", "low"}
    again = run_rules(con, "tst", D, "run2")  # idempotent per snapshot
    assert fetch_one(con, "SELECT count(*) FROM dq_issues WHERE source='tst'")[0] == again.height
    card = scorecard(con, "tst", D)
    assert card["records"] == 2 and card["persons"] == 2
    assert card["pct_persons_full_dob"] == 50.0
    assert 0 <= card["dq_score"] < 100
    assert card["issues_by_rule"]["DQ010"]["entities"] == 1
    assert card["export_gate_passed"] is True


def test_scorecard_empty_source(con):
    card = scorecard(con, "nothing", D)
    assert card["status"] == "failed" and card["dq_score"] is None
