import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

# Mock environment variables before importing main
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

    with patch("main.send_telegram_message", new_callable=AsyncMock) as mock_send:
        await engine.check_alerts()
        mock_send.assert_not_called()

    # 2. Test Pomodoro Lock
    mock_get_state.return_value = {
        "last_user_interaction_at": (now - timedelta(hours=1)).isoformat(),
        "pomodoro_active": True
    }
    with patch("main.send_telegram_message", new_callable=AsyncMock) as mock_send:
        await engine.check_alerts()
        mock_send.assert_not_called()
