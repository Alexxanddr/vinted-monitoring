import asyncio
import time

import httpx
import pytest

from vinted_monitoring.engine import Engine
from vinted_monitoring.models import Item, Query, Settings
from vinted_monitoring.server import Auth, make_server
from vinted_monitoring.store import Store
from vinted_monitoring.upstream import Blocked, Incomplete, UpstreamError, Vinted

_sdk_clients = []


def sdk_vinted(settings, transport):
    from vinted import VintedClient

    client = VintedClient()
    _sdk_clients.append(client)

    async def request(method, url, **kwargs):
        return await transport.request(
            method,
            url,
            params=kwargs.get("params"),
            timeout=kwargs["timeout"],
            follow_redirects=False,
        )

    client._session.session.request = request
    return Vinted(settings, client)


@pytest.fixture(autouse=True)
async def close_sdk_clients():
    yield
    while _sdk_clients:
        await _sdk_clients.pop().close()


def item(n, price="10"):
    return Item(
        id=str(n),
        title=f"Item {n}",
        price=price,
        currency="EUR",
        url=f"https://www.vinted.it/items/{n}-item",
    )


class Catalog:
    blocked = False

    def __init__(self):
        self.pages = {1: [item(1)]}
        self.error = None
        self.calls = []

    async def page(self, query, page, limit):
        self.calls.append(page)
        if self.error:
            raise self.error
        return self.pages.get(page, [])


@pytest.fixture
async def setup(tmp_path):
    settings = Settings(
        database=str(tmp_path / "state.sqlite"),
        page_size=2,
        max_pages=2,
        telegram_token="12345:fake-token",
        telegram_chat_id="test-destination",
    )
    store = Store(settings.database)
    catalog = Catalog()
    sent = []

    async def telegram(request):
        sent.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(telegram)) as client:
        engine = Engine(store, catalog, settings, client)
        yield engine, catalog, sent
    store.close()


def create(engine, text="computer", name="Computers"):
    return engine.store.create(name, Query(text=text), 600)["id"]


async def scan(engine, watch_id):
    await engine.scan(engine.store.watch(watch_id))


async def test_baseline_restart_repost_new_id_and_overlap(setup):
    engine, catalog, sent = setup
    a, b = create(engine), create(engine, "i5", "Processors")
    await scan(engine, a)
    await scan(engine, b)
    await engine.deliver()
    assert not sent
    path = engine.settings.database
    engine.store.close()
    engine.store = Store(path)
    catalog.pages = {1: [item(2), item(1, "5")], 2: []}
    await scan(engine, a)
    await scan(engine, b)
    await engine.deliver()
    assert len(sent) == 1
    assert b"Computers, Processors" in sent[0].content
    assert b"Item 2" in sent[0].content
    await scan(engine, a)
    await engine.deliver()
    assert len(sent) == 1


async def test_filter_edit_resets_baseline_and_retains_queue(setup):
    engine, catalog, sent = setup
    a = create(engine)
    await scan(engine, a)
    catalog.pages = {1: [item(2)]}
    await scan(engine, a)
    assert engine.store.status()["backlog"] == 1
    assert engine.store.update(a, Query(text="different"))["baseline_reset"]
    catalog.pages = {1: [item(3)]}
    await scan(engine, a)
    await engine.deliver()
    assert len(sent) == 1 and b"Item 2" in sent[0].content
    engine.store.pause(a, True)
    engine.store.pause(a, False)
    engine.store.delete(a)
    assert not engine.store.watches()


async def test_incomplete_baseline_is_atomic_and_gap_does_not_advance(setup):
    engine, catalog, _ = setup
    a = create(engine)
    catalog.pages = {1: [item(4), item(3)], 2: [item(2), item(1)]}
    await scan(engine, a)
    assert not engine.store.watch(a)["baseline"]
    assert engine.store.db.execute("SELECT count(*) FROM seen").fetchone()[0] == 0
    catalog.pages = {1: [item(1)]}
    await scan(engine, a)
    before = engine.store.watch(a)["last_success"]
    # Simulate long downtime and more items than the recoverable page budget.
    engine.store.db.execute("UPDATE watches SET last_success=?", (time.time() - 86400,))
    engine.store.db.commit()
    catalog.pages = {1: [item(5), item(4)], 2: [item(3), item(2)]}
    await scan(engine, a)
    assert engine.store.watch(a)["last_success"] < before
    assert engine.store.status()["backlog"] == 0
    assert "budget" in engine.store.watch(a)["error"]


