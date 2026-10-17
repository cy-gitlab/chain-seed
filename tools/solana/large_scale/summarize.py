#!/usr/bin/env python3
"""Summarize one Solana measurement window without inferring voting from liveness."""
import argparse
from collections import defaultdict
import csv
from datetime import datetime
import json
from pathlib import Path
import statistics
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tools.blockchain.lib.project_resources import project_resources
from send_transactions import finalized

def plot(directory, series):
    for field, label in [('total_mem_gib', 'Total container memory (GiB)'),
                         ('total_cpu_cores', 'Total container CPU (logical cores)')]:
        points = [r for r in series if r['complete'] and r['cpu_valid']]
        if not points:
            continue
        xmax = max(r['elapsed_sec'] for r in points) or 1
        ymax = max(r[field] for r in points)*1.1 or 1
        coords = ' '.join(f'{70+r["elapsed_sec"]/xmax*680:.2f},{350-r[field]/ymax*270:.2f}' for r in points)
        svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="820" height="420" viewBox="0 0 820 420">',
               '<rect width="100%" height="100%" fill="white"/>',
               f'<text x="70" y="30" font-size="18">{label}</text>',
               '<path d="M70 70 V350 H750" stroke="black" fill="none"/>',
               f'<polyline points="{coords}" fill="none" stroke="#176ca4" stroke-width="2"/>',
               '<text x="340" y="405">Sampling time (hours)</text>']
        for i in range(6):
            svg.append(f'<text x="{70+i*136}" y="375">{xmax*i/5/3600:.1f}</text>')
            svg.append(f'<text x="5" y="{350-i*54}">{ymax*i/5:.1f}</text>')
        (directory/(field+'.svg')).write_text('\n'.join(svg+['</svg>']))


