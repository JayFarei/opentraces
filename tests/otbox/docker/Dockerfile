# otbox-runtime — Linux image for the otbox `docker` driver (Tier 0).
#
# The `local` driver needs none of this and is the offline-verified
# default. This image only matters when you want stronger process /
# filesystem isolation via `otbox up --driver docker`.
#
# Build (needs network, one-time):  otbox image build
#
# The build context is the opentraces repo root.
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates tmux \
    && rm -rf /var/lib/apt/lists/*

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_DISABLE_IMPLICIT_TOKEN=1

WORKDIR /opt/opentraces
COPY packages/opentraces-schema /opt/opentraces/packages/opentraces-schema
COPY pyproject.toml README.md hatch_build.py /opt/opentraces/
COPY src /opt/opentraces/src
COPY skill /opt/opentraces/skill

RUN pip install -e /opt/opentraces/packages/opentraces-schema \
    && pip install -e "/opt/opentraces[dev]"

# A box's bind-mounted workspace is mounted here at runtime.
RUN mkdir -p /otbox/box
WORKDIR /otbox/box/project

# The container is kept alive by `otbox` (docker run ... sleep infinity);
# commands are delivered via `docker exec`.
CMD ["sleep", "infinity"]