async def test_duplicate_pages_and_moving_first_page(setup):
    engine, catalog, _ = setup
    catalog.pages = {1: [item(3), item(2)], 2: [item(2)]}
    assert {item.id for item in await engine.collect(Query(text="x"))} == {"2", "3"}
    original = catalog.page
    calls = 0

    async def moving(*args):
        nonlocal calls
        calls += 1
        if calls == 3:
            return [item(4), item(3)]
        return await original(*args)

    catalog.pages[2] = [item(1)]
    catalog.page = moving
    with pytest.raises(Incomplete, match="moving first"):
        await engine.collect(Query(text="x"))


async def test_scan_error_does_not_baseline(setup):
    engine, catalog, _ = setup
    a = create(engine)
    catalog.error = UpstreamError("bad schema")
    await scan(engine, a)
    assert not engine.store.watch(a)["baseline"]
    assert engine.store.watch(a)["failures"] == 1


async def test_global_baseline_suppresses_old_items_in_new_watch(setup):
    engine, catalog, sent = setup
    a = create(engine)
    await scan(engine, a)
    b = create(engine, "other", "Other")
    catalog.pages = {1: []}
    await scan(engine, b)
    catalog.pages = {1: [item(1)]}
    await scan(engine, b)
    await engine.deliver()
    assert not sent


async def test_telegram_failure_restart_and_at_least_once_crash(setup):
    engine, catalog, sent = setup
    a = create(engine)
    await scan(engine, a)
    catalog.pages = {1: [item(2)]}
    await scan(engine, a)
    real = engine.telegram

    async def fail(text):
        return 123

    engine.telegram = fail
    await engine.deliver()
    row = engine.store.db.execute("SELECT * FROM notifications WHERE delivered IS NULL").fetchone()
    assert row["attempts"] == 1 and row["due"] > time.time()
    engine.store.db.execute("DELETE FROM metadata WHERE key='telegram_not_before'")
    engine.store.db.execute("UPDATE notifications SET due=0")
    engine.store.db.commit()

    async def crash_before(text):
        raise RuntimeError("crash before send")

    engine.telegram = crash_before
    with pytest.raises(RuntimeError):
        await engine.deliver()
    assert not sent

    async def crash_after(text):
        await real(text)
        raise RuntimeError("crash after send")

    engine.telegram = crash_after
    with pytest.raises(RuntimeError):
        await engine.deliver()
    assert len(sent) == 1 and engine.store.status()["backlog"] == 1
    engine.telegram = real
    await engine.deliver()
    await engine.deliver()
    assert len(sent) == 2 and engine.store.status()["backlog"] == 0


async def test_telegram_rate_limit_and_malformed(setup):
    engine, _, _ = setup
    for response, expected in [
        (httpx.Response(429, json={"parameters": {"retry_after": 120}}), 120),
        (httpx.Response(500), 60),
        (httpx.Response(200, json={"ok": False}), 60),
        (httpx.Response(200, text="bad json"), 60),
    ]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as client:
            engine.client = client
            assert await engine.telegram("test") == expected


def test_process_lock_and_schema(tmp_path):
    path = str(tmp_path / "state.sqlite")
    store = Store(path)
    try:
        with pytest.raises(RuntimeError, match="already owned"):
            Store(path)
        store.db.execute("PRAGMA user_version=99")
    finally:
        store.close()
    with pytest.raises(RuntimeError, match="newer"):
        Store(path)


async def test_tick_serializes_concurrent_manual_runs(setup):
    engine, catalog, _ = setup
    create(engine)
    await asyncio.gather(engine.tick(), engine.tick())
    assert catalog.calls == [1]


@pytest.mark.parametrize(
    "payload,valid",
    [({"items": []}, True), ({}, False), ({"items": None}, False), ({"items": [{"id": 1}]}, False)],
)
async def test_valid_empty_vs_schema(payload, valid):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as client:
        vinted = sdk_vinted(Settings(), client)
        if valid:
            assert await vinted.page(Query(text="x"), 1, 10) == []
        else:
            with pytest.raises(UpstreamError, match="schema"):
                await vinted.page(Query(text="x"), 1, 10)


@pytest.mark.parametrize("code", [401, 403])
async def test_blocks_never_retry(code):
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(code)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        vinted = sdk_vinted(Settings(), client)
        for _ in range(2):
            with pytest.raises(Blocked):
                await vinted.page(Query(text="x"), 1, 10)
        assert len(calls) == 1


