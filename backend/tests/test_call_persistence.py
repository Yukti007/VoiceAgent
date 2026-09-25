from app.agent.session import add_call_message, create_call, end_call
from app.database.database import session_scope
from app.database.models import Call, CallMessage


def test_call_lifecycle_and_message_ordering(business_id):
    call_id = create_call(business_id, room_name="test-room-123")

    with session_scope() as session:
        call = session.get(Call, call_id)
        assert call.status == "in_progress"
        assert call.ended_at is None

    add_call_message(call_id, role="assistant", text="Hello, thank you for calling Sharma Dental Care.")
    add_call_message(call_id, role="user", text="Hi, I need a dental appointment.")
    add_call_message(call_id, role="user", text="   ")  # blank text must be dropped

    with session_scope() as session:
        messages = (
            session.query(CallMessage)
            .filter(CallMessage.call_id == call_id)
            .order_by(CallMessage.timestamp)
            .all()
        )
        assert [m.role for m in messages] == ["assistant", "user"]

    end_call(call_id, status="completed")

    with session_scope() as session:
        call = session.get(Call, call_id)
        assert call.status == "completed"
        assert call.ended_at is not None
        assert call.duration_seconds is not None
        assert call.duration_seconds >= 0


async def test_call_message_writer_persists_in_order_off_loop(business_id):
    from app.agent.session import CallMessageWriter

    call_id = create_call(business_id, room_name="test-room-writer")
    writer = CallMessageWriter(call_id)
    for i in range(20):
        writer.enqueue("user" if i % 2 else "assistant", f"message {i}")
    writer.enqueue("user", "  ")  # blank text must be dropped
    await writer.aclose()

    with session_scope() as session:
        texts = [
            m.text
            for m in session.query(CallMessage)
            .filter(CallMessage.call_id == call_id)
            .order_by(CallMessage.timestamp, CallMessage.id)
            .all()
        ]
    assert texts == [f"message {i}" for i in range(20)]
