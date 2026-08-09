"""Blockchain extensions for SeedEmu."""

from .compiler import ChainSeedDocker
from .extension import ChainSeedExtension, descriptor

__version__ = descriptor.version

__all__ = ["ChainSeedDocker", "ChainSeedExtension", "descriptor", "__version__"]

