#!/usr/bin/env python3
"""Network component shared with the D00 blockchain examples."""

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from seedemu.compiler import Docker
from seedemu.services import WebService
from examples.D00_ethereum_poa import mini_internet


def run(host_total=1, dumpfile=None):
    emu = mini_internet.build_emulator(hosts_per_as=host_total, topology="full")
    emu.addLayer(WebService())
    if dumpfile is not None:
        emu.dump(dumpfile)
    else:
        emu.render()
        emu.compile(Docker(), "./output", override=True)
    return emu


if __name__ == "__main__":
    run()
