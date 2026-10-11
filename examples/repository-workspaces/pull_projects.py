"""Prepare several projects together using one persistent repository cache."""

import argparse
import os
from contextlib import ExitStack
from pathlib import Path

from anyo.repositories import HTTPSCredentials, RepositoryCache


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("projects", nargs="+", help="Trusted HTTPS repository URLs")
    parser.add_argument("--ref", default="refs/heads/main", help="Full Git ref to refresh")
    parser.add_argument(
        "--local", action="store_true", help="Treat projects as trusted local Git paths"
    )
    args = parser.parse_args()
    token = os.environ.get("REPOSITORY_TOKEN")
    credentials = (
        HTTPSCredentials(os.environ.get("REPOSITORY_USERNAME", "x-access-token"), token)
        if token and not args.local
        else None
    )
    cache = RepositoryCache(
        os.environ.get("ANYO_REPOSITORY_CACHE", ".anyo/repositories"),
        namespace=os.environ.get("ANYO_REPOSITORY_NAMESPACE", "review-app"),
    )

    # Hold every workspace until the cross-project work finishes. If preparing
    # a later project fails, ExitStack still releases all earlier workspaces.
    with ExitStack() as stack:
        workspaces: list[Path] = []
        for number, project in enumerate(args.projects, start=1):
            checkout = stack.enter_context(
                cache.checkout(
                    url=None if args.local else project,
                    local_path=project if args.local else None,
                    ref=args.ref,
                    credentials=credentials,
                )
            )
            workspaces.append(checkout.path)
            print(f"Project {number}: {checkout.commit_sha} at {checkout.path}", flush=True)
            for warning in checkout.warnings:
                print(f"Project {number} content limit: {warning}", flush=True)

        # Put your cross-project analysis here. All paths are still valid.
        for number, workspace in enumerate(workspaces, start=1):
            names = sorted(p.name for p in workspace.iterdir() if p.name != ".git")
            print(f"Project {number} root entries: {', '.join(names[:20])}")

    print("Workspaces released; downloaded Git data remains cached.")


if __name__ == "__main__":
    main()
