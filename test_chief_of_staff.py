import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timedelta
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

# Mock environment variables
with patch.dict(os.environ, {
    "TELEGRAM_TOKEN": "fake_token",
    "SUPABASE_URL": "https://fake.supabase.co",
    "SUPABASE_KEY": "fake_key",
    "GROQ_API_KEY": "fake_groq_key",
    "GEMINI_API_KEY": "fake_gemini_key",
    "MUCHIRI_CHAT_ID": "12345"
}):
    import main

@pytest.fixture
def anyio_backend():
    return 'asyncio'

@pytest.mark.anyio
@patch("main.supabase")
@patch("main.send_telegram_message")
@patch("main.genai.GenerativeModel")
async def test_evaluate_project_velocity_stalled(mock_genai, mock_send_msg, mock_supabase):
    # Mock projects
    mock_supabase.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(data=[
        {"id": 1, "name": "Portfolio", "priority": 10, "status": "active", "deadline": None}
    ])

    # Mock activity logs (old update)
    old_time = (datetime.now(ZoneInfo("Africa/Nairobi")) - timedelta(hours=13)).isoformat()
    mock_supabase.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[
        {"created_at": old_time}
    ])

    # Mock Gemini
    mock_model = MagicMock()
    mock_model.generate_content_async = AsyncMock(return_value=MagicMock(text="Test Nudge"))
    mock_genai.return_value = mock_model

    await main.evaluate_project_velocity()

    mock_send_msg.assert_called()
    assert any("Test Nudge" in call.args[1] for call in mock_send_msg.call_args_list)

@pytest.mark.anyio
@patch("main.supabase")
@patch("main.send_telegram_message")
async def test_evaluate_habit_velocity_broken_dopamine(mock_send_msg, mock_supabase):
    # Mock habits
    last_completed = (datetime.now(ZoneInfo("Africa/Nairobi")) - timedelta(days=2)).isoformat()
    mock_supabase.table.return_value.select.return_value.execute.return_value = MagicMock(data=[
        {"name": "dopamine_integrity", "streak": 0, "last_completed_at": last_completed}
    ])

    await main.evaluate_habit_velocity()

    mock_send_msg.assert_called()
    args, kwargs = mock_send_msg.call_args
    assert "Systems Failure Alert" in args[1]
    assert kwargs["reminder_type"] == "habit_alert"

@pytest.mark.anyio
@patch("main.supabase")
@patch("main.send_telegram_message")
async def test_research_command(mock_send_msg, mock_supabase):
    mock_insert = MagicMock()
    mock_supabase.table.return_value.insert.return_value = mock_insert

    # Mock FastAPI Request
    mock_request = MagicMock()
    mock_request.json = AsyncMock(return_value={
        "message": {
            "chat": {"id": 12345},
            "text": "/research GNN Architecture"
        }
    })

    # BackgroundTasks
    bg_tasks = MagicMock()

    await main.telegram_webhook(mock_request, bg_tasks)

    mock_supabase.table.assert_any_call("knowledge_graph")
    mock_insert.execute.assert_called()
    mock_send_msg.assert_called()
    assert "latent space is expanding" in mock_send_msg.call_args[0][1]
