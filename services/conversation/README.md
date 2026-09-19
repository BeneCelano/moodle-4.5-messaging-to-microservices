# ConversationService

The conversation context extracted from Moodle: conversations (individual, group, self), their
messages and members, read and deletion state per user, contacts, contact requests and blocks. It
stores the message and publishes an event; delivery is someone else's job.

Layout: `app/domain` (entities, events, ports), `app/application` (use cases), `app/infrastructure`
(SQLAlchemy repositories, unit of work with outbox, RabbitMQ publisher and relay), `app/api` (routes),
`app/core` (settings, engine, logging, metrics), `app/main.py` (wiring). `migrations/` holds the
Alembic revision of the schema, `scripts/` the seed from the monolith, the reconciliation and the
smoke test, `tests/` the unit suite.

## API

Everything is under `/api/v1`, with `Authorization: Bearer <token>`; the full list with the schemas
is on `/docs`. The route groups:

- `/conversations` — create, read, enable/disable, members, mute, delete;
- `/conversations/{id}/messages` and `/messages` — send (also `/messages/instant`, which resolves or
  creates the individual conversation first), history, read marks, deletions, search;
- `/users/{id}/conversations` — lists and unread counts, as the message drawer asks them;
- `/contacts`, `/blocks`, `/contact-requests` — the relationships between users.

Probes and metrics outside the prefix: `/healthz`, `/readyz`, `/metrics`.

## Events

Published on the topic exchange `moodle.messaging`, routing key = event type, common envelope
(`event_id`, `event_type`, `event_version`, `occurred_at`, `producer`, `correlation_id`, `payload`):

- `conversation.message.sent` — message id, conversation, sender, recipient ids, text, format,
  source component and event name;
- `conversation.contactrequest.created` — request id, the two users, time;
- `conversation.conversation.created` — conversation id, type, member ids.

At least once: consumers deduplicate on `event_id`.

## Tests

```bash
python -m venv .venv && ./.venv/Scripts/pip install -e ".[dev]" && ./.venv/Scripts/python.exe -m pytest -q
```

(`.venv/bin/` on Linux and macOS.) In-memory SQLite, nothing else needed.
