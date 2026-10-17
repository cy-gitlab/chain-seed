"""Execution templates for the transaction generator.

User sampling weights live in profiles/tx_mix_mainnet_v4.json. These defaults
preserve the imported experiment's synthetic contract costs and envelope mix.
See README.md for tuning and the scope of the mainnet calibration.
"""

PROFILE_NAME = "mainnet-calibrated-success-v4"

TRANSACTION_ENVELOPES = [
    {
        "name": "dynamic_fee",
        "weight": 86.2421
    },
    {
        "name": "legacy",
        "weight": 13.6989
    },
    {
        "name": "access_list",
        "weight": 0.059
    }
]

SETUP_CONTRACTS = [
    {
        "name": "erc20_transfer",
        "storage_writes": 5,
        "calldata_words": 2,
        "gas_limit": 300000
    },
    {
        "name": "erc20_approve",
        "storage_writes": 5,
        "calldata_words": 2,
        "gas_limit": 300000
    },
    {
        "name": "token_transfer_from",
        "storage_writes": 5,
        "calldata_words": 3,
        "gas_limit": 350000
    },
    {
        "name": "dex_swap",
        "storage_writes": 22,
        "calldata_words": 24,
        "gas_limit": 800000
    },
    {
        "name": "nft_action",
        "storage_writes": 5,
        "calldata_words": 2,
        "gas_limit": 350000
    },
    {
        "name": "account_abstraction",
        "storage_writes": 26,
        "calldata_words": 43,
        "gas_limit": 1000000
    },
    {
        "name": "generic_light",
        "storage_writes": 3,
        "calldata_words": 2,
        "gas_limit": 300000
    },
    {
        "name": "generic_medium",
        "storage_writes": 20,
        "calldata_words": 11,
        "gas_limit": 750000
    },
    {
        "name": "generic_heavy",
        "storage_writes": 64,
        "calldata_words": 53,
        "gas_limit": 2000000
    }
]

DEPLOYMENT_CONTRACT = {
    "storage_writes": 4,
    "runtime_padding_bytes": 3747,
    "gas_limit": 2000000
}

TRANSACTION_TEMPLATES = {
    "native_transfer": {
        "kind": "native_transfer",
        "gas_limit": 21000,
        "data_bytes": 0,
        "target_gas_p50": 21000
    },
    "erc20_transfer": {
        "kind": "contract_call",
        "contract": "erc20_transfer",
        "selector": "a9059cbb",
        "gas_limit": 150000,
        "data_bytes": 68,
        "target_gas_p50": 46097
    },
    "generic_contract_medium": {
        "kind": "contract_call",
        "contract": "generic_medium",
        "selector": "dead0002",
        "gas_limit": 650000,
        "data_bytes": 356,
        "target_gas_p50": 127759
    },
    "generic_contract_heavy": {
        "kind": "contract_call",
        "contract": "generic_heavy",
        "selector": "dead0003",
        "gas_limit": 2000000,
        "data_bytes": 1700,
        "target_gas_p50": 368275
    },
    "generic_contract_light": {
        "kind": "contract_call",
        "contract": "generic_light",
        "selector": "dead0001",
        "gas_limit": 150000,
        "data_bytes": 68,
        "target_gas_p50": 38762
    },
    "erc20_approve": {
        "kind": "contract_call",
        "contract": "erc20_approve",
        "selector": "095ea7b3",
        "gas_limit": 150000,
        "data_bytes": 68,
        "target_gas_p50": 47128
    },
    "dex_swap": {
        "kind": "contract_call",
        "contract": "dex_swap",
        "selector": "3593564c",
        "gas_limit": 800000,
        "data_bytes": 762,
        "target_gas_p50": 143572
    },
    "token_transfer_from": {
        "kind": "contract_call",
        "contract": "token_transfer_from",
        "selector": "23b872dd",
        "gas_limit": 180000,
        "data_bytes": 100,
        "target_gas_p50": 48506
    },
    "nft_action": {
        "kind": "contract_call",
        "contract": "nft_action",
        "selector": "a22cb465",
        "gas_limit": 180000,
        "data_bytes": 68,
        "target_gas_p50": 46619
    },
    "account_abstraction": {
        "kind": "contract_call",
        "contract": "account_abstraction",
        "selector": "1fad948c",
        "gas_limit": 1000000,
        "data_bytes": 1380,
        "target_gas_p50": 173833
    },
    "contract_deploy": {
        "kind": "contract_deploy",
        "gas_limit": 2000000,
        "data_bytes": 0,
        "target_gas_p50": 873056
    }
}
