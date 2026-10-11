"""Concrete provider packages, each owning its configuration and event handling.

Import an adapter from its provider package, such as
``from anyo.integrations.github import GitHubProvider``. This namespace does not
eagerly discover or import providers. Shared contracts live in ``anyo.hooks``.
"""