async def test_vinted_rate_limit_retry_after_and_timeout(monkeypatch):
    calls = []

    def limited(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "200"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(limited)) as client:
        vinted = sdk_vinted(Settings(), client)
        for _ in range(2):
            with pytest.raises(UpstreamError):
                await vinted.page(Query(text="x"), 1, 10)
        assert len(calls) == 1
        assert vinted.not_before > time.time() + 190

    async def no_sleep(_):
        pass

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    calls.clear()

    def timeout(request):
        calls.append(request)
        raise httpx.ReadTimeout("contains-sensitive-url", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        vinted = sdk_vinted(Settings(), client)
        with pytest.raises(UpstreamError, match="network") as error:
            await vinted.page(Query(text="x"), 1, 10)
        assert "sensitive" not in str(error.value)
        assert len(calls) == 3


async def test_http_auth_health_and_mcp_validation(setup):
    engine, _, _ = setup
    settings = Settings(transport="http", http_token="a" * 32, allowed_hosts=["testserver"])
    mcp = make_server(engine, settings)
    engine.task = asyncio.create_task(asyncio.sleep(100))
    app = Auth(mcp.streamable_http_app(), "a" * 32)
    try:
        async with mcp.session_manager.run():
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://testserver"
            ) as client:
                assert (await client.get("/mcp")).status_code == 401
                assert (await client.get("/health/live")).json() == {"alive": True}
                assert (await client.get("/health/ready")).json() == {"ready": True}
                headers = {
                    "Authorization": "Bearer " + "a" * 32,
                    "Accept": "application/json, text/event-stream",
                }
                request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
                response = await client.post("/mcp", json=request, headers=headers)
                assert response.status_code == 200
                tools = response.json()["result"]["tools"]
                assert {
                    "search_items",
                    "create_watch",
                    "list_watches",
                    "update_watch",
                    "pause_watch",
                    "resume_watch",
                    "delete_watch",
                } <= {t["name"] for t in tools}
                request.update(
                    method="tools/call",
                    params={
                        "name": "search_items",
                        "arguments": {"query": {"text": "x", "last_minutes": 30}, "limit": 0},
                    },
                )
                result = await client.post("/mcp", json=request, headers=headers)
                assert result.json()["result"]["isError"]
                headers["Origin"] = "https://attacker.invalid"
                assert (await client.post("/mcp", json=request, headers=headers)).status_code == 401
    finally:
        engine.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await engine.task


def test_configuration_validation():
    from pydantic import ValidationError

    for values in ({"transport": "http"}, {"telegram_token": "x"}, {"max_pages": 0}):
        with pytest.raises(ValidationError):
            Settings(**values)
    for values in (
        {"text": "x", "min_price": 10, "max_price": 1},
        {"text": "x", "marketplace": "DE"},
        {"text": " "},
    ):
        with pytest.raises(ValidationError):
            Query(**values)


async def test_stdio_round_trip(tmp_path):
    import os
    import sys
    from pathlib import Path

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = {key: value for key, value in os.environ.items() if not key.startswith("VM_")}
    env.update(
        VM_DATABASE=str(tmp_path / "stdio.sqlite"),
        PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"),
    )
    parameters = StdioServerParameters(
        command=sys.executable, args=["-m", "vinted_monitoring.server"], env=env
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert len(tools.tools) == 8
            result = await session.call_tool("list_watches", {})
            assert not result.isError
            result = await session.call_tool(
                "create_watch", {"name": "No destination", "query": {"text": "x"}}
            )
            assert result.isError


async def test_block_and_cooldown_survive_restart(setup):
    engine, _, _ = setup
    engine.store.suspend()
    engine.store.set_cooldown("vinted_not_before", time.time() + 200)
    path = engine.settings.database
    engine.store.close()
    engine.store = Store(path)
    assert engine.store.suspended()
    assert engine.store.cooldown("vinted_not_before") > time.time() + 190
    engine.store.clear_block()
    assert not engine.store.suspended()


async def test_management_and_prolonged_failure_alert_once(setup, caplog):
    engine, catalog, _ = setup
    mcp = make_server(engine, engine.settings)
    result = await mcp.call_tool("create_watch", {"name": "Watch", "query": {"text": "fixture"}})
    assert result
    watch_id = engine.store.watches()[0]["id"]
    await mcp.call_tool("pause_watch", {"watch_id": watch_id})
    assert engine.store.watch(watch_id)["paused"]
    await mcp.call_tool("resume_watch", {"watch_id": watch_id})
    await mcp.call_tool("update_watch", {"watch_id": watch_id, "interval_seconds": 120})
    assert engine.store.watch(watch_id)["interval"] == 120
    catalog.error = UpstreamError("failure")
    await scan(engine, watch_id)
    engine.store.db.execute("UPDATE watches SET failure_since=?", (time.time() - 7200,))
    engine.store.db.commit()
    await engine.tick()
    await engine.tick()
    assert sum("prolonged" in r.message for r in caplog.records) == 1
    await mcp.call_tool("delete_watch", {"watch_id": watch_id})
    assert not engine.store.watches()


async def test_error_envelope_is_not_empty_baseline():
    for payload in ({"items": [], "code": 100}, {"items": [], "errors": ["bad"]}):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
        ) as client:
            vinted = sdk_vinted(Settings(), client)
            with pytest.raises(UpstreamError):
                await vinted.page(Query(text="fixture"), 1, 10)


