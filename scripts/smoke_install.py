"""Check a base-only wheel installation without test or optional dependencies."""

import asyncio
import importlib.metadata
import importlib.util
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory

import anyo


async def main() -> None:
    installed = Path(anyo.__file__).resolve()
    assert "site-packages" in installed.parts, installed
    for optional in ("httpx2", "redis", "pytest"):
        assert importlib.util.find_spec(optional) is None, optional
    resources = {
        "anyo": ["py.typed", "_prompt_envelope.txt"],
        "anyo.integrations.omo": ["_bridge.mjs"],
        "anyo.ui": ["assets/index.html", "assets/login.html", "assets/ui.css", "assets/ui.js"],
    }
    for package, names in resources.items():
        for name in names:
            resource = files(package).joinpath(name)
            assert resource.is_file(), (package, name)
            resource.read_bytes()
    with TemporaryDirectory() as temporary:
        application = anyo.create_application(
            queue_url=f"sqlite:///{Path(temporary) / 'queue.sqlite3'}"
        )
        application.add_hook(
            provider="github",
            action="pr_created",
            organization="example",
            repository="example",
            pipeline=lambda event: None,
            hook_id="installation-smoke",
            secret="installation-smoke-only",
        )
        http_app = application.asgi()
        async with anyo.lifespan(http_app):
            assert callable(http_app)
    print(f"Verified anyo {importlib.metadata.version('anyo')} at {installed}")


if __name__ == "__main__":
    asyncio.run(main())
