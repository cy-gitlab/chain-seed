#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../../.." && pwd)"
IMAGE="${LIGHTHOUSE_FORK_IMAGE:-handsonsecurity/seedemu-ethereum:pos2.0-seedemu-lite}"
BASE_IMAGE="${LIGHTHOUSE_BASE_IMAGE:-handsonsecurity/seedemu-ethereum:pos2.0}"
BUILD_CONTEXT="$REPO_ROOT/docker_images/seedemu-ethereum/pos2.0-lighthouse-fork"
NETWORK_ARG="${DOCKER_BUILD_NETWORK:-host}"
PROGRESS="${DOCKER_BUILD_PROGRESS:-plain}"
PROFILE_PATCH="${LIGHTHOUSE_PROFILE_PATCH:-$BUILD_CONTEXT/patches/seedemu-lite-profile.patch}"
PROFILE_PATCH_TARGET="$BUILD_CONTEXT/patches/seedemu-lite-profile.patch"
PROFILE_PATCH_BACKUP=""
LOCAL_GIT_DEPS_PATCH="${LIGHTHOUSE_LOCAL_GIT_DEPS_PATCH:-$BUILD_CONTEXT/patches/local-git-deps.patch}"
LOCAL_GIT_DEPS_PATCH_TARGET="$BUILD_CONTEXT/patches/local-git-deps.patch"
LOCAL_GIT_DEPS_PATCH_BACKUP=""
SOURCE_TARBALL="$BUILD_CONTEXT/.build/lighthouse-src.tar"
GIT_VENDOR_TARBALL="$BUILD_CONTEXT/.build/vendor-git.tar"
DEPOSIT_CONTRACT_DIR="$BUILD_CONTEXT/.build/deposit-contract"
CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-4}"
LIGHTHOUSE_CARGO_PROFILE="${LIGHTHOUSE_CARGO_PROFILE:-maxperf}"
LIGHTHOUSE_CARGO_FEATURES="${LIGHTHOUSE_CARGO_FEATURES:-portable,gnosis}"
APT_MIRROR="${APT_MIRROR:-http://mirrors.tuna.tsinghua.edu.cn/debian}"
APT_SECURITY_MIRROR="${APT_SECURITY_MIRROR:-http://mirrors.tuna.tsinghua.edu.cn/debian-security}"
PROXY_BUILD_ARGS=()

mkdir -p "$(dirname "$SOURCE_TARBALL")"

