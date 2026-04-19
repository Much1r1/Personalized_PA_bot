import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

with patch.dict(os.environ, {
    "TELEGRAM_TOKEN": "fake_token",
    "SUPABASE_URL": "https://fake.supabase.co",
    "SUPABASE_KEY": "fake_key",
    "GROQ_API_KEY": "fake_groq_key",
    "GEMINI_API_KEY": "fake_gemini_key"
}):
    from main import app, get_llm_response, current_chat_id

@pytest.fixture
def anyio_backend():
    return 'asyncio'

@pytest.mark.anyio
@patch("main.genai.GenerativeModel")
@patch("main.fetch_chat_history")
@patch("main.send_telegram_message")
@patch("main.dispatcher.dispatch")
async def test_schedule_tool_call(mock_dispatch, mock_send_msg, mock_history, mock_model_class):
    # Setup
    current_chat_id.set(12345)
    mock_history.return_value = []

    # Mock Model and Chat
    mock_model = MagicMock()
    mock_model_class.return_value = mock_model
    mock_chat = AsyncMock()
    mock_model.start_chat.return_value = mock_chat

    # Mock Tool Call Response
    mock_tool_call = MagicMock()
    mock_tool_call.name = "get_schedule"
    mock_tool_call.args = {"max_results": 3}

    mock_part = MagicMock()
    mock_part.function_call = mock_tool_call

    mock_content = MagicMock()
    mock_content.parts = [mock_part]

    mock_candidate = MagicMock()
    mock_candidate.content = mock_content

    mock_response_with_tool = MagicMock()
    mock_response_with_tool.candidates = [mock_candidate]

    # Final Response (after tool execution)
    mock_final_response = MagicMock()
    mock_final_response.candidates = [MagicMock(content=MagicMock(parts=[MagicMock(function_call=None)]))]
    mock_final_response.text = "Here is your schedule for April 20th: - Event 1"

    mock_chat.send_message_async.side_effect = [mock_response_with_tool, mock_final_response]

    # Mock Tool Execution
    mock_dispatch.return_value = "- Event 1"

    # Execute
    with patch.dict(os.environ, {"GEMINI_API_KEY": "fake_key"}):
        response = await get_llm_response("Pull my schedule for April 20th")

    # Assertions
    assert "Event 1" in response
    mock_dispatch.assert_called_once()
    args, kwargs = mock_dispatch.call_args
    assert args[0].name == "get_schedule"
    print("Tool call verified!")
