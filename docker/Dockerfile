# Production redfish_ctl image.
#
# Build:
#   docker build --build-arg REDFISH_BUILD_REVISION=<commit> \
#     -f docker/Dockerfile -t redfish-ctl .
# One-shot CLI equivalent to `redfish_ctl system`:
#   docker run --rm --env-file .env.redfish redfish-ctl system
# OTLP sidecar equivalent to `redfish_ctl exporter --output otlp`:
#   docker run --rm --env-file .env.redfish redfish-ctl exporter --output otlp --once
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /src

COPY pyproject.toml setup.py requirements.txt README.md LICENSE ./
COPY idrac_ctl ./idrac_ctl
COPY redfish_ctl ./redfish_ctl

RUN python -m pip install --upgrade pip \
    && python -m pip install build wheel \
    && python -m pip wheel --no-cache-dir --wheel-dir /wheelhouse ".[otlp]"

FROM python:3.12-slim AS runtime

ARG REDFISH_BUILD_REVISION=unknown

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    REDFISH_BUILD_REVISION=${REDFISH_BUILD_REVISION}

LABEL org.opencontainers.image.revision=${REDFISH_BUILD_REVISION}

RUN groupadd --system redfish \
    && useradd \
        --system \
        --gid redfish \
        --home-dir /home/redfish \
        --create-home \
        redfish

WORKDIR /work

COPY --from=builder /wheelhouse /wheelhouse

RUN python -m pip install \
        --no-cache-dir \
        --no-index \
        --find-links=/wheelhouse \
        "redfish_ctl[otlp]" \
    && rm -rf /wheelhouse \
    && python -m pip check

USER redfish

ENTRYPOINT ["redfish_ctl"]
CMD ["--help"]
