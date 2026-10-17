#!/usr/bin/env python3
"""Send native SOL transfers through the bootstrap and record finalized receipts."""
import argparse
import csv
import json
import math
from pathlib import Path
import re
import signal
import subprocess
import time
from _keys import _solana_pubkey
from _rpc import deployment_info, rpc


def receipts(url, signatures, output):
    known = {}
    for offset in range(0, len(signatures), 256):
        batch = signatures[offset:offset+256]
        row = dict(timestamp=time.time(), signatures=batch)
        try:
            values = rpc(url, 'getSignatureStatuses', [batch, {'searchTransactionHistory':True}])['value']
            if len(values) != len(batch):
                raise ValueError('Incomplete signature-status response')
            row['statuses'] = values
            known.update(zip(batch, values))
        except Exception as error:
            row['error'] = str(error)
            known.update((signature, None) for signature in batch)
        output.write(json.dumps(row)+'\n'); output.flush()
    return known


def finalized(status):
    return bool(status and status.get('err') is None and status.get('confirmationStatus') == 'finalized')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deployment', type=Path, required=True)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--rate', type=float, default=0.05)
    parser.add_argument('--duration', type=int, default=18000)
    parser.add_argument('--amount', type=float, default=0.001)
    parser.add_argument('--recipients', type=int, default=128)
    parser.add_argument('--submit-timeout', type=int, default=90)
    parser.add_argument('--drain-timeout', type=int, default=1200)
    args = parser.parse_args()
    if not all(math.isfinite(v) and v > 0 for v in (args.rate,args.amount)) or min(args.duration,args.recipients,args.submit_timeout,args.drain_timeout) <= 0:
        parser.error('Rates, amounts, counts and timings must be positive')
    _, nodes = deployment_info(args.deployment)
    directory = args.run_dir/'transactions'
    directory.mkdir(parents=True, exist_ok=False)
    # Receiving accounts need no deployment transaction; preserve the native-transfer workload.
    destinations = [_solana_pubkey(f'transfer-recipient:{i:03d}') for i in range(args.recipients)]
    url = f'http://{nodes[0]["ip"]}:8899'
    config = dict(status='running', started_at=time.time(), duration=args.duration,
        rate=args.rate, amount=args.amount, recipients=args.recipients,
        expected=math.ceil(args.rate*args.duration), attempted=0, sent=0, failed=0, skipped=0)
    state_path = directory/'run_info.json'
    state_path.write_text(json.dumps(config, indent=2)+'\n')
    def interrupted(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    start = due = time.monotonic()
    deadline = start+args.duration
    next_receipts = start+60
    signatures, statuses = [], {}
    try:
        with (directory/'sender.csv').open('x', newline='') as output, (directory/'receipts.jsonl').open('x') as observations:
            writer = csv.DictWriter(output, fieldnames=['timestamp','status','signature','message'])
            writer.writeheader(); output.flush()
            while time.monotonic() < deadline:
                now = time.monotonic()
                if now >= due:
                    missed = max(0, int((now-due)*args.rate))
                    config['skipped'] += missed
                    due += (missed+1)/args.rate
                    recipient = destinations[config['attempted']%len(destinations)]
                    config['attempted'] += 1
                    command = ['docker','exec',nodes[0]['container'],'timeout','--kill-after=5',str(args.submit_timeout),
                        'solana','--url','http://127.0.0.1:8899','transfer',
                        '--from','/opt/solana/config/faucet.json','--fee-payer','/opt/solana/config/faucet.json',
                        '--allow-unfunded-recipient','--skip-preflight','--commitment','processed',
                        '--no-wait','--use-udp',recipient,str(args.amount)]
                    try:
                        result = subprocess.run(command, capture_output=True, text=True, timeout=args.submit_timeout+15)
                    except subprocess.TimeoutExpired as error:
                        config['failed'] += 1
                        writer.writerow(dict(timestamp=time.time(), status='uncertain', signature='', message=str(error)))
                        output.flush()
                        raise RuntimeError('Docker transfer request timed out; no replacement was sent') from error
                    message = result.stdout+'\n'+result.stderr
                    match = re.search(r'(?<![1-9A-HJ-NP-Za-km-z])[1-9A-HJ-NP-Za-km-z]{80,100}(?![1-9A-HJ-NP-Za-km-z])', message)
                    signature = match[0] if match else ''
                    status = 'sent' if result.returncode == 0 and signature else 'failed'
                    if result.returncode == 124:
                        status = 'uncertain'
                    writer.writerow(dict(timestamp=time.time(), status=status, signature=signature, message=message))
                    output.flush()
                    if status == 'sent':
                        if signature in signatures:
                            raise RuntimeError('Duplicate submitted signature')
                        signatures.append(signature); config['sent'] += 1
                    else:
                        config['failed'] += 1
                    state_path.write_text(json.dumps(config, indent=2)+'\n')
                    if status == 'uncertain':
                        raise RuntimeError('Transfer timed out with an uncertain outcome; inspect sender.csv')
                if time.monotonic() >= next_receipts:
                    statuses = receipts(url, signatures, observations)
                    next_receipts = time.monotonic()+60
                time.sleep(min(.5, max(0, min(due, deadline)-time.monotonic())))
            config['sending_finished_at'] = time.time()
            drain = time.monotonic()+args.drain_timeout
            while True:
                statuses = receipts(url, signatures, observations)
                if all(finalized(statuses.get(signature)) for signature in signatures) or time.monotonic() >= drain:
                    break
                time.sleep(min(5, max(0, drain-time.monotonic())))
            config['finalized'] = sum(finalized(statuses.get(signature)) for signature in signatures)
            config['status'] = 'complete' if (config['sent'] > 0 and config['failed'] == 0
                and config['sent'] >= .95*config['expected'] and config['finalized'] == config['sent']) else 'failed'
    except KeyboardInterrupt:
        config['status'] = 'interrupted'
    except BaseException:
        config['status'] = 'failed'
        raise
    finally:
        config['stopped_at'] = time.time()
        state_path.write_text(json.dumps(config, indent=2)+'\n')
    print(json.dumps(config, indent=2))
    return 0 if config['status'] == 'complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())
