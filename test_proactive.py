import os
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from fastapi.testclient import TestClient
from datetime import datetime, timezone, timedelta

# Mock environment before imports
mock_env = {
    "INTERNAL_CRON_SECRET": "test_secret",
    "SUPABASE_URL": "https://test.supabase.co",
    "SUPABASE_KEY": "test_key",
    "TELEGRAM_TOKEN": "test_token",
    "MUCHIRI_CHAT_ID": "12345",
    "GROQ_API_KEY": "test_groq_key",
    "GEMINI_API_KEY": "test_gemini_key"
}

with patch.dict(os.environ, mock_env):
    from main import app
    from proactive_service import ProactiveService, NotificationStatus, NotificationLogCreate

client = TestClient(app)

@pytest.fixture
def mock_supabase():
    mock = MagicMock()
    return mock

@pytest.mark.asyncio
async def test_proactive_service_create_log(mock_supabase):
    service = ProactiveService(mock_supabase)
    log_data = NotificationLogCreate(
        chat_id="12345",
        notification_type="test_type",
        content="test content"
    )

    mock_supabase.table().insert().execute.return_value = MagicMock(data=[{"id": "some-uuid", "status": "dispatched"}])

    res = await service.create_log(log_data)
    assert res["status"] == "dispatched"
    mock_supabase.table.assert_called_with("notification_logs")

def test_morning_brief_endpoint_unauthorized():
    response = client.post("/api/v1/proactive/morning-brief")
    assert response.status_code == 403

def test_morning_brief_endpoint_authorized():
    with patch("proactive_router.engine.generate_morning_brief", new_callable=AsyncMock) as mock_brief:
        response = client.post(
            "/api/v1/proactive/morning-brief",
            headers={"Authorization": "Bearer test_secret"}
        )
        assert response.status_code in [200, 202]
        assert response.json()["status"] == "accepted"

@pytest.mark.asyncio
@patch("proactive_engine.genai.GenerativeModel")
async def test_evaluate_nudges_logic(mock_genai, mock_supabase):
    from proactive_engine import ProactiveEngine
    from telegram_client import TelegramClient

    mock_telegram = MagicMock(spec=TelegramClient)
    mock_telegram.send_message = AsyncMock(return_value={"success": True})

    engine = ProactiveEngine(mock_supabase, mock_telegram, "fake_key")

    # Mock pending logs
    threshold = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    mock_supabase.table().select().eq().lt().execute.return_value = MagicMock(data=[
        {
            "id": "log-1",
            "chat_id": "12345",
            "notification_type": "task_reminder",
            "entity_type": "user_tasks",
            "entity_id": "task-101",
            "content": "Original Task Message",
            "nudge_count": 0
        }
    ])

    # Mock task check and nudge count fetch
    mock_task_res = MagicMock()
    mock_task_res.data = [{"status": "pending"}]

    mock_nudge_res = MagicMock()
    mock_nudge_res.data = [{"nudge_count": 0}]

    mock_supabase.table().select().eq().execute.side_effect = [
        mock_task_res,
        mock_nudge_res
    ]

    mock_genai.return_value.generate_content_async = AsyncMock(return_value=MagicMock(text="Follow up nudge"))

    await engine.evaluate_nudges()

    assert mock_telegram.send_message.called
    mock_supabase.table().update.assert_called()
