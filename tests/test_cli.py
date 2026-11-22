"""CLI smoke tests (typer CliRunner)."""

import shutil
from datetime import date

from typer.testing import CliRunner

from slo.cli import app
from slo.config import PROJECT_ROOT


def D_today():
    return date.today()


runner = CliRunner()


def test_help_lists_plan_commands():
    out = runner.invoke(app, ["--help"]).output
    for cmd in (
        "fetch",
        "normalise",
        "dq",
        "fix",
        "crosslist",
        "delta",
        "export",
        "request",
        "screen",
        "report",
        "all",
        "offline-demo",
        "version",
    ):
        assert cmd in out, cmd


def test_version():
    r = runner.invoke(app, ["version"])
    assert r.exit_code == 0 and "sanctions-list-ops" in r.output


def test_offline_demo_and_step_commands(tmp_path):
    data = tmp_path / "demo"
    r = runner.invoke(app, ["offline-demo", "--data-dir", str(data)])
    assert r.exit_code == 0, r.output
    assert "offline demo OK" in r.output
    d = ["--date", str(D_today()), "--data-dir", str(data)]
    for cmd in (
        ["dq", "--source", "eu"],
        ["fix", "--source", "uk"],
        ["crosslist"],
        ["delta", "--source", "un"],
        ["export", "--system", "payments_screening"],
        ["report"],
    ):
        res = runner.invoke(app, [*cmd, *d])
        assert res.exit_code == 0, (cmd, res.output)
    assert "gate_passed=True" in runner.invoke(app, ["export", *d]).output


def test_request_commands(tmp_path, monkeypatch):
    req = tmp_path / "requests"
    req.mkdir()
    for f in ("requests.csv", "internal_list.yaml"):
        shutil.copyfile(PROJECT_ROOT / "requests" / f, req / f)
    monkeypatch.setattr(
        "slo.cli._ws",
        lambda data_dir: __import__("slo.config", fromlist=["Workspace"]).Workspace(
            data_dir=tmp_path, requests_dir=req
        ),
    )
    r = runner.invoke(
        app,
        [
            "request",
            "add",
            "--type",
            "add_internal",
            "--source",
            "internal",
            "--entity",
            "internal:INT-0042",
            "--payload",
            '{"id": "INT-0042", "name": "Cli Example BV"}',
            "--rationale",
            "test",
            "--maker",
            "alice",
        ],
    )
    assert r.exit_code == 0 and "R007" in r.output
    bad = runner.invoke(app, ["request", "approve", "R007", "--checker", "alice"])
    assert bad.exit_code == 1 and "four-eyes" in bad.output
    assert runner.invoke(app, ["request", "approve", "R007", "--checker", "bob"]).exit_code == 0
    assert "R007" in runner.invoke(app, ["request", "apply"]).output
    assert "implemented" in runner.invoke(app, ["request", "list"]).output
    assert (
        runner.invoke(app, ["request", "reject", "R005", "--checker", "analyst.b"]).exit_code == 1
    )  # maker == checker
    assert (
        runner.invoke(
            app,
            [
                "request",
                "add",
                "--type",
                "nope",
                "--source",
                "x",
                "--rationale",
                "r",
                "--maker",
                "m",
            ],
        ).exit_code
        == 1
    )


def test_export_gate_blocks_offline_demo(tmp_path):
    fx = tmp_path / "fx"
    shutil.copytree(PROJECT_ROOT / "tests" / "fixtures", fx)
    ofac = fx / "ofac.xml"
    ofac.write_text(
        ofac.read_text().replace("<Record_Count>6</Record_Count>", "<Record_Count>7</Record_Count>")
    )
    r = runner.invoke(
        app, ["offline-demo", "--fixtures-dir", str(fx), "--data-dir", str(tmp_path / "demo")]
    )
    assert r.exit_code == 1
    assert "export gate blocked: ['ofac']" in r.output
