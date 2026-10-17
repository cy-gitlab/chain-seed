#!/usr/bin/env python3
"""Require two healthy full-network observations and an advancing chain."""
import argparse
from pathlib import Path
from _health import monitor, read_nodes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--miner-state', type=Path)
    parser.add_argument('--timeout', type=int, default=1800)
    parser.add_argument('--interval', type=int, default=60)
    args = parser.parse_args()
    if min(args.timeout, args.interval) <= 0:
        parser.error('timeout and interval must be positive')
    return monitor(read_nodes(args.deployment), args.output, args.miner_state,
        interval=args.interval, duration=args.timeout, gate=True)


if __name__ == '__main__':
    raise SystemExit(main())
