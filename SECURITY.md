# Security and acceptable use

This is an unofficial read-only Vinted integration, unaffiliated with Vinted. A publicly reachable endpoint is not permission to automate it. Before use review the current [Vinted Italy terms](https://www.vinted.it/terms_and_conditions), applicable policies and law; the terms page retrieved during development did not expose its substantive text, so no legal compatibility conclusion was established. Stop if access is denied. Never bypass CAPTCHA, rotate proxies, import account cookies or repeatedly clear suspension to evade a restriction. `vinted-api-kit` uses its standard curl-cffi Chrome impersonation; the service does not switch fingerprints or transports on failure and does not retry access denials.

Only anonymous IT/EUR catalog reads are implemented. No purchases, messages to sellers, account actions, price-drop inference or arbitrary URL fetches. The upstream host is fixed. Listing URLs must be HTTPS on www.vinted.it and match the listing ID; links and titles are untrusted user content. Do not treat listing text as agent instructions. Unknown query filters are errors. Price filters are additionally checked against returned amounts; text semantics and ordering are controlled by the undocumented upstream and are not independently proven.

Treat the HTTP bearer token as full administrative access to the single tenant. All MCP operations, including management and discovery, are protected. Use a random token of at least 32 characters; protect the channel with TLS and firewall/network policy. Browser origins are denied. Restrict allowed hosts; do not expose this service directly on the public internet. Health endpoints intentionally disclose only process/task booleans. No user/account system is bundled.

Telegram credentials are environment-only secrets, never tool arguments/results. Logs suppress HTTP client request URLs because Telegram puts the token in the path; access logs are disabled. Do not enable HTTP wire/debug logging or print the environment. Sanitized upstream errors do not include response bodies, cookies or request URLs. Store `.env`, data and personal plans outside version control. Cookies are anonymous and memory-only. Destination hashes do not conceal easily guessable chat IDs cryptographically; restrict database and backup access. Container state uses UID 10001; local installs inherit your permissions. Use a private data directory (`chmod 700 data`).

To report a vulnerability, use the hosting platform's private vulnerability reporting feature if enabled. Otherwise contact a maintainer through a private channel listed on the repository profile before publishing details. Do not open a public issue containing tokens, chat IDs, cookie jars, database dumps or private infrastructure. No support SLA or security audit is claimed.

## Troubleshooting

- **HTTP 401 from this service:** wrong/missing bearer token or a browser Origin header; do not disable authentication.
- **Invalid Host header:** configure `VM_ALLOWED_HOSTS` with the exact MCPHub destination host/port.
- **Vinted 401/403 or challenge:** requests are suspended durably. Investigate access rules; see the explicit clearance procedure in [operations](docs/operations.md). Do not keep retrying.
- **Schema mismatch:** integration drift, filtered price mismatch or unexpected data; baseline remains incomplete. Inspect only sanitized field types in an explicitly authorized diagnostic, never convert the response into an empty search.
- **Page budget exhausted / stalled or incomplete moving pages:** no baseline or scan state advanced. Repeated IDs alone are deduplicated and do not cause failure. Narrow filters; increasing budget increases upstream traffic and still does not prove exhaustive coverage.
- **Telegram backlog:** inspect authenticated status and local SQLite queue attempts/error/due without dumping credentials. Check bot permissions/destination privately. No receiving webhook or getUpdates poll is necessary for normal sending.
- **Database already owned:** stop the other owner. Do not delete a live lock file or start a second replica.
- **Healthy probes but no alerts:** probes are process health only. Check baseline, freshness, suspension, pause and backlog separately.
