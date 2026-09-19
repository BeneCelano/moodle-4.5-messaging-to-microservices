"""Composition of the text of the notifications the service produces itself."""

from __future__ import annotations

import re

from ..domain.entities import Recipient

PLACEHOLDER = re.compile(r"\{\$a->(\w+)\}|\{\$a\}")
"""Placeholders of Moodle language files: `{$a->name}` with several values, `{$a}` with one."""

NAME_FIELDS = ("firstname", "lastname")
"""The name fields the view exposes, hence the only ones the format can use."""

DEFAULT_FORMAT = "firstname lastname"
"""Form used when the site delegates the format to the language, its initial setting."""


def compose(template: str, values: dict[str, str]) -> str:
    """Replace the placeholders of the template with the given values."""

    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        if name is None:
            return values.get("a", "")
        return values.get(name, "")

    return PLACEHOLDER.sub(substitute, template)


def full_name(recipient: Recipient, name_format: str | None = None) -> str:
    """Compose a person's name according to the site format (`fullnamedisplay`, field
    names as words).
    """
    fmt = (name_format or "").strip()
    if not fmt or fmt == "language":
        fmt = DEFAULT_FORMAT

    values = {"firstname": recipient.first_name, "lastname": recipient.last_name}

    def substitute(match: re.Match[str]) -> str:
        word = match.group(0)
        if word in NAME_FIELDS:
            return values.get(word, "")
        # A profile field the view does not expose.
        return ""

    composed = re.sub(r"\w+", substitute, fmt)
    return " ".join(composed.split())
