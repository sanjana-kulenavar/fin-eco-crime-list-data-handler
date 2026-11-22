"""End-to-end: the offline pipeline on the real-record fixtures."""

import json

import httpx
import pytest

from slo import fetch as fetch_mod
from slo.config import Workspace
from slo.pipeline import ingest_step, loaded_sources, report_step
from slo.report import END, START, findings_markdown, update_readme
from slo.store import connect, fetch_one, query_frame
from tests.conftest import FIXTURES, D


def test_all_sources_ok(demo):
    ws, summary = demo
    assert set(summary["sources"]) == {"eu", "un", "ofac", "uk", "nl", "internal"}
    for s, card in summary["sources"].items():
        assert card["status"] == "ok", s
        assert card["export_gate_passed"], s
        assert 0 <= card["dq_score"] <= 100
    con = connect(ws.db_path)
    counts = dict(query_frame(con, "SELECT source, count(*) FROM entities GROUP BY 1").rows())
    assert counts == {"eu": 5, "un": 6, "ofac": 6, "uk": 6, "nl": 4, "internal": 10}
    runs = query_frame(
        con, "SELECT source, status, record_count_source, record_count_parsed, raw_sha256 FROM runs"
    )
    assert runs.height == 6 and set(runs["status"]) == {"ok"}
    assert (runs["record_count_source"] == runs["record_count_parsed"]).all()
    assert runs["raw_sha256"].str.len_chars().min() == 64
    assert loaded_sources(con, D) == ["eu", "un", "ofac", "uk", "nl", "internal"]
    con.close()


def test_expected_quirks_found(demo):
    ws, _ = demo
    con = connect(ws.db_path)
    issues = query_frame(con, "SELECT DISTINCT source || ':' || rule_id AS k FROM dq_issues")[
        "k"
    ].to_list()
    for expected in (
        "eu:DQ002",
        "eu:DQ020",
        "eu:DQ012",
        "un:DQ004",
        "un:DQ030",
        "uk:DQ011",
        "uk:DQ013",
        "uk:DQ031",
        "eu:DQ050",
        "uk:DQ012",
    ):
        assert expected in issues, expected
    link = fetch_one(
        con, "SELECT eu_key, uk_key, ofac_key FROM crosslinks WHERE un_key = 'un:6908048'"
    )
    assert link == ("eu:13", "uk:IRQ0062", "ofac:7843")  # Saddam Hussein on all four lists
    # approved alias-enrichment request R002 applied to the EU record
    assert (
        fetch_one(
            con,
            "SELECT count(*) FROM names_clean WHERE entity_key='eu:13' AND full_name='Saddam Husayn al-Tikriti'",
        )[0]
        == 1
    )
    con.close()


def test_artefacts_written(demo):
    ws, summary = demo
    rep = ws.report_dir(str(D))
    for f in (
        "summary.json",
        "summary.md",
        "dq_scorecard.json",
        "crosslist.json",
        "fix_examples.json",
        "runs.csv",
        "dq_issues_sample.csv",
        "dq_scores.png",
        "screening/metrics.json",
        "screening/pr_curve.png",
        "screening/recall_by_perturbation.png",
        "screening/fp_at_95_recall.png",
        "screening/missed_hits_raw.csv",
        "screening/top_false_positives_clean.csv",
    ):
        assert (rep / f).exists(), f
    for s in ("eu", "un", "ofac", "uk", "nl", "internal"):
        delta = json.loads((ws.deltas_dir / s / f"{D}.json").read_text())
        assert delta["initial_load"] is True
    for system in ("payments_screening", "customer_screening", "delta_feed"):
        m = json.loads((ws.exports_dir / system / f"{D}.manifest.json").read_text())
        assert m["dq_gate_passed"] and m["record_count"] > 0
    metrics = json.loads((rep / "screening" / "metrics.json").read_text())
    assert set(metrics["runs"]) == {"raw", "clean"}
    assert metrics["synthetic_data"]["n_customers"] == 400
    assert len(metrics["synthetic_data"]["customers_sha256"]) == 64
    assert json.loads((ws.reports_dir / "latest.json").read_text()) == {"snapshot_date": str(D)}
    assert summary["crosslist"]["stats"]["un_eu"] >= 1


def test_rerun_is_idempotent_and_report_step(demo):
    ws, summary = demo
    con = connect(ws.db_path)
    before = fetch_one(con, "SELECT count(*) FROM names")[0]
    r = ingest_step(con, ws, "eu", D, "again", None)  # re-load from the stored raw file
    assert r["status"] == "ok" and fetch_one(con, "SELECT count(*) FROM names")[0] == before
    again = report_step(con, ws, D)
    assert again["sources"]["eu"]["records"] == summary["sources"]["eu"]["records"]
    assert again["screening"]["runs"]["raw"]["n_customers"] == 400
    con.close()


def test_ingest_failure_is_recorded(tmp_path):
    ws = Workspace(data_dir=tmp_path)
    con = connect(ws.db_path)
    r = ingest_step(con, ws, "un", D, "x", None)
    assert r["status"] == "failed" and "no raw file" in r["error"]
    assert fetch_one(con, "SELECT status FROM runs")[0] == "failed"
    con.close()


def test_runs_raw_path_is_relative(demo):
    ws, _ = demo
    con = connect(ws.db_path)
    paths = [r[0] for r in con.execute("SELECT raw_path FROM runs").fetchall()]
    con.close()
    assert paths and all(not p.startswith("/") for p in paths)


def test_readme_update(tmp_path, demo):
    _, summary = demo
    block = findings_markdown(summary, "data/reports/x")
    assert "Screening experiment" in block and "| EU |" in block
    readme = tmp_path / "README.md"
    readme.write_text(f"intro\n{START}\nSTALE-BLOCK\n{END}\noutro\n")
    assert update_readme(readme, block)
    text = readme.read_text()
    assert "STALE-BLOCK" not in text and "intro" in text and "outro" in text and "| EU |" in text
    no_markers = tmp_path / "other.md"
    no_markers.write_text("nothing")
    assert not update_readme(no_markers, block)
    assert not update_readme(tmp_path / "missing.md", block)


class _FakeStream:
    def __init__(self, body: bytes, status: int = 200):
        self.body, self.status = body, status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            raise httpx.HTTPStatusError(
                "boom",
                request=httpx.Request("GET", "http://x"),
                response=httpx.Response(self.status),
            )

    def iter_bytes(self, _n):
        yield self.body


def test_fetch_retries_then_succeeds(tmp_path, monkeypatch):
    calls = []
    body = (FIXTURES / "un.xml").read_bytes()

    def fake_stream(method, url, **kw):
        calls.append(url)
        return _FakeStream(body if len(calls) > 1 else b"", 200 if len(calls) > 1 else 503)

    monkeypatch.setattr(fetch_mod.httpx, "stream", fake_stream)
    monkeypatch.setattr(fetch_mod.time, "sleep", lambda s: None)
    result = fetch_mod.fetch_source(
        "un", {"url": "http://example.invalid/un.xml", "format": "xml"}, tmp_path, "2026-11-22"
    )
    assert len(calls) == 2 and result.bytes == len(body) and len(result.sha256) == 64
    assert result.path == tmp_path / "un" / "2026-11-22.xml"


def test_fetch_gives_up(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_mod.httpx, "stream", lambda *a, **k: _FakeStream(b"", 200))
    monkeypatch.setattr(fetch_mod.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="after 3 attempts"):
        fetch_mod.download("http://example.invalid/x", tmp_path / "x.xml")
    assert not (tmp_path / "x.xml.part").exists()
