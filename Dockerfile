# Dockerfile -- containerised TOML parser.
#
# Reads TOML from stdin (or from mounted files in --merge mode), writes
# JSON or tab-separated key/value lines to stdout. Consumers whose host
# contract is Docker + Git + just keep it that way by running the parser
# inside a container rather than requiring host-side Python.
#
# Usage (from repo root):
#   docker build -t toml-bridge:local .
#   printf '[gui]\nmode = "wayland"\n' | docker run --rm -i toml-bridge:local
#
# The published image is pulled by consumers rather than built by them;
# a consumer that already assembles its own tooling image may instead
# lift the parser out of this one with
# `COPY --from=ghcr.io/ycpss91255-docker/toml-bridge:<tag> \
#       /usr/local/bin/toml-bridge /usr/local/bin/toml-bridge`,
# which is how ycpss91255-docker/base's test-tools image carries it.
#
# The `tool-pin:` markers below are the org's pin-bot annotations: they
# name the upstream each ARG tracks so a version bump is mechanical.

# tool-pin: toml-bridge-python dockerhub library/python pattern=^3\.13\.[0-9]+-alpine3\.22$
ARG PYTHON_VERSION=3.13.13-alpine3.22
FROM python:${PYTHON_VERSION}

# Vendored tomli: the backport of stdlib tomllib (PEP 680), MIT-licensed,
# ~1000 lines, zero transitive dependencies. Python 3.11+ uses the stdlib
# tomllib; tomli is the fallback for older runtimes (3.6+). Both are
# installed so the bridge script works on any Python >= 3.6 without
# conditional pip logic.
# tool-pin: tomli github-release hukkin/tomli
ARG TOMLI_VERSION=2.4.1
RUN pip install --no-cache-dir "tomli==${TOMLI_VERSION}"

COPY toml_bridge.py /usr/local/bin/toml-bridge
RUN chmod +x /usr/local/bin/toml-bridge

ENTRYPOINT ["python3", "/usr/local/bin/toml-bridge"]
