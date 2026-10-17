# Ethereum Transaction Generator

Generate mixed transaction loads against a funded Ethereum RPC endpoint.
Use the same chain ID, mnemonic and account count as the target network.

Run the following commands from this directory.

## Deploy contracts

```bash
python deploy_contracts.py \
  --rpc-url http://10.150.0.72:8545 --chain-id 1337 --accounts 10 \
  --output results/contracts.json
```

These contracts are reused by later contract-call transactions. Contract-creation
transactions in the load create separate new contracts.

The default mnemonic matches the Ethereum examples. For other funded accounts,
pass `--mnemonic-file FILE` to both deployment and sending.

## Collect receipts

Start in one terminal, before sending transactions:

```bash
python collect_tx_receipts.py \
  --rpc-url http://10.150.0.72:8545 \
  --tx-log results/tx-load/tx_sender.csv --output results/tx_receipts.csv \
  --mode blocks --interval 2 --sender-state results/tx-load/run_info.json
```

With `--sender-state`, collection exits after the sender finishes and all sent
transactions have receipts. A drain timeout returns a nonzero exit code.
Without this option, press Ctrl-C to stop collection.

## Send transactions

In another terminal, from the same directory:

```bash
python send_transactions.py \
  --rpc-url http://10.150.0.72:8545 --chain-id 1337 --accounts 10 \
  --contracts-file results/contracts.json --log-dir results/tx-load \
  --phases steady:1:300
```

The sender exits when its schedule finishes. Wait for the receipt collector too.
Use new output paths for another run.

## Change the load

- `--profile PATH`: JSON mapping transaction types to sampling weights; pass it to both deployment and sending.
- `--phases warmup:1:60,steady:20:300,recovery:1:60`: name, TPS and seconds per phase.
- `--rpc-url URL`: repeat for multiple submission endpoints on the same chain.
- `--compose-file PATH`: discover RPCs from a SeedEmu PoS Compose file instead of specifying a URL.
- `--send-workers N`, `--sign-workers N`, `--seed N`: sending/signing concurrency and random seed.
- `--dry-run`: show the workload without connecting or writing output.

The default [profile](profiles/tx_mix_mainnet_v4.json) keeps the proportions used in
our experiments. They come from our analysis of real Ethereum Mainnet transactions
in the Xatu `canonical_execution_transaction` dataset: blocks 25,797,000–25,804,999,
with 2,257,633 transactions, including 2,219,980 successful transactions. The 11
category weights are percentages of those successful transactions. Calls are
classified using transaction fields and selected function selectors; remaining
calls are grouped by gas usage.

Copy the JSON and adjust the weights to suit your workload, for example:

```json
{
  "native_transfer": 40,
  "erc20_transfer": 30,
  "dex_swap": 20,
  "contract_deploy": 10
}
```

Weights need not add up to 100; the sender samples proportionally. Omitted types
and zero weights are disabled. Deployment only creates the reusable contracts
needed by the selected call types. After enabling another call type, deploy its
contracts before sending with the updated profile. Actual counts vary with random
sampling; `--seed` controls the random seed.

## Execution parameters

[_templates.py](_templates.py) contains the execution defaults. Adjust these
defaults when changing transaction costs:

| Setting | Controls |
| --- | --- |
| `TRANSACTION_TEMPLATES` | Transaction kind, target contract, selector, calldata length and gas limit. `target_gas_p50` records the calibration target; it does not enforce actual gas usage. |
| `SETUP_CONTRACTS` | Reusable synthetic contracts: storage writes, calldata words read and deployment gas limit. |
| `DEPLOYMENT_CONTRACT` | Bytecode used for contract-creation load: storage writes and code padding. Set the transaction gas limit in `TRANSACTION_TEMPLATES["contract_deploy"]`. |
| `TRANSACTION_ENVELOPES` | Relative weights of EIP-1559, legacy and access-list formats. The experimental defaults use the observed supported-format mix, renormalized; blob and set-code formats are not generated. |

Deploy fresh contracts after changing their templates. These are synthetic cost
models for the observed transaction categories, rather than full ERC20, DEX or NFT
applications. Sending records the selected weights, execution parameters and
configuration hashes in `run_info.json`.

See `--help` for fee, concurrency and other command-line parameters.
