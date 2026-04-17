import os
import json
from unittest.mock import patch, MagicMock, AsyncMock

# Mock environment variables before importing the app
with patch.dict(os.environ, {
    "SUPABASE_URL": "https://fake.supabase.co",
    "SUPABASE_KEY": "fake_key",
    "GOOGLE_CLIENT_ID": "fake_id",
    "GOOGLE_PROJECT_ID": "fake_project",
    "GOOGLE_CLIENT_SECRET": "fake_secret",
    "GOOGLE_REDIRECT_URI": "http://localhost/callback"
}):
    from sync_service import app

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
@patch("sync_service.supabase.table")
async def test_health_check_success(mock_table, async_client):
    # Mock supabase response for health check
    mock_execute = MagicMock()
    mock_execute.execute.return_value = MagicMock(data=[{"id": 1}])
    mock_table.return_value.select.return_value.limit.return_value = mock_execute

    response = await async_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "SUCCESS", "message": "Connection to Supabase is active."}

@pytest.mark.anyio
@patch("sync_service.supabase.table")
async def test_health_check_failure(mock_table, async_client):
    # Mock supabase failure
    mock_table.side_effect = Exception("Connection timeout")

    response = await async_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "FAILURE"
    assert "Connection timeout" in data["error"]
