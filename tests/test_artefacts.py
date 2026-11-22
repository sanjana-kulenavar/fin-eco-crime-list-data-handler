import json

import pytest

from slo.artefacts import issues_matrix, latest_snapshot, load_artefacts, load_deltas, sweep_frame
from tests.conftest import D


def test_load_artefacts_from_demo_without_raw_or_db(demo, tmp_path):
    ws, _ = demo
    import shutil

    copy = tmp_path / "data"
    shutil.copytree(ws.reports_dir, copy / "reports")
    shutil.copytree(ws.deltas_dir, copy / "deltas")  # no raw/, no duckdb
    art = load_artefacts(copy, ws.requests_dir)
    assert art.snapshot_date == str(D)
    assert set(art.summary["sources"]) >= {"eu", "un"}
    assert art.issues_sample.height > 0 and art.deltas.height == 6
    assert art.requests.height >= 5 and set(art.top_false_positives) <= {"raw", "clean"}
    m = issues_matrix(art.summary)
    assert {"source", "rule_id", "issues"} <= set(m.columns)
    s = sweep_frame(art.summary)
    assert set(s["mode"]) == {"raw", "clean"} and s.height == 42


def test_latest_snapshot_fallbacks(tmp_path):
    assert latest_snapshot(tmp_path) is None
    (tmp_path / "2026-01-01").mkdir()
    (tmp_path / "2026-02-01").mkdir()
    assert latest_snapshot(tmp_path) == "2026-02-01"
    (tmp_path / "latest.json").write_text(json.dumps({"snapshot_date": "2026-01-01"}))
    assert latest_snapshot(tmp_path) == "2026-01-01"
    assert load_deltas(tmp_path / "none").height == 0
    with pytest.raises(FileNotFoundError):
        load_artefacts(tmp_path / "empty", tmp_path)
