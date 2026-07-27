from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any

from .storage import atomic_write_json, utc_now


OSF_PROJECT_ID = "c5upf"
OSF_FILES_URL = f"https://api.osf.io/v2/nodes/{OSF_PROJECT_ID}/files/"


def archive_public_catalog(destination: Path, timeout: int = 30) -> Path:
    """Archive public OSF metadata without interpreting it as Beacon edges."""
    request = urllib.request.Request(
        OSF_FILES_URL,
        headers={"User-Agent": "seedemu-p2p-fidelity/0.1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        catalog: dict[str, Any] = json.load(response)
    output = {
        "source": "etherbee-osf-public",
        "project_id": OSF_PROJECT_ID,
        "retrieved_at": utc_now(),
        "catalog": catalog,
        "usage": "auxiliary-only; no records are interpreted as G0/C edges",
    }
    atomic_write_json(destination, output)
    return destination
