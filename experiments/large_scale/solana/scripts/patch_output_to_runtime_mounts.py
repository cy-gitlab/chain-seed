#!/usr/bin/env python3
"""Wrapper around the shared D61 no-build mount patcher."""

from __future__ import annotations

import runpy
from pathlib import Path


PATCHER = Path(__file__).resolve().parents[2] / "ethereum" / "scripts" / "patch_output_to_runtime_mounts.py"

if __name__ == "__main__":
    runpy.run_path(str(PATCHER), run_name="__main__")
