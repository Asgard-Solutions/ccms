"""
In-process async pub/sub event bus.

This mimics a distributed message broker (RabbitMQ/Azure Service Bus) so that
service modules stay decoupled. When migrating to a real broker, only this
module needs to be replaced; publishers and subscribers keep the same API.

Event naming convention mirrors RabbitMQ topic exchanges:
  <aggregate>.<past-tense-verb>   e.g. "appointment.booked"
"""
import asyncio
import logging
from collections import defaultdict
from typing import Awaitable, Callable, Any

logger = logging.getLogger("event_bus")

Handler = Callable[[dict], Awaitable[Any]]

_subscribers: dict[str, list[Handler]] = defaultdict(list)


def subscribe(event_name: str, handler: Handler) -> None:
    _subscribers[event_name].append(handler)
    logger.info("Subscribed handler %s to event %s", handler.__name__, event_name)


async def publish(event_name: str, payload: dict) -> None:
    handlers = _subscribers.get(event_name, [])
    logger.info("Event %s published to %d handler(s)", event_name, len(handlers))
    if not handlers:
        return
    # Fire handlers concurrently; isolate failures so one bad subscriber
    # does not break the others (same semantics as a broker with DLQ).
    results = await asyncio.gather(
        *[_safe_call(h, event_name, payload) for h in handlers],
        return_exceptions=False,
    )
    return results


async def _safe_call(handler: Handler, event_name: str, payload: dict) -> None:
    try:
        await handler(payload)
    except Exception as exc:  # noqa: BLE001 - deliberate catch-all for bus isolation
        logger.exception(
            "Event handler %s failed for event %s: %s",
            handler.__name__,
            event_name,
            exc,
        )


def clear() -> None:
    """Testing helper."""
    _subscribers.clear()
