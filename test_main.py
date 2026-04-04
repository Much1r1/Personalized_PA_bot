import os
import json
from unittest.mock import patch, MagicMock, AsyncMock

with patch.dict(os.environ, {
    "TELEGRAM_TOKEN": "fake_token",
    "SUPABASE_URL": "https://fake.supabase.co",
    "SUPABASE_KEY": "fake_key",
    "GROQ_API_KEY": "fake_groq_key"
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


def make_groq_response(content: str):
    mock_response = MagicMock()
    mock_response.choices = [
        MagicMock(message=MagicMock(content=content))
    ]
    return mock_response


@pytest.mark.anyio
@patch("main.groq_client.chat.completions.create")
@patch("main.supabase.table")
@patch("main.send_telegram_message")
async def test_telegram_webhook_full_cycle(mock_send_msg, mock_supabase, mock_groq, async_client):
    # First call = classify_intent, second call = get_llm_response
    mock_groq.side_effect = [
        make_groq_response('{"category": "Build Mode", "content": "FastAPI testing"}'),
        make_groq_response("Here's what I know about FastAPI testing.")
    ]

    mock_table = MagicMock()
    mock_supabase.return_value = mock_table
    mock_execute = MagicMock()
    mock_execute.execute = MagicMock(return_value=MagicMock(data=[{"role": "user", "content": "Hi"}]))
    mock_table.select.return_value.eq.return_value.order.return_value.limit.return_value = mock_execute
    mock_table.insert.return_value = mock_execute
    mock_table.upsert.return_value = mock_execute

    mock_send_msg.return_value = None

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
    assert mock_send_msg.called


@pytest.mark.anyio
@patch("main.groq_client.chat.completions.create")
@patch("main.supabase.table")
@patch("main.send_telegram_message")
async def test_telegram_webhook_project_zayn(mock_send_msg, mock_supabase, mock_groq, async_client):
    mock_groq.side_effect = [
        make_groq_response('{"category": "Project Zayn", "skincare_done": true, "workout_done": false, "content": "Skincare done"}'),
        make_groq_response("Logged your skincare routine!")
    ]

    mock_table = MagicMock()
    mock_supabase.return_value = mock_table
    mock_execute = MagicMock()
    mock_execute.execute = MagicMock(return_value=MagicMock(data=[]))
    mock_table.select.return_value.eq.return_value.order.return_value.limit.return_value = mock_execute
    mock_table.insert.return_value = mock_execute
    mock_table.upsert.return_value = mock_execute

    mock_send_msg.return_value = None

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