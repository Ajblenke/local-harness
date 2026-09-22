## What changed

- [ ] Controller contract and fail-closed policy
- [ ] Runner-owned observations and registered checks
- [ ] Private telemetry store and loopback dashboard
- [ ] Transparent judge scorecard
- [ ] Internal-network Docker sandbox
- [ ] Durable session memory and maintenance skill

## Trust evidence

- [ ] `uv run ruff check .`
- [ ] `uv run pytest -q`
- [ ] Example contract returns `advance`
- [ ] Negative contract fixtures return `double_check` or `halt`
- [ ] Telemetry import is incremental and creates a mode-`0600` database
- [ ] Dashboard rejects non-loopback bind addresses
- [ ] Docker Compose validates, or the missing Docker dependency is recorded

## Human decisions

List score-policy changes, sandbox exceptions, schema migrations, and residual risk. Never include
transcripts, secrets, model artifacts, or state-database contents.
