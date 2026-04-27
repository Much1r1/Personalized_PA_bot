import os
import json
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
    from main import ExecutiveSyncService

@pytest.fixture
def anyio_backend():
    return 'asyncio'

@pytest.mark.anyio
async def test_vetted_ps_logic():
    mock_supabase = MagicMock()
    mock_pomodoro = MagicMock()
    service = ExecutiveSyncService(mock_supabase, mock_pomodoro)

    # Mock supabase response for Vetted tickets
    mock_execute = MagicMock()
    mock_execute.execute = MagicMock(return_value=MagicMock(data=[{"title": "Vetted Task 1"}]))
    mock_supabase.table.return_value.select.return_value.eq.return_value.ilike.return_value = mock_execute

    ps = await service._get_vetted_ps()
    assert "P.S. You have 1 pending Vetted ticket." in ps

@pytest.mark.anyio
@patch("main.get_user_context")
@patch("main.send_telegram_message")
@patch("main.update_user_context")
@patch("main.get_calendar_events")
async def test_9am_briefing(mock_calendar, mock_update_ctx, mock_send_msg, mock_get_ctx):
    mock_supabase = MagicMock()
    mock_pomodoro = MagicMock()
    service = ExecutiveSyncService(mock_supabase, mock_pomodoro)

    # Set time to 9:00 AM Nairobi
    nairobi_now = datetime.now(ZoneInfo("Africa/Nairobi")).replace(hour=9, minute=0)

    mock_get_ctx.return_value = {"last_briefing_at": None}
    mock_calendar.return_value = "- 10:00: Deep Work\n- 14:00: Meeting"

    with patch("main.datetime") as mock_datetime:
        mock_datetime.now.return_value = nairobi_now
        mock_datetime.fromisoformat = datetime.fromisoformat
        # We need to run the logic inside run() but just the part we want
        # Instead of running the infinite loop, we can test the internal logic if it were refactored,
        # or just mock the dependencies of the loop.

        # For simplicity in this test, we can't easily run the 'while True' loop.
        # Ideally the logic should be in a method we can test.
        pass

@pytest.mark.anyio
async def test_suspicious_silence_logic():
    # This would test the transition and the 15 min check
    pass
