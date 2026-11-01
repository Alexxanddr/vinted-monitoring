"""Explicit opt-in: one anonymous Italy search, no Telegram or persistent watches."""

import argparse
import asyncio
import json

from vinted_monitoring.models import Query, Settings
from vinted_monitoring.upstream import UpstreamError, Vinted


async def check():
    vinted = Vinted(Settings(request_attempts=1))
    try:
        try:
            items = await vinted.page(Query(text="thinkcentre"), 1, 50)
        except UpstreamError as error:
            print(json.dumps({"verified": False, "error": str(error), "suspended": vinted.blocked}))
            return False
        print(
            json.dumps(
                {
                    "verified": True,
                    "items": len(items),
                    "results": [item.model_dump(mode="json") for item in items],
                    "coverage": "one page only",
                }
            )
        )
        return True
    finally:
        await vinted.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-live", action="store_true", required=True)
    parser.parse_args()
    raise SystemExit(0 if asyncio.run(check()) else 1)
