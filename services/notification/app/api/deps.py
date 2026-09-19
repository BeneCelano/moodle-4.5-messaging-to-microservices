"""Dependencies of the HTTP layer."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from ..application.notification_service import NotificationService
from ..core.config import Settings, get_settings


def get_notification_service(request: Request) -> NotificationService:
    return request.app.state.notification_service


def require_token(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Check the token shared with the authorised callers."""
    if not settings.api_token:
        # Token unset: the check is skipped.
        return

    expected = f"Bearer {settings.api_token}"
    if authorization != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing or invalid bearer token."
        )


def get_correlation_id(x_request_id: Annotated[str | None, Header()] = None) -> str | None:
    return x_request_id


def get_idempotency_key(idempotency_key: Annotated[str | None, Header()] = None) -> str | None:
    return idempotency_key


AuthDep = Annotated[None, Depends(require_token)]
NotificationServiceDep = Annotated[NotificationService, Depends(get_notification_service)]
CorrelationDep = Annotated[str | None, Depends(get_correlation_id)]
IdempotencyDep = Annotated[str | None, Depends(get_idempotency_key)]
