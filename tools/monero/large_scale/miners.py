#!/usr/bin/env python3
"""Initialize a verified miner cohort, optionally ramp it, or rotate it periodically."""
import argparse
import fcntl
import json
from pathlib import Path
import signal
import time
from _health import nodes_digest, read_nodes
from _miners import rotate
from _ramp import ramp


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment', type=Path, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--count', type=int, required=True)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--initialize', action='store_true')
    parser.add_argument('--ramp-step', type=int, default=0, help='Initialize in steps; 0 directly selects the target count')
    parser.add_argument('--minimum-seconds', type=int, default=600)
    parser.add_argument('--ramp-timeout', type=int, default=21600, help='Maximum seconds at each ramp level')
    parser.add_argument('--interval', type=int, default=900)
    parser.add_argument('--duration', type=int, default=0, help='Rotation seconds; 0 until Ctrl-C')
    args = parser.parse_args()
    nodes = read_nodes(args.deployment)
    if not 1 <= args.count <= len(nodes) or min(args.interval, args.minimum_seconds, args.ramp_timeout) <= 0 or min(args.duration, args.ramp_step) < 0:
        parser.error('Invalid miner count or timing')
    if args.ramp_step and not args.initialize:
        parser.error('--ramp-step requires --initialize')
    def interrupted(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    args.state.parent.mkdir(parents=True, exist_ok=True)
    with args.state.with_suffix('.controller.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.initialize:
            if args.state.exists():
                parser.error('miner state exists; choose a new run directory')
            if args.ramp_step:
                ramp(nodes, args.count, args.ramp_step, args.seed, args.state,
                    args.state.parent/'miner-ramp.jsonl', args.minimum_seconds,
                    args.minimum_seconds, args.ramp_timeout)
            else:
                print(json.dumps(rotate(nodes, args.count, args.seed, args.state)), flush=True)
            return 0
        state = json.loads(args.state.read_text())
        if state['nodes_sha256'] != nodes_digest(nodes) or state['expected_miners'] != args.count or state['seed'] != args.seed:
            parser.error('state differs from deployment/count/seed; initialize the intended configuration first')
        config = dict(started_at=time.time(), interval=args.interval, duration=args.duration,
            count=args.count, seed=args.seed, nodes_sha256=nodes_digest(nodes), status='running')
        record = args.state.parent/'miner-controller.json'
        with record.open('x') as handle:
            json.dump(config, handle, indent=2)
        start = time.monotonic()
        due = start + args.interval
        try:
            while not args.duration or time.monotonic()-start < args.duration:
                if time.monotonic() >= due:
                    print(json.dumps(rotate(nodes, args.count, args.seed, args.state)), flush=True)
                    due = time.monotonic()+args.interval
                time.sleep(0.5)
            config['status'] = 'complete'
        except KeyboardInterrupt:
            config['status'] = 'stopped'
        except BaseException:
            config['status'] = 'failed'
            raise
        finally:
            config['stopped_at'] = time.time()
            record.write_text(json.dumps(config, indent=2)+'\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
