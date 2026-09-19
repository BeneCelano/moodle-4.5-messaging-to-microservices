# NotificationService

Notifications and the popup panel, extracted from Moodle in phase 5. Two entry points:

- **HTTP**, called by the monolith: `POST /api/v1/notifications` writes a notification, the reads
  under `/api/v1/users/{id}/notifications` serve the panel and the counters, `/notifications/{id}/read`
  and `/users/{id}/notifications/read-all` mark, `/maintenance/cleanup` is called by the scheduled
  task, `/users/{id}/personal-data/*` serves the privacy provider, `/templates` receives the
  templates. Bearer token, documentation on `/docs`.
- **Queue**: it consumes `conversation.contactrequest.created` from the ConversationService and writes
  the contact request notification, and it publishes `notification.created`, which the DeliveryGateway
  consumes. Exchange `moodle.messaging`, routing key = event type, deduplication on `event_id`
  through `processed_events`.

The users and the site settings are not copied: they are read through `user_view` and `site_view`,
created by `scripts/schema.sql` on the monolith database.

Code in `app/` (domain, application with the event handler and the text composition, infrastructure
with repositories, outbox relay and consumer, api, core), schema revision in `migrations/`, seed,
reconciliation and smoke test in `scripts/`.

The contact request templates, one per language, are seeded from the monolith, which owns the
language packs:

```bash
sudo -u www-data php message/cli/seed_notification_templates.php
```

Unit tests, on SQLite in memory with a double of the two views:

```bash
python -m venv .venv && ./.venv/Scripts/pip install -e ".[dev]" && ./.venv/Scripts/python.exe -m pytest -q
```
