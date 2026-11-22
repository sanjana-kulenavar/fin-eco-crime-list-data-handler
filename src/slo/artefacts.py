"""Read committed run artefacts (used by the dashboard; never touches raw data or the DB)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from slo.requests_log import load_requests


@dataclass
class Artefacts:
    snapshot_date: str
    summary: dict
    issues_sample: pl.DataFrame
    fix_examples: list[dict]
    deltas: pl.DataFrame
    requests: pl.DataFrame
    top_false_positives: dict[str, pl.DataFrame]


def latest_snapshot(reports_dir: Path) -> str | None:
    pointer = reports_dir / "latest.json"
    if pointer.exists():
        return json.loads(pointer.read_text())["snapshot_date"]
    dates = sorted(p.name for p in reports_dir.glob("20*-*-*") if p.is_dir())
    return dates[-1] if dates else None


def load_deltas(deltas_dir: Path) -> pl.DataFrame:
    rows = []
    for path in sorted(deltas_dir.glob("*/*.json")):
        d = json.loads(path.read_text())
        rows.append(
            {
                "source": d["source"],
                "to": d["to"],
                "from": d["from"],
                "initial_load": d["initial_load"],
                **d["counts"],
            }
        )
    schema = {
        "source": pl.String,
        "to": pl.String,
        "from": pl.String,
        "initial_load": pl.Boolean,
        "added": pl.Int64,
        "removed": pl.Int64,
        "amended": pl.Int64,
    }
    return pl.DataFrame(rows, schema=schema)


def load_artefacts(
    data_dir: Path, requests_dir: Path, snapshot_date: str | None = None
) -> Artefacts:
    reports = data_dir / "reports"
    snap = snapshot_date or latest_snapshot(reports)
    if snap is None:
        raise FileNotFoundError(
            f"no reports under {reports}; run `slo all` or `slo offline-demo` first"
        )
    rep = reports / snap
    summary = json.loads((rep / "summary.json").read_text())
    fps = {}
    for mode in ("raw", "clean"):
        p = rep / "screening" / f"top_false_positives_{mode}.csv"
        if p.exists():
            fps[mode] = pl.read_csv(p, try_parse_dates=True)
    reqs = load_requests(requests_dir / "requests.csv")
    req_df = pl.DataFrame(
        [
            {
                k: getattr(r, k)
                for k in (
                    "request_id",
                    "request_type",
                    "target_entity_key",
                    "status",
                    "maker",
                    "checker",
                    "received_at",
                    "decided_at",
                    "rationale",
                )
            }
            for r in reqs
        ]
    )
    return Artefacts(
        snapshot_date=snap,
        summary=summary,
        issues_sample=pl.read_csv(rep / "dq_issues_sample.csv", infer_schema_length=0),
        fix_examples=json.loads((rep / "fix_examples.json").read_text()),
        deltas=load_deltas(data_dir / "deltas"),
        requests=req_df,
        top_false_positives=fps,
    )


def issues_matrix(summary: dict) -> pl.DataFrame:
    """Long table rule x source -> issue count, for the heatmap."""
    rows = []
    for source, card in summary["sources"].items():
        for rule, v in card.get("issues_by_rule", {}).items():
            rows.append(
                {
                    "source": source,
                    "rule_id": rule,
                    "issues": v["issues"],
                    "entities": v["entities"],
                    "severity": v["severity"],
                }
            )
    return pl.DataFrame(rows)


def sweep_frame(summary: dict) -> pl.DataFrame:
    scr = summary.get("screening") or {}
    rows = [{"mode": mode, **s} for mode, ev in scr.get("runs", {}).items() for s in ev["sweep"]]
    return pl.DataFrame(rows)
