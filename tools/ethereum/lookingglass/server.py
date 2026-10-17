#!/usr/bin/env python3
"""Run the packaged Lookingglass viewer against local observations."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from chainseed.ethereum.lookingglass.server import main

if __name__ == "__main__":
    main()