def quantile(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    p = (len(xs)-1)*q
    i = int(p)
    return xs[i]+(xs[min(i+1, len(xs)-1)]-xs[i])*(p-i)


def write_csv(path, rows):
    if rows:
        with path.open('w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=list(dict.fromkeys(k for r in rows for k in r)))
            w.writeheader()
            w.writerows(rows)


def coverage(times, start, duration, interval):
    end = start+duration
    intervals = sorted((max(start, t-interval), min(end, t)) for t in set(times)
                       if t >= start and t-interval <= end)
    total, right = 0, start
    for left, finish in intervals:
        total += max(0, finish-max(left, right))
        right = max(right, finish)
    return min(100, 100*total/duration)


def transfer_result(directory):
    state = json.loads((directory/'run_info.json').read_text())
    with (directory/'sender.csv').open() as handle:
        attempts = list(csv.DictReader(handle))
    signatures = [row['signature'] for row in attempts if row['status'] == 'sent']
    latest = {}
    with (directory/'receipts.jsonl').open() as handle:
        for line in handle:
            row = json.loads(line)
            if 'statuses' in row:
                if len(row['signatures']) != len(row['statuses']):
                    raise ValueError('Incomplete receipt record')
                latest.update(zip(row['signatures'], row['statuses']))
            else:
                latest.update((signature, None) for signature in row['signatures'])
    included = sum(finalized(latest.get(signature)) for signature in signatures)
    valid = (state['status'] == 'complete' and state['expected'] > 0
        and len(attempts) == state['attempted'] and len(signatures) == state['sent']
        and len(set(signatures)) == len(signatures) and len(signatures) == len(attempts)
        and state['failed'] == 0 and included == len(signatures) == state['finalized']
        and len(signatures) >= .95*state['expected'])
    return dict(status='passed' if valid else 'failed', sent=len(signatures),
        finalized=included, target_tps=state['rate'], expected=state['expected'],
        started_at=state['started_at'], duration_seconds=state['duration'])


def summarize(directory):
    destination = directory/'summary'
    destination.mkdir(parents=True, exist_ok=True)
    result, reasons = dict(chain='solana'), []
    try:
        measurement = json.loads((directory/'measurement.json').read_text())
        start = measurement['started_at']
        duration = measurement['duration'] or measurement['stopped_at']-start
        interval, count = measurement['interval'], measurement['nodes']
        if duration <= 0 or interval <= 0:
            raise ValueError('Invalid observation window')
        result.update(nodes=count, duration_seconds=duration)
        if measurement['status'] != 'complete':
            reasons.append('measurement_incomplete')
        if measurement.get('stopped_at', 0) < start+duration:
            reasons.append('measurement_ended_early')
    except (OSError, ValueError, KeyError) as error:
        result.update(status='failed', failure_reasons=['measurement_configuration_missing'], error=str(error))
        (destination/'result.json').write_text(json.dumps(result, indent=2)+'\n')
        return result
    try:
        resources = project_resources(directory, destination)
    except (OSError, ValueError, KeyError) as error:
        resources = []
        result['resource_error'] = str(error)
        reasons.append('resource_identity_unverified')
    groups = defaultdict(list)
    for row in resources:
        groups[row['sample']].append(row)
    first = min(groups, key=int) if groups else None
    series = []
    for sample in sorted(groups, key=int):
        rows = groups[sample]
        timestamp = datetime.fromisoformat(rows[0]['timestamp']).timestamp()
        if not start <= timestamp <= start+duration:
            continue
        present = sum(int(row['containers']) for row in rows if row['node_type'] in ('solana_bootstrap','solana_validator'))
        item = dict(sample=sample, timestamp=timestamp, elapsed_sec=timestamp-start,
            nodes=present, complete=present == count and all(row['project_complete'] for row in rows),
            cpu_valid=sample != first,
            total_mem_gib=sum(float(row['mem_total_bytes']) for row in rows)/2**30,
            total_cpu_cores=sum(float(row['cpu_avg_percent'])*int(row['containers']) for row in rows)/100)
        for row in rows:
            role = row['node_type']
            item[role+'_mem_gib'] = float(row['mem_total_bytes'])/2**30
            item[role+'_cpu_cores'] = float(row['cpu_avg_percent'])*int(row['containers'])/100
        series.append(item)
    complete = [row for row in series if row['complete'] and row['cpu_valid']]
    result['resource_coverage_pct'] = coverage([row['timestamp'] for row in complete], start, duration, interval)
    if result['resource_coverage_pct'] < 95:
        reasons.append('incomplete_resource_window')
    if any(not row['complete'] for row in series):
        reasons.append('node_count_mismatch')
    for metric in ('total_mem_gib', 'total_cpu_cores'):
        values = [row[metric] for row in complete]
        result[metric+'_mean'] = statistics.mean(values) if values else None
        result[metric+'_p95'] = quantile(values, .95)
        result[metric+'_max'] = max(values) if values else None
    write_csv(destination/'resources.csv', series)
    plot(destination, series)
    health = []
    try:
        with (directory/'health.jsonl').open() as handle:
            for line in handle:
                row = json.loads(line)
                if start <= row['timestamp'] <= start+duration:
                    health.append({k:v for k,v in row.items() if k not in ('votes','processes')})
    except (OSError, ValueError, KeyError) as error:
        reasons.append('health_evidence_missing')
        result['health_error'] = str(error)
    healthy = sum(bool(row.get('healthy')) for row in health)
    result.update(health_coverage_pct=coverage([row['timestamp'] for row in health],start,duration,interval),
        healthy_round_pct=100*healthy/len(health) if health else None)
    if result['health_coverage_pct'] < 95 or not health or healthy/len(health) < .99:
        reasons.append('chain_health_or_coverage')
    slots = [row['finalized_slot'] for row in health if 'finalized_slot' in row]
    result['finalized_slot_delta'] = slots[-1]-slots[0] if len(slots)>1 else None
    if len(slots)<2 or slots[-1] <= slots[0] or any(row.get('stalled') for row in health):
        reasons.append('no_continuous_finalization')
    if any(row.get('active_agave', 0) != count for row in health):
        reasons.append('active_process_count_mismatch')
    for field in ('active_agave','current_validators','delinquent_validators','current_stake_pct','finalized_lag'):
        values = [row[field] for row in health if row.get(field) is not None]
        result[field+'_min'] = min(values) if values else None
        result[field+'_max'] = max(values) if values else None
    transactions = [row['transaction_count'] for row in health if 'transaction_count' in row]
    result['transaction_count_delta_including_votes'] = transactions[-1]-transactions[0] if len(transactions)>1 else None
    if (directory/'transactions').exists():
        try:
            result['transfers'] = transfer_result(directory/'transactions')
            if result['transfers']['status'] != 'passed':
                reasons.append('transfer_load_or_inclusion_failed')
        except (OSError, ValueError, KeyError) as error:
            reasons.append('transfer_evidence_missing')
            result['transfer_error'] = str(error)
    result.update(status='passed' if not reasons else 'failed', failure_reasons=reasons)
    (destination/'result.json').write_text(json.dumps(result, indent=2)+'\n')
    write_csv(destination/'result.csv', [{**result,'failure_reasons':';'.join(reasons),
        'transfers':json.dumps(result.get('transfers', {}))}])
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.run_dir.resolve())
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['status'] == 'passed' else 1)
