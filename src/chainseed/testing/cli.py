#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Optional, Sequence

from .blockchain import BlockchainTestRunner


COMMANDS = ["clean", "compile", "build", "up", "readiness", "probe", "test", "down", "all"]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run a ChainSeed example manifest.")
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--artifact-dir", type=Path)
    args = parser.parse_args(argv)

    os.environ["TEST_RUNNER_COMMAND"] = args.command
    runner = BlockchainTestRunner(args.manifest, artifact_dir=args.artifact_dir)
    return getattr(runner, args.command)()


if __name__ == "__main__":
    raise SystemExit(main())
