"""Metrics exposed to the collector."""

from prometheus_client import Counter, Gauge, Histogram

REQUEST_DURATION = Histogram(
    "conversation_request_duration_seconds",
    "Duration of the requests served, by route and outcome.",
    labelnames=("method", "route", "status"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

REQUESTS_IN_FLIGHT = Gauge(
    "conversation_requests_in_flight",
    "Requests currently being processed.",
)

MESSAGES_PERSISTED = Counter(
    "conversation_messages_persisted_total",
    "Messages persisted, by conversation type.",
    labelnames=("conversation_type",),
)

EVENTS_PUBLISHED = Counter(
    "conversation_events_published_total",
    "Events delivered to the broker, by type.",
    labelnames=("event_type",),
)

EVENTS_PUBLISH_FAILED = Counter(
    "conversation_events_publish_failed_total",
    "Failed delivery attempts to the broker, by event type.",
    labelnames=("event_type",),
)

OUTBOX_PENDING = Gauge(
    "conversation_outbox_pending",
    "Events recorded and not yet delivered to the broker.",
)
