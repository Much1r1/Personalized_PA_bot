from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime
import uuid

class NotificationStatus(str, Enum):
    DISPATCHED = "dispatched"
    ACKNOWLEDGED = "acknowledged"
    COMPLETED = "completed"
    STALE = "stale"

class NotificationLogBase(BaseModel):
    chat_id: str
    notification_type: str
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)

class NotificationLogCreate(NotificationLogBase):
    pass

class NotificationLog(NotificationLogBase):
    id: uuid.UUID
    status: NotificationStatus
    nudge_count: int
    last_nudge_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

class ProactiveService:
    def __init__(self, supabase_client):
        self.supabase = supabase_client
        self.table = "notification_logs"

    async def create_log(self, log_data: NotificationLogCreate) -> Dict[str, Any]:
        payload = log_data.model_dump()
        payload["status"] = NotificationStatus.DISPATCHED.value
        res = self.supabase.table(self.table).insert(payload).execute()
        return res.data[0]

    async def update_status(self, log_id: str, status: NotificationStatus) -> Dict[str, Any]:
        res = self.supabase.table(self.table).update({"status": status.value}).eq("id", log_id).execute()
        return res.data[0]

    async def increment_nudge(self, log_id: str, new_content: str) -> Dict[str, Any]:
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo
        # Fetch current log to get nudge_count
        current = self.supabase.table(self.table).select("nudge_count").eq("id", log_id).execute()
        if not current.data:
            raise ValueError(f"Log {log_id} not found")

        new_count = current.data[0]["nudge_count"] + 1
        update_data = {
            "nudge_count": new_count,
            "last_nudge_at": datetime.now(ZoneInfo("Africa/Nairobi")).isoformat(),
            "content": new_content
        }

        if new_count >= 2:
            update_data["status"] = NotificationStatus.STALE.value

        res = self.supabase.table(self.table).update(update_data).eq("id", log_id).execute()
        return res.data[0]

    async def get_dispatch_pending(self) -> List[Dict[str, Any]]:
        # Find dispatched notifications that might need a nudge (e.g., older than 1 hour)
        from datetime import timedelta
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            from backports.zoneinfo import ZoneInfo
        threshold = (datetime.now(ZoneInfo("Africa/Nairobi")) - timedelta(hours=1)).isoformat()

        res = self.supabase.table(self.table)\
            .select("*")\
            .eq("status", NotificationStatus.DISPATCHED.value)\
            .lt("created_at", threshold)\
            .execute()
        return res.data

    async def mark_completed_by_entity(self, entity_type: str, entity_id: str):
        self.supabase.table(self.table)\
            .update({"status": NotificationStatus.COMPLETED.value})\
            .eq("entity_type", entity_type)\
            .eq("entity_id", entity_id)\
            .neq("status", NotificationStatus.STALE.value)\
            .execute()
