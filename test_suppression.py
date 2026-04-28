import os
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Mock env
with patch.dict(os.environ, {
    "TELEGRAM_TOKEN": "fake_token",
    "SUPABASE_URL": "https://fake.supabase.co",
    "SUPABASE_KEY": "fake_key",
    "GROQ_API_KEY": "fake_groq_key",
    "MUCHIRI_CHAT_ID": "12345"
}):
    import main

@pytest.fixture
def anyio_backend():
    return 'asyncio'

@pytest.mark.anyio
@patch("main.supabase.table")
async def test_should_skip_reminder(mock_table):
    # Mock return data for a recently sent reminder
    mock_execute = MagicMock()
    mock_execute.execute.return_value = MagicMock(data=[{"content": "Test reminder"}])
    mock_table.return_value.select.return_value.eq.return_value.eq.return_value.gte.return_value = mock_execute

    skip = await main.should_skip_reminder("123", "task_nudge", "Test reminder")
    assert skip is True

    # Mock no data
    mock_execute.execute.return_value = MagicMock(data=[])
    skip = await main.should_skip_reminder("123", "task_nudge", "Different")
    assert skip is False

@pytest.mark.anyio
@patch("main.get_user_state")
@patch("main.get_user_context")
@patch("main.supabase.table")
async def test_nudge_engine_suppression(mock_table, mock_get_ctx, mock_get_state):
    engine = main.NudgeEngine(MagicMock())

    now = datetime.now(ZoneInfo("Africa/Nairobi"))

    # 1. Test Nag Kill-Switch (interaction < 10m ago)
    mock_get_state.return_value = {
        "last_user_interaction_at": (now - timedelta(minutes=5)).isoformat(),
        "pomodoro_active": False
    }
    mock_get_ctx.return_value = {"current_block_type": None}

    # Mock tasks due
    mock_execute = MagicMock()
    mock_execute.execute.return_value = MagicMock(data=[{"id": 1, "chat_id": "123", "title": "Task 1"}])
    mock_table.return_value.select.return_value.eq.return_value.lte.return_value.is_.return_value.is_.return_value = mock_execute

    # We'll patch send_telegram_message to see if it's called
    with patch("main.send_telegram_message", new_callable=AsyncMock) as mock_send:
        # We need to run one iteration of the loop logic.
        # Since run() is a while True, we might need to refactor or use a timeout.
        # Let's mock asyncio.sleep to raise an exception to break the loop
        with patch("asyncio.sleep", side_effect=InterruptedError):
            try:
                await engine.run()
            except InterruptedError:
                pass

        # Should NOT be called due to 10m rule
        mock_send.assert_not_called()

    # 2. Test Pomodoro Lock
    mock_get_state.return_value = {
        "last_user_interaction_at": (now - timedelta(hours=1)).isoformat(),
        "pomodoro_active": True
    }
    with patch("main.send_telegram_message", new_callable=AsyncMock) as mock_send:
        with patch("asyncio.sleep", side_effect=InterruptedError):
            try:
                await engine.run()
            except InterruptedError:
                pass
        mock_send.assert_not_called()

from unittest.mock import AsyncMock
