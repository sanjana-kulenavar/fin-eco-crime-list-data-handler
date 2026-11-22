"""Run the real Streamlit script headless against artefacts only (no raw data, no database)."""

import shutil

import pytest

from slo.config import PROJECT_ROOT

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest


def test_dashboard_renders_all_tabs(demo, tmp_path, monkeypatch):
    ws, _ = demo
    data = tmp_path / "data"
    shutil.copytree(ws.reports_dir, data / "reports")
    shutil.copytree(ws.deltas_dir, data / "deltas")
    monkeypatch.setenv("SLO_DATA_DIR", str(data))
    monkeypatch.setenv("SLO_REQUESTS_DIR", str(ws.requests_dir))
    at = AppTest.from_file(str(PROJECT_ROOT / "app" / "dashboard.py"), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    assert [t.label for t in at.tabs] == [
        "Overview",
        "Data quality",
        "Cross-list",
        "Deltas",
        "Screening experiment",
        "Requests log",
    ]
    assert len(at.metric) >= 6 and len(at.dataframe) >= 6
    at.slider[0].set_value(90).run()
    assert not at.exception
