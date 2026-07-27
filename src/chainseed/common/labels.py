"""Stable label names used for node discovery and observability."""

SEED_META_LABEL_PREFIX = "org.seedsecuritylabs.seedemu.meta."
CHAINSEED_LABEL_PREFIX = SEED_META_LABEL_PREFIX + "chainseed."


def ethereum_label(name: str) -> str:
    return SEED_META_LABEL_PREFIX + "ethereum." + name

