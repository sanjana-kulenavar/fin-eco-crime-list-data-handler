import csv
import json
from datetime import UTC, datetime

import polars as pl

from slo.delta import compute_delta, store_delta
from slo.dq.engine import run_rules
from slo.dq.fixes import build_clean
from slo.export import export_all
from slo.models import RawCountry, RawName
from slo.store import insert_frame
from tests.conftest import D, load, org, person


def _run(con, source, declared, parsed):
    now = datetime.now(UTC).replace(tzinfo=None)
    insert_frame(
        con,
        "runs",
        pl.DataFrame(
            [
                {
                    "run_id": "r",
                    "started_at": now,
                    "source": source,
                    "snapshot_date": D,
                    "record_count_source": declared,
                    "record_count_parsed": parsed,
                    "status": "ok",
                }
            ]
        ),
    )


def _prepare(con, tmp_path):
    good = [
        person(
            "1",
            "Ali Hassan",
            names=[
                RawName("Ali Hassan", "primary"),
                RawName("Aly", strength="weak"),
                RawName("Али Хасан", "non_latin"),
            ],
            countries=[RawCountry("nationality", "IQ")],
        ),
        org("2", "Example Trading BV"),
    ]
    load(con, "good", good)
    load(con, "bad", [person("9", "Blocked Person")])
    _run(con, "good", 2, 2)
    _run(con, "bad", 5, 1)  # count mismatch -> DQ060 -> gate blocks
    for s in ("good", "bad"):
        run_rules(con, s, D, "r")
        build_clean(con, s, D)
        store_delta(con, compute_delta(con, s, D), tmp_path / "deltas")


def test_exports_and_gate(con, tmp_path):
    _prepare(con, tmp_path)
    manifests = export_all(con, tmp_path / "exports", D, ["good", "bad"])
    assert set(manifests) == {"payments_screening", "customer_screening", "delta_feed"}
    for m in manifests.values():
        assert m["sources"] == ["good"] and list(m["blocked_sources"]) == ["bad"]
        assert m["dq_gate_passed"] is False and len(m["sha256"]) == 64
    with (tmp_path / "exports" / "payments_screening" / f"{D}.csv").open() as fh:
        rows = list(csv.DictReader(fh, delimiter=";"))
    # strong, Latin-script names only: weak alias and Cyrillic name are excluded
    assert sorted(r["name"] for r in rows) == ["Ali Hassan", "Example Trading BV"]
    ali = next(r for r in rows if r["name"] == "Ali Hassan")
    assert (ali["dob_from"], ali["nationality_iso2"], ali["list_source"]) == (
        "1970-05-17",
        "IQ",
        "good",
    )
    lines = (tmp_path / "exports" / "customer_screening" / f"{D}.jsonl").read_text().splitlines()
    ent = json.loads(lines[0])
    assert ent["entity_key"] == "good:1" and len(ent["names"]) == 3
    feed = json.loads((tmp_path / "exports" / "delta_feed" / f"{D}.json").read_text())
    assert {op["op"] for op in feed["operations"]} == {"upsert"} and len(feed["operations"]) == 2
    assert manifests["payments_screening"]["record_count"] == 2
    assert (tmp_path / "exports" / "samples" / "payments_screening.manifest.json").exists()


def test_raw_variant_and_all_passing(con, tmp_path):
    _prepare(con, tmp_path)
    m = export_all(
        con, tmp_path / "exports", D, ["good"], clean=False, systems=("customer_screening",)
    )
    assert m["customer_screening"]["dq_gate_passed"] is True
    assert m["customer_screening"]["variant"] == "raw"
