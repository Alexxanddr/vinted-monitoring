import asyncio
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlencode, urljoin

from vinted import VintedClient, VintedError
from vinted.exceptions import VintedNetworkError

from .models import Item, Query, Settings


class UpstreamError(Exception):
    """Sanitized error, safe for logs and MCP responses."""


class Blocked(UpstreamError):
    pass


class Incomplete(UpstreamError):
    pass


def retry_after(value: str | None) -> float:
    try:
        return max(0, float(value))
    except (TypeError, ValueError):
        try:
            return max(
                0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
            )
        except (TypeError, ValueError, OverflowError):
            return 0


class Vinted:
    def __init__(self, settings: Settings, client: VintedClient | None = None):
        self.settings = settings
        self.client = client if client is not None else VintedClient()
        self.blocked = False
        self.on_block = lambda: None
        self.on_cooldown = lambda until: None
        self.not_before = 0.0
        self.lock = asyncio.Lock()
        # shortcut: SDK 1.3.1 has no response hook; keep its private seam pinned and tested.
        session = self.client._session.session
        session.trust_env = False
        session.proxies = {"all": ""}
        original_request = session.request

        async def guarded_request(method, url, **kwargs):
            self.check_access()
            kwargs.update(timeout=self.settings.request_timeout, allow_redirects=False)
            response = await original_request(method=method, url=url, **kwargs)
            status = response.status_code
            if status in (401, 403):
                self.suspend(f"Vinted access blocked ({status}); no automatic retry")
            if status == 429:
                delay = max(60, retry_after(response.headers.get("retry-after")))
                self.not_before = time.time() + delay
                self.on_cooldown(self.not_before)
                raise UpstreamError("Vinted rate limited; respecting Retry-After")
            if status >= 500:
                raise VintedNetworkError("Vinted unavailable", RuntimeError("upstream 5xx"))
            if status != 200:
                raise UpstreamError(f"Vinted HTTP {status}")
            if method.upper() == "GET":
                if "text/html" in response.headers.get("content-type", ""):
                    self.suspend("Vinted returned HTML challenge; automatic requests suspended")
                # Validate before the SDK can turn a missing items field into an empty list.
                try:
                    data = response.json()
                    if (
                        not isinstance(data, dict)
                        or not isinstance(data.get("items"), list)
                        or data.get("error")
                        or data.get("errors")
                        or data.get("code", 0) != 0
                        or data.get("success") is False
                    ):
                        raise ValueError()
                except (ValueError, TypeError):
                    raise UpstreamError("Vinted response schema or filter mismatch") from None
            return response

        session.request = guarded_request

    def check_access(self):
        if self.blocked:
            raise Blocked("Vinted blocked; administrator clearance required after investigation")
        if time.time() < self.not_before:
            raise UpstreamError("Vinted Retry-After cooldown")

    def suspend(self, message):
        self.blocked = True
        self.on_block()
        raise Blocked(message)

    async def close(self):
        await self.client.close()

    async def page(self, query: Query, page: int, limit: int) -> list[Item]:
        params = {"search_text": query.text, "currency": query.currency}
        for name in ("min_price", "max_price"):
            value = getattr(query, name)
            if value is not None:
                params[{"min_price": "price_from", "max_price": "price_to"}[name]] = str(value)
        url = "https://www.vinted.it/catalog?" + urlencode(params)
        async with self.lock:
            for attempt in range(self.settings.request_attempts):
                self.check_access()
                try:
                    data_items = await self.client.search_items(
                        url=url,
                        per_page=limit,
                        page=page,
                        order=query.order,
                        raw_data=True,
                    )
                    break
                except VintedError as error:
                    # The SDK wraps response-hook errors during both HEAD refresh and GET.
                    cause = error
                    while isinstance(cause, VintedNetworkError):
                        cause = cause.original_error
                    if isinstance(cause, UpstreamError):
                        raise cause from None
                    if not isinstance(error, VintedNetworkError):
                        raise UpstreamError("Vinted client failure") from None
                    if attempt + 1 == self.settings.request_attempts:
                        raise UpstreamError(
                            "Vinted network timeout or service unavailable"
                        ) from None
                    await asyncio.sleep(2**attempt)
            try:
                if not isinstance(data_items, list):
                    raise ValueError()
                items = []
                for raw in data_items:
                    price = raw["price"]
                    item = Item(
                        id=str(raw["id"]),
                        title=raw["title"],
                        price=price["amount"],
                        currency=price["currency_code"],
                        url=urljoin("https://www.vinted.it/", raw["url"]),
                    )
                    if (query.min_price is not None and item.price < query.min_price) or (
                        query.max_price is not None and item.price > query.max_price
                    ):
                        raise ValueError()
                    items.append(item)
                if len(items) > limit:
                    raise ValueError()
                return items
            except (ValueError, KeyError, TypeError):
                raise UpstreamError("Vinted response schema or filter mismatch") from None
