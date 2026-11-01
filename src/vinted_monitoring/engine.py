import asyncio
import hashlib
import logging
import time

import httpx

from .models import Item, Query, Settings
from .store import Store
from .upstream import Incomplete, UpstreamError, retry_after

log = logging.getLogger(__name__)


class Engine:
    def __init__(self, store: Store, vinted, settings: Settings, client: httpx.AsyncClient):
        self.store, self.vinted, self.settings, self.client = store, vinted, settings, client
        self.lock = asyncio.Lock()
        self.destination = hashlib.sha256(
            (
                settings.telegram_chat_id.get_secret_value()
                if settings.telegram_chat_id
                else "unconfigured"
            ).encode()
        ).hexdigest()

    async def collect(self, query):
        items = {}
        first_ids = None
        # Scan to an actual short page. Never stop at one known/max ID: ordering can move.
        for page in range(1, self.settings.max_pages + 1):
            batch = await self.vinted.page(query, page, self.settings.page_size)
            ids = [item.id for item in batch]
            if len(ids) != len(set(ids)) or set(ids) & items.keys():
                raise Incomplete("moving pagination: duplicate IDs; no state advanced")
            if first_ids is None:
                first_ids = ids
            for item in batch:
                items[item.id] = item
            if len(batch) < self.settings.page_size:
                if page > 1:
                    check = await self.vinted.page(query, 1, self.settings.page_size)
                    if [item.id for item in check] != first_ids:
                        raise Incomplete("moving first page; no state advanced")
                return list(items.values())
        raise Incomplete("page budget exhausted; no state advanced; narrow the search")

    async def scan(self, watch):
        try:
            async with asyncio.timeout(self.settings.scan_timeout):
                items = await self.collect(Query.model_validate_json(watch["query"]))
            self.store.commit_scan(watch, items, self.destination)
        except (UpstreamError, TimeoutError) as error:
            message = str(error) if isinstance(error, UpstreamError) else "scan deadline exceeded"
            failures = watch["failures"] + 1
            with self.store.db:
                self.store.db.execute(
                    """UPDATE watches SET error=?,failures=?,
                    failure_since=COALESCE(failure_since,?),due=? WHERE id=?""",
                    (
                        message,
                        failures,
                        time.time(),
                        time.time() + min(3600, watch["interval"] * 2 ** min(failures - 1, 4)),
                        watch["id"],
                    ),
                )
            if failures == 1:
                log.warning("watch %s scan failed: %s", watch["id"], message)

    async def telegram(self, text):
        token = self.settings.telegram_token.get_secret_value()
        try:
            response = await self.client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": self.settings.telegram_chat_id.get_secret_value(),
                    "text": text[:4000],
                    "link_preview_options": {"is_disabled": True},
                },
                timeout=self.settings.request_timeout,
            )
            if response.status_code == 429:
                try:
                    delay = float(response.json()["parameters"]["retry_after"])
                except (ValueError, KeyError, TypeError):
                    delay = 60
                return max(1, delay, retry_after(response.headers.get("retry-after")))
            if response.status_code != 200:
                return 60
            data = response.json()
            if (
                not isinstance(data, dict)
                or data.get("ok") is not True
                or not isinstance(data.get("result"), dict)
            ):
                return 60
            return None
        except (httpx.RequestError, ValueError, TypeError):
            return 60

    async def deliver(self):
        if (
            not self.settings.telegram_token
            or self.store.cooldown("telegram_not_before") > time.time()
        ):
            return
        rows = self.store.db.execute(
            """SELECT * FROM notifications WHERE delivered IS NULL
            AND destination=? ORDER BY due,item_id""",
            (self.destination,),
        ).fetchall()
        for row in rows:
            if row["due"] > time.time():
                continue
            item = Item.model_validate_json(row["payload"])
            names = [
                r[0]
                for r in self.store.db.execute(
                    "SELECT watch_name FROM matches WHERE item_id=? AND destination=? ORDER BY watch_name",
                    (row["item_id"], self.destination),
                )
            ]
            text = (
                f"{item.title[:1000]}\n{item.price} {item.currency}\n{item.url}\n"
                f"Searches: {', '.join(names)[:1500]}"
            )
            delay = await self.telegram(text)
            # A crash after send and before this commit can duplicate delivery (at least once).
            with self.store.db:
                if delay is None:
                    self.store.db.execute(
                        """UPDATE notifications SET delivered=?,error=NULL
                        WHERE item_id=? AND destination=?""",
                        (time.time(), row["item_id"], self.destination),
                    )
                else:
                    first_failure = self.store.db.execute(
                        "INSERT OR IGNORE INTO metadata VALUES('delivery_failure_since',?)",
                        (str(time.time()),),
                    ).rowcount
                    if first_failure:
                        log.warning("Telegram delivery failed; persistent queue retained")
                    self.store.db.execute(
                        "INSERT OR REPLACE INTO metadata VALUES(?,?)",
                        ("telegram_not_before", str(time.time() + delay)),
                    )
                    self.store.db.execute(
                        """UPDATE notifications SET due=?,attempts=attempts+1,
                        error='Telegram delivery failed' WHERE item_id=? AND destination=?""",
                        (
                            time.time() + max(delay, min(3600, 2 ** min(row["attempts"], 11))),
                            row["item_id"],
                            self.destination,
                        ),
                    )
            if delay is not None:
                break
            await asyncio.sleep(1.1)
        pending = self.store.db.execute(
            "SELECT 1 FROM notifications WHERE delivered IS NULL AND destination=?",
            (self.destination,),
        ).fetchone()
        if not pending:
            with self.store.db:
                self.store.db.execute(
                    "DELETE FROM metadata WHERE key IN "
                    "('delivery_failure_since','delivery_alerted')"
                )

    async def tick(self):
        async with self.lock:
            for watch in self.store.watches():
                if (
                    self.settings.telegram_token
                    and not watch["paused"]
                    and watch["due"] <= time.time()
                    and not self.vinted.blocked
                ):
                    await self.scan(self.store.watch(watch["id"]))
            await self.deliver()
            failed_since = self.store.cooldown("delivery_failure_since")
            if (
                failed_since
                and time.time() - failed_since >= self.settings.failure_alert_seconds
                and not self.store.cooldown("delivery_alerted")
            ):
                log.error("Telegram delivery has prolonged failure; inspect persistent backlog")
                self.store.set_cooldown("delivery_alerted", 1)
            # One prolonged-failure alert per failure episode, durable across restarts.
            for watch in self.store.watches():
                if (
                    watch["failure_since"]
                    and not watch["alerted"]
                    and time.time() - watch["failure_since"] >= self.settings.failure_alert_seconds
                ):
                    log.error(
                        "watch %s has prolonged failure; inspect authenticated status", watch["id"]
                    )
                    with self.store.db:
                        self.store.db.execute(
                            "UPDATE watches SET alerted=1 WHERE id=?", (watch["id"],)
                        )
                    # Logs are the independent alert channel when Telegram itself is unavailable.

    async def run(self):
        while True:
            await self.tick()
            await asyncio.sleep(self.settings.tick_seconds)
