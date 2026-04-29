import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any
from supabase import Client

class PomodoroService:
    def __init__(self, supabase_client: Client):
        self.supabase = supabase_client

    def _get_user_id(self, user_id: str) -> str:
        """Ensures the user_id matches UUID format if it's a Telegram ID."""
        try:
            # Check if it's already a valid UUID
            uuid.UUID(str(user_id))
            return str(user_id)
        except ValueError:
            # If not a UUID, generate a deterministic one from the Telegram ID.
            # This satisfies the requirement for UUID format while maintaining
            # a 1:1 mapping with Telegram users.
            # We use a fixed namespace for consistency.
            namespace = uuid.NAMESPACE_DNS
            return str(uuid.uuid5(namespace, f"telegram:{user_id}"))

    async def start_session(self, user_id: str, duration_minutes: int = 25, task_id: Optional[int] = None, session_type: str = 'work', chat_id: Optional[str] = None) -> Dict[str, Any]:
        """Starts a new Pomodoro session, cancelling any active ones."""
        formatted_user_id = self._get_user_id(user_id)
        await self.stop_session(user_id)

        start_time = datetime.now(timezone.utc)
        end_time = start_time + timedelta(minutes=duration_minutes)

        payload = {
            "user_id": formatted_user_id,
            "chat_id": chat_id or str(user_id),
            "task_id": task_id,
            "start_time": start_time.isoformat(),
            "end_time": end_time.isoformat(),
            "status": "active",
            "type": session_type
        }

        response = self.supabase.table("pomodoro_sessions").insert(payload).execute()
        return response.data[0]

    async def stop_session(self, user_id: str) -> bool:
        """Cancels any active Pomodoro sessions for the user."""
        formatted_user_id = self._get_user_id(user_id)
        response = self.supabase.table("pomodoro_sessions") \
            .update({"status": "cancelled"}) \
            .eq("user_id", formatted_user_id) \
            .eq("status", "active") \
            .execute()
        return len(response.data) > 0

    async def get_active_session(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Retrieves the current active session for a user."""
        formatted_user_id = self._get_user_id(user_id)
        response = self.supabase.table("pomodoro_sessions") \
            .select("*") \
            .eq("user_id", formatted_user_id) \
            .eq("status", "active") \
            .order("start_time", desc=True) \
            .limit(1) \
            .execute()

        if response.data:
            return response.data[0]
        return None

    async def complete_session(self, session_id: str) -> bool:
        """Marks a session as completed."""
        response = self.supabase.table("pomodoro_sessions") \
            .update({"status": "completed"}) \
            .eq("id", session_id) \
            .eq("status", "active") \
            .execute()
        return len(response.data) > 0
