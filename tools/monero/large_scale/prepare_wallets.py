#!/usr/bin/env python3
"""Fund sender wallets and wait for spendable outputs before measurement."""
import argparse
import json
from pathlib import Path
import signal
from _transactions import Load


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment', type=Path, required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--funding-rounds', type=int, default=16)
    parser.add_argument('--timeout', type=int, default=10800)
    args = parser.parse_args()
    if min(args.funding_rounds, args.timeout) <= 0:
        parser.error('funding-rounds and timeout must be positive')
    if (args.run_dir/'wallets-ready.json').exists():
        parser.error('wallets already prepared; use a new run directory')
    def interrupted(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    load = Load(args.deployment, args.run_dir, 0.3, args.funding_rounds, funding=True)
    try:
        load.prepare(args.timeout)
        load.save_ready()
        print(json.dumps(dict(status='ready', senders=len(load.senders),
            output=str(args.run_dir/'wallets-ready.json'))))
        return 0
    except KeyboardInterrupt:
        load.event('interrupted', durable=True)
        return 130
    finally:
        load.close()


if __name__ == '__main__':
    raise SystemExit(main())
