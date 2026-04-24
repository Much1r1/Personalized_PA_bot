import pytest
import uuid
from unittest.mock import MagicMock, patch
from pomodoro_service import PomodoroService

@pytest.fixture
def mock_supabase():
    return MagicMock()

@pytest.fixture
def pomodoro_service(mock_supabase):
    return PomodoroService(mock_supabase)

@pytest.mark.asyncio
async def test_start_session_uuid_conversion(pomodoro_service, mock_supabase):
    telegram_user_id = "123456789"
    mock_table = mock_supabase.table.return_value
    mock_insert = mock_table.insert.return_value
    mock_execute = mock_insert.execute.return_value

    # The expected UUID from uuid.uuid5(uuid.NAMESPACE_DNS, "telegram:123456789")
    expected_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"telegram:{telegram_user_id}"))

    mock_execute.data = [{"id": "session-uuid", "user_id": expected_uuid}]

    # Mock stop_session to avoid actual DB call
    with patch.object(pomodoro_service, 'stop_session', return_value=True):
        await pomodoro_service.start_session(telegram_user_id)

    mock_supabase.table.assert_any_call("pomodoro_sessions")
    args, kwargs = mock_table.insert.call_args
    assert args[0]["user_id"] == expected_uuid
    # Verify it IS a valid UUID
    uuid.UUID(args[0]["user_id"])

@pytest.mark.asyncio
async def test_start_session_with_actual_uuid(pomodoro_service, mock_supabase):
    existing_uuid = str(uuid.uuid4())
    mock_table = mock_supabase.table.return_value
    mock_insert = mock_table.insert.return_value
    mock_execute = mock_insert.execute.return_value
    mock_execute.data = [{"id": "session-uuid", "user_id": existing_uuid}]

    with patch.object(pomodoro_service, 'stop_session', return_value=True):
        await pomodoro_service.start_session(existing_uuid)

    args, kwargs = mock_table.insert.call_args
    assert args[0]["user_id"] == existing_uuid
