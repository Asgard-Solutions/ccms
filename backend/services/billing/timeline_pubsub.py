"""In-process pub/sub for ClaimDetail timeline streaming.

WebSocket subscribers register a queue per claim_id; `publish()`
fan-outs every emitted event to every connected subscriber. The
queues are bounded so a slow / disconnected client cannot bloat
memory — we drop the oldest events when the queue is full.

This module is intentionally simple and single-process. A multi-
worker deployment (Gunicorn / multiple Kubernetes replicas) would
need Redis pub/sub or NATS as the transport — out of scope for the
demo. The single-worker uvicorn that ships with CCMS today gets the
benefit without the operational overhead.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any

log = logging.getLogger("ccms.billing.timeline")

# Per-queue cap. Beyond this, the oldest event is dropped to make
# room. A reasonable bound for an idle UI tab.
_QUEUE_MAX = 64

# claim_id -> set of subscriber queues
_SUBSCRIBERS: dict[str, set[asyncio.Queue]] = defaultdict(set)


def subscribe(claim_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
    _SUBSCRIBERS[claim_id].add(q)
    log.debug(
        "billing.timeline.subscribe",
        extra={"claim_id": claim_id, "subscribers": len(_SUBSCRIBERS[claim_id])},
    )
    return q


def unsubscribe(claim_id: str, q: asyncio.Queue) -> None:
    if claim_id in _SUBSCRIBERS:
        _SUBSCRIBERS[claim_id].discard(q)
        if not _SUBSCRIBERS[claim_id]:
            _SUBSCRIBERS.pop(claim_id, None)


def publish(claim_id: str, event: dict[str, Any]) -> None:
    """Push an event to every subscriber. Non-blocking — drops the
    oldest queued event when a subscriber's queue is full. Never
    raises so callers don't have to wrap us in a try."""
    qs = _SUBSCRIBERS.get(claim_id)
    if not qs:
        return
    for q in list(qs):
        if q.full():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            q.put_nowait(event)
        except Exception:
            # Subscriber closed mid-iteration — let the WS loop
            # reap it on next read; nothing to do here.
            pass


def subscriber_count(claim_id: str) -> int:
    return len(_SUBSCRIBERS.get(claim_id, ()))
