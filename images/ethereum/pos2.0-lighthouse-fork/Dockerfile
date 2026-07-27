# syntax=docker/dockerfile:1.7
ARG RUST_IMAGE=debian:bullseye-slim
ARG BASE_IMAGE=handsonsecurity/seedemu-ethereum:pos2.0

FROM ${RUST_IMAGE} AS lighthouse-builder

ARG DEBIAN_FRONTEND=noninteractive
ARG APT_MIRROR=http://deb.debian.org/debian
ARG APT_SECURITY_MIRROR=http://deb.debian.org/debian-security
ARG LIGHTHOUSE_REF=v8.1.3
ARG RUST_TOOLCHAIN=1.88.0
ENV PATH="/root/.cargo/bin:${PATH}"

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    set -eux; \
    printf '%s\n' \
        'Acquire::Retries "10";' \
        'Acquire::http::Timeout "60";' \
        'Acquire::https::Timeout "60";' \
        'Acquire::http::Pipeline-Depth "0";' \
        > /etc/apt/apt.conf.d/99seedemu-retries; \
    printf 'deb %s bullseye main\n' "$APT_MIRROR" > /etc/apt/sources.list; \
    printf 'deb %s bullseye-security main\n' "$APT_SECURITY_MIRROR" >> /etc/apt/sources.list; \
    printf 'deb %s bullseye-updates main\n' "$APT_MIRROR" >> /etc/apt/sources.list; \
    packages=" \
        build-essential \
        ca-certificates \
        clang \
        cmake \
        curl \
        git \
        libssl-dev \
        pkg-config \
        protobuf-compiler \
        unzip \
    "; \
    rm -rf /var/lib/apt/lists/*; \
    rm -rf /var/cache/apt/archives/partial/*; \
    for attempt in 1 2 3 4 5; do \
        apt-get update \
        && apt-get install -y --download-only --no-install-recommends $packages \
        && break; \
        if [ "$attempt" = 5 ]; then \
            exit 1; \
        fi; \
        rm -rf /var/lib/apt/lists/*; \
        rm -rf /var/cache/apt/archives/partial/*; \
        sleep "$((attempt * 10))"; \
    done; \
    apt-get install -y --no-download --no-install-recommends $packages; \
    rm -rf /var/lib/apt/lists/*

RUN --mount=type=cache,target=/root/.rustup,sharing=locked \
    --mount=type=cache,target=/root/.cargo/registry,sharing=locked \
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
    | sh -s -- -y --profile minimal --default-toolchain "${RUST_TOOLCHAIN}" \
    && rustc --version \
    && cargo --version

WORKDIR /src
COPY .build/lighthouse-src.tar /tmp/lighthouse-src.tar
COPY .build/vendor-git.tar /tmp/vendor-git.tar
COPY .build/deposit-contract /src/deposit-contract
ENV LIGHTHOUSE_DEPOSIT_CONTRACT_SPEC_URL="file:///src/deposit-contract/validator_registration.json"
ENV LIGHTHOUSE_DEPOSIT_CONTRACT_TESTNET_URL="file:///src/deposit-contract/unsafe_validator_registration.json"
RUN mkdir -p /src/lighthouse \
    && tar -xf /tmp/lighthouse-src.tar -C /src/lighthouse \
    && mkdir -p /src/vendor-git \
    && tar -xf /tmp/vendor-git.tar -C /src/vendor-git \
    && rm /tmp/lighthouse-src.tar /tmp/vendor-git.tar
RUN if [ -d /src/deposit-contract/contracts ]; then \
        mkdir -p /src/lighthouse/common/deposit_contract/contracts \
        && cp /src/deposit-contract/contracts/* /src/lighthouse/common/deposit_contract/contracts/; \
    fi
RUN for dir in /src/lighthouse/common/eth2_network_config/built_in_network_configs/*; do \
        if [ -f "$dir/genesis.ssz" ]; then \
            true; \
        elif [ -f "$dir/genesis.ssz.zip" ]; then \
            unzip -p "$dir/genesis.ssz.zip" genesis.ssz > "$dir/genesis.ssz"; \
        else \
            : > "$dir/genesis.ssz"; \
        fi; \
    done
COPY patches/local-git-deps.patch /tmp/local-git-deps.patch
COPY patches/seedemu-lite-profile.patch /tmp/seedemu-lite-profile.patch
RUN cd /src/lighthouse && git apply /tmp/local-git-deps.patch
RUN cd /src/lighthouse && git apply /tmp/seedemu-lite-profile.patch
ARG CARGO_BUILD_JOBS=4
ARG LIGHTHOUSE_CARGO_PROFILE=maxperf
ARG LIGHTHOUSE_CARGO_FEATURES=portable,gnosis
RUN --mount=type=cache,target=/root/.rustup,sharing=locked \
    --mount=type=cache,target=/root/.cargo/registry,sharing=locked \
    --mount=type=cache,target=/root/.cargo/git,sharing=locked \
    --mount=type=cache,target=/src/lighthouse/target,sharing=locked \
    cd /src/lighthouse \
    && (ulimit -n 65535 || true) \
    && cargo build -j "${CARGO_BUILD_JOBS}" --bin lighthouse --features "${LIGHTHOUSE_CARGO_FEATURES}" --profile "${LIGHTHOUSE_CARGO_PROFILE}" \
    && cp "/src/lighthouse/target/${LIGHTHOUSE_CARGO_PROFILE}/lighthouse" /tmp/lighthouse

FROM ${BASE_IMAGE}

ARG LIGHTHOUSE_REF
ARG SEEDEMU_LIGHTHOUSE_PATCH_SHA
LABEL org.seedemu.lighthouse.ref="${LIGHTHOUSE_REF}" \
      org.seedemu.lighthouse.component_patch_sha256="${SEEDEMU_LIGHTHOUSE_PATCH_SHA}"

COPY --from=lighthouse-builder /tmp/lighthouse /usr/local/bin/lighthouse
RUN lighthouse --version
