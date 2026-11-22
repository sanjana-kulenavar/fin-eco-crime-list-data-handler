"""Download source files with retries and record their fingerprints."""

from __future__ import annotations

import hashlib
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

USER_AGENT = "Mozilla/5.0 (compatible; sanctions-list-ops/1.0; portfolio project)"
EXTENSIONS = {"xml": "xml", "ftm_json": "json"}


@dataclass
class FetchResult:
    source: str
    path: Path
    sha256: str
    bytes: int


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def raw_path(raw_dir: Path, source: str, snapshot_date: str, fmt: str) -> Path:
    return raw_dir / source / f"{snapshot_date}.{EXTENSIONS.get(fmt, 'dat')}"


def download(url: str, dest: Path, *, retries: int = 3, timeout: float = 120.0) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with httpx.stream(
                "GET",
                url,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
                timeout=timeout,
            ) as resp:
                resp.raise_for_status()
                with tmp.open("wb") as fh:
                    for chunk in resp.iter_bytes(1 << 20):
                        fh.write(chunk)
            if tmp.stat().st_size == 0:
                raise httpx.HTTPError("empty response body")
            tmp.replace(dest)
            return
        except httpx.HTTPError as exc:
            last = exc
            if attempt < retries:
                time.sleep(2**attempt)
    tmp.unlink(missing_ok=True)
    raise RuntimeError(f"download failed after {retries} attempts: {url}: {last}") from last


def fetch_source(source: str, cfg: dict, raw_dir: Path, snapshot_date: str) -> FetchResult:
    dest = raw_path(raw_dir, source, snapshot_date, cfg["format"])
    download(cfg["url"], dest)
    return FetchResult(source, dest, sha256_file(dest), dest.stat().st_size)


def copy_local(
    source: str, src_file: Path, fmt: str, raw_dir: Path, snapshot_date: str
) -> FetchResult:
    """Use a local file as the raw download (offline demo, tests)."""
    dest = raw_path(raw_dir, source, snapshot_date, fmt)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src_file, dest)
    return FetchResult(source, dest, sha256_file(dest), dest.stat().st_size)
