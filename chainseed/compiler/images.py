"""Platform-specific Docker images selected by ChainSeedDocker."""

from seedemu.compiler import BASE_IMAGE, BASE_IMAGE_ARM64, DockerImage, Platform

from ..common.systems import CHAINLINK, ETHEREUM_LEGACY, ETHEREUM_POS, MONERO, SOLANA


def _image(name: str, software=(), subset=None) -> DockerImage:
    return DockerImage(
        name=name,
        software=list(software),
        local=False,
        subset=subset,
    )


ETHEREUM_SOFTWARE = (
    "software-properties-common",
    "python3",
    "python3-pip",
)

IMAGE_CATALOG = {
    Platform.AMD64: {
        ETHEREUM_LEGACY: _image(
            "handsonsecurity/seedemu-ethereum:legacy",
            ETHEREUM_SOFTWARE,
            subset=BASE_IMAGE,
        ),
        ETHEREUM_POS: _image(
            "handsonsecurity/seedemu-ethereum:pos2.0",
            ETHEREUM_SOFTWARE,
            subset=BASE_IMAGE,
        ),
        MONERO: _image(
            "handsonsecurity/seedemu-monero:latest",
            subset=BASE_IMAGE,
        ),
        SOLANA: _image(
            "handsonsecurity/seedemu-solana:1.0",
            subset=BASE_IMAGE,
        ),
        CHAINLINK: _image("amanvelani/chainlink-develop:amd64"),
    },
    Platform.ARM64: {
        ETHEREUM_LEGACY: _image(
            "handsonsecurity/seedemu-ethereum-arm64:legacy",
            ETHEREUM_SOFTWARE,
            subset=BASE_IMAGE_ARM64,
        ),
        ETHEREUM_POS: _image(
            "handsonsecurity/seedemu-ethereum:pos2.0",
            ETHEREUM_SOFTWARE,
            subset=BASE_IMAGE_ARM64,
        ),
        MONERO: _image(
            "handsonsecurity/seedemu-monero:latest",
            subset=BASE_IMAGE_ARM64,
        ),
        SOLANA: _image(
            "handsonsecurity/seedemu-solana:1.0",
            subset=BASE_IMAGE_ARM64,
        ),
        CHAINLINK: _image("amanvelani/chainlink-develop:arm64"),
    },
}


__all__ = ["IMAGE_CATALOG"]
