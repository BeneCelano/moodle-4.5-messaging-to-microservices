# DeliveryGateway

Delivers on email and push what the other two services publish on the queue. Nobody calls it: it
subscribes to `conversation.message.sent` and `notification.created` on the exchange
`moodle.messaging`, decides the channels of each recipient as `message_send()` does in Moodle (site
defaults, user preferences, registered devices, read through four views on the monolith database)
and performs the deliveries with retries. `popup` is not a channel here: it belongs to the
notification context.

## How it works

Two stages, so that replicas can share the work of one big event:

1. acceptance — the event is recorded, the channels resolved, one row per (event, recipient,
   channel) written, one assignment per row queued; a redelivered event is a no-op thanks to the
   unique constraint;
2. execution — each assignment is consumed by any replica, the provider is contacted, a failure is
   retried with growing wait (five attempts) and then parked in `dead_letter` with the whole envelope.

Queues: `<prefix>.events`, `<prefix>.work`, `<prefix>.retry`. HTTP only for `/healthz`, `/readyz` and
`/metrics` (`delivery_gateway_deliveries{status=...}`).

Folders: `app/domain/channels.py` (the channel choice), `app/application/deliver.py` (the two
stages), `app/infrastructure` (repositories, consumer, views, the `email` and `airnotifier`
processors), `scripts/` (`schema.sql`, `seed_processors.sql`, smoke test), `tests/`.

## Tests

```bash
python -m venv .venv && ./.venv/Scripts/pip install -e ".[dev]"
./.venv/Scripts/python.exe -m pytest -q
```

SQLite in memory, fake views, fake processors that fail on command. The channel tests follow the
branches of `message_send()`, including the string `"0"` and the empty preference.
