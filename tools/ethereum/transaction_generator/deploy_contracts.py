#!/usr/bin/env python3
"""Deploy reusable workload contracts and save their addresses for send_transactions.py."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from decimal import Decimal
from typing import Any
from _workload import (DEFAULT_CHAIN_ID, DEFAULT_MNEMONIC, DEFAULT_PROFILE, TEMPLATES_FILE,
    WorkloadProfile, atomic_json, build_accounts, connect, decimal_arg,
    load_profile, raw_transaction_bytes, utc_now)
from compose_endpoints import discover_execution_rpc_urls


def deploy_contracts(
    args: argparse.Namespace,
    profile: WorkloadProfile,
    rpc_url: str,
) -> dict[str, Any]:
    accounts = build_accounts(args.mnemonic, args.accounts)
    w3 = connect(rpc_url)
    if int(w3.eth.chain_id) != args.chain_id:
        raise RuntimeError(f"RPC chain id is {w3.eth.chain_id}, expected {args.chain_id}")
    sender = accounts[0]
    nonce = int(w3.eth.get_transaction_count(sender.address, "pending"))
    floor_wei = int(args.gas_price_floor_gwei * Decimal(10**9))
    gas_price = max(floor_wei, int(Decimal(int(w3.eth.gas_price)) * args.gas_price_multiplier))
    deployments: list[dict[str, Any]] = []
    contract_addresses: dict[str, str] = {}
    for contract in profile.setup_contracts:
        tx = {
            "value": 0,
            "data": contract.init_code,
            "gas": contract.gas_limit,
            "gasPrice": gas_price,
            "nonce": nonce,
            "chainId": args.chain_id,
        }
        signed = w3.eth.account.sign_transaction(tx, sender.key)
        tx_hash = w3.eth.send_raw_transaction(raw_transaction_bytes(signed))
        receipt = w3.eth.wait_for_transaction_receipt(
            tx_hash,
            timeout=args.receipt_timeout,
        )
        status = int(receipt.get("status", 0))
        address = str(receipt.get("contractAddress") or "")
        if status != 1 or not address:
            raise RuntimeError(
                f"setup contract {contract.name!r} deployment failed: "
                f"status={status}, receipt={receipt}"
            )
        contract_addresses[contract.name] = address
        deployments.append(
            {
                "name": contract.name,
                "contract_address": address,
                "deployment_tx_hash": tx_hash.hex(),
                "deployment_block_number": int(receipt["blockNumber"]),
                "init_code_sha256": hashlib.sha256(contract.init_code).hexdigest(),
                "gas_limit": contract.gas_limit,
            }
        )
        nonce += 1

    payload = {
        "status": "ready",
        "deployed_at": utc_now(),
        "rpc_url": rpc_url,
        "chain_id": args.chain_id,
        "profile": profile.name,
        "profile_path": str(args.profile.resolve()),
        "profile_sha256": hashlib.sha256(args.profile.read_bytes()).hexdigest(),
        "templates_sha256": hashlib.sha256(TEMPLATES_FILE.read_bytes()).hexdigest(),
        "account_count": args.accounts,
        "contract_address": deployments[0]["contract_address"] if deployments else "",
        "contract_addresses": contract_addresses,
        "deployments": deployments,
    }
    atomic_json(args.output, payload)
    return payload

def main():
    p = argparse.ArgumentParser(description=__doc__)
    target = p.add_mutually_exclusive_group(required=True)
    target.add_argument('--rpc-url', help='Execution HTTP RPC used to deploy contracts')
    target.add_argument('-f', '--compose-file', type=Path, help='Discover an execution RPC from Compose')
    p.add_argument('--chain-id', type=int, default=DEFAULT_CHAIN_ID)
    p.add_argument('--mnemonic', default=DEFAULT_MNEMONIC)
    p.add_argument('--mnemonic-file', type=Path)
    p.add_argument('--accounts', type=int, default=200)
    p.add_argument('--profile', type=Path, default=DEFAULT_PROFILE,
                   help='JSON mapping transaction type names to sampling weights')
    p.add_argument('--output', type=Path, required=True, help='JSON file containing deployed contract addresses')
    p.add_argument('--gas-price-floor-gwei', type=decimal_arg, default=Decimal('1'))
    p.add_argument('--gas-price-multiplier', type=decimal_arg, default=Decimal('2'))
    p.add_argument('--receipt-timeout', type=float, default=180)
    p.add_argument('--dry-run', action='store_true')
    args = p.parse_args()
    if args.chain_id < 1 or args.accounts < 2 or args.receipt_timeout <= 0:
        p.error('chain-id/receipt-timeout must be positive and accounts >=2')
    if args.gas_price_floor_gwei < 0 or args.gas_price_multiplier < 1:
        p.error('gas price floor must be nonnegative and multiplier >=1')
    if args.mnemonic_file:
        args.mnemonic = args.mnemonic_file.read_text().strip()
        if not args.mnemonic:
            p.error('mnemonic file is empty')
    profile = load_profile(args.profile)
    if args.dry_run:
        print(json.dumps(dict(profile=profile.name, chain_id=args.chain_id,
            contracts=[dict(name=c.name, gas_limit=c.gas_limit,
                init_code_sha256=hashlib.sha256(c.init_code).hexdigest()) for c in profile.setup_contracts],
            output=str(args.output)), indent=2))
        return 0
    if args.output.exists():
        p.error('output exists; choose a new path')
    rpc_url = args.rpc_url or discover_execution_rpc_urls(args.compose_file, 1)[0]
    print(json.dumps(deploy_contracts(args, profile, rpc_url), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
