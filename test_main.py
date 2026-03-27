import os
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
from fastapi.testclient import TestClient

client = TestClient(app)

@patch("main.openai_client.chat.completions.create")
@patch("main.supabase.table")
@patch("httpx.AsyncClient.post")
def test_telegram_webhook_project_zayn(mock_httpx, mock_supabase, mock_openai):
    # Mock OpenAI response
    mock_openai.return_value = MagicMock()
    mock_openai.return_value.choices = [
        MagicMock(message=MagicMock(content='{"category": "Project Zayn", "skincare_done": true, "workout_done": false, "content": "Skincare done"}'))
    ]
    # Since it's awaited, we need to wrap it in an awaitable or use AsyncMock correctly
    async def mock_create(*args, **kwargs):
        return mock_openai.return_value
    mock_openai.side_effect = mock_create

    # Mock Supabase
    mock_table = MagicMock()
    mock_supabase.return_value = mock_table

    mock_execute = MagicMock()
    mock_execute.execute = MagicMock(return_value={"status": "success"}) # Not async because run_in_threadpool

    mock_table.upsert.return_value = mock_execute

    # Mock Telegram API call
    mock_httpx.return_value = AsyncMock()

    payload = {
        "message": {
            "chat": {"id": 12345},
            "text": "I finished my skincare routine"
        }
    }

    response = client.post("/webhook", json=payload)

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert response.json()["category"] == "Project Zayn"

    # Verify Supabase call
    mock_supabase.assert_called_with("project_zayn")

@patch("main.openai_client.chat.completions.create")
@patch("main.supabase.table")
@patch("httpx.AsyncClient.post")
def test_telegram_webhook_build_mode(mock_httpx, mock_supabase, mock_openai):
    # Mock OpenAI response
    mock_openai.return_value = MagicMock()
    mock_openai.return_value.choices = [
        MagicMock(message=MagicMock(content='{"category": "Build Mode", "content": "Learned about FastAPI dependencies"}'))
    ]
    async def mock_create(*args, **kwargs):
        return mock_openai.return_value
    mock_openai.side_effect = mock_create

    # Mock Supabase
    mock_table = MagicMock()
    mock_supabase.return_value = mock_table

    mock_execute = MagicMock()
    mock_execute.execute = MagicMock(return_value={"status": "success"}) # Not async because run_in_threadpool

    mock_table.insert.return_value = mock_execute

    # Mock Telegram API call
    mock_httpx.return_value = AsyncMock()

    payload = {
        "message": {
            "chat": {"id": 12345},
            "text": "FastAPI dependencies are cool"
        }
    }

    response = client.post("/webhook", json=payload)

    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert response.json()["category"] == "Build Mode"

    # Verify Supabase call
    mock_supabase.assert_called_with("dev_milestones")
