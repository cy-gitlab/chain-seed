"""Generate the one retained Solana runtime configuration during compilation."""
from pathlib import Path
import re
import yaml
from _runtime import (install_genesis_voting_keypairs, mount_genesis_shared_dir,
    mount_start_barrier_dir, _validator_script_indices, _bootstrap_script_dir,
    _patch_genesis_epoch_config, _start_barrier_script,
    _bootstrap_genesis_ledger_publish_script, _ensure_agave_flag_each_command,
    _ensure_agave_option_each_command, _patch_validator_restart_loop,
    _patch_voting_validator_light_rpc, _strip_agave_lines,
    _patch_voting_validator_restricted_peers, VALIDATOR_RPC_FLAGS_TO_STRIP)
from _keys import _solana_pubkey

# The final experiment's ultra settings, with overwritten options consolidated.
AGAVE_OPTIONS = {
    '--accounts-db-cache-limit-mb': '32', '--unified-scheduler-handler-threads': '1',
    '--limit-ledger-size': '50000000', '--snapshot-interval-slots': '0',
    '--accounts-index-bins': '8', '--accounts-index-scan-results-limit-mb': '16',
    '--rpc-threads': '1', '--rpc-blocking-threads': '1', '--contact-debug-interval': '600000',
    '--log-messages-bytes-limit': '1024', '--accounts-db-access-storages-method': 'file',
    '--rpc-niceness-adjustment': '10', '--snapshot-packager-niceness-adjustment': '10',
    '--tpu-coalesce-ms': '5',
}
AGAVE_FLAGS = ('--disable-banking-trace', '--skip-startup-ledger-verification',
    '--no-incremental-snapshots', '--tpu-disable-quic', '--tpu-enable-udp')


def copied_script(directory, marker):
    matches = [p for p in directory.iterdir() if p.is_file() and marker in p.read_text(errors='ignore')]
    if len(matches) != 1:
        raise ValueError(f'Expected one {marker!r} script in {directory}, found {len(matches)}')
    return matches[0]


def apply_options(text):
    lines = text.splitlines(keepends=True)
    for flag, value in AGAVE_OPTIONS.items():
        _ensure_agave_option_each_command(lines, flag, value)
    for flag in AGAVE_FLAGS:
        _ensure_agave_flag_each_command(lines, flag)
    return ''.join(lines)


def configure_bootstrap(path, config):
    _patch_genesis_epoch_config(path, config['slots_per_epoch'], True,
        config['target_tick_duration'], config['ticks_per_slot'])
    text = path.read_text()
    text = text.replace('mkdir -p "$LEDGER_DIR"\n',
        'mkdir -p "$LEDGER_DIR"\nGENESIS_SHARED_DIR=/opt/solana/config/genesis-shared\n'
        'mkdir -p "$GENESIS_SHARED_DIR"\n', 1)
    old = '    --bootstrap-validator "$LEDGER_DIR/identity.json" "$LEDGER_DIR/vote-account.json" "$LEDGER_DIR/stake-account.json" \\\n'
    if text.count(old) != 1:
        raise ValueError('Bootstrap genesis command changed')
    extra = (f'    --bootstrap-validator-lamports {config["balance_lamports"]} \\\n'
        f'    --bootstrap-validator-stake-lamports {config["bootstrap_stake_lamports"]} \\\n'
        '    --validator-accounts-file "$CONFIG_DIR/validator-accounts.yml" \\\n'
        f'    --creation-time "{config["genesis_creation_time"]}" \\\n')
    text = text.replace(old, old+extra, 1)
    marker = '# 3) Point the local CLI at this node\'s RPC.'
    if text.count(marker) != 1:
        raise ValueError('Bootstrap startup sequence changed')
    text = text.replace(marker, _bootstrap_genesis_ledger_publish_script()
        + _start_barrier_script('$LEDGER_DIR/identity.json', 2)+'\n'+marker, 1)
    path.write_text(apply_options(text))


