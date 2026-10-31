import asyncio
from typing import Any, AsyncIterator, Dict, Set


class EventBroker:
    def __init__(self) -> None:
        self._subscribers: Set[asyncio.Queue] = set()

    async def publish(self, event: Dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    async def subscribe(self) -> AsyncIterator[Dict[str, Any]]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.add(queue)
        try:
            while True:
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=20.0)
                except asyncio.TimeoutError:
                    yield {"type": "heartbeat"}
        finally:
            self._subscribers.discard(queue)


event_broker = EventBroker()
