"""Blockchain extensions for SeedEmu."""

from .extension import ChainSeedExtension, check_seedemu_compatibility, descriptor

check_seedemu_compatibility()

from .compiler import ChainSeedDocker

__version__ = descriptor.version

__all__ = ["ChainSeedDocker", "ChainSeedExtension", "descriptor", "__version__"]

