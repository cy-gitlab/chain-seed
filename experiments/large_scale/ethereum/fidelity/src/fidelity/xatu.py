from __future__ import annotations

import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from . import __version__
from .config import PipelineConfig
from .storage import DatasetLayout, atomic_write_json, read_json, sha256_file, utc_now


XATU_BASE_URL = "https://data.ethpandaops.io/xatu"


def parquet_url(network: str, table: str, day: date) -> str:
    return (
        f"{XATU_BASE_URL}/{network}/databases/default/{table}/"
        f"{day.year}/{day.month}/{day.day}.parquet"
    )


def _remote_exists(url: str, timeout: int = 20) -> bool:
    headers = {"User-Agent": f"seedemu-p2p-fidelity/{__version__}"}
    request = urllib.request.Request(url, headers=headers, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status == 200
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        if error.code not in (400, 403, 405):
            raise

    # Some object stores reject HEAD. A one-byte range avoids downloading the file.
    request = urllib.request.Request(
        url,
        headers={**headers, "Range": "bytes=0-0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status in (200, 206)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        raise


def discover_latest_complete_day(
    network: str,
    tables: Iterable[str],
    lookback_days: int,
    today: date | None = None,
) -> date:
    current = today or datetime.now(timezone.utc).date()
    for offset in range(lookback_days + 1):
        candidate = current - timedelta(days=offset)
        if all(_remote_exists(parquet_url(network, table, candidate)) for table in tables):
            return candidate
    raise RuntimeError(
        f"no complete Xatu date found in the previous {lookback_days} days"
    )


def resolve_date(config: PipelineConfig) -> date:
    if config.date == "latest":
        return discover_latest_complete_day(
            config.network,
            config.required_tables,
            config.lookback_days,
        )
    return date.fromisoformat(config.date)


def _download(url: str, destination: Path, timeout: int = 120) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    offset = partial.stat().st_size if partial.exists() else 0
    headers = {"User-Agent": f"seedemu-p2p-fidelity/{__version__}"}
    if offset:
        headers["Range"] = f"bytes={offset}-"

    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        if offset and response.status != 206:
            partial.unlink()
            offset = 0
        content_range = response.headers.get("Content-Range", "")
        total_size = None
        if "/" in content_range:
            total_text = content_range.rsplit("/", 1)[1]
            if total_text.isdigit():
                total_size = int(total_text)
        elif response.headers.get("Content-Length", "").isdigit():
            total_size = offset + int(response.headers["Content-Length"])

        print(
            f"downloading {url} -> {destination}"
            + (f" ({offset}/{total_size} bytes)" if total_size else ""),
            file=sys.stderr,
            flush=True,
        )
        mode = "ab" if offset else "wb"
        with partial.open(mode) as output:
            downloaded = offset
            last_report = time.monotonic()
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
                downloaded += len(chunk)
                now = time.monotonic()
                if now - last_report >= 30:
                    if total_size:
                        percent = 100.0 * downloaded / total_size
                        progress = f"{downloaded}/{total_size} bytes ({percent:.1f}%)"
                    else:
                        progress = f"{downloaded} bytes"
                    print(f"download progress: {progress}", file=sys.stderr, flush=True)
                    last_report = now
        if total_size is not None and downloaded != total_size:
            raise RuntimeError(
                f"incomplete download for {url}: got {downloaded}, expected {total_size}"
            )
    partial.replace(destination)


def _new_manifest(config: PipelineConfig, resolved_day: date) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "dataset_id": f"xatu-{config.network}-{resolved_day.isoformat()}",
        "source": "xatu",
        "network": config.network,
        "date": resolved_day.isoformat(),
        "created_at": utc_now(),
        "tool_version": __version__,
        "raw_files": {},
        "unavailable_optional_tables": {},
        "processing_runs": {},
    }


def fetch_dataset(
    config: PipelineConfig,
    datasets_root: Path,
    resolved_day: date | None = None,
) -> DatasetLayout:
    day = resolved_day or resolve_date(config)
    dataset_id = f"xatu-{config.network}-{day.isoformat()}"
    layout = DatasetLayout(datasets_root.resolve(), dataset_id)
    layout.create()

    if layout.manifest_path.exists():
        manifest = read_json(layout.manifest_path)
        if manifest.get("date") != day.isoformat() or manifest.get("network") != config.network:
            raise RuntimeError("existing dataset manifest does not match requested source")
    else:
        manifest = _new_manifest(config, day)
        atomic_write_json(layout.manifest_path, manifest)

    for table in config.tables:
        url = parquet_url(config.network, table, day)
        destination = layout.raw / f"{table}.parquet"
        existing = manifest["raw_files"].get(table)
        if destination.exists():
            checksum = sha256_file(destination)
            if existing and checksum != existing["sha256"]:
                raise RuntimeError(
                    f"immutable raw file checksum mismatch: {destination}"
                )
        else:
            unavailable = manifest.get("unavailable_optional_tables", {}).get(table)
            if table in config.optional_tables and unavailable:
                # A dataset manifest is a reproducibility boundary. Do not let a
                # later publication silently change an already pinned raw set.
                continue
            available = _remote_exists(url)
            if not available and table in config.optional_tables:
                manifest.setdefault("unavailable_optional_tables", {})[table] = {
                    "url": url,
                    "checked_at": utc_now(),
                    "reason": "no public partition for the dataset date",
                }
                atomic_write_json(layout.manifest_path, manifest)
                continue
            if not available:
                raise RuntimeError(f"required Xatu partition is unavailable: {url}")
            _download(url, destination)
            checksum = sha256_file(destination)

        entry = {
            "path": str(destination.relative_to(layout.path)),
            "url": url,
            "size_bytes": destination.stat().st_size,
            "sha256": checksum,
            "verified_at": utc_now(),
        }
        if existing and any(
            existing.get(key) != entry[key]
            for key in ("path", "url", "size_bytes", "sha256")
        ):
            raise RuntimeError(f"raw manifest entry changed for {table}")
        manifest["raw_files"][table] = entry
        manifest.setdefault("unavailable_optional_tables", {}).pop(table, None)
        atomic_write_json(layout.manifest_path, manifest)

    return layout


def raw_file(layout: DatasetLayout, table: str) -> Path:
    manifest = read_json(layout.manifest_path)
    try:
        relative = manifest["raw_files"][table]["path"]
    except KeyError as error:
        raise RuntimeError(f"raw table is not present in manifest: {table}") from error
    return layout.path / relative
