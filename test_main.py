import os
import json
from unittest.mock import patch, MagicMock, AsyncMock

# Mock environment variables before importing main
with patch.dict(os.environ, {
    "TELEGRAM_TOKEN": "fake_token",
    "SUPABASE_URL": "https://fake.supabase.co",
    "SUPABASE_KEY": "fake_key",
    "OPENAI_API_KEY": "fake_openai_key"
}):
    from main import app

import pytest
from httpx import AsyncClient, ASGITransport

@pytest.fixture
def anyio_backend():
    return 'asyncio'

@pytest.fixture
async def async_client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client

@pytest.mark.anyio
@patch("main.openai_client.chat.completions.create")
@patch("main.genai.GenerativeModel")
@patch("main.supabase.table")
@patch("main.send_telegram_draft")
@patch("main.send_telegram_message")
async def test_telegram_webhook_full_cycle(mock_send_msg, mock_send_draft, mock_supabase, mock_gemini, mock_openai, async_client):
    # 1. Mock OpenAI Intent Classification
    mock_intent_response = MagicMock()
    mock_intent_response.choices = [
        MagicMock(message=MagicMock(content='{"category": "Build Mode", "content": "FastAPI testing"}'))
    ]
    mock_openai.return_value = AsyncMock(return_value=mock_intent_response)()

    # 2. Mock Gemini Chat Completion (Streaming)
    mock_model = MagicMock()
    mock_gemini.return_value = mock_model
    mock_chat = MagicMock()
    mock_model.start_chat.return_value = mock_chat

    mock_chunk1 = MagicMock()
    mock_chunk1.text = "Hello "
    mock_chunk2 = MagicMock()
    mock_chunk2.text = "world!"

    async def mock_send_message_async(*args, **kwargs):
        async def gen():
            yield mock_chunk1
            yield mock_chunk2
        return gen()

    mock_chat.send_message_async.side_effect = mock_send_message_async

    # 3. Mock Supabase
    mock_table = MagicMock()
    mock_supabase.return_value = mock_table

    mock_execute = MagicMock()
    mock_execute.execute = MagicMock(return_value=MagicMock(data=[{"role": "user", "content": "Hi"}]))

    mock_table.select.return_value.eq.return_value.order.return_value.limit.return_value = mock_execute
    mock_table.insert.return_value = mock_execute
    mock_table.upsert.return_value = mock_execute

    # 4. Mock Telegram calls (already patched)
    mock_send_msg.return_value = AsyncMock()
    mock_send_draft.return_value = AsyncMock()

    payload = {
        "message": {
            "chat": {"id": 12345},
            "text": "Tell me about FastAPI"
        }
    }

    response = await async_client.post("/webhook", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["category"] == "Build Mode"

    # Verify Supabase calls
    assert mock_supabase.call_count >= 3
    assert mock_send_msg.called

@pytest.mark.anyio
@patch("main.openai_client.chat.completions.create")
@patch("main.genai.GenerativeModel")
@patch("main.supabase.table")
@patch("main.send_telegram_draft")
@patch("main.send_telegram_message")
async def test_telegram_webhook_project_zayn(mock_send_msg, mock_send_draft, mock_supabase, mock_gemini, mock_openai, async_client):
    # 1. Mock OpenAI Intent Classification
    mock_intent_response = MagicMock()
    mock_intent_response.choices = [
        MagicMock(message=MagicMock(content='{"category": "Project Zayn", "skincare_done": true, "workout_done": false, "content": "Skincare done"}'))
    ]
    mock_openai.return_value = AsyncMock(return_value=mock_intent_response)()

    # 2. Mock Gemini Chat Completion (Streaming)
    mock_model = MagicMock()
    mock_gemini.return_value = mock_model
    mock_chat = MagicMock()
    mock_model.start_chat.return_value = mock_chat

    mock_chunk1 = MagicMock()
    mock_chunk1.text = "Logged your skincare!"

    async def mock_send_message_async(*args, **kwargs):
        async def gen():
            yield mock_chunk1
        return gen()

    mock_chat.send_message_async.side_effect = mock_send_message_async

    # 3. Mock Supabase
    mock_table = MagicMock()
    mock_supabase.return_value = mock_table
    mock_execute = MagicMock()
    mock_execute.execute = MagicMock(return_value=MagicMock(data=[]))
    mock_table.select.return_value.eq.return_value.order.return_value.limit.return_value = mock_execute
    mock_table.insert.return_value = mock_execute
    mock_table.upsert.return_value = mock_execute

    # 4. Mock Telegram calls (already patched)
    mock_send_msg.return_value = AsyncMock()

    payload = {
        "message": {
            "chat": {"id": 12345},
            "text": "I did my skincare"
        }
    }

    response = await async_client.post("/webhook", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["category"] == "Project Zayn"
    mock_supabase.assert_any_call("project_zayn")
