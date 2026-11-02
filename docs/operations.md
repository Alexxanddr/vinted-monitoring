# State and operations

## Scan contract

IDs, not timestamps or maximum ID, identify listings. Every watch has its own ID set; notifications have a global `(item_id, destination_hash)` unique key. Titles, images and prices are never heuristic identities. Same ID at a lower price stays silent; a new ID is a new listing even if its title/image is identical.

A scan walks newest-first pages until an explicit short page (including an explicit empty list). All required item identity, price, currency and URL fields are validated. A missing/non-list `items` field is an error. Repeated IDs within/across pages are deduplicated. A full page that adds no new IDs rejects the scan as stalled pagination. After reaching a short page, the first page is fetched again: its ordering/membership may change provided every rechecked ID was already collected; previously unseen IDs reject the scan. Full page budget, timeout, block, schema or filter mismatch also reject it. Seen IDs, baseline and notification enqueue commit atomically only after successful collection. The first complete scan silently seeds global suppression history and watch IDs; it never produces a backlog flood. A baseline larger than the page budget cannot finish: narrow its filters or deliberately raise the bounded budget.

Defaults: 50 items × 5 pages plus one first-page consistency recheck, maximum 6 catalog calls per watch per scan, plus the SDK's anonymous homepage HEAD before each catalog call (at most 12 HTTP requests without retries). At most 250 returned IDs can be inspected, but a full fifth page is incomplete; successful coverage must end before/at a short fifth page. Requests may retry on transient network/5xx failures (up to 3 attempts, waits 1s and 2s). The whole scan has a 300s deadline. Request timeout is applied to each SDK HEAD/GET; the scan deadline is the total limit. Multiple watches scan serially, so large workloads delay later watches; there is no fixed-rate execution guarantee.

This is **bounded best effort**, not a stable snapshot or an archive. The undocumented endpoint supplies no verified total count/cursor contract. A short page is a heuristic for the end of the available result window; source truncation or moving middle pages can still hide IDs. The first-page recheck rejects newly observed IDs outside the collected set, but cannot detect every middle-page gap. Listings that appear and disappear between successful scans cannot be recovered. A long interruption is recoverable only for IDs still visible inside a complete bounded result window. An exhausted budget leaves the previous successful timestamp untouched and exposes an error; it does not silently rebaseline or claim the gap was recovered. No automatic time-window claim is made.

Query changes reset the watch baseline and its ID associations. Name/interval changes do not. Global suppression/delivery history remains, including across delete/recreate. Previously queued messages survive query changes, pause and deletion; these operations stop/change scans, not already committed delivery obligations. Baseline IDs globally suppress later first-time matches at the same destination. Overlapping active watches attach names to the same notification; all due scans finish before delivery, so their names are normally grouped. A later match after delivery records the association but does not send a second message. If a newly baselined watch sees an item before another watch first discovers it, baseline suppression wins.

## Queue and Telegram

Notifications persist in SQLite, including retry due time, attempts and sanitized error. Delivery is text only: title, decimal price/currency, canonical Vinted URL and matching watch names. Long titles/names are bounded to stay under Telegram's message limit; HTML/Markdown interpretation and link previews are disabled. No photos are implemented. At most one message per 1.1 seconds is sent; a failed attempt stops that delivery batch. Telegram 429 uses its `parameters.retry_after` and HTTP Retry-After; other failures wait at least 60 seconds with increasing delay up to an hour. A per-destination service cooldown is persisted after failures so later queue rows cannot bypass Retry-After. Failed rows remain pending; there is no dead-letter deletion or automatic data pruning.

Only successful HTTP 200 plus Telegram `ok: true` and a result object marks delivery. A crash before send leaves the row pending. A crash after send but before SQLite commits can cause a duplicate: **at-least-once, never exactly-once**. A crash after commit does not resend. A timeout after Telegram accepted a message has the same ambiguity. Missing/wrong credentials cause backlog; destination changes create a new deduplication scope, and old-destination pending rows remain stored but are not delivered to the new destination. Switching back resumes those rows.

## Blocking and failure visibility

401/403 or catalog HTML challenges persist a Vinted suspension flag before returning an error. A transport guard stops before the SDK can refresh/retry a block. Its standard curl-cffi/Chrome transport is preserved; no alternate fingerprints, proxy fallback or challenge-solving behavior is added. Restarting does not clear suspension. After investigating access rules, stop the owner and explicitly run:

