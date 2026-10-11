"""Fetch a branch or exact commit and keep Git data for the next worker."""

import os

from anyo.repositories import HTTPSCredentials, RepositoryCache


def main() -> None:
    cache = RepositoryCache(
        os.environ.get("ANYO_REPOSITORY_CACHE", ".anyo/repositories"),
        namespace=os.environ.get("ANYO_REPOSITORY_NAMESPACE", "review-app"),
    )
    token = os.environ.get("REPOSITORY_TOKEN")
    credentials = (
        HTTPSCredentials(os.environ.get("REPOSITORY_USERNAME", "x-access-token"), token)
        if token
        else None
    )
    commit = os.environ.get("REPOSITORY_COMMIT")
    ref = os.environ.get("REPOSITORY_REF")
    if commit and ref:
        raise ValueError("Set REPOSITORY_COMMIT or REPOSITORY_REF, not both")
    with cache.checkout(
        url=os.environ.get("REPOSITORY_URL"),
        local_path=os.environ.get("REPOSITORY_LOCAL_PATH"),
        commit=commit,
        ref=ref if commit else (ref or "refs/heads/main"),
        fetch_ref=os.environ.get("REPOSITORY_FETCH_REF"),
        credentials=credentials,
    ) as checkout:
        print(f"Ready: {checkout.commit_sha}")
        if commit:
            print(f"Pinned commit cache hit: {checkout.cache_hit}")
        print(f"Workspace: {checkout.path}")
        for warning in checkout.warnings:
            print(f"Content limit: {warning}")
        print("Root entries:", ", ".join(sorted(p.name for p in checkout.path.iterdir())[:20]))
        # Use the workspace here, before this context exits:
        # result = anyo.inject_prompt("review.md", workspace=checkout.path)
    print("Workspace released; cached Git data is ready for the next worker.")


if __name__ == "__main__":
    main()
