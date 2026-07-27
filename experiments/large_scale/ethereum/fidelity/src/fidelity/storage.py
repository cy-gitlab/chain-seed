from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


@dataclass(frozen=True)
class DatasetLayout:
    root: Path
    dataset_id: str

    @property
    def path(self) -> Path:
        return self.root / self.dataset_id

    @property
    def raw(self) -> Path:
        return self.path / "raw"

    @property
    def normalized(self) -> Path:
        return self.path / "normalized"

    @property
    def derived(self) -> Path:
        return self.path / "derived"

    @property
    def reports(self) -> Path:
        return self.path / "reports"

    @property
    def manifest_path(self) -> Path:
        return self.path / "manifest.json"

    def create(self) -> None:
        for directory in (
            self.raw,
            self.normalized,
            self.derived,
            self.reports,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_dataset_path(cls, path: Path) -> "DatasetLayout":
        path = path.resolve()
        return cls(path.parent, path.name)