```sh
VM_DATABASE=data/monitor.sqlite3 vinted-monitoring --clear-vinted-block
```

This command clears the flag only and makes no request. It is not a CAPTCHA workaround; do not clear repeatedly to probe a denial. Without Telegram configured the scheduler leaves existing watches unchanged; only immediate search is available. Cookies exist only in process memory, shared across requests; no authenticated cookies are accepted or persisted. With cookie persistence disabled, SDK 1.3.1 refreshes anonymous cookies with a homepage HEAD before each catalog request, including retry attempts. Vinted 429 defers requests for at least 60 seconds or Retry-After, whichever is longer; scheduler failures use interval-based exponential backoff capped at one hour. Vinted and Telegram cooldown deadlines persist across restarts; do not restart to evade rate limits.

`/health/live` only reports that the HTTP event loop answers. `/health/ready` reports that the polling task is still running; it does **not** mean Vinted or Telegram works. Neither endpoint reveals queries, IDs, tokens, destinations, database paths or errors. Authenticated `monitoring_status` and `list_watches` expose backlog count, next delivery due time, pending error count and maximum attempts, per-watch baseline, last success and errors. Alert on stale last success, pending baseline, nonzero/growing backlog and Vinted suspension separately from process probes. First scan/delivery failures log once, and prolonged scan/delivery failures log once per episode (durable flag, reset on recovery); logs are the independent alert channel and must be collected by the supervisor. Delivery failure details and due/attempt counters are stored in the queue. Logs are not a Telegram fallback and no external alerting provider is bundled.

## Concurrency, shutdown and storage

One POSIX advisory lock is held next to the database for the process lifetime. An asyncio lock serializes tools and complete scan/delivery cycles. Manual invocation of `tick` uses that same lock. Do not bypass these interfaces or copy the live database to create another owner. Lock semantics are tested on local filesystems only; NFS/shared filesystem locking is not supported. Keep one replica with Recreate strategy and an RWO volume. On SIGTERM HTTP shuts down and pending polling is cancelled; an unfinished scan has not committed. Interrupted delivery has the at-least-once ambiguity above. Do not forcibly remove a live lock file: advisory ownership is released when the process exits.

Schema versions use SQLite `user_version`; migrations run transactionally at startup (v1 core tables, v2 durable suspension metadata). Newer schemas fail closed. WAL with synchronous FULL is used. Database rows and seen history grow indefinitely: size/backup monitoring is required; no arbitrary retention window is used because pruning deduplication could re-notify old IDs.

## Backup, restore and upgrade

Stop the service, then copy the entire data directory (database and any WAL/SHM files); do not copy only a running main SQLite file. For online backups use SQLite's backup API from an authorized maintenance process without starting a second service owner. Protect backups like the original state: they contain private searches, listing metadata and destination hashes (not Telegram tokens).

Restore with service stopped, preserving owner UID 10001 for containers. Start a single owner on the restored volume. A stale backup can repeat notifications delivered after that backup. Never restore one backup into two active replicas. Before upgrading, stop and back up, review CHANGELOG and run offline tests, rebuild the image, then start once. Downgrade only with a compatible pre-upgrade backup; do not edit `user_version` manually. Do not delete the volume to resolve a migration error.

Build and optional publication (replace image name; no registry account is assumed):

```sh
docker build -t vinted-monitoring:0.1.1 .
# After choosing a registry and obtaining its credentials separately:
docker tag vinted-monitoring:0.1.1 REGISTRY/OWNER/vinted-monitoring:0.1.1
docker push REGISTRY/OWNER/vinted-monitoring:0.1.1
```

GitHub Actions publishes `ghcr.io/alexxanddr/vinted-monitoring:latest` and `sha-<full-commit>` after successful checks on `master`. Linux amd64 and arm64 images are built and smoke-tested on native runners, then combined under those multi-platform tags. Pull requests never publish. Forks derive their registry path from their own GitHub repository, need Packages write permission, and must configure package visibility separately before anonymous pulls work. No registry credentials are required to pull the public upstream image. See [GitHub Container registry documentation](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

Never bake `.env`, state or personal rollout plans into an image. No platforms beyond Linux amd64/arm64 are published.
