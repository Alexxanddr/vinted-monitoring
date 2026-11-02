# Verification record

Development checks on 2026-11-01; these are observations, not promises about a future upstream response.

- Python 3.12 offline suite: 39 passing tests. Coverage includes silent/atomic baseline and transaction rollback, restart persistence, overlapping watches, filter edits, same/new IDs, duplicate/moving pages, exhausted recovery budget, explicit empty versus malformed/error envelopes, strict prices/URLs, real vinted-api-kit search/parameter mapping and pre-retry HEAD/GET guards, 401/403 suspension, 429/cooldown and timeout retries, Telegram failures, before/after-send crash ambiguity, process/async locking, migration, stdio round trip, HTTP authentication and MCP validation.
- An end-to-end test exercises HTTP MCP, the real Vinted SDK, background polling, two overlapping watches, mocked Telegram delivery, edits and restart on the same SQLite database. Only outbound network responses are mocked; the production scheduler and persistence are used.
- Pre-publication `pip-audit` of the locked runtime requirements reported no known vulnerabilities at check time; this is an advisory database check, not a security audit of the application.
- Ruff lint and formatting passed. `uv.lock` validated offline; exported hashed runtime requirements match the lock.
- Docker image built and ran as UID 10001 on Linux arm64. The smoke script uses `--network none`, read-only root and a fresh temporary named volume. It verified readiness, discovery of all eight tools, shutdown/restart and persisted SQLite watch/suspension state. It removed its containers and volume afterward.
- Compose configuration parsed using placeholder values only. Generic Kubernetes kustomization rendered locally; no cluster was contacted, and no server-side Kubernetes admission/storage behavior was tested.
- Live SDK search: `thinkcentre`, newest-first, page 1, limit 50 returned **50 validated items**. A stale non-editable local package was detected before any request and rebuilt; local source-check instructions now force reinstall before running.
- Live price filters/orders: `thinkcentre i5`, minimum EUR 10 and maximum EUR 150 returned 10 validated items for each of newest_first, relevance, price_low_to_high and price_high_to_low. All returned prices respected the bounds; ascending/descending price sequences matched those two order parameters in this sample. Newest/relevance ranking was accepted upstream but cannot be independently proven without reliable listing timestamps/relevance scores.
- Live pagination: page 2 returned 10 distinct IDs. This confirms that a second page can be requested; it does not prove snapshot consistency or exhaustive coverage across pages.
- Live HTTP MCP: authenticated `search_items` returned 10 items; the unauthenticated endpoint rejected requests. Watch create, pause, resume, query update/baseline reset and delete succeeded against temporary state.
- Live baseline/recovery: a narrow `ThinkCentre M710s SFF i5` query between EUR 10 and EUR 90 established a complete baseline containing one observed ID, with zero Telegram requests. A second live scan remained silent. Closing/reopening SQLite preserved the watch and zero backlog. Scans were manually invoked through the production engine; the autonomous background scheduler is additionally exercised by the offline end-to-end test.
- Live container: the packaged HTTP server, running as UID 10001, returned 10 items for `thinkcentre i5` under EUR 150 through its actual localhost HTTP MCP endpoint. Every returned price respected the upper bound. The temporary container had no Telegram credentials and was removed with its volume after verification.
- Earlier history: a previous direct-httpx implementation encountered a schema/filter mismatch and a diagnostic 403. After explicit reauthorization to retry using the requested library, the SDK-backed live checks above succeeded with no new access denial. Those historical failures are not the current SDK integration result. No bypass, proxy rotation, account login, purchase or seller contact occurred.
- Telegram: mocked HTTP only. No real bot message was sent; destination permissions and real Telegram delivery remain unverified.
- GHCR publication: native Linux amd64 and arm64 builds each passed the MCP/persistence smoke check before publication. The multi-platform latest/commit tags were published successfully by [GitHub Actions](https://github.com/Alexxanddr/vinted-monitoring/actions/runs/41357295747). Anonymous manifest retrieval and an anonymous arm64 pull succeeded with an empty Docker configuration; the downloaded artifact also passed the offline container smoke check.
- Remote CI status is available in the repository's GitHub Actions checks. No release, cluster rollout, MCPHub registration or installed Hermes skill update is included. MIT is supplied as the default license for the previously empty project.

See [operations](operations.md) for the coverage and delivery limits and [configuration](configuration.md) for the concrete post-installation discovery procedure. The standalone live check is opt-in and excluded from ordinary CI.

## Pagination regression check — 2026-11-02

A production watch had no successful baseline because the collector rejected an ID
repeated across pages. The original behavior was reproduced by a failing test.
Live read-only sampling of the same query found 50/50/50/48 rows, one repeated
ID, and 197 unique listings. A first-page recheck may change order/membership
while containing only IDs already collected from the complete page walk.

The candidate collector completed this real scan without opening the service
database or sending Telegram messages. Regression tests cover silent atomic
baseline completion with overlap, within-page duplicates, covered-ID reshuffling,
new recheck IDs, stalled full pages and raw-page budget exhaustion.
