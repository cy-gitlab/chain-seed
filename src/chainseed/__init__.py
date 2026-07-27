"""Blockchain extensions for SeedEmu."""

from .extension import ChainSeedExtension, descriptor

__version__ = descriptor.version

__all__ = ["ChainSeedExtension", "descriptor", "__version__"]

