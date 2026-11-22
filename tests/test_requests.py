import pytest
import yaml

from slo.requests_log import (
    RequestError,
    active_requests,
    add_request,
    apply_requests,
    approve,
    load_requests,
    parse_internal,
    reject,
)


def _add(path, **kw):
    args = dict(
        request_type="add_internal",
        target_source="internal",
        target_entity_key="internal:INT-0100",
        payload={"id": "INT-0100", "name": "New Example BV", "type": "organisation"},
        rationale="test",
        maker="alice",
        requested_by="FEC",
    )
    args.update(kw)
    return add_request(path, **args)


def test_shipped_request_log_covers_every_type(requests_dir):
    reqs = load_requests(requests_dir / "requests.csv")
    assert len(reqs) >= 5
    assert {r.request_type for r in reqs} == {
        "add_internal",
        "remove_internal",
        "false_positive_suppression",
        "alias_enrichment",
        "data_correction",
    }
    assert {"implemented", "rejected", "pending"} <= {r.status for r in reqs}
    for r in reqs:
        if r.status in ("approved", "implemented", "rejected"):
            assert r.checker and r.checker != r.maker


def test_four_eyes(requests_dir):
    path = requests_dir / "requests.csv"
    r = _add(path)
    assert r.request_id == "R007" and r.status == "pending"
    with pytest.raises(RequestError, match="four-eyes"):
        approve(path, r.request_id, "alice")
    with pytest.raises(RequestError, match="four-eyes"):
        approve(path, r.request_id, "  ALICE ")
    with pytest.raises(RequestError, match="four-eyes"):
        approve(path, r.request_id, "")
    approved = approve(path, r.request_id, "bob")
    assert approved.status == "approved" and approved.checker == "bob"
    with pytest.raises(RequestError, match="only pending"):
        reject(path, r.request_id, "carol")
    with pytest.raises(RequestError, match="no such"):
        approve(path, "R999", "bob")
    with pytest.raises(RequestError, match="unknown request_type"):
        _add(path, request_type="delete_everything")


def test_apply_add_and_remove_internal(requests_dir):
    path = requests_dir / "requests.csv"
    before = len(parse_internal(requests_dir / "internal_list.yaml").entities)
    add = _add(path)
    rm = _add(
        path, request_type="remove_internal", target_entity_key="internal:INT-0002", payload={}
    )
    rej = _add(
        path,
        payload={"id": "INT-0200", "name": "Rejected BV"},
        target_entity_key="internal:INT-0200",
    )
    approve(path, add.request_id, "bob")
    approve(path, rm.request_id, "bob")
    reject(path, rej.request_id, "bob")
    assert apply_requests(requests_dir) == [add.request_id, rm.request_id]
    entries = yaml.safe_load((requests_dir / "internal_list.yaml").read_text())["entities"]
    ids = {e["id"] for e in entries}
    assert "INT-0100" in ids and "INT-0002" not in ids and "INT-0200" not in ids
    assert len(entries) == before  # one added, one removed
    statuses = {r.request_id: r.status for r in load_requests(path)}
    assert statuses[add.request_id] == statuses[rm.request_id] == "implemented"
    assert statuses[rej.request_id] == "rejected"
    assert apply_requests(requests_dir) == []  # idempotent
    result = parse_internal(requests_dir / "internal_list.yaml")
    new = next(e for e in result.entities if e.source_id == "INT-0100")
    assert new.primary_name == "New Example BV" and new.programmes == ["INTERNAL"]


def test_active_requests(requests_dir):
    active = active_requests(requests_dir, "alias_enrichment")
    assert [r.request_id for r in active] == ["R002"]  # R006 is still pending
    assert active[0].payload["aliases"][0] == "Saddam Husayn al-Tikriti"


def test_missing_files(tmp_path):
    assert load_requests(tmp_path / "none.csv") == []
    assert parse_internal(tmp_path / "none.yaml").entities == []
