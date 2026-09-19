"""Translation of domain errors into HTTP responses."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..domain.errors import (
    ConversationNotFound,
    InvalidConversation,
    MessageNotFound,
    MessageTooLong,
    NotAMember,
)

STATUS_BY_ERROR: dict[type[Exception], int] = {
    ConversationNotFound: 404,
    MessageNotFound: 404,
    InvalidConversation: 422,
    MessageTooLong: 422,
    NotAMember: 403,
}


def register_exception_handlers(app: FastAPI) -> None:
    """Register the handlers that translate domain errors."""

    async def handle_domain_error(request: Request, exc: Exception) -> JSONResponse:
        """Return the status that corresponds to the domain error."""
        status = STATUS_BY_ERROR.get(type(exc), 500)
        return JSONResponse(
            status_code=status,
            content={"detail": str(exc), "error": type(exc).__name__},
        )

    for error_type in STATUS_BY_ERROR:
        app.add_exception_handler(error_type, handle_domain_error)
