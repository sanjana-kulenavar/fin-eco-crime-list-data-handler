"""List change requests: maker/checker (four-eyes) workflow and the internal list.

``requests/requests.csv`` is the audit log. ``requests/internal_list.yaml`` is the
golden source for the bank's own internal list (source ``internal``).
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from slo.models import ParseResult, RawDob, RawEntity, RawName

REQUEST_TYPES = (
    "add_internal",
    "remove_internal",
    "false_positive_suppression",
    "alias_enrichment",
    "data_correction",
)
STATUSES = ("pending", "approved", "rejected", "implemented")


class RequestError(ValueError):
    """Raised for invalid request operations (e.g. maker approving their own request)."""


@dataclass
class ChangeRequest:
    request_id: str
    received_at: str
    requested_by: str
    request_type: str
    target_source: str
    target_entity_key: str
    payload_json: str
    rationale: str
    maker: str
    checker: str = ""
    status: str = "pending"
    decided_at: str = ""
    implemented_at: str = ""
    evidence_ref: str = ""

    @property
    def payload(self) -> dict[str, Any]:
        return json.loads(self.payload_json) if self.payload_json else {}


FIELDNAMES = [f.name for f in fields(ChangeRequest)]


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_requests(path: Path) -> list[ChangeRequest]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return [ChangeRequest(**row) for row in csv.DictReader(fh)]


def save_requests(path: Path, requests: list[ChangeRequest]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        for r in requests:
            writer.writerow(asdict(r))


def add_request(
    path: Path,
    *,
    request_type: str,
    target_source: str,
    target_entity_key: str,
    payload: dict[str, Any],
    rationale: str,
    maker: str,
    requested_by: str,
    evidence_ref: str = "",
) -> ChangeRequest:
    if request_type not in REQUEST_TYPES:
        raise RequestError(
            f"unknown request_type {request_type!r}; expected one of {REQUEST_TYPES}"
        )
    requests = load_requests(path)
    next_no = max((int(r.request_id[1:]) for r in requests), default=0) + 1
    req = ChangeRequest(
        request_id=f"R{next_no:03d}",
        received_at=_now(),
        requested_by=requested_by,
        request_type=request_type,
        target_source=target_source,
        target_entity_key=target_entity_key,
        payload_json=json.dumps(payload, sort_keys=True),
        rationale=rationale,
        maker=maker,
        evidence_ref=evidence_ref,
    )
    save_requests(path, [*requests, req])
    return req


def _decide(path: Path, request_id: str, checker: str, status: str) -> ChangeRequest:
    requests = load_requests(path)
    for r in requests:
        if r.request_id == request_id:
            if r.status != "pending":
                raise RequestError(
                    f"{request_id} is {r.status}, only pending requests can be decided"
                )
            if not checker or checker.strip().lower() == r.maker.strip().lower():
                raise RequestError(
                    f"four-eyes violation: checker must differ from maker ({r.maker})"
                )
            r.checker, r.status, r.decided_at = checker, status, _now()
            save_requests(path, requests)
            return r
    raise RequestError(f"no such request {request_id}")


def approve(path: Path, request_id: str, checker: str) -> ChangeRequest:
    return _decide(path, request_id, checker, "approved")


def reject(path: Path, request_id: str, checker: str) -> ChangeRequest:
    return _decide(path, request_id, checker, "rejected")


# --- internal list -------------------------------------------------------------------


def load_internal_list(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(data.get("entities", []))


def save_internal_list(path: Path, entries: list[dict[str, Any]]) -> None:
    header = "# Bank-internal list (fictional). Managed only through requests/requests.csv.\n"
    path.write_text(
        header + yaml.safe_dump({"entities": entries}, sort_keys=False, allow_unicode=True)
    )


def parse_internal(path: Path) -> ParseResult:
    entities = []
    for e in load_internal_list(path):
        names = [RawName(e["name"], "primary", strength="strong")]
        names += [RawName(a, "alias", strength="weak") for a in e.get("aliases", [])]
        entities.append(
            RawEntity(
                source_id=e["id"],
                entity_type=e.get("type", "organisation"),
                primary_name=e["name"],
                programmes=["INTERNAL"],
                remarks=f"{e.get('reason', '')} (request {e.get('request_id', '?')})".strip(),
                names=names,
                dobs=[RawDob(e["dob"])] if e.get("dob") else [],
            )
        )
    n = len(entities)
    return ParseResult("internal", entities, n, n, None, [])


def apply_requests(requests_dir: Path) -> list[str]:
    """Implement approved requests that change the internal list; mark them implemented.

    Alias enrichment, data corrections and suppressions are applied on every run by
    :mod:`slo.dq.fixes` / the matcher (they act on rebuilt clean tables), and are
    marked implemented here the first time they are picked up.
    """
    req_path = requests_dir / "requests.csv"
    list_path = requests_dir / "internal_list.yaml"
    requests = load_requests(req_path)
    entries = load_internal_list(list_path)
    done = []
    for r in requests:
        if r.status != "approved":
            continue
        if r.request_type == "add_internal":
            entry = {**r.payload, "request_id": r.request_id}
            if not any(e["id"] == entry["id"] for e in entries):
                entries.append(entry)
        elif r.request_type == "remove_internal":
            entity_id = r.target_entity_key.removeprefix("internal:")
            entries = [e for e in entries if e["id"] != entity_id]
        r.status, r.implemented_at = "implemented", _now()
        done.append(r.request_id)
    if done:
        save_internal_list(list_path, entries)
        save_requests(req_path, requests)
    return done


def active_requests(requests_dir: Path, request_type: str) -> list[ChangeRequest]:
    """Approved or implemented requests of a type (applied on every rebuild)."""
    return [
        r
        for r in load_requests(requests_dir / "requests.csv")
        if r.request_type == request_type and r.status in ("approved", "implemented")
    ]
