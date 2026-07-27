#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ETHEREUM_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

export LIGHTHOUSE_BASE_IMAGE="${LIGHTHOUSE_BASE_IMAGE:-handsonsecurity/seedemu-ethereum:pos2.0}"
export LIGHTHOUSE_FORK_IMAGE="${LIGHTHOUSE_FORK_IMAGE:-handsonsecurity/seedemu-ethereum:pos2.0-baseline-components}"

if [[ -z "${LIGHTHOUSE_REF:-}" ]]; then
    detected_version="$(docker run --rm "$LIGHTHOUSE_BASE_IMAGE" lighthouse --version | awk 'NR == 1 {print $2}')"
    export LIGHTHOUSE_REF="${detected_version%%-*}"
fi

case "$LIGHTHOUSE_REF" in
    v7.0.1)
        default_patch="$ETHEREUM_DIR/lighthouse_patches/seedemu-component-profile-v7.0.1.patch"
        default_local_git_deps_patch="$ETHEREUM_DIR/lighthouse_patches/local-git-deps-v7.0.1.patch"
        ;;
    *)
        default_patch="$ETHEREUM_DIR/lighthouse_patches/seedemu-component-profile.patch"
        default_local_git_deps_patch=""
        ;;
esac

export LIGHTHOUSE_PROFILE_PATCH="${LIGHTHOUSE_PROFILE_PATCH:-$default_patch}"
if [[ -n "$default_local_git_deps_patch" ]]; then
    export LIGHTHOUSE_LOCAL_GIT_DEPS_PATCH="${LIGHTHOUSE_LOCAL_GIT_DEPS_PATCH:-$default_local_git_deps_patch}"
fi

exec "$SCRIPT_DIR/build_lighthouse_seedemu_fork.sh"
