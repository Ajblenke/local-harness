FROM ghcr.io/astral-sh/uv:0.12.7 AS uv
FROM node:24.21.0-bookworm-slim

COPY --from=uv /uv /uvx /bin/
RUN apt-get update \
    && apt-get install --yes --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global @earendil-works/pi-coding-agent@0.85.1

ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python
ENV UV_PROJECT_ENVIRONMENT=/opt/local-harness/.venv
ENV PATH=/opt/local-harness/.venv/bin:$PATH
ENV PI_CODING_AGENT_DIR=/tmp/pi-config
ENV PI_OFFLINE=1

COPY pyproject.toml uv.lock README.md /opt/local-harness/
COPY harness /opt/local-harness/harness
WORKDIR /opt/local-harness
RUN uv python install 3.13.15 \
    && uv sync --frozen --all-groups \
    && test "$(node --version)" = "v24.21.0" \
    && uv --version | grep -q '^uv 0.12.7 ' \
    && test "$(python --version)" = "Python 3.13.15" \
    && test "$(PI_CODING_AGENT_DIR=/tmp/pi-build-check pi --version)" = "0.85.1"

COPY sandbox/pi/models.json /opt/pi-config-template/models.json
COPY sandbox/pi/settings.json /opt/pi-config-template/settings.json
COPY pi/extensions/telemetry.ts /opt/pi-config-template/extensions/telemetry.ts
WORKDIR /workspace
USER node:node
ENTRYPOINT ["python", "/opt/local-harness/harness/sandbox_entrypoint.py"]
CMD ["--help"]
