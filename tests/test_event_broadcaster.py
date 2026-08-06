import asyncio
import anyio
import pytest

from app.services.event_broadcaster import EventBroadcaster


@pytest.mark.anyio
async def test_event_broadcaster_register_unregister():
    broadcaster = EventBroadcaster()
    queue = await broadcaster.register()
    assert len(broadcaster._subscribers) == 1

    await broadcaster.unregister(queue)
    assert len(broadcaster._subscribers) == 0


@pytest.mark.anyio
async def test_event_broadcaster_async_broadcast():
    broadcaster = EventBroadcaster()
    queue = await broadcaster.register()

    payload = {"id": 123, "event_type": "person_entered_roi", "camera_id": 1}
    await broadcaster.broadcast_event_async(payload)

    message = await asyncio.wait_for(queue.get(), timeout=2.0)
    assert "event: alarm" in message
    assert '"id": 123' in message

    await broadcaster.unregister(queue)


@pytest.mark.anyio
async def test_event_broadcaster_sync_broadcast():
    broadcaster = EventBroadcaster()
    loop = asyncio.get_running_loop()
    broadcaster.set_loop(loop)

    queue = await broadcaster.register()

    payload = {"id": 456, "event_type": "crossed_line", "camera_id": 2}
    broadcaster.broadcast_event_sync(payload)

    await asyncio.sleep(0.05)

    message = await asyncio.wait_for(queue.get(), timeout=2.0)
    assert "event: alarm" in message
    assert '"id": 456' in message

    await broadcaster.unregister(queue)
