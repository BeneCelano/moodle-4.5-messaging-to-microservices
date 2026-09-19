"""Text composition: the smallest piece of code of the service and the one with the most
important declared limit, a person's name composed from two fields instead of the
monolith's six.
"""

from __future__ import annotations

from app.application.rendering import compose, full_name

from .conftest import person


class TestSubstitution:
    """Placeholder substitution."""

    def test_named_placeholders(self):
        assert compose("{$a->user} scrive a {$a->other}", {"user": "Anna", "other": "Bruno"}) == (
            "Anna scrive a Bruno"
        )

    def test_single_placeholder(self):
        """The form without a name is used in language files when the template has one
        value.
        """
        assert compose("Hello {$a}", {"a": "Anna"}) == "Hello Anna"

    def test_missing_value(self):
        """The least harmful of the three options: leaving it would show the system's mechanics, raising
        would turn an incomplete template into a notification never delivered.
        """
        assert compose("Hello {$a->user}", {}) == "Hello "

    def test_no_placeholders(self):
        assert compose("You have a request", {"user": "Anna"}) == "You have a request"

    def test_markup_preserved(self):
        composed = compose('<a href="{$a->url}">rispondi</a>', {"url": "https://x/y"})
        assert composed == '<a href="https://x/y">rispondi</a>'


class TestFullName:
    """Composition of a person's name."""

    def test_default_form(self):
        assert full_name(person(1, first_name="Anna", last_name="Rossi")) == "Anna Rossi"

    def test_language_delegation(self):
        assert full_name(person(1), "language") == "Anna Rossi"

    def test_configured_format(self):
        assert full_name(person(1), "lastname firstname") == "Rossi Anna"

    def test_hidden_fields_removed(self):
        """The view exposes two name fields only."""
        assert full_name(person(1), "firstname middlename lastname") == "Anna Rossi"
        assert full_name(person(1), "alternatename lastname") == "Rossi"

    def test_empty_format(self):
        assert full_name(person(1), "") == "Anna Rossi"
        assert full_name(person(1), None) == "Anna Rossi"

    def test_missing_name(self):
        assert full_name(person(1, first_name="", last_name="Rossi")) == "Rossi"
