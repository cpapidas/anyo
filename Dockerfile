# syntax=docker/dockerfile:1
# Anyo with pinned OpenCode and native OmO executables.
# Runs the minimal review example by default; consumer code can replace /app.

FROM python:3.12-slim-bookworm AS wheel
ARG ANYO_VERSION=0.0.dev0
WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/
RUN SETUPTOOLS_SCM_PRETEND_VERSION_FOR_ANYO="$ANYO_VERSION" \
    python -m pip wheel --no-cache-dir --no-deps --wheel-dir /wheels .

FROM python:3.12-slim-bookworm AS opencode-download
ARG TARGETARCH
# Change the version and both independently verified checksums together.
ARG OPENCODE_VERSION=1.18.34
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) archive=opencode-linux-x64-baseline.tar.gz; \
        digest=24b0d458d21ef548b2752166303defcf7f4945b049fb4876ab78dfaf86d81b27 ;; \
      arm64) archive=opencode-linux-arm64.tar.gz; \
        digest=bbdb3f00c2c51e42e315525233151309724226a8776da8e9145e3b0fa3d5310f ;; \
      *) echo "Unsupported target architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    curl --fail --show-error --location --retry 3 \
      "https://github.com/anomalyco/opencode/releases/download/v${OPENCODE_VERSION}/${archive}" \
      --output /tmp/opencode.tar.gz; \
    printf '%s  /tmp/opencode.tar.gz\n' "$digest" | sha256sum --check -; \
    mkdir /tmp/opencode; \
    tar -xzf /tmp/opencode.tar.gz -C /tmp/opencode; \
    install -m 0755 /tmp/opencode/opencode /usr/local/bin/opencode

FROM opencode-download AS omo-download
ARG TARGETARCH
# Change the version and both upstream SHA256SUMS values together.
ARG OMO_VERSION=5.1.21
RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) asset=omo-linux-x64-baseline; \
        digest=2f70450b94dfcecccd4a204ae700f426abf8723bca2514e7cb21a705a84cc5ad ;; \
      arm64) asset=omo-linux-arm64; \
        digest=5ab5f5aefec157658666437871573e7c1df5f55293f0f8cbe1bd9eab967ab601 ;; \
      *) echo "Unsupported target architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    curl --fail --show-error --location --retry 3 \
      "https://github.com/code-yeongyu/oh-my-openagent/releases/download/v${OMO_VERSION}/${asset}" \
      --output /tmp/omo; \
    printf '%s  /tmp/omo\n' "$digest" | sha256sum --check -; \
    install -m 0755 /tmp/omo /usr/local/bin/omo

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    XDG_CONFIG_HOME=/state/config \
    XDG_DATA_HOME=/state/data \
    XDG_CACHE_HOME=/state/cache \
    XDG_STATE_HOME=/state/state
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates git openssh-client ripgrep tini libstdc++6 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 anyo \
    && useradd --uid 10001 --gid anyo --create-home anyo \
    && mkdir -p /app /workspace /state/config /state/data /state/cache /state/state \
    && chown -R anyo:anyo /app /workspace /state
COPY --from=opencode-download /usr/local/bin/opencode /usr/local/bin/opencode
COPY --from=omo-download /usr/local/bin/omo /usr/local/bin/omo
COPY --from=wheel /wheels/ /tmp/wheels/
RUN for wheel in /tmp/wheels/anyo-*.whl; do \
      python -m pip install --no-cache-dir "${wheel}[opencode]"; \
    done \
    && rm -rf /tmp/wheels
USER anyo
RUN opencode --version && omo --version && python -c "import anyo"
COPY --chown=10001:10001 examples/harness-review/main.py examples/harness-review/review.md /app/
WORKDIR /app
EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "/app/main.py"]
