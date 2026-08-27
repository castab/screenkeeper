# syntax=docker/dockerfile:1

##
## Builder stage - compiles python-snappy against libsnappy-dev and installs
## the project into an isolated venv. Nothing from this stage ships at
## runtime except the venv contents.
##
FROM python:3.12-slim-bookworm AS builder

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libsnappy-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build

# hatchling needs README.md present because pyproject.toml declares
# readme = "README.md".
COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir .

##
## Final stage - only the libsnappy runtime shared library, no compiler, no
## build headers, no test tooling, non-root user.
##
FROM python:3.12-slim-bookworm AS final

ARG VERSION=0.0.0-dev
LABEL org.opencontainers.image.title="signage-controller" \
      org.opencontainers.image.description="Local desired-state controller for LG webOS signage televisions" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.source="https://github.com/castab/screenkeeper"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libsnappy1v5 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Fixed uid/gid so bind-mounted host state directories can be chowned
# predictably if a named volume isn't used.
RUN groupadd --system --gid 10001 signage \
    && useradd --system --uid 10001 --gid signage \
        --home-dir /var/lib/signage-controller --shell /usr/sbin/nologin signage

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# Default state directory: created and chowned here so that when this path is
# used as a *named volume* target, Docker initializes the volume with this
# ownership on first creation. state_store.py still enforces 0700/0600 on the
# directory and pairing-key file itself.
RUN mkdir -p /var/lib/signage-controller \
    && chown signage:signage /var/lib/signage-controller
VOLUME ["/var/lib/signage-controller"]

# config.yaml is user-supplied and must never be baked into the image. It is
# expected to be bind-mounted read-only at /etc/signage-controller/config.yaml
# at runtime; Docker creates that mount point directory automatically.

USER signage
WORKDIR /var/lib/signage-controller

ENTRYPOINT ["signage-controller", "--config", "/etc/signage-controller/config.yaml", "--state-dir", "/var/lib/signage-controller"]
CMD ["run"]
