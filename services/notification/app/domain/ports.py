"""Ports of the domain: the contracts the domain imposes on the infrastructure."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .entities import Recipient


@runtime_checkable
class NotificationMetrics(Protocol):
    """Count of the notifications written."""

    def persisted(self, component: str) -> None:
        """Record a written notification, attributed to the component that produced it.
        """
        ...


@runtime_checkable
class Clock(Protocol):
    """Source of time, in seconds since the epoch as in the monolith."""

    def now(self) -> int:
        """Current time."""
        ...


@runtime_checkable
class SiteFacts(Protocol):
    """The monolith facts read through the views: the recipient (language, name of the sender), the site
    name and the name format.
    """

    async def recipients(self, user_ids: list[int]) -> dict[int, Recipient]:
        """Return the recipients found, indexed by identifier."""
        ...

    async def site_settings(self) -> dict[str, str]:
        """Return the site settings needed for composition."""
        ...


@runtime_checkable
class UnitOfWork(Protocol):
    """Transactional boundary of a use case."""

    notifications: object
    templates: object
    commands: object
    events: object

    async def __aenter__(self) -> UnitOfWork: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...
