"""Installed consumer used by isolated thread/process worker tests."""

import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

import anyo
from anyo import HookEvent
from anyo.integrations.betterstack import BetterStackEvent


def pipeline(event: HookEvent) -> None:
    assert isinstance(event, BetterStackEvent)
    target = Path(str(event.payload["output_directory"])) / f"{event.action}.json"
    target.write_text(json.dumps(asdict(event)))


async def main() -> None:
    for family in ("incident", "error"):
        anyo.add_hook(
            provider="betterstack", action=family, event_type="*", pipeline=pipeline, hook_id=family
        )
    async with anyo.worker(
        queue_url=sys.argv[1],
        queue_namespace=sys.argv[2],
        execution_mode=sys.argv[3],
        poll_interval=0.01,
    ) as runner:
        await asyncio.wait_for(runner.wait_idle(), 15)
        records = await runner.inspect()
        assert len(records) == 2 and all(r.status == "successful" for r in records)


if __name__ == "__main__":
    asyncio.run(main())