def configure_validator(path, index):
    text = path.read_text()
    begin = text.find('# Wait for the bootstrap RPC to come up.')
    end = text.find('echo "[seedemu-solana] starting validator on $SELF_IP (entrypoint $ENTRYPOINT)"')
    if begin < 0 or end <= begin:
        raise ValueError('Validator account setup sequence changed')
    genesis = '''# Identity, vote and stake accounts are already present in genesis.
GENESIS_SHARED_DIR=/opt/solana/config/genesis-shared
GENESIS_LEDGER_TAR="$GENESIS_SHARED_DIR/genesis-ledger.tar"
GENESIS_LEDGER_MARKER="$LEDGER_DIR/.seedemu-genesis-ledger-ready"
if [ ! -f "$GENESIS_LEDGER_MARKER" ]; then
  until [ -f "$GENESIS_LEDGER_TAR" ]; do sleep 1; done
  rm -rf "$LEDGER_DIR/rocksdb" "$LEDGER_DIR/accounts_index" \\
    "$LEDGER_DIR/genesis.bin" "$LEDGER_DIR/genesis.tar.bz2" "$LEDGER_DIR/ledger.lock"
  tar -C "$LEDGER_DIR" -xf "$GENESIS_LEDGER_TAR"
  rm -f "$LEDGER_DIR/ledger.lock" "$LEDGER_DIR/rocksdb/LOCK"
  touch "$GENESIS_LEDGER_MARKER"
fi
'''
    text = text[:begin]+genesis+_start_barrier_script('$LEDGER_DIR/identity.json', 2)+'\n'+text[end:]
    lines = text.splitlines(keepends=True)
    for flag in ('--no-genesis-fetch', '--no-snapshot-fetch', '--no-wait-for-vote-to-start-leader'):
        _ensure_agave_flag_each_command(lines, flag)
    text, _ = _patch_validator_restart_loop(''.join(lines), 'genesis voting', 'VALIDATOR_RESTART_DELAY')
    text, _ = _patch_voting_validator_light_rpc(text)
    text = apply_options(text)
    text, _ = _strip_agave_lines(text, VALIDATOR_RPC_FLAGS_TO_STRIP)
    if index > 1:
        text, _ = _patch_voting_validator_restricted_peers(text,
            [_solana_pubkey('bootstrap:identity'), _solana_pubkey('validator:001:identity')])
    path.write_text(text)


def prepare_output(output, config):
    from tools.blockchain.lib.runtime_mounts import patch_output
    indices = _validator_script_indices(output)
    if len(indices) != config['validators']-1:
        raise ValueError('Compiled validator count differs from requested count')
    install_genesis_voting_keypairs(output, indices, config['validator_stake_lamports'],
        config['balance_lamports'], 1, config['core_stake_lamports'])
    mount_genesis_shared_dir(output)
    mount_start_barrier_dir(output)
    configure_bootstrap(copied_script(_bootstrap_script_dir(output), '# seedemu-solana: genesis / bootstrap validator'), config)
    for directory, index in indices.items():
        configure_validator(copied_script(directory, '# seedemu-solana: validator joining the private cluster'), index)
    compose = output/'docker-compose.yml'
    patch_output(output, compose, '/seedemu-node', 1000000, False, False)
    payload = yaml.safe_load(compose.read_text())
    groups = dict(infrastructure=[], core=[], validators=[])
    for name, service in payload['services'].items():
        display = service.get('labels', {}).get('org.seedsecuritylabs.seedemu.meta.displayname', '')
        if display == 'Solana-Bootstrap-000' or display == 'Solana-Validator-001':
            groups['core'].append(name)
        elif display.startswith('Solana-Validator-'):
            groups['validators'].append(name)
        else:
            groups['infrastructure'].append(name)
        service['logging'] = dict(driver='json-file', options={'max-size': '10m', 'max-file': '2'})
    if len(groups['core']) != 2 or len(groups['validators']) != config['validators']-2:
        raise ValueError('Startup groups do not match the deployment')
    compose.write_text(yaml.safe_dump(payload, sort_keys=False))
    for group, services in groups.items():
        (output/f'{group}-services.txt').write_text('\n'.join(services)+'\n' if services else '')
