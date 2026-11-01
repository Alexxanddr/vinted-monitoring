"""Exercise the full service; mock only outbound HTTP, never polling or SDK searches."""

import asyncio
import json
from contextlib import asynccontextmanager

import httpx
from curl_cffi.requests import AsyncSession

from vinted_monitoring import server
from vinted_monitoring.models import Settings


async def test_http_sdk_poll_delivery_and_restart(tmp_path, monkeypatch):
    catalog = [
        {
            "id": 1,
            "title": "Old fixture",
            "url": "/items/1-fixture",
            "price": {"amount": "10.00", "currency_code": "EUR"},
        }
    ]
    requests = []
    delivered = []
    engines = []
    real_client = httpx.AsyncClient
    real_engine = server.Engine

    async def vinted_http(self, method, url, **kwargs):
        requests.append((method, url))
        assert kwargs["allow_redirects"] is False
        if method == "HEAD":
            assert url == "https://www.vinted.it"
            return httpx.Response(200, request=httpx.Request(method, url))
        assert url == "https://api.vinted.it/svc-catalogue/items"
        params = kwargs["params"]
        start = (params["page"] - 1) * params["per_page"]
        return httpx.Response(
            200,
            json={"items": catalog[start : start + params["per_page"]]},
            request=httpx.Request(method, url),
        )

    async def telegram_http(request):
        assert request.url.host == "api.telegram.org"
        data = json.loads(request.content)
        assert data["chat_id"] == "offline-destination"
        delivered.append(data["text"])
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(delivered)}})

    def engine(*args):
        result = real_engine(*args)
        engines.append(result)
        return result

    monkeypatch.setattr(AsyncSession, "request", vinted_http)
    monkeypatch.setattr(server, "Engine", engine)
    monkeypatch.setattr(
        server.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(telegram_http), **kwargs),
    )
    settings = Settings(
        database=str(tmp_path / "monitor.sqlite3"),
        transport="http",
        http_token="x" * 32,
        allowed_hosts=["testserver"],
        telegram_token="12345:offline-token",
        telegram_chat_id="offline-destination",
        page_size=2,
        max_pages=3,
        tick_seconds=0.1,
    )

    @asynccontextmanager
    async def connect():
        async with server.runtime(settings) as mcp:
            app = server.Auth(mcp.streamable_http_app(), "x" * 32)
            async with mcp.session_manager.run():
                async with real_client(
                    transport=httpx.ASGITransport(app), base_url="http://testserver"
                ) as client:

                    async def call(name, arguments=None):
                        response = await client.post(
                            "/mcp",
                            json={
                                "jsonrpc": "2.0",
                                "id": 1,
                                "method": "tools/call",
                                "params": {"name": name, "arguments": arguments or {}},
                            },
                            headers={
                                "Authorization": "Bearer " + "x" * 32,
                                "Accept": "application/json, text/event-stream",
                            },
                        )
                        assert response.status_code == 200
                        result = response.json()["result"]
                        assert not result.get("isError"), result
                        return json.loads(result["content"][0]["text"])

                    yield call

    async with connect() as call:
        found = await call("search_items", {"query": {"text": "thinkcentre"}, "limit": 50})
        assert found["items"][0]["id"] == "1"
        assert (await call("list_watches"))["watches"] == []
        assert not delivered
        a = (await call("create_watch", {"name": "Computers", "query": {"text": "thinkcentre"}}))[
            "id"
        ]
        b = (await call("create_watch", {"name": "Processors", "query": {"text": "i5"}}))["id"]
        # The actual background task, independently of MCP, must establish both baselines.
        async with asyncio.timeout(3):
            while not all(w["baseline"] for w in engines[-1].store.watches()):
                await asyncio.sleep(0.02)
        assert len(engines[-1].store.watches()) == 2
        assert not delivered
        catalog.insert(
            0,
            {
                "id": 2,
                "title": "New fixture",
                "url": "/items/2-fixture",
                "price": {"amount": "12.00", "currency_code": "EUR"},
            },
        )
        async with engines[-1].lock:
            with engines[-1].store.db:
                engines[-1].store.db.execute("UPDATE watches SET due=0")
        async with asyncio.timeout(4):
            while not delivered:
                await asyncio.sleep(0.02)
        # Acquire the same lock: wait until delivery persistence has committed too.
        status = await call("monitoring_status")
        assert status["backlog"] == 0
        assert len(delivered) == 1
        assert "Computers, Processors" in delivered[0]
        assert "New fixture" in delivered[0] and "12.00 EUR" in delivered[0]
        await call("pause_watch", {"watch_id": a})
        assert engines[-1].store.watch(a)["paused"]
        await call("resume_watch", {"watch_id": a})
        await call("update_watch", {"watch_id": b, "query": {"text": "different"}})
        await engines[-1].tick()
        assert engines[-1].store.watch(b)["baseline"]
        assert len(delivered) == 1
    assert engines[-1].task.done()
    async with connect() as call:
        assert len((await call("list_watches"))["watches"]) == 2
        async with engines[-1].lock:
            with engines[-1].store.db:
                engines[-1].store.db.execute("UPDATE watches SET due=0")
        await engines[-1].tick()
        assert len(delivered) == 1
        assert (await call("monitoring_status"))["backlog"] == 0
        await call("delete_watch", {"watch_id": a})
        await call("delete_watch", {"watch_id": b})
        assert (await call("list_watches"))["watches"] == []
    assert requests and all("vinted.it" in url for _, url in requests)
