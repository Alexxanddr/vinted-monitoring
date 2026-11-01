# Vinted Monitoring

A single-user, single-process service: anonymous Italy/EUR Vinted search through `vinted-api-kit`, persistent SQLite watches, direct Telegram notifications and MCP over stdio or Streamable HTTP. Polling, filtering, deduplication and delivery do not use an LLM. Hermes can disconnect without stopping a separately running HTTP service.

**Status: experimental, limited live validation completed on 2026-11-01.** The `vinted-api-kit==1.3.1` path returned 50 validated results in a direct search. Live checks also covered price bounds, all four order parameters, page 2, authenticated HTTP MCP (including inside the container), and a narrow silent baseline followed by a silent rescan and SQLite reopen. Telegram delivery has been tested with mocks only; long-term operation and exhaustive upstream coverage are not established. See the [verification record](docs/verification.md).

The SDK retains its standard curl-cffi transport, including its default Chrome impersonation. The service adds no fingerprint rotation, CAPTCHA handling, purchase, seller contact or account modification, and stops on access denial.

## Quick start

Requires Python 3.12 (tested), Linux or macOS with local POSIX file locking. Windows is not supported. No Vinted credentials are required or accepted. Create your own Telegram bot and destination; both stay in administrator configuration. This service only calls `sendMessage`, never receives Telegram updates. No real Telegram message is sent by tests.

```sh
python3.12 -m venv .venv
. .venv/bin/activate
pip install --require-hashes -r requirements.txt
pip install --no-deps .
cp .env.example .env
# Edit .env; replace the HTTP placeholder and set both Telegram variables.
set -a; . ./.env; set +a
export VM_TRANSPORT=http
vinted-monitoring
```

The example `.env` uses shell-compatible assignments. Docker Compose reads it directly. The process defaults to stdio if `VM_TRANSPORT` is absent. Without Telegram configuration immediate search works, but MCP watch creation is rejected. Starting the service with an existing populated database enables delivery: do not attach real Telegram credentials during an unauthorized test.

```sh
cp .env.example .env
# Configure local secrets, then:
docker compose up --build -d
docker compose logs --tail=50
curl -fsS http://127.0.0.1:8000/health/ready
```

Compose publishes only loopback and keeps SQLite in `monitor-data`. Never run `docker compose down -v` unless intentionally deleting all state. The container runs as UID 10001. Only one process may open a state directory; do not start a stdio copy against an HTTP service's database.

## Direct local SDK check

This opt-in command uses the same `vinted-api-kit` adapter as the service, without MCP, SQLite or Telegram. It searches `thinkcentre`, page 1, newest first, up to 50 results, without a price filter:

```sh
uv sync --frozen --extra dev --no-editable --reinstall-package vinted-monitoring
uv run --no-editable python scripts/live_check.py --allow-live
```

It prints validated results and exits with status 0 on success, 1 on failure. No `.env` file or Telegram credentials are needed. Run only when authorized for live access; stop after an access denial. This command is excluded from CI.

## Tools and examples

All HTTP MCP operations require the administrator bearer token. Tool arguments never contain Telegram credentials or destinations. All management operations are intended for the single trusted installer.

| Tool | Purpose |
| --- | --- |
| `search_items(query, limit=10, page=1)` | Immediate one-page search, no persistent state or notification |
| `create_watch(name, query, interval_seconds=600)` | Independent polling; initial complete scan is silent |
| `list_watches()` | Queries, baseline, scheduling, freshness and scan errors |
| `update_watch(watch_id, query?, interval_seconds?, name?)` | Entire query replacement; changed filters trigger a new silent baseline |
| `pause_watch(watch_id)` / `resume_watch(watch_id)` | Pause/resume scanning, retain seen IDs |
| `delete_watch(watch_id)` | Remove watch; retain global delivery history and queued messages |
| `monitoring_status()` | Scan status, blocked state and pending delivery count |

“Show the 10 newest ThinkCentre i5 under €150” → `search_items`:

```json
{"query":{"text":"ThinkCentre i5","marketplace":"IT","max_price":"150","currency":"EUR","order":"newest_first"},"limit":10,"page":1}
```

“Monitor ThinkCentre i5 under €150” → `create_watch`:

```json
{"name":"ThinkCentre i5","query":{"text":"ThinkCentre i5","max_price":"150","order":"newest_first"},"interval_seconds":600}
```

“Pause watch 1” → `pause_watch({"watch_id":1})`. “Change its maximum to €120” → `update_watch` with `watch_id=1` and the **whole** new query. “Resume” → `resume_watch`; it retains the baseline and reports limited recovery. MCP descriptions and Pydantic schemas are the executable contract.

Newest is a sort order, **not a time filter**. No listing timestamp is trusted, no “last 30 minutes” filter exists, and price drops never trigger alerts. Other immediate orders: `relevance`, `price_low_to_high`, `price_high_to_low`. Watches require `newest_first`. Text, marketplace, min/max price, EUR, order and pagination are the entire supported filter surface; unknown fields are rejected.

## Documentation

- [Configuration and MCP integration](docs/configuration.md)
- [State, delivery, coverage and operations](docs/operations.md)
- [Security, terms and troubleshooting](SECURITY.md)
- [Dependency investigation](docs/research.md) and [verification record](docs/verification.md)
- [Generic Kubernetes examples](examples/kubernetes/README.md)
- [Distributable Hermes skill](skills/vinted-monitoring/SKILL.md)
- [Contributing and checks](CONTRIBUTING.md), [changes](CHANGELOG.md)

MIT is the proposed default license for this previously empty repository; see [LICENSE](LICENSE). The MIT-licensed `vinted-api-kit` is installed as a pinned dependency; no upstream client code is vendored. Dependency licenses remain their own.
