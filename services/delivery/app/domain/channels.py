"""The choice of the delivery channels."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass


class UnknownProvider(Exception):
    """The site declares no settings for that component/provider pair."""


@dataclass(frozen=True, slots=True)
class Recipient:
    """The recipient, with the fields needed to decide the channels and deliver."""

    user_id: int
    email: str | None
    mail_format: int
    auth_method: str
    suspended: int
    deleted: int
    email_stop: int
    first_name: str
    last_name: str

    @property
    def is_deliverable(self) -> bool:
        """Whether the recipient can receive anything: the first check of
        `message_output_email::send_message()`, valid for every channel.
        """
        return not (self.deleted or self.suspended or self.auth_method == "nologin")

    @property
    def full_name(self) -> str:
        """Name shown in the header of the delivery."""
        return f"{self.first_name} {self.last_name}".strip()


def php_truthy(value: str | None) -> bool:
    """Truthiness of a string according to PHP: the empty string and "0" are false, the
    rest is true.
    """
    return value not in (None, "", "0")


def split_preference(value: str) -> list[str]:
    """Split a preference value into processor names."""
    return value.split(",")


def resolve_channels(
    *,
    processors: Sequence[str],
    component: str,
    provider: str,
    recipient: Recipient,
    user_preferences: Mapping[str, str],
    site_defaults: Mapping[str, str],
    is_configured: Callable[[str, Recipient], bool],
) -> list[str]:
    """Return the channels on which the message goes to that recipient."""
    preference_base = f"{component}_{provider}"
    preference_name = f"message_provider_{preference_base}_enabled"

    chosen: list[str] = []

    for name in processors:
        locked_key = f"{name}_provider_{preference_base}_locked"
        if locked_key not in site_defaults:
            raise UnknownProvider(
                f"The site does not declare {locked_key}: the pair {component}/{provider} is not "
                f"among the registered notification providers."
            )

        locked = php_truthy(site_defaults[locked_key])

        # A channel is forced when the choice is locked and the channel is among the
        # defaults.
        forced = False
        if locked:
            default_enabled = site_defaults.get(preference_name)
            if default_enabled is not None:
                forced = name in split_preference(default_enabled)

        configured = is_configured(name, recipient)

        if forced and configured:
            chosen.append(name)
            continue

        # Choice not locked, processor usable, notifications not switched off by the
        # user.
        if forced or locked or not configured or recipient.email_stop:
            continue

        user_preference = user_preferences.get(preference_name)
        if user_preference:
            if name in split_preference(user_preference):
                chosen.append(name)
        else:
            # Preference absent or empty: the site settings apply.
            default_enabled = site_defaults.get(preference_name)
            if default_enabled is not None and name in split_preference(default_enabled):
                chosen.append(name)

    return chosen
