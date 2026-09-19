"""The translation from domain to response."""

from __future__ import annotations

import dataclasses

import pytest
from pydantic import ValidationError

from app.api.routers.notifications import _to_out
from app.api.schemas import NotificationOut
from app.domain.entities import Notification


def _notification(**overrides: object) -> Notification:
    """A complete notification, every field set to a non-default value."""
    fields: dict[str, object] = {
        "user_id_from": 2,
        "user_id_to": 3,
        "component": "mod_assign",
        "event_type": "assign_notification",
        "subject": "Assignment published",
        "full_message": "The teacher published an assignment.",
        "full_message_format": 1,
        "full_message_html": "<p>The teacher published an assignment.</p>",
        "small_message": "Assignment published",
        "context_url": "http://esempio/mod/assign/view.php?id=7",
        "context_url_name": "See the assignment",
        "custom_data": '{"cmid": 7}',
        "time_created": 1_700_000_000,
        "time_read": 1_700_000_500,
        "id": 42,
    }
    fields.update(overrides)
    return Notification(**fields)  # type: ignore[arg-type]


def test_entity_has_slots() -> None:
    """Declares the reason of the defect, so that it does not come back: slots are what prevents adding an
    attribute to a domain entity by mistake.
    """
    with pytest.raises(TypeError):
        vars(_notification())

    assert "__slots__" in Notification.__dict__


def test_all_fields_kept() -> None:
    """No field is lost, and none changes value."""
    notification = _notification()
    out = _to_out(notification)

    assert isinstance(out, NotificationOut)
    for field in dataclasses.fields(Notification):
        assert getattr(out, field.name) == getattr(notification, field.name), field.name


def test_optional_fields_empty() -> None:
    """An unread notification without context is the ordinary case: the form of every notification
    just written.
    """
    out = _to_out(
        _notification(time_read=None, context_url=None, context_url_name=None, custom_data=None)
    )

    assert out.time_read is None
    assert out.context_url is None
    assert out.context_url_name is None
    assert out.custom_data is None


def test_missing_id_refused() -> None:
    """The domain entity allows a null id, the response does not: only what was persisted leaves the
    interface.
    """
    with pytest.raises(ValidationError):
        _to_out(_notification(id=None))
