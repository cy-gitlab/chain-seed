"""Blockchain image requirements owned by ChainSeed.

SeedEmu provides the generic :class:`ImageRef` transport contract; concrete
blockchain repositories, tags, and architecture variants belong here.
"""

from typing import Iterable, Tuple

from seedemu.core import ImageRef, Node


ImageRefs = Tuple[ImageRef, ...]

ETHEREUM_LEGACY_IMAGES: ImageRefs = (
    ImageRef(
        "handsonsecurity/seedemu-ethereum",
        tag="legacy",
        platform="amd64",
    ),
    ImageRef(
        "handsonsecurity/seedemu-ethereum-arm64",
        tag="legacy",
        platform="arm64",
    ),
)

ETHEREUM_POS_IMAGES: ImageRefs = (
    ImageRef("handsonsecurity/seedemu-ethereum", tag="pos2.0"),
)

MONERO_IMAGES: ImageRefs = (
    ImageRef("handsonsecurity/seedemu-monero", tag="latest"),
)

SOLANA_IMAGES: ImageRefs = (
    ImageRef("handsonsecurity/seedemu-solana", tag="1.0"),
)

CHAINLINK_IMAGES: ImageRefs = (
    ImageRef(
        "amanvelani/chainlink-develop",
        tag="amd64",
        platform="amd64",
    ),
    ImageRef(
        "amanvelani/chainlink-develop",
        tag="arm64",
        platform="arm64",
    ),
)


def apply_base_images(node: Node, image_refs: Iterable[ImageRef]) -> Node:
    """Attach one or more platform-aware image requirements to ``node``."""

    for image_ref in image_refs:
        node.setBaseImage(image_ref)
    return node


__all__ = [
    "CHAINLINK_IMAGES",
    "ETHEREUM_LEGACY_IMAGES",
    "ETHEREUM_POS_IMAGES",
    "MONERO_IMAGES",
    "SOLANA_IMAGES",
    "apply_base_images",
]