async def test_relative_url_strict_prices_and_filter_check():
    raw = {
        "id": 123,
        "title": "Fixture",
        "url": "/items/123-fixture",
        "price": {"amount": "10.00", "currency_code": "EUR"},
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"items": [raw]}))
    ) as client:
        vinted = sdk_vinted(Settings(), client)
        assert (await vinted.page(Query(text="fixture"), 1, 10))[0].id == "123"
        with pytest.raises(UpstreamError):
            await vinted.page(Query(text="fixture", max_price="5"), 1, 10)
        raw["url"] = "https://attacker.invalid/items/123"
        with pytest.raises(UpstreamError):
            await vinted.page(Query(text="fixture"), 1, 10)


async def test_later_queue_rows_cannot_bypass_telegram_cooldown(setup):
    engine, catalog, _ = setup
    a = create(engine)
    await scan(engine, a)
    catalog.pages = {1: [item(3), item(2)], 2: []}
    await scan(engine, a)
    attempts = []

    async def fail(text):
        attempts.append(text)
        return 120

    engine.telegram = fail
    await engine.deliver()
    await engine.deliver()
    assert len(attempts) == 1
    assert engine.store.status()["backlog"] == 2


async def test_html_challenge_persists_suspension(setup):
    engine, _, _ = setup
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, text="challenge", headers={"Content-Type": "text/html"})
        )
    ) as client:
        vinted = sdk_vinted(engine.settings, client)
        vinted.on_block = engine.store.suspend
        with pytest.raises(Blocked):
            await vinted.page(Query(text="fixture"), 1, 10)
        assert engine.store.suspended()


def test_v1_upgrade_and_canonical_lock(tmp_path):
    import sqlite3

    path = str(tmp_path / "state.sqlite")
    store = Store(path)
    store.close()
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE metadata")
        db.execute("PRAGMA user_version=1")
    store = Store(path)
    try:
        assert store.db.execute("PRAGMA user_version").fetchone()[0] == 2
        alias = tmp_path / "alias.sqlite"
        alias.symlink_to(path)
        with pytest.raises(RuntimeError, match="already owned"):
            Store(str(alias))
    finally:
        store.close()


async def test_failed_database_commit_rolls_back_entire_baseline(setup):
    import sqlite3

    engine, _, _ = setup
    a = create(engine)
    engine.store.db.execute("""CREATE TRIGGER fail_insert BEFORE INSERT ON seen
        WHEN NEW.item_id='2' BEGIN SELECT RAISE(ABORT, 'simulated disk failure'); END""")
    engine.store.db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        engine.store.commit_scan(engine.store.watch(a), [item(1), item(2)], engine.destination)
    assert not engine.store.watch(a)["baseline"]
    assert engine.store.db.execute("SELECT count(*) FROM seen").fetchone()[0] == 0
    assert engine.store.db.execute("SELECT count(*) FROM notifications").fetchone()[0] == 0


async def test_immediate_search_total_deadline(setup):
    from mcp.server.fastmcp.exceptions import ToolError

    engine, catalog, _ = setup
    settings = engine.settings.model_copy(update={"scan_timeout": 0.01})
    mcp = make_server(engine, settings)

    async def slow(*args):
        await asyncio.sleep(10)
        return []

    catalog.page = slow
    with pytest.raises(ToolError, match="immediate search deadline exceeded"):
        await mcp.call_tool("search_items", {"query": {"text": "fixture"}})
    assert not engine.store.watches()


