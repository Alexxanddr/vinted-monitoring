# Vinted integration research

Checked 2026-11-01 against upstream source, release metadata and the installed 1.3.1 package. This dependency inspection sent no request to Vinted. These findings describe an unofficial client implementation, not a supported Vinted API contract.

## Decision

Use **`vinted-api-kit==1.3.1`** as requested. Search goes through its public `VintedClient.search_items(..., raw_data=True)` method, retaining its URL parameter mapping, cookie initialization and standard curl-cffi transport. `httpx` is used only for Telegram, not Vinted.

The SDK has no public response hook or configurable authentication-retry policy. A narrowly scoped guard on its pinned underlying session validates the response before the SDK's permissive parser runs, sets a timeout, disables redirects/environment proxies and stops before its internal 401/403 refresh/retry branch. The guard neither constructs catalog requests nor substitutes another HTTP client. Offline regression tests exercise the real SDK search path and assert no second HEAD/GET occurs after denial. Review this private seam before upgrading the pinned dependency.

Keep anonymous cookies in memory with the SDK's default non-persistent storage. Consequently the SDK performs a homepage HEAD before each catalog call; this overhead is documented rather than hidden behind an additional cookie-storage implementation. No caller-supplied Vinted account cookies or proxies are accepted.

## Package and revision

The current PyPI release is **1.3.1**, uploaded **2026-10-21**. Its verified publishing commit is **`a33a90eb70fe2356e8debec81d995e2642543b97`**. This identifies the released artifact; the latest branch HEAD was not independently verified. PyPI lists wheel SHA-256 `cf764c372b744f03405df23f8395f80eef354ce9a707f6d63d0248ffaa1a3398`. [PyPI release and provenance](https://pypi.org/project/vinted-api-kit/1.3.1/)

Upstream metadata requires Python **>=3.10** and the single direct runtime dependency **`curl-cffi>=0.12.0,<0.17.0`**. The build backend is Hatchling. The license is MIT; retain the copyright and permission notice if copying substantial code. [Package metadata](https://github.com/vlymar1/vinted-api-kit/blob/main/pyproject.toml), [license](https://github.com/vlymar1/vinted-api-kit/blob/main/LICENSE)

## Public surface

`from vinted import VintedClient` exposes an asynchronous context manager. `search_items(url, per_page=20, page=1, timestamp=None, order=None, raw_data=False)` returns catalog models or raw item dictionaries. Seller profile/listing methods also exist; item-details lookup is deprecated. Cookie persistence is optional and disabled by default, with JSON, pickle, and Mozilla formats. [Client implementation](https://github.com/vlymar1/vinted-api-kit/blob/main/vinted/client.py)

## Catalog request shape

The source constructs `GET https://api.vinted.<market>/svc-catalogue/items`; `.com` is exceptional: `https://api.www.vinted.com/svc-catalogue/items`. It translates these public catalog query fields:

| Public URL field | Gateway query field |
| --- | --- |
| `search_text` | `search_text` |
| `/catalog/<id>-...` or `catalog[]` | `attribute_ids[catalog]` |
| `brand_ids[]`, `size_ids[]`, `color_ids[]` | `attribute_ids[brand]`, `attribute_ids[size]`, `attribute_ids[color]` |
| `material_ids[]`, `patterns_ids[]` | `attribute_ids[material]`, `attribute_ids[patterns]` |
| `status_ids[]` and `status[]` | `attribute_ids[status]` |
| `price_from`, `price_to`, `currency`, `order` | same names |

Repeated filter IDs become comma-separated values. `page`, `per_page`, and a `time` epoch parameter are supplied separately. Empty values are dropped. The library reads only the top-level `items` field and discards any pagination metadata; no response pagination schema was verified. A bounded page scan must therefore state its limit and must not imply a complete market snapshot. [Catalog source](https://github.com/vlymar1/vinted-api-kit/blob/main/vinted/api/catalog.py)

The session derives `Accept-Language`, `locale`, and `Referer` from the market URL. Default headers include `X-Money-Object: true`. [Session configuration](https://github.com/vlymar1/vinted-api-kit/blob/main/vinted/session.py), [header constants](https://github.com/vlymar1/vinted-api-kit/blob/main/vinted/constants.py)

## Compatibility and failure behavior

The October 2026 changelog reports removal of `/api/v2/catalog/items`, migration to `svc-catalogue/items`, and a subsequent move from `/web/gateway` to the `api.` host. It also reports unsupported country/city/disposal filters and removed item timestamps. These are upstream observations, not independently verified live behavior. [Changelog](https://github.com/vlymar1/vinted-api-kit/blob/main/CHANGELOG.md)

The catalog implementation uses `data.get("items", [])`: a JSON error object without `items` can look like a successful empty result. The models similarly default absent identity and monetary fields; brand/size/condition are extracted from localized display text and prices are converted to floats. This can conceal a changed response schema. [Catalog parsing](https://github.com/vlymar1/vinted-api-kit/blob/main/vinted/api/catalog.py), [item models](https://github.com/vlymar1/vinted-api-kit/blob/main/vinted/models/item.py)

The session uses Chrome impersonation. Before a request it loads stored cookies or refreshes them using a homepage HEAD; without persistence it refreshes on each request. A 401 or 403 triggers cookie refresh and one repeated GET before raising an authentication error. HTTP 429 raises a separate rate-limit exception. Those internal requests conflict with an application policy of stopping immediately on access denial. [Session request implementation](https://github.com/vlymar1/vinted-api-kit/blob/main/vinted/session.py)

The application validates the JSON object, require an explicit list-valued `items`, require valid item IDs and monetary values, and report malformed payloads as failures. It uses decimal prices and locally observed timestamps, allows only Italy, bounds timeouts/pages and serializes requests. These are project design choices derived from the failure modes above.

## MCPHub transport

MCPHub supports `streamable-http` upstream entries using `type`, `url`, and optional `headers`. Its settings file contains an `mcpServers` mapping; after initial startup the dashboard or settings-file update plus restart manages entries. Keep credentials outside public examples and verify against the installed MCPHub version before changing the authoritative configuration. [MCPHub configuration](https://github.com/samanhappy/mcphub), [server API fields](https://github.com/samanhappy/mcphub/blob/main/docs/api-reference/servers.mdx)

## Limits

The SDK-backed integration now has limited live validation: direct search, price filters, all order parameters, page 2, authenticated MCP, a narrow silent baseline/rescan and the container HTTP path succeeded on 2026-11-01 after explicit reauthorization. This is not proof of long-term availability, authoritative item timestamps or exhaustive coverage. Telegram delivery remains mocked only. See [verification](verification.md) for observations and limits. Public source pages can lag the repository; the release commit above is established by PyPI provenance, and the installed package's session/catalog source was inspected directly.
