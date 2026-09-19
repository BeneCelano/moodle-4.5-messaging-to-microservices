"""The choice of the channels."""

from __future__ import annotations

import pytest

from app.domain.channels import (
    Recipient,
    UnknownProvider,
    php_truthy,
    resolve_channels,
    split_preference,
)

COMPONENT = "moodle"
PROVIDER = "instantmessage"
BASE = f"{COMPONENT}_{PROVIDER}"
ENABLED = f"message_provider_{BASE}_enabled"

PROCESSORS = ["email", "airnotifier"]


def recipient(**changes: object) -> Recipient:
    """An ordinary recipient, field by field."""
    values: dict = {
        "user_id": 42,
        "email": "tizio@esempio.it",
        "mail_format": 1,
        "auth_method": "manual",
        "suspended": 0,
        "deleted": 0,
        "email_stop": 0,
        "first_name": "Tizio",
        "last_name": "Caio",
    }
    values.update(changes)
    return Recipient(**values)  # type: ignore[arg-type]


def site(**changes: str) -> dict[str, str]:
    """Minimal site settings for the resolution to be possible."""
    values = {
        f"email_provider_{BASE}_locked": "0",
        f"airnotifier_provider_{BASE}_locked": "0",
        ENABLED: "email",
    }
    values.update(changes)
    return values


def resolve(**changes: object) -> list[str]:
    """Call the resolution with sensible defaults."""
    kwargs: dict = {
        "processors": PROCESSORS,
        "component": COMPONENT,
        "provider": PROVIDER,
        "recipient": recipient(),
        "user_preferences": {},
        "site_defaults": site(),
        "is_configured": lambda name, dest: True,
    }
    kwargs.update(changes)
    return resolve_channels(**kwargs)  # type: ignore[arg-type]


class TestPhpTruthiness:
    """The string "0" is false in PHP and true in Python."""

    def test_zero_string_is_false(self) -> None:
        assert php_truthy("0") is False

    def test_empty_string_is_false(self) -> None:
        assert php_truthy("") is False

    def test_absent_is_false(self) -> None:
        assert php_truthy(None) is False

    def test_one_is_true(self) -> None:
        assert php_truthy("1") is True

    def test_zero_does_not_lock(self) -> None:
        """The real case: with "0" the choice stays with the user."""
        channels = resolve(user_preferences={ENABLED: "airnotifier"})
        assert channels == ["airnotifier"]


class TestUserPreference:
    def test_user_preference_wins(self) -> None:
        channels = resolve(user_preferences={ENABLED: "airnotifier"})
        assert channels == ["airnotifier"]

    def test_unlisted_processor_excluded(self) -> None:
        channels = resolve(user_preferences={ENABLED: "email"})
        assert channels == ["email"]

    def test_multiple_channels(self) -> None:
        channels = resolve(user_preferences={ENABLED: "email,airnotifier"})
        assert channels == ["email", "airnotifier"]

    def test_absent_preference_fallback(self) -> None:
        channels = resolve(user_preferences={})
        assert channels == ["email"]

    def test_empty_preference_fallback(self) -> None:
        """The empty string does not mean "no channel": in the original it falls back on
        the site settings.
        """
        channels = resolve(user_preferences={ENABLED: ""})
        assert channels == ["email"]


class TestAdminForcing:
    def test_locked_default_forces(self) -> None:
        """With the choice locked the user's preference is not consulted."""
        channels = resolve(
            site_defaults=site(**{f"email_provider_{BASE}_locked": "1"}),
            user_preferences={ENABLED: "airnotifier"},
        )
        assert "email" in channels

    def test_forced_unconfigured_excluded(self) -> None:
        """A forced but unusable channel stays out: the original reports it and goes on.
        """
        channels = resolve(
            site_defaults=site(**{f"email_provider_{BASE}_locked": "1"}),
            is_configured=lambda name, dest: name != "email",
        )
        assert "email" not in channels

    def test_locked_not_default_excludes(self) -> None:
        """Locked and not among the defaults means: no delivery."""
        channels = resolve(
            site_defaults=site(**{f"airnotifier_provider_{BASE}_locked": "1"}),
            user_preferences={ENABLED: "email,airnotifier"},
        )
        assert channels == ["email"]


class TestEmailStop:
    def test_email_stop_excludes_all(self) -> None:
        channels = resolve(recipient=recipient(email_stop=1), user_preferences={ENABLED: "email"})
        assert channels == []

    def test_forcing_beats_email_stop(self) -> None:
        """A channel forced by the administrator reaches even who switched notifications off: in the original
        the forcing branch precedes the `emailstop` check.
        """
        channels = resolve(
            recipient=recipient(email_stop=1),
            site_defaults=site(**{f"email_provider_{BASE}_locked": "1"}),
        )
        assert channels == ["email"]


class TestUnusableProcessor:
    def test_unconfigured_excluded(self) -> None:
        channels = resolve(
            user_preferences={ENABLED: "email,airnotifier"},
            is_configured=lambda name, dest: name == "email",
        )
        assert channels == ["email"]


class TestUnknownProvider:
    def test_missing_lock_setting(self) -> None:
        """Malformed event, not failed delivery: retrying would not make it valid."""
        with pytest.raises(UnknownProvider):
            resolve(site_defaults={ENABLED: "email"})


class TestRecipient:
    def test_deleted_excluded(self) -> None:
        assert recipient(deleted=1).is_deliverable is False

    def test_suspended_excluded(self) -> None:
        assert recipient(suspended=1).is_deliverable is False

    def test_nologin_excluded(self) -> None:
        assert recipient(auth_method="nologin").is_deliverable is False

    def test_ordinary_recipient(self) -> None:
        assert recipient().is_deliverable is True


class TestSplitting:
    def test_spaces_not_trimmed(self) -> None:
        """Fidelity to the original, which compares the pieces as `explode()` returns
        them.
        """
        assert split_preference("email, airnotifier") == ["email", " airnotifier"]
