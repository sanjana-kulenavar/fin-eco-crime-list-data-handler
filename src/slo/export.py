"""Exports in the formats of three (simulated) screening systems, behind the DQ gate."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

from slo.dq.engine import export_gate

SYSTEMS = ("payments_screening", "customer_screening", "delta_feed")
SAMPLE_ROWS = 50
PAYMENTS_COLUMNS = [
    "uid",
    "name",
    "name_type",
    "entity_type",
    "dob_from",
    "dob_to",
    "nationality_iso2",
    "programme",
    "list_source",
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tables(clean: bool) -> tuple[str, str, str]:
    if clean:
        return "names_clean", "birth_dates_clean", "identifiers_clean"
    return "names", "birth_dates", "identifiers"


def _entity_payloads(
    con: duckdb.DuckDBPyConnection, source: str, d: date, clean: bool
) -> dict[str, dict]:
    names_t, dobs_t, ids_t = _tables(clean)
    p = [source, d]
    ents = {
        r[0]: {
            "entity_key": r[0],
            "list_source": source,
            "entity_type": r[1],
            "primary_name": r[2],
            "programmes": list(r[3] or []),
            "listed_on": str(r[4]) if r[4] else None,
            "names": [],
            "dobs": [],
            "identifiers": [],
            "countries": [],
        }
        for r in con.execute(
            "SELECT entity_key, entity_type, primary_name, programmes, listed_on FROM entities "
            "WHERE source = ? AND snapshot_date = ? ORDER BY entity_key",
            p,
        ).fetchall()
    }
    for key, full, ntype, strength, script in con.execute(
        f"SELECT entity_key, full_name, name_type, strength, script FROM {names_t} "
        "WHERE source = ? AND snapshot_date = ? ORDER BY name_id",
        p,
    ).fetchall():
        ents[key]["names"].append(
            {"name": full, "type": ntype, "strength": strength, "script": script}
        )
    for key, raw, lo, hi, prec in con.execute(
        f"SELECT entity_key, raw, date_from, date_to, precision FROM {dobs_t} "
        "WHERE source = ? AND snapshot_date = ? ORDER BY raw",
        p,
    ).fetchall():
        ents[key]["dobs"].append(
            {
                "raw": raw,
                "from": str(lo) if lo else None,
                "to": str(hi) if hi else None,
                "precision": prec,
            }
        )
    for key, t, num, c in con.execute(
        f"SELECT entity_key, id_type, norm_number, country_iso2 FROM {ids_t} "
        "WHERE source = ? AND snapshot_date = ? ORDER BY 1, 2, 3",
        p,
    ).fetchall():
        ents[key]["identifiers"].append({"type": t, "number": num, "country": c})
    for key, role, c in con.execute(
        "SELECT DISTINCT entity_key, role, country_iso2 FROM countries WHERE source = ? AND snapshot_date = ? "
        "AND country_iso2 IS NOT NULL ORDER BY 1, 2, 3",
        p,
    ).fetchall():
        ents[key]["countries"].append({"role": role, "iso2": c})
    return ents


def _payment_rows(ent: dict) -> list[dict]:
    dobs = [x for x in ent["dobs"] if x["from"]]
    nats = sorted(
        {c["iso2"] for c in ent["countries"] if c["role"] in ("nationality", "citizenship")}
    )
    rows = []
    for i, n in enumerate(ent["names"]):
        if n["script"] != "latin" or n["strength"] == "weak" or not n["name"].strip():
            continue  # payments engines get strong, Latin-script names only
        rows.append(
            {
                "uid": f"{ent['entity_key']}#{i}",
                "name": n["name"][:200],
                "name_type": n["type"],
                "entity_type": ent["entity_type"],
                "dob_from": min(x["from"] for x in dobs) if dobs else "",
                "dob_to": max(x["to"] for x in dobs) if dobs else "",
                "nationality_iso2": nats[0] if nats else "",
                "programme": "|".join(ent["programmes"]),
                "list_source": ent["list_source"],
            }
        )
    return rows


def _manifest(
    system: str,
    d: date,
    path: Path,
    count: int,
    included: list[str],
    blocked: dict[str, list[str]],
    clean: bool,
) -> dict:
    return {
        "system": system,
        "snapshot_date": str(d),
        "file": path.name,
        "sources": included,
        "blocked_sources": blocked,
        "record_count": count,
        "sha256": _sha256(path),
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dq_gate_passed": not blocked,
        "variant": "clean" if clean else "raw",
    }


def export_all(
    con: duckdb.DuckDBPyConnection,
    exports_dir: Path,
    d: date,
    sources: list[str],
    *,
    clean: bool = True,
    systems: tuple[str, ...] = SYSTEMS,
) -> dict[str, dict]:
    """Write each system's export for snapshot ``d``. Sources failing the DQ gate are excluded."""
    included, blocked = [], {}
    for s in sources:
        ok, reasons = export_gate(con, s, d)
        if ok:
            included.append(s)
        else:
            blocked[s] = reasons
    payloads: dict[str, dict] = {}
    for s in included:
        payloads.update(_entity_payloads(con, s, d, clean))
    manifests = {}
    samples = exports_dir / "samples"
    samples.mkdir(parents=True, exist_ok=True)
    for system in systems:
        out_dir = exports_dir / system
        out_dir.mkdir(parents=True, exist_ok=True)
        if system == "payments_screening":
            rows = [r for e in payloads.values() for r in _payment_rows(e)]
            path = out_dir / f"{d}.csv"
            for target, subset in ((path, rows), (samples / f"{system}.csv", rows[:SAMPLE_ROWS])):
                with target.open("w", newline="", encoding="utf-8") as fh:
                    w = csv.DictWriter(fh, fieldnames=PAYMENTS_COLUMNS, delimiter=";")
                    w.writeheader()
                    w.writerows(subset)
            count = len(rows)
        elif system == "customer_screening":
            path = out_dir / f"{d}.jsonl"
            lines = [json.dumps(e, ensure_ascii=False, sort_keys=True) for e in payloads.values()]
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            (samples / f"{system}.jsonl").write_text(
                "\n".join(lines[:SAMPLE_ROWS]) + "\n", encoding="utf-8"
            )
            count = len(lines)
        else:  # delta_feed
            ops = []
            by_source: dict[str, list] = defaultdict(list)
            for s, key, ctype in con.execute(
                "SELECT source, entity_key, change_type FROM deltas WHERE to_date = ? ORDER BY 1, 3, 2",
                [d],
            ).fetchall():
                if s in included:
                    by_source[s].append((key, ctype))
            for s in included:
                for key, ctype in by_source[s]:
                    if ctype == "removed":
                        ops.append({"op": "delete", "entity_key": key})
                    else:
                        ops.append(
                            {
                                "op": "upsert",
                                "entity_key": key,
                                "change": ctype,
                                "entity": payloads.get(key),
                            }
                        )
            path = out_dir / f"{d}.json"
            path.write_text(
                json.dumps({"snapshot_date": str(d), "operations": ops}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            (samples / f"{system}.json").write_text(
                json.dumps(
                    {"snapshot_date": str(d), "operations": ops[:SAMPLE_ROWS]},
                    indent=1,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            count = len(ops)
        manifest = _manifest(system, d, path, count, included, blocked, clean)
        (out_dir / f"{d}.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (samples / f"{system}.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        manifests[system] = manifest
    return manifests