detect_lighthouse_ref_from_image() {
    local image="$1"
    local version

    docker image inspect "$image" >/dev/null
    version="$(docker run --rm "$image" lighthouse --version | awk 'NR == 1 {print $2}')"
    version="${version%%-*}"
    if [[ ! "$version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "failed to parse Lighthouse version from image $image: $version" >&2
        return 1
    fi
    printf '%s\n' "$version"
}

LIGHTHOUSE_REF="${LIGHTHOUSE_REF:-$(detect_lighthouse_ref_from_image "$BASE_IMAGE")}"
SOURCE_DIR="${LIGHTHOUSE_SOURCE_DIR:-/tmp/lighthouse-${LIGHTHOUSE_REF}}"
GIT_VENDOR_DIR="${LIGHTHOUSE_GIT_VENDOR_DIR:-/tmp/lighthouse-git-vendor-${LIGHTHOUSE_REF}}"

cleanup_profile_patch() {
    if [[ -n "$PROFILE_PATCH_BACKUP" && -f "$PROFILE_PATCH_BACKUP" ]]; then
        cp "$PROFILE_PATCH_BACKUP" "$PROFILE_PATCH_TARGET"
        rm -f "$PROFILE_PATCH_BACKUP"
    fi
    if [[ -n "$LOCAL_GIT_DEPS_PATCH_BACKUP" && -f "$LOCAL_GIT_DEPS_PATCH_BACKUP" ]]; then
        cp "$LOCAL_GIT_DEPS_PATCH_BACKUP" "$LOCAL_GIT_DEPS_PATCH_TARGET"
        rm -f "$LOCAL_GIT_DEPS_PATCH_BACKUP"
    fi
}

trap cleanup_profile_patch EXIT

copy_patch_if_needed() {
    local source="$1"
    local target="$2"
    local backup_var="$3"
    local source_abs
    local target_abs

    source_abs="$(readlink -f "$source")"
    target_abs="$(readlink -f "$target")"
    if [[ "$source_abs" != "$target_abs" ]]; then
        if [[ ! -f "$source_abs" ]]; then
            echo "patch file not found: $source_abs" >&2
            exit 1
        fi
        local backup
        backup="$(mktemp)"
        cp "$target_abs" "$backup"
        cp "$source_abs" "$target_abs"
        printf -v "$backup_var" '%s' "$backup"
    fi
}

PROFILE_PATCH_ABS="$(readlink -f "$PROFILE_PATCH")"
PROFILE_PATCH_TARGET_ABS="$(readlink -f "$PROFILE_PATCH_TARGET")"
copy_patch_if_needed "$PROFILE_PATCH_ABS" "$PROFILE_PATCH_TARGET_ABS" PROFILE_PATCH_BACKUP
PROFILE_PATCH_SHA="$(sha256sum "$PROFILE_PATCH_ABS" | awk '{print $1}')"

LOCAL_GIT_DEPS_PATCH_ABS="$(readlink -f "$LOCAL_GIT_DEPS_PATCH")"
LOCAL_GIT_DEPS_PATCH_TARGET_ABS="$(readlink -f "$LOCAL_GIT_DEPS_PATCH_TARGET")"
copy_patch_if_needed "$LOCAL_GIT_DEPS_PATCH_ABS" "$LOCAL_GIT_DEPS_PATCH_TARGET_ABS" LOCAL_GIT_DEPS_PATCH_BACKUP

if [[ ! -d "$SOURCE_DIR/.git" ]]; then
    git clone --depth 1 --branch "$LIGHTHOUSE_REF" https://github.com/sigp/lighthouse.git "$SOURCE_DIR"
fi

git -C "$SOURCE_DIR" rev-parse --verify HEAD >/dev/null
git -C "$SOURCE_DIR" archive --format=tar HEAD -o "$SOURCE_TARBALL"

mkdir -p "$GIT_VENDOR_DIR"

clone_git_dep() {
    local url="$1"
    local dir="$2"
    local rev="$3"
    local target="$GIT_VENDOR_DIR/$dir"

    if [[ ! -d "$target/.git" ]]; then
        git clone "$url" "$target"
    fi

    if ! git -C "$target" cat-file -e "$rev^{commit}" 2>/dev/null; then
        git -C "$target" fetch --depth 1 origin "$rev"
    fi

    git -C "$target" checkout --detach "$rev"
    git -C "$target" submodule update --init --recursive --depth 1
}

case "$LIGHTHOUSE_REF" in
    v7.0.1)
        clone_git_dep https://github.com/sigp/xdelta3-rs xdelta3-rs 4db64086bb02e9febb584ba93b9d16bb2ae3825a
        clone_git_dep https://github.com/sigp/quick-protobuf.git quick-protobuf 681f413312404ab6e51f0b46f39b0075c6f4ebfd
        clone_git_dep https://github.com/sigp/lmdb-rs lmdb-rs f33845c6469b94265319aac0ed5085597862c27e
        clone_git_dep https://github.com/sigp/libmdbx-rs libmdbx-rs e6ff4b9377c1619bcf0bfdf52bee5a980a432a1a
        ;;
    *)
        clone_git_dep https://github.com/sigp/rust-libp2p.git rust-libp2p defcaf1a78cf5b70a723b3fee0e0be051c1dbd88
        clone_git_dep https://github.com/sigp/xdelta3-rs xdelta3-rs 4db64086bb02e9febb584ba93b9d16bb2ae3825a
        clone_git_dep https://github.com/sigp/quick-protobuf.git quick-protobuf 87c4ccb9bb2af494de375f5f6c62850badd26304
        clone_git_dep https://github.com/sigp/lmdb-rs lmdb-rs f33845c6469b94265319aac0ed5085597862c27e
        clone_git_dep https://github.com/sigp/libmdbx-rs libmdbx-rs e6ff4b9377c1619bcf0bfdf52bee5a980a432a1a
        ;;
