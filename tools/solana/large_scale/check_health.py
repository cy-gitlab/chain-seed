#!/usr/bin/env python3
"""Check finalized progress and scoped processes, then observe a stable window."""
import argparse
from pathlib import Path
from _rpc import monitor


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--core', action='store_true', help='Check only bootstrap and the first validator')
    parser.add_argument('--timeout', type=int)
    parser.add_argument('--interval', type=int)
    parser.add_argument('--settle-seconds', type=int)
    args = parser.parse_args()
    timeout = args.timeout if args.timeout is not None else (1800 if args.core else 7200)
    interval = args.interval if args.interval is not None else (15 if args.core else 60)
    settle = args.settle_seconds if args.settle_seconds is not None else (120 if args.core else 2400)
    if min(timeout, interval) <= 0 or settle < 0 or timeout <= settle:
        parser.error('Require positive timings and timeout > settle-seconds >=0')
    raise SystemExit(monitor(args.deployment, args.output, timeout, interval,
        core=args.core, gate=True, settle_seconds=settle))