@pytest.mark.parametrize("code", [401, 403, 429])
async def test_sdk_catalog_denial_cannot_refresh_or_retry(code):
    calls = []

    def respond(request):
        calls.append(request.method)
        if request.method == "HEAD":
            return httpx.Response(200)
        return httpx.Response(code, headers={"Retry-After": "120"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport:
        vinted = sdk_vinted(Settings(), transport)
        for _ in range(2):
            with pytest.raises(UpstreamError):
                await vinted.page(Query(text="fixture"), 1, 10)
        assert calls == ["HEAD", "GET"]
        assert vinted.blocked == (code != 429)


async def test_real_sdk_search_builds_request_and_preserves_transport(monkeypatch):
    from vinted import VintedClient

    client = VintedClient()
    _sdk_clients.append(client)
    calls = []
    searches = []
    real_search = client.search_items

    async def search(**kwargs):
        searches.append(kwargs)
        return await real_search(**kwargs)

    monkeypatch.setattr(client, "search_items", search)

    async def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        raw = {
            "id": 123,
            "title": "Fixture",
            "url": "/items/123-fixture",
            "price": {"amount": "10.00", "currency_code": "EUR"},
        }
        return httpx.Response(200, json={"items": [raw]}, request=httpx.Request(method, url))

    monkeypatch.setattr(client._session.session, "request", request)
    vinted = Vinted(Settings(request_timeout=7), client)
    result = await vinted.page(Query(text="ThinkCentre i5", min_price="5", max_price="150"), 2, 50)
    assert result[0].id == "123"
    assert searches[0]["raw_data"] is True
    assert searches[0]["page"] == 2 and searches[0]["per_page"] == 50
    assert searches[0]["order"] == "newest_first"
    assert [c[0] for c in calls] == ["HEAD", "GET"]
    assert calls[1][1] == "https://api.vinted.it/svc-catalogue/items"
    assert calls[1][2]["params"]["search_text"] == "ThinkCentre i5"
    assert calls[1][2]["params"]["price_from"] == "5"
    assert calls[1][2]["params"]["price_to"] == "150"
    assert all(c[2]["timeout"] == 7 and not c[2]["allow_redirects"] for c in calls)
    assert all(c[2]["impersonate"] == "chrome" for c in calls)
    assert client._session.session.proxies == {"all": ""}


async def test_overlapping_catalog_pages_complete_silent_baseline(setup):
    engine, catalog, sent = setup
    watch_id = create(engine)
    engine.settings.page_size = 3
    catalog.pages = {1: [item(4), item(3), item(2)], 2: [item(3), item(1)]}
    await scan(engine, watch_id)
    watch = engine.store.watch(watch_id)
    assert watch["baseline"] == 1, watch["error"]
    assert watch["last_success"] is not None
    assert engine.store.db.execute("SELECT count(*) FROM seen").fetchone()[0] == 4
    await engine.deliver()
    assert not sent


async def test_reordered_first_page_with_already_collected_ids_is_complete(setup):
    engine, catalog, _ = setup
    original = catalog.page
    calls = 0
    catalog.pages = {1: [item(3), item(2)], 2: [item(1)]}

    async def reordered(*args):
        nonlocal calls
        calls += 1
        return [item(1), item(3)] if calls == 3 else await original(*args)

    catalog.page = reordered
    assert {i.id for i in await engine.collect(Query(text="x"))} == {"1", "2", "3"}


async def test_repeated_full_page_cannot_complete_baseline(setup):
    engine, catalog, _ = setup
    engine.settings.max_pages = 3
    catalog.pages = {1: [item(2), item(1)], 2: [item(2), item(1)], 3: []}
    with pytest.raises(Incomplete, match="no progress"):
        await engine.collect(Query(text="x"))


async def test_duplicate_ids_do_not_make_a_full_last_page_complete(setup):
    engine, catalog, _ = setup
    catalog.pages = {1: [item(3), item(2)], 2: [item(2), item(1)]}
    with pytest.raises(Incomplete, match="budget"):
        await engine.collect(Query(text="x"))


async def test_duplicate_ids_within_page_are_deduplicated(setup):
    engine, catalog, _ = setup
    catalog.pages = {1: [item(2), item(2)], 2: [item(1)]}
    assert {i.id for i in await engine.collect(Query(text="x"))} == {"1", "2"}