esac

tar --exclude-vcs -C "$GIT_VENDOR_DIR" -cf "$GIT_VENDOR_TARBALL" .

mkdir -p "$DEPOSIT_CONTRACT_DIR"

fetch_contract() {
    local url="$1"
    local target="$2"

    if [[ ! -s "$target" ]]; then
        curl -fsSL "$url" -o "$target"
    fi
}

fetch_contract \
    https://raw.githubusercontent.com/ethereum/eth2.0-specs/v0.12.1/deposit_contract/contracts/validator_registration.json \
    "$DEPOSIT_CONTRACT_DIR/validator_registration.json"
fetch_contract \
    https://raw.githubusercontent.com/sigp/unsafe-eth2-deposit-contract/v0.9.2.1/unsafe_validator_registration.json \
    "$DEPOSIT_CONTRACT_DIR/unsafe_validator_registration.json"

python3 - "$DEPOSIT_CONTRACT_DIR" <<'PY'
import json
import sys
from pathlib import Path

base = Path(sys.argv[1])
contracts = base / "contracts"
contracts.mkdir(parents=True, exist_ok=True)

items = [
    (
        base / "validator_registration.json",
        contracts / "v0.12.1_validator_registration.json",
        contracts / "v0.12.1_validator_registration.bytecode",
    ),
    (
        base / "unsafe_validator_registration.json",
        contracts / "v0.12.1_testnet_validator_registration.json",
        contracts / "v0.12.1_testnet_validator_registration.bytecode",
    ),
]

for source, abi_target, bytecode_target in items:
    contract = json.loads(source.read_text())
    abi_target.write_text(json.dumps(contract["abi"], separators=(",", ":")))
    bytecode_target.write_text(json.dumps(contract["bytecode"], separators=(",", ":")))
PY

for proxy_var in HTTP_PROXY HTTPS_PROXY NO_PROXY http_proxy https_proxy no_proxy; do
    if [[ -n "${!proxy_var:-}" ]]; then
        PROXY_BUILD_ARGS+=(--build-arg "$proxy_var=${!proxy_var}")
    fi
done

docker build \
    --network="$NETWORK_ARG" \
    --progress="$PROGRESS" \
    "${PROXY_BUILD_ARGS[@]}" \
    --build-arg BASE_IMAGE="$BASE_IMAGE" \
    --build-arg APT_MIRROR="$APT_MIRROR" \
    --build-arg APT_SECURITY_MIRROR="$APT_SECURITY_MIRROR" \
    --build-arg LIGHTHOUSE_REF="$LIGHTHOUSE_REF" \
    --build-arg SEEDEMU_LIGHTHOUSE_PATCH_SHA="$PROFILE_PATCH_SHA" \
    --build-arg CARGO_BUILD_JOBS="$CARGO_BUILD_JOBS" \
    --build-arg LIGHTHOUSE_CARGO_PROFILE="$LIGHTHOUSE_CARGO_PROFILE" \
    --build-arg LIGHTHOUSE_CARGO_FEATURES="$LIGHTHOUSE_CARGO_FEATURES" \
    -t "$IMAGE" \
    "$BUILD_CONTEXT"

docker run --rm "$IMAGE" lighthouse --version
LIGHTHOUSE_PROFILE_PATCH="$PROFILE_PATCH_ABS" \
    "$SCRIPT_DIR/verify_lighthouse_component_image.sh" "$IMAGE"
