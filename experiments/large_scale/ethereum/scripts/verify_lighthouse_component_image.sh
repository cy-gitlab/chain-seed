#!/usr/bin/env bash

if [[ -z "${BASH_VERSION:-}" ]]; then
    echo "verify_lighthouse_component_image.sh requires bash." >&2
    exit 2
fi

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ETHEREUM_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
IMAGE="${1:-handsonsecurity/seedemu-ethereum:pos2.0-baseline-components}"

docker image inspect "$IMAGE" >/dev/null
version="$(docker run --rm "$IMAGE" lighthouse --version | awk 'NR == 1 {print $2}')"
version="${version%%-*}"

if [[ -n "${LIGHTHOUSE_PROFILE_PATCH:-}" ]]; then
    patch_file="$LIGHTHOUSE_PROFILE_PATCH"
else
    case "$version" in
        v7.0.1)
            patch_file="$ETHEREUM_DIR/lighthouse_patches/seedemu-component-profile-v7.0.1.patch"
            ;;
        *)
            patch_file="$ETHEREUM_DIR/lighthouse_patches/seedemu-component-profile.patch"
            ;;
    esac
fi

if [[ ! -f "$patch_file" ]]; then
    echo "Lighthouse component patch not found: $patch_file" >&2
    exit 1
fi

expected_sha="$(sha256sum "$patch_file" | awk '{print $1}')"
actual_sha="$(docker image inspect "$IMAGE" --format '{{index .Config.Labels "org.seedemu.lighthouse.component_patch_sha256"}}')"

if [[ "$actual_sha" != "$expected_sha" ]]; then
    echo "Lighthouse component image is stale or incompatible: $IMAGE" >&2
    echo "expected_patch_sha256=$expected_sha" >&2
    echo "image_patch_sha256=${actual_sha:-<missing>}" >&2
    echo "rebuild with: $SCRIPT_DIR/build_lighthouse_component_fork.sh" >&2
    exit 1
fi

if ! docker run --rm --entrypoint grep "$IMAGE" \
    -a -q SEED_EMU_LIGHTHOUSE_MAX_PROCESSOR_WORKERS /usr/local/bin/lighthouse; then
    echo "Lighthouse binary does not contain dynamic processor worker-cap support: $IMAGE" >&2
    exit 1
fi

printf 'verified Lighthouse component image=%s version=%s patch_sha256=%s\n' \
    "$IMAGE" "$version" "$actual_sha"
