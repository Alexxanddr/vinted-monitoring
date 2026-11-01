# Contributing

Keep the service single-process, single-tenant and deterministic. Do not add an LLM to scans or delivery. Public files must not contain personal paths, private hosts, chat IDs, credentials or cluster details. Documentation and code comments are English.

Python 3.12 and Docker are the tested development stack:

```sh
uv sync --frozen --extra dev
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
uv export --frozen --no-dev --no-emit-project --no-editable --no-header --output-file requirements.txt >/dev/null
docker build -t vinted-monitoring:test .
python3 scripts/container_check.py
```

Update `uv.lock` when changing dependencies and regenerate the hashed requirements file. The MCP SDK is deliberately constrained to the inspected 1.x line. `vinted-api-kit` is pinned to 1.3.1 because its private transport seam is guarded to disable authentication retries and validate response envelopes. Tests mock only that seam and exercise the real public SDK search path; do not replace them with mocks of `search_items`. Build backend downloads are separate from the locked runtime dependencies. No dependency license ownership is transferred; inspect license metadata when updating. Current runtime dependencies use permissive licenses (including the SDK MIT, httpx BSD-3-Clause and transitive cryptography Apache-2.0/BSD-3-Clause); retain notices when redistributing dependency artifacts.

Tests use synthetic listings, mocked HTTP and a fake destination; no Telegram messages are sent. Container checks disable external networking, use a temporary volume and remove their own containers/volume. CI must not have real Telegram or Vinted credentials. Live checks are never part of CI:

```sh
# Only after explicit permission for this concrete live read:
uv run python scripts/live_check.py --allow-live
```

The script searches `thinkcentre` with newest-first ordering, page 1 and limit 50, matching the standalone SDK example. It makes at most a homepage initialization and one catalog request, with no retries, and returns a nonzero exit code on failure. Stop the session's live tests after any access denial; the standalone diagnostic does not open the service database. A strict schema error or block is a failed integration check, not a successful empty search.
