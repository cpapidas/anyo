"""GitHub's implementation of the shared trigger provider contract."""

from __future__ import annotations

from collections.abc import Mapping

from anyo.hooks import HookEvent
from anyo.integrations.github._configuration import validate_configuration
from anyo.integrations.github._webhooks import decode_webhook


class GitHubProvider:
    """Receive signed GitHub deliveries without a GitHub API client.

    ``organization`` identifies the target repository's owner, including a
    personal account. Repository names and owners match case-insensitively.
    The adapter only emits ``pr_created`` for ``pull_request`` / ``opened``.
    """

    name = "github"
    secret_env = "ANYO_GITHUB_WEBHOOK_SECRET"

    def validate(self, action: str, configuration: Mapping[str, object]) -> Mapping[str, str]:
        """Return a normalized snapshot or raise ``ValueError``."""
        return validate_configuration(action, configuration)

    def decode(self, body: bytes, headers: Mapping[str, str], secret: str) -> HookEvent | None:
        """Authenticate original bytes, then decode a supported delivery.

        Header names must be lowercase. Invalid signatures raise a 401
        ``WebhookError``; malformed authenticated input raises a 400 error.
        Authenticated setup pings and unsupported events/actions return ``None``.
        """
        return decode_webhook(body, headers, secret, provider_name=self.name)

    def matches(self, configuration: Mapping[str, str], event: HookEvent) -> bool:
        """Match the target repository; a pull request's fork is irrelevant."""
        return (
            event.provider == self.name
            and event.action == "pr_created"
            and configuration["organization"] == event.organization.lower()
            and configuration["repository"] == event.repository.lower()
        )
