"""Configuration loading and workspace paths."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"
FIXTURES_DIR = PROJECT_ROOT / "tests" / "fixtures"

LIST_SOURCES = ("eu", "un", "ofac", "uk", "nl")


def _load_yaml(name: str, config_dir: Path | None = None) -> Any:
    path = (config_dir or CONFIG_DIR) / name
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@cache
def load_sources() -> dict[str, dict[str, Any]]:
    return _load_yaml("sources.yaml")


@cache
def load_dq_rules() -> dict[str, dict[str, Any]]:
    """Return enabled-or-not rules keyed by rule id."""
    data = _load_yaml("dq_rules.yaml")
    return {r["id"]: r for r in data["rules"]}


@cache
def load_screening_config() -> dict[str, Any]:
    return _load_yaml("screening.yaml")


@dataclass(frozen=True)
class Workspace:
    """All filesystem locations used by one pipeline run.

    The live pipeline uses ``data/``; the offline demo and tests use a separate
    directory so they never overwrite committed artefacts.
    """

    data_dir: Path
    requests_dir: Path = PROJECT_ROOT / "requests"
    readme_path: Path | None = None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "slo.duckdb"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def deltas_dir(self) -> Path:
        return self.data_dir / "deltas"

    @property
    def exports_dir(self) -> Path:
        return self.data_dir / "exports"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def synth_dir(self) -> Path:
        return self.data_dir / "synth"

    def report_dir(self, snapshot_date: str) -> Path:
        return self.reports_dir / snapshot_date


def default_workspace() -> Workspace:
    return Workspace(data_dir=PROJECT_ROOT / "data", readme_path=PROJECT_ROOT / "README.md")
