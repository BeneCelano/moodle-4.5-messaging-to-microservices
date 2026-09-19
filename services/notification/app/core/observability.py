"""Metrics exposed to the collector."""

from prometheus_client import Counter, Gauge, Histogram

REQUEST_DURATION = Histogram(
    "notification_request_duration_seconds",
    "Duration of the requests served, by route and outcome.",
    labelnames=("method", "route", "status"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)

REQUESTS_IN_FLIGHT = Gauge(
    "notification_requests_in_flight",
    "Requests currently being processed.",
)

NOTIFICATIONS_PERSISTED = Counter(
    "notification_notifications_persisted_total",
    "Notifications written, by producing component.",
    labelnames=("component",),
)

EVENTS_PUBLISHED = Counter(
    "notification_events_published_total",
    "Events delivered to the broker, by type.",
    labelnames=("event_type",),
)

EVENTS_PUBLISH_FAILED = Counter(
    "notification_events_publish_failed_total",
    "Failed delivery attempts to the broker, by event type.",
    labelnames=("event_type",),
)

OUTBOX_PENDING = Gauge(
    "notification_outbox_pending",
    "Events recorded and not yet delivered to the broker.",
)

EVENTS_CONSUMED = Counter(
    "notification_events_consumed_total",
    "Events received from the other contexts, by type and outcome.",
    labelnames=("event_type", "outcome"),
)
