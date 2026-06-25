import os
import json
import asyncio
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
import google.generativeai as genai
from supabase import Client
from proactive_service import ProactiveService, NotificationLogCreate, NotificationStatus
from telegram_client import TelegramClient

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

class ProactiveEngine:
    def __init__(self, supabase: Client, telegram: TelegramClient, gemini_api_key: str):
        self.supabase = supabase
        self.telegram = telegram
        self.proactive_service = ProactiveService(supabase)
        if gemini_api_key:
            genai.configure(api_key=gemini_api_key)
        self.model = genai.GenerativeModel("gemini-1.5-flash")

    async def generate_morning_brief(self, chat_id: str):
        """
        Aggregates today's priorities, calendar, and habits to generate a context-aware brief.
        """
        now = datetime.now(ZoneInfo("Africa/Nairobi"))
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()

        # 1. Fetch Calendar
        cal_res = self.supabase.table("user_schedules")\
            .select("*")\
            .eq("chat_id", chat_id)\
            .gte("start_time", start_of_day)\
            .lte("start_time", end_of_day)\
            .order("start_time")\
            .execute()

        # 2. Fetch High Impact Tasks
        tasks_res = self.supabase.table("user_tasks")\
            .select("*")\
            .eq("chat_id", chat_id)\
            .eq("status", "pending")\
            .gte("impact_score", 7)\
            .execute()

        # 3. Fetch Habits (e.g. dopamine_integrity)
        habits_res = self.supabase.table("habits")\
            .select("*")\
            .execute()

        context_prompt = f"""
        Current Time: {now.strftime('%Y-%m-%d %H:%M:%S')}
        User: Muchiri (High-performance Engineer)

        Today's Schedule:
        {json.dumps(cal_res.data, indent=2)}

        Priority Tasks (Impact >= 7):
        {json.dumps(tasks_res.data, indent=2)}

        Habit Status:
        {json.dumps(habits_res.data, indent=2)}

        Generate a sharp, proactive morning briefing.
        Tone: Senior Executive Assistant, direct, no fluff, witty but professional.
        Address Muchiri as 'Chief' or 'G'.
        Focus on the most critical high-impact task and any tight calendar gaps.
        """

        try:
            response = await self.model.generate_content_async(context_prompt)
            brief_text = response.text.strip()
        except Exception as e:
            brief_text = "Morning Chief. You've got a busy stack today. Check your calendar and crush those high-impact tasks."
            print(f"Gemini Error in morning brief: {e}")

        # Send and Log
        await self.telegram.send_message(chat_id, brief_text)
        await self.proactive_service.create_log(NotificationLogCreate(
            chat_id=chat_id,
            notification_type="morning_brief",
            content=brief_text,
            metadata={"generated_at": now.isoformat()}
        ))

    async def evaluate_nudges(self):
        """
        Scans for dispatched notifications and sends context-aware follow-up nudges if uncompleted.
        """
        pending_logs = await self.proactive_service.get_dispatch_pending()

        for log in pending_logs:
            chat_id = log["chat_id"]
            entity_type = log["entity_type"]
            entity_id = log["entity_id"]

            # Check if the entity is actually still pending
            is_still_pending = True
            if entity_type == "user_tasks" and entity_id:
                task_res = self.supabase.table("user_tasks").select("status").eq("id", entity_id).execute()
                if task_res.data and task_res.data[0]["status"] == "completed":
                    is_still_pending = False
                    await self.proactive_service.update_status(log["id"], NotificationStatus.COMPLETED)

            if is_still_pending:
                # Generate a context-aware nudge
                nudge_prompt = f"""
                You previously sent this message to Muchiri: "{log['content']}"
                It's been over an hour and he hasn't acknowledged it or completed the task.

                Generate a sharp, non-cringe follow-up nudge.
                Keep it under 2 sentences. Be direct.
                Example: "Yo Muchiri, still waiting on that Project ZAYN update. Let's not let the streak slip."
                """

                try:
                    response = await self.model.generate_content_async(nudge_prompt)
                    nudge_text = response.text.strip()
                except Exception as e:
                    nudge_text = f"Following up on this: {log['content'][:50]}..."

                # Send Nudge
                await self.telegram.send_message(chat_id, nudge_text)

                # Update Log (increments nudge_count, might flip to STALE)
                await self.proactive_service.increment_nudge(log["id"], nudge_text)
