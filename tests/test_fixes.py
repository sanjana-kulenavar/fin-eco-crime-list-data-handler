from slo.dq.engine import run_rules
from slo.dq.fixes import build_clean
from slo.models import RawDob, RawIdentifier, RawName
from slo.requests_log import add_request, approve
from slo.store import fetch_one, query_frame
from tests.conftest import D, load, person


def _table_hash(con, table):
    return fetch_one(
        con, f"SELECT md5(string_agg(t::VARCHAR, '|' ORDER BY t::VARCHAR)) FROM {table} t"
    )[0]


def _messy():
    return [
        person(
            "1",
            "Ali  Hassan",
            names=[
                RawName("Ali  Hassan", "primary"),
                RawName(""),
                RawName("ALI HASSAN", strength="weak"),
            ],
            dobs=[RawDob("1960"), RawDob("1961"), RawDob("1962"), RawDob("1970")],
            identifiers=[
                RawIdentifier("passport", "P 123-456", "IQ"),
                RawIdentifier("passport", "P123456", "IQ"),
            ],
        ),
        person("2", "Сергей Иванов", names=[RawName("Сергей Иванов", "primary")]),
    ]


def test_fixes_build_clean_tables_and_leave_raw_untouched(con):
    load(con, "tst", _messy())
    run_rules(con, "tst", D, "r")
    before = {t: _table_hash(con, t) for t in ("names", "birth_dates", "identifiers")}
    result = build_clean(con, "tst", D)
    assert {t: _table_hash(con, t) for t in before} == before  # raw tables unchanged
    assert result["fixes"]["DQ002"] == 1 and result["fixes"]["DQ004"] == 1
    assert result["fixes"]["DQ005"] == 1 and result["fixes"]["DQ003"] == 1
    assert (
        result["fixes"]["DQ012"] == 1
        and result["fixes"]["DQ031"] == 1
        and result["fixes"]["DQ030"] == 1
    )

    names = query_frame(
        con, "SELECT entity_key, full_name, generated FROM names_clean ORDER BY 1, 2"
    )
    assert names.filter(names["entity_key"] == "tst:1")["full_name"].to_list() == ["Ali Hassan"]
    generated = names.filter(names["generated"])
    assert generated["full_name"].to_list() == ["Sergei Ivanov"] or generated[
        "full_name"
    ].to_list() == ["Sergey Ivanov"]

    dobs = query_frame(
        con, "SELECT raw, precision FROM birth_dates_clean WHERE entity_key = 'tst:1' ORDER BY raw"
    )
    assert dobs.rows() == [("1960 to 1962", "range"), ("1970", "year")]
    ids = query_frame(con, "SELECT raw_number FROM identifiers_clean WHERE entity_key = 'tst:1'")
    assert ids["raw_number"].to_list() == ["P123456"]

    fixed = query_frame(
        con, "SELECT rule_id, bool_and(fixed) AS f FROM dq_issues GROUP BY 1 ORDER BY 1"
    )
    as_dict = dict(fixed.rows())
    assert as_dict["DQ002"] and as_dict["DQ005"] and as_dict["DQ012"] and as_dict["DQ031"]
    assert not as_dict.get("DQ021", False)  # not auto-fixable -> stays open
    log = query_frame(con, "SELECT rule_id, before, after FROM fix_log WHERE rule_id = 'DQ012'")
    assert log.rows() == [("DQ012", "1960,1961,1962", "1960 to 1962")]


def test_build_clean_is_idempotent(con):
    load(con, "tst", _messy())
    first = build_clean(con, "tst", D)
    second = build_clean(con, "tst", D)
    assert first == second
    assert fetch_one(con, "SELECT count(*) FROM fix_log")[0] == sum(first["fixes"].values())


def test_requests_applied_in_clean_tables(con, requests_dir):
    load(con, "tst", [person("1", "Ali Hassan"), person("2", "Omar Saleh")])
    path = requests_dir / "requests.csv"
    r1 = add_request(
        path,
        request_type="alias_enrichment",
        target_source="tst",
        target_entity_key="tst:1",
        payload={"aliases": ["Aly Hasan"]},
        rationale="t",
        maker="m",
        requested_by="x",
    )
    r2 = add_request(
        path,
        request_type="data_correction",
        target_source="tst",
        target_entity_key="tst:2",
        payload={"field": "dob", "value": "1955-02-03"},
        rationale="t",
        maker="m",
        requested_by="x",
    )
    add_request(
        path,
        request_type="alias_enrichment",
        target_source="tst",
        target_entity_key="tst:2",
        payload={"aliases": ["Never Applied"]},
        rationale="t",
        maker="m",
        requested_by="x",
    )
    approve(path, r1.request_id, "checker")
    approve(path, r2.request_id, "checker")
    build_clean(con, "tst", D, requests_dir)
    names = query_frame(
        con, "SELECT full_name FROM names_clean WHERE entity_key='tst:1' ORDER BY 1"
    )["full_name"].to_list()
    assert names == ["Ali Hassan", "Aly Hasan"]
    dob = fetch_one(con, "SELECT raw FROM birth_dates_clean WHERE entity_key='tst:2'")
    assert dob == ("1955-02-03",)
    assert (
        fetch_one(con, "SELECT count(*) FROM names_clean WHERE full_name = 'Never Applied'")[0] == 0
    )
    log = query_frame(con, "SELECT rule_id FROM fix_log ORDER BY 1")["rule_id"].to_list()
    assert log == [f"REQ:{r1.request_id}", f"REQ:{r2.request_id}"]
