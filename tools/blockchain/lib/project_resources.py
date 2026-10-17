"""Recover project/role identity from retained container IDs, never from counts."""
from collections import Counter, defaultdict
import csv
import hashlib
import json
import re
from datetime import datetime

import yaml


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def project_resources(directory, output_dir=None):
    output_dir = output_dir or directory
    raw = directory/'memory_raw.csv'
    compose = directory/'observed-compose.yml'
    snapshot = directory/'container-identity.json'
    services = yaml.safe_load(compose.read_text())['services']
    names = {s['container_name']: s for s in services.values()}
    identities = {}
    for item in json.loads(snapshot.read_text()):
        name = item['name'].lstrip('/')
        if name not in names:
            raise ValueError(f'Cleanup snapshot contains unexpected container: {name}')
        prefix = item['id'][:12]
        if prefix in identities:
            raise ValueError(f'Duplicate container ID prefix: {prefix}')
        identities[prefix] = name
    if set(identities.values()) != set(names):
        raise ValueError('Container snapshot does not cover the complete Compose project')
    roles = {}
    for name, svc in names.items():
        match = re.search(r'-Monero-(seed|miner|full)-(\d+)-', name)
        if match:
            roles[name] = 'monero_' + match[1]
        elif '-Solana-Bootstrap-' in name:
            roles[name] = 'solana_bootstrap'
        elif '-Solana-Validator-' in name:
            roles[name] = 'solana_validator'
        else:
            role = svc.get('labels', {}).get('org.seedsecuritylabs.seedemu.meta.role', '')
            roles[name] = 'router' if 'Router' in role or role == 'RouteServer' else 'infrastructure'
    expected = Counter(roles.values())
    rotation_path = directory/'miner-rotation.jsonl'
    transitions = []
    cohorts = []
    if rotation_path.exists():
        for line in rotation_path.read_text().splitlines():
            event = json.loads(line)
            if event['event'] == 'transition_start':
                transitions.append([event['time'], float('inf')])
            elif event['event'] == 'transition_commit':
                transitions[-1][1] = event['time']
                cohorts.append((event['time'], set(event['active_indices'])))
        if not cohorts:
            raise ValueError('Missing verified rotation cohort')
    sample_times = {}
    if cohorts:
        with raw.open() as source:
            for r in csv.DictReader(source):
                sample_times[r['sample']] = datetime.fromisoformat(r['timestamp']).timestamp()
    windows = {}
    previous = None
    for sample in sorted(sample_times, key=int):
        timestamp = sample_times[sample]
        windows[sample] = (previous if previous is not None else timestamp, timestamp)
        previous = timestamp
    groups = defaultdict(dict)
    seen = defaultdict(set)
    excluded = Counter()
    with raw.open() as source:
        for r in csv.DictReader(source):
            identity = r['container_id'][:12]
            name = identities.get(identity)
            if name is None:
                excluded[identity] += 1
                continue
            sample = r['sample']
            if identity in seen[sample]:
                raise ValueError(f'Duplicate resource record: sample={sample}, id={identity}')
            seen[sample].add(identity)
            role = roles[name]
            if cohorts and role.startswith('monero_'):
                match = re.search(r'-Monero-(?:seed|miner|full)-(\d+)-', name)
                index = int(match[1])
                begin, end = windows[sample]
                if any(start <= end and finish >= begin for start,finish in transitions):
                    role = 'monero_transition'
                else:
                    active = next((ids for timestamp,ids in reversed(cohorts) if timestamp <= end), None)
                    if active is None:
                        raise ValueError('Resource sample precedes verified cohort')
                    role = 'monero_active_miner' if index in active else 'monero_non_mining'
            agg = groups[sample].setdefault(role, dict(sample=sample,
                timestamp=r['timestamp'], elapsed_sec=r['elapsed_sec'], node_type=role,
                containers=0, mem_total_bytes=0, cpu_total_percent=0.0))
            if agg['elapsed_sec'] != r['elapsed_sec']:
                raise ValueError(f'Inconsistent timestamp within resource sample {sample}')
            agg['containers'] += 1
            agg['mem_total_bytes'] += int(r['mem_usage_bytes'])
            agg['cpu_total_percent'] += float(r['cpu_percent'])
    rows = []
    for sample in sorted(groups, key=int):
        rs = groups[sample]
        complete = set(seen[sample]) == set(identities)
        for role in sorted(rs):
            row = rs[role]
            row['cpu_avg_percent'] = row.pop('cpu_total_percent') / row['containers']
            row['project_complete'] = complete
            rows.append(row)
    with (output_dir/'memory_by_type_project.csv').open('w', newline='') as f:
        if rows:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    audit = dict(method='raw container IDs joined to retained project identity snapshot and Compose',
        expected_containers=len(names), expected_roles=dict(expected),
        samples=len(groups), excluded_container_rows=dict(excluded),
        complete_samples=sum(set(ids) == set(identities) for ids in seen.values()),
        sources={str(p.name):digest(p) for p in (raw, compose, snapshot, *([rotation_path] if cohorts else []))})
    (output_dir/'resource_scope_audit.json').write_text(json.dumps(audit, indent=2)+'\n')
    return rows
