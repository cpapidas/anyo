"""Run a pipeline when a configured repository receives a new pull request."""

import logging
import os

import anyo
from anyo import HookEvent

logger = logging.getLogger(__name__)


def review_pull_request(event: HookEvent) -> None:
    """Replace this function's body with your own pipeline code."""
    logger.info(
        "Received pull request %s/%s#%s at %s",
        event.organization,
        event.repository,
        event.pull_request_number,
        event.head_sha,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    anyo.add_hook(
        provider="github",
        action="pr_created",
        organization=os.environ["ANYO_GITHUB_ORGANIZATION"],
        repository=os.environ["ANYO_GITHUB_REPOSITORY"],
        pipeline=review_pull_request,
    )
    anyo.start()


if __name__ == "__main__":
    main()
