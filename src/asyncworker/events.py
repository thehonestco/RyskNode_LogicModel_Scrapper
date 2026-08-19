"""
Redis Pub/Sub event publisher for task completion events.

After a Celery task completes (success, failure, or retry), the result is
automatically published to a Redis pub/sub channel following the convention:

    <app_name>-events:<event_name>

Channel naming examples:
    rysknode-events:scrape_single_company.completed
    rysknode-events:sync_data_gov_state.failed
    rysknode-events:batch_scrape_companies.retrying

Subscribing (from any service):
    PSUBSCRIBE rysknode-events:*

Architecture:
    ┌──────────┐  task   ┌─────────┐  result    ┌─────────┐  subscribe  ┌───────────┐
    │ FastAPI   │───────►│  Redis   │◄───────── │  Celery  │────────────►│  Redis    │
    │ (API)     │        │ (broker) │           │ (worker) │             │ (pub/sub) │
    └──────────┘        └─────────┘           └─────────┘             └───────────┘
                                                                            │
                                                                    ┌───────┴───────┐
                                                                    │ Any Subscriber │
                                                                    │ (other service)│
                                                                    └───────────────┘
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import redis as redis_lib

logger = logging.getLogger(__name__)

# Default app name prefix for event channels
APP_NAME: str = os.getenv("CELERY_EVENT_APP_NAME", "rysknode")


class EventPublisher:
    """
    Publishes task results to Redis pub/sub event channels.

    Thread-safe: uses a lazy-initialized Redis client.  One instance should
    be shared per process (the module-level singleton handles this).

    Args:
        redis_url:  Redis connection URL.  Defaults to ``CELERY_BROKER_URL``.
        app_name:   Prefix for channel names.  Defaults to ``CELERY_EVENT_APP_NAME``.
    """

    def __init__(
        self,
        redis_url: str | None = None,
        app_name: str | None = None,
    ) -> None:
        self._redis_url: str = redis_url or os.getenv(
            "CELERY_BROKER_URL", "redis://localhost:6379/0"
        )
        self._app_name: str = app_name or APP_NAME
        self._client: redis_lib.Redis | None = None  # type: ignore[type-arg]

    @property
    def client(self) -> redis_lib.Redis:  # type: ignore[type-arg]
        """Lazy-initialize the Redis client on first use."""
        if self._client is None:
            self._client = redis_lib.Redis.from_url(
                self._redis_url,
                decode_responses=True,
            )
        return self._client

    def publish(self, event_name: str, payload: dict[str, Any]) -> int:
        """
        Publish an event to ``<app_name>-events:<event_name>``.

        Args:
            event_name: Dot-separated event identifier
                        (e.g. ``scrape_single_company.completed``).
            payload:    JSON-serializable dict with event data.

        Returns:
            Number of subscribers that received the message.
        """
        channel = f"{self._app_name}-events:{event_name}"
        message = json.dumps(payload, default=str)
        try:
            count: int = self.client.publish(channel, message)
            logger.info("Published event to '%s' (%d subscribers)", channel, count)
            return count
        except redis_lib.RedisError as exc:
            logger.error("Failed to publish event to '%s': %s", channel, exc)
            return 0

    def close(self) -> None:
        """Close the Redis connection and release resources."""
        if self._client is not None:
            self._client.close()
            self._client = None

    @classmethod
    def channel_pattern(cls, app_name: str | None = None) -> str:
        """
        Return the glob pattern for subscribing to all events.

        Usage in a subscriber service::

            import redis
            r = redis.Redis()
            pubsub = r.pubsub()
            pubsub.psubscribe(EventPublisher.channel_pattern())
            for message in pubsub.listen():
                print(message)
        """
        return f"{app_name or APP_NAME}-events:*"


# ── Module-level singleton ───────────────────────────────────────────
_publisher: EventPublisher | None = None


def get_publisher() -> EventPublisher:
    """Return the module-level ``EventPublisher`` singleton (lazy init)."""
    global _publisher
    if _publisher is None:
        _publisher = EventPublisher()
    return _publisher


def publish_event(event_name: str, payload: dict[str, Any]) -> int:
    """
    Convenience function to publish an event via the singleton publisher.

    This is the primary interface used by signal handlers in
    ``asyncworker/__init__.py``.
    """
    return get_publisher().publish(event_name, payload)
