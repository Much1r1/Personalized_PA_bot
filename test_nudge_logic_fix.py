import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import re

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

def test_intent_guard_regex():
    regex = r"\?|how|why|what"
    assert re.search(regex, "how does this work?", re.IGNORECASE)
    assert re.search(regex, "What is a nudge?", re.IGNORECASE)
    assert re.search(regex, "why am I getting this?", re.IGNORECASE)
    assert re.search(regex, "Tell me?", re.IGNORECASE)
    assert not re.search(regex, "Nudge me please", re.IGNORECASE)
    assert not re.search(regex, "I am working", re.IGNORECASE)

@pytest.mark.anyio
@patch("main.get_user_state")
@patch("httpx.AsyncClient.post")
async def test_send_telegram_message_cooldown(mock_post, mock_get_state):
    main.MUCHIRI_CHAT_ID = "12345"
    main.last_nudge_sent_at = datetime.now(ZoneInfo("Africa/Nairobi")) - timedelta(minutes=30)

    # Attempt to send a proactive nudge within 1 hour
    await main.send_telegram_message("12345", "Nudge", reminder_type="task_nudge")

    # Should NOT have called httpx.post
    assert not mock_post.called

    # Reset last_nudge_sent_at to more than 1 hour ago
    main.last_nudge_sent_at = datetime.now(ZoneInfo("Africa/Nairobi")) - timedelta(hours=2)
    mock_get_state.return_value = {"is_muted": False}
    mock_post.return_value = MagicMock(status_code=200)
    mock_post.return_value.raise_for_status = MagicMock()

    await main.send_telegram_message("12345", "Nudge", reminder_type="task_nudge")

    # Should HAVE called httpx.post
    assert mock_post.called

@pytest.mark.anyio
@patch("main.get_user_state")
@patch("httpx.AsyncClient.post")
async def test_send_telegram_message_mute(mock_post, mock_get_state):
    main.MUCHIRI_CHAT_ID = "12345"
    main.last_nudge_sent_at = None

    # User is muted
    now = datetime.now(ZoneInfo("Africa/Nairobi"))
    muted_until = (now + timedelta(hours=1)).isoformat()
    mock_get_state.return_value = {"is_muted": True, "muted_until": muted_until}

    await main.send_telegram_message("12345", "Nudge", reminder_type="task_nudge")

    # Should NOT have called httpx.post
    assert not mock_post.called

    # Alarm should bypass mute
    await main.send_telegram_message("12345", "Alarm", reminder_type="alarm")
    assert mock_post.called

@pytest.mark.anyio
@patch("main.intent_classifier.classify")
@patch("main.get_llm_response")
@patch("main.send_telegram_message")
@patch("main.supabase.table")
async def test_webhook_intent_guard(mock_table, mock_send, mock_llm, mock_classify):
    # Mocking necessary parts for the webhook
    mock_classify.return_value = {"category": "Nudge"}
    mock_llm.return_value = "LLM response"

    # Mocking state updates
    mock_table.return_value.update.return_value.eq.return_value.execute = MagicMock()
    mock_table.return_value.insert.return_value.execute = MagicMock()

    # Case 1: Inquiry message should skip automated nudge
    payload = {
        "message": {
            "chat": {"id": 12345},
            "from": {"id": 12345},
            "text": "How does the nudge work?"
        }
    }

    from fastapi import BackgroundTasks
    # We need to mock background tasks or handle them
    class MockRequest:
        async def json(self):
            return payload

    bg_tasks = BackgroundTasks()
    await main.telegram_webhook(MockRequest(), bg_tasks)

    # Ensure get_nudge_message (via send_telegram_message with manual_nudge_request) was NOT called
    # Note: send_telegram_message will still be called for the LLM response
    nudge_calls = [call for call in mock_send.call_args_list if call.kwargs.get("reminder_type") == "manual_nudge_request"]
    assert len(nudge_calls) == 0

    # Case 2: Regular nudge request
    payload["message"]["text"] = "Nudge me"
    await main.telegram_webhook(MockRequest(), bg_tasks)
    nudge_calls = [call for call in mock_send.call_args_list if call.kwargs.get("reminder_type") == "manual_nudge_request"]
    assert len(nudge_calls) == 1
