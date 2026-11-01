# Configuration and MCP

All settings use environment variables with `VM_` prefix. Secrets are not loaded from `.env` automatically; Compose loads it, or the shell/operator injects variables. Unknown tool query fields fail validation. Environment is parsed using Pydantic Settings; unrelated variables are ignored.

| Variable | Default | Validation / meaning |
| --- | --- | --- |
| `VM_DATABASE` | `data/monitor.sqlite3` | SQLite path, local filesystem; parent created |
| `VM_TRANSPORT` | `stdio` | `stdio` or `http` |
| `VM_HOST` | `127.0.0.1` | HTTP bind; container uses `0.0.0.0` |
| `VM_PORT` | `8000` | 1–65535 |
| `VM_ALLOWED_HOSTS` | `["127.0.0.1:*","localhost:*","vinted-monitoring:8000"]` | JSON array of permitted HTTP Host values; exact hosts or `:*` port suffix |
| `VM_HTTP_TOKEN` | absent | Required for HTTP, minimum 32 printable ASCII characters without whitespace; generate a random secret |
| `VM_TELEGRAM_TOKEN` | absent | Bot token in numeric-ID:token format; configure together with nonempty chat ID |
| `VM_TELEGRAM_CHAT_ID` | absent | Administrator destination, never exposed by tools |
| `VM_PAGE_SIZE` | `50` | 1–96 |
| `VM_MAX_PAGES` | `5` | 1–20 per scan, plus one first-page consistency check |
| `VM_REQUEST_TIMEOUT` | `15` | HTTP timeout in seconds, 1–60 |
| `VM_REQUEST_ATTEMPTS` | `3` | 1–5 Vinted attempts for network/5xx failures only |
| `VM_SCAN_TIMEOUT` | `300` | Entire scan deadline in seconds, 5–1800 |
| `VM_TICK_SECONDS` | `5` | Scheduler sleep, 0.1–60 seconds |
| `VM_FAILURE_ALERT_SECONDS` | `3600` | One error log per prolonged failure episode; minimum 60 |

Query text is 1–200 characters including at least one non-whitespace character. Prices are finite decimals, 0–1,000,000, maximum two decimal places, minimum <= maximum. Only IT/EUR is accepted. Watch names are 1–100 characters, intervals 60–604800 seconds. Immediate search limits 1–96 and pages 1–100. These bounds describe requests, not guaranteed upstream availability.

## stdio

Example client configuration (replace executable with an installed absolute executable path if required by your host):

```json
{"mcpServers":{"vinted-monitoring":{"command":"vinted-monitoring","env":{"VM_TRANSPORT":"stdio","VM_DATABASE":"data/monitor.sqlite3"}}}}
```

Inject Telegram settings using the client's private environment if watches are wanted. Stdio starts **the entire service**, including polling, and owns its database until the client closes stdin. For reliable unattended monitoring use HTTP under a supervisor instead. Never share the same database across separate stdio and HTTP owners; the second process fails its lock before opening SQLite. Distinct databases can duplicate alerts and are not a redundancy strategy.

## Streamable HTTP and MCPHub

The endpoint is `/mcp`. Example private MCPHub entry based on its `mcpServers` format:

```json
{
  "mcpServers": {
    "vinted-monitoring": {
      "type": "streamable-http",
      "url": "http://vinted-monitoring:8000/mcp",
      "headers": {
        "Authorization": "Bearer REPLACE_WITH_PRIVATE_VM_HTTP_TOKEN"
      }
    }
  }
}
```

This is a template, not environment interpolation: replace the marker through MCPHub's private configuration mechanism. Do not publish the populated entry. Keep VM_HTTP_TOKEN synchronized with that header, add the actual service Host to VM_ALLOWED_HOSTS, and check the installed MCPHub version's server configuration schema before editing it. See [upstream configuration](https://github.com/samanhappy/mcphub) and [server fields](https://github.com/samanhappy/mcphub/blob/main/docs/api-reference/servers.mdx).

Bind loopback for local use. Remote bearer tokens require TLS termination on trusted infrastructure and network restrictions. In-cluster plaintext HTTP is only appropriate within an explicitly trusted network. Browser Origin headers are rejected. Every MCP route, including discovery and management, requires the same token; health routes do not. There is no OAuth authorization server and no anonymous search route.

Verification after registration:

1. Read the configured MCPHub server entry (without printing secrets) and check connection status.
2. Invoke MCP `initialize` with a protocol version negotiated by the SDK, then `tools/list` through the configured connection; require all eight tools from the README.
3. Through Hermes ask to list watches and monitoring status. These are offline operations and do not send Telegram messages.
4. Only with explicit live authorization invoke a narrow `search_items`. Do not create a watch as a connection test.
5. A concrete Telegram test requires explicit authorization for that destination and message. Use a separate test database; establish its silent baseline, then introduce a new synthetic ID using a mock upstream. Never rely on a random new live listing as a delivery test.

Install the distributable skill only during an authorized rollout. It teaches tool selection, baseline and coverage, not credentials. MCPHub's actual exposed catalog is authoritative; a README entry does not establish installation.
