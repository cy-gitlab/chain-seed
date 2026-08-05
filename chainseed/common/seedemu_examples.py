"""Locate example modules from the installed SeedEmu source checkout."""

from pathlib import Path
import sys

import seedemu


def enable_seedemu_examples() -> Path:
    """Make the installed SeedEmu checkout's top-level examples importable."""

    package_file = getattr(seedemu, "__file__", None)
    if package_file is None:
        raise RuntimeError("cannot locate the installed SeedEmu package")

    checkout_root = Path(package_file).resolve().parent.parent
    examples_dir = checkout_root / "examples"
    if not examples_dir.is_dir():
        raise RuntimeError(
            "ChainSeed examples require SeedEmu to be installed from an "
            "editable source checkout that includes the complete examples directory"
        )

    root = str(checkout_root)
    if root not in sys.path:
        sys.path.append(root)
    return checkout_root


__all__ = ["enable_seedemu_examples"]
