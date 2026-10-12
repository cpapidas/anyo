"""Isolated installed consumer, also importable by spawned pipeline processes."""

import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

import anyo
from anyo import HookEvent
from anyo.integrations.notion import NotionEvent


def pipeline(event: HookEvent) -> None:
    assert isinstance(event, NotionEvent)
    target = Path(str(event.payload["output_directory"])) / f"{event.event_type}.json"
    # Exclusive creation detects accidental repeat execution after replacement.
    with target.open("x") as output:
        json.dump(asdict(event), output)


async def main() -> None:
    anyo.add_hook(
        provider="notion", action="event", event_type="*", pipeline=pipeline, hook_id="notion"
    )
    async with anyo.worker(
        queue_url=sys.argv[1],
        queue_namespace=sys.argv[2],
        execution_mode=sys.argv[3],
        poll_interval=0.01,
    ) as runner:
        await asyncio.wait_for(runner.wait_idle(), 15)
        records = await runner.inspect()
        assert len(records) == 3 and all(r.status == "successful" for r in records)


if __name__ == "__main__":
    asyncio.run(main())
