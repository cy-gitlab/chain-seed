#!/usr/bin/env python3
"""Send native transfers, then verify inclusion on two observer nodes."""
import argparse
import json
import math
from pathlib import Path
import signal
import time
from _transactions import Load


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment', type=Path, required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--rate', type=float, default=0.3)
    parser.add_argument('--duration', type=int, default=18000)
    parser.add_argument('--drain-timeout', type=int, default=1200)
    args = parser.parse_args()
    if not math.isfinite(args.rate) or args.rate <= 0 or min(args.duration, args.drain_timeout) <= 0:
        parser.error('rate, duration and drain-timeout must be positive')
    if not (args.run_dir/'wallets-ready.json').is_file():
        parser.error('run prepare_wallets.py first')
    def interrupted(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    load = Load(args.deployment, args.run_dir, args.rate)
    try:
        load.load_ready()
        load.begin(args.duration)
        while load.clock() < load.end:
            load.tick()
            time.sleep(min(0.2, max(0, load.end-load.clock())))
        result = load.finish(args.drain_timeout)
        print(json.dumps(result, indent=2))
        return 0 if result['status'] == 'passed' else 1
    except KeyboardInterrupt:
        load.event('interrupted', durable=True)
        return 130
    finally:
        load.close()


if __name__ == '__main__':
    raise SystemExit(main())
