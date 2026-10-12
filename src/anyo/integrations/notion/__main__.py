"""Explicit command-line entry point for first-time Notion verification."""

import argparse
import asyncio
import sys
from pathlib import Path

from anyo.integrations.notion._setup import serve


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect a Notion verification token candidate")
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify", help="Run a setup-only listener; never dispatch jobs")
    verify.add_argument("--token-file", required=True, type=Path)
    verify.add_argument("--host", default="127.0.0.1")
    verify.add_argument("--port", type=int, default=8000)
    verify.add_argument("--timeout", type=float, default=600, help="Overall lifetime in seconds")
    args = parser.parse_args()
    try:
        asyncio.run(
            serve(args.token_file.absolute(), host=args.host, port=args.port, timeout=args.timeout)
        )
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError):
        print(
            "Notion setup failed; check the address and private token destination.", file=sys.stderr
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
