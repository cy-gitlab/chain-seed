#!/usr/bin/env python3
"""Patch generated D61 output to use a forked Lighthouse binary/profile."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


DEFAULT_OFFICIAL_IMAGE = "handsonsecurity/seedemu-ethereum:pos2.0"
DEFAULT_FORK_IMAGE = "handsonsecurity/seedemu-ethereum:pos2.0-seedemu-lite"
SUMMARY_NAME = "lighthouse-fork-summary.json"


def patch_dockerfile(path: Path, official_image: str, fork_image: str) -> bool:
    original = path.read_text()
    pattern = re.compile(rf"^FROM\s+{re.escape(official_image)}(?P<suffix>\s.*)?$", re.MULTILINE)

    def replace(match: re.Match[str]) -> str:
        return f"FROM {fork_image}{match.group('suffix') or ''}"

    patched, count = pattern.subn(replace, original, count=1)
    if count:
        path.write_text(patched)
        return True
    return False


def should_patch_beacon(script: str, scope: str) -> bool:
    if "lighthouse --debug-level" not in script or " bn " not in script:
        return False
    if "SEED_EMU_LIGHTHOUSE_PROFILE=" in script:
        return False
    if scope == "all":
        return True
    if scope == "non-historic":
        return "--reconstruct-historic-states" not in script
    raise ValueError(f"unsupported scope: {scope}")


def _component_tokens(components: str) -> set[str]:
    raw = components.strip().lower()
    if not raw or raw == "all":
        return {"all"}
    return {token for token in re.split(r"[,;\s]+", raw) if token}


def _rayon_enabled(components: str) -> bool:
    tokens = _component_tokens(components)
    return "all" in tokens or "rayon" in tokens


def _processor_worker_cap_enabled(components: str) -> bool:
    tokens = _component_tokens(components)
    return (
        "all" in tokens
        or "processor" in tokens
        or "processor-workers" in tokens
    )


def patch_start_script(
    path: Path,
    scope: str,
    rayon_threads: int,
    components: str,
    processor_workers_cap: int | None,
) -> bool:
    original = path.read_text(errors="ignore")
    if not should_patch_beacon(original, scope):
        return False

    env_parts = [
        "SEED_EMU_LIGHTHOUSE_PROFILE=lite",
        f"SEED_EMU_LIGHTHOUSE_COMPONENTS={components}",
    ]
    if _rayon_enabled(components):
        env_parts.append(f"SEED_EMU_LIGHTHOUSE_RAYON_THREADS={rayon_threads}")
        env_parts.append(f"RAYON_NUM_THREADS={rayon_threads}")
    if processor_workers_cap is not None and _processor_worker_cap_enabled(components):
        env_parts.append(f"SEED_EMU_LIGHTHOUSE_MAX_PROCESSOR_WORKERS={processor_workers_cap}")
    env_prefix = " ".join(env_parts) + " "
    patched = original.replace(
        "lighthouse --debug-level",
        f"{env_prefix}lighthouse --debug-level",
        1,
    )
    if patched == original:
        return False
    path.write_text(patched)
    return True


def patch_output(
    output_dir: Path,
    official_image: str,
    fork_image: str,
    profile: str,
    scope: str,
    rayon_threads: int,
    components: str,
    processor_workers_cap: int | None,
) -> dict[str, object]:
    dockerfiles_seen = 0
    dockerfiles_patched = 0
    start_scripts_seen = 0
    start_scripts_patched = 0

    if profile == "official":
        summary = {
            "profile": profile,
            "official_image": official_image,
            "fork_image": "",
            "dockerfiles_seen": 0,
            "dockerfiles_patched": 0,
            "start_scripts_seen": 0,
            "start_scripts_patched": 0,
            "scope": scope,
            "rayon_threads": rayon_threads,
            "components": components,
            "processor_workers_cap": processor_workers_cap,
        }
        (output_dir / SUMMARY_NAME).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
        return summary

    dockerfile_candidates = list(output_dir.glob("*/Dockerfile"))
    dockerfile_candidates.extend(output_dir.glob("dummies/*"))
    for dockerfile in sorted(path for path in dockerfile_candidates if path.is_file()):
        dockerfiles_seen += 1
        if patch_dockerfile(dockerfile, official_image, fork_image):
            dockerfiles_patched += 1

    script_candidates = [
        path
        for path in output_dir.glob("hnode_*/*")
        if path.is_file() and path.name != "Dockerfile"
    ]
    for script in sorted(script_candidates):
        start_scripts_seen += 1
        if patch_start_script(script, scope, rayon_threads, components, processor_workers_cap):
            start_scripts_patched += 1

    summary = {
        "profile": profile,
        "official_image": official_image,
        "fork_image": fork_image,
        "dockerfiles_seen": dockerfiles_seen,
        "dockerfiles_patched": dockerfiles_patched,
        "start_scripts_seen": start_scripts_seen,
        "start_scripts_patched": start_scripts_patched,
        "scope": scope,
        "rayon_threads": rayon_threads,
        "components": components,
        "processor_workers_cap": processor_workers_cap,
    }
    (output_dir / SUMMARY_NAME).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("./output"))
    parser.add_argument("--official-image", default=DEFAULT_OFFICIAL_IMAGE)
    parser.add_argument("--fork-image", default=DEFAULT_FORK_IMAGE)
    parser.add_argument("--profile", choices=["official", "seedemu-lite"], default="official")
    parser.add_argument("--scope", choices=["all", "non-historic"], default="non-historic")
    parser.add_argument("--rayon-threads", type=int, default=2)
    parser.add_argument(
        "--processor-workers-cap",
        type=int,
        default=None,
        help=(
            "Optional worker cap for processor/processor-workers component runs. "
            "The generated beacon start scripts pass it through to the Lighthouse fork."
        ),
    )
    parser.add_argument(
        "--components",
        default="all",
        help=(
            "Comma-separated component list for component-aware Lighthouse forks. "
            "Supported tokens: all, cache, light-client, processor, "
            "processor-workers, processor-queues, processor-gossip, rayon, network."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> None:
    args = parse_args(argv)
    if args.rayon_threads < 1:
        raise SystemExit("--rayon-threads must be positive")
    if args.processor_workers_cap is not None and args.processor_workers_cap < 1:
        raise SystemExit("--processor-workers-cap must be positive")
    summary = patch_output(
        output_dir=args.output,
        official_image=args.official_image,
        fork_image=args.fork_image,
        profile=args.profile,
        scope=args.scope,
        rayon_threads=args.rayon_threads,
        components=args.components,
        processor_workers_cap=args.processor_workers_cap,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main(sys.argv[1:])
