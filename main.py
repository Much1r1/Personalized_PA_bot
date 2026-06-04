import os
import base64
import json
from typing import Optional, List, Dict, Any, Callable
from contextvars import ContextVar
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from groq import AsyncGroq
import google.generativeai as genai
from supabase import create_client, Client
import httpx
import asyncio
import re
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from telegram_client import TelegramClient
from dotenv import load_dotenv
from pomodoro_service import PomodoroService
from intent_classifier import IntentClassifier
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from datetime import datetime, timezone, timedelta
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo
from google.auth.transport.requests import Request as GoogleRequest

load_dotenv()

# Environment Variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MUCHIRI_CHAT_ID = os.getenv("MUCHIRI_CHAT_ID")

# Initialize Clients
groq_client = AsyncGroq(api_key=GROQ_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
scheduler = AsyncIOScheduler(timezone=ZoneInfo("Africa/Nairobi"))
pomodoro_service = PomodoroService(supabase)
intent_classifier = IntentClassifier(groq_client, supabase)

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# Context variable to store chat_id
current_chat_id: ContextVar[str] = ContextVar("current_chat_id")

# State for proactive nudges
last_nudge_sent_at: Optional[datetime] = None


async def get_user_state(chat_id: str) -> Dict[str, Any]:
    """Fetch user state from Supabase, creating it if it doesn't exist."""
    try:
        res = await run_in_threadpool(
            lambda: supabase.table("user_state").select("*").eq("chat_id", chat_id).execute()
        )
        if res.data:
            return res.data[0]

        # Create default state if missing
        default_state = {
            "chat_id": chat_id,
            "pomodoro_active": False,
            "is_muted": False,
            "muted_until": None
        }
        res = await run_in_threadpool(
            lambda: supabase.table("user_state").insert(default_state).execute()
        )
        return res.data[0]
    except Exception as e:
        print(f"Error fetching user state: {e}")
        return {
            "chat_id": chat_id,
            "pomodoro_active": False,
            "is_muted": False,
            "muted_until": None
        }


async def update_user_state(chat_id: str, **kwargs):
    """Update user state in Supabase."""
    try:
        kwargs["updated_at"] = datetime.now(ZoneInfo("Africa/Nairobi")).isoformat()
        await run_in_threadpool(
            lambda: supabase.table("user_state").update(kwargs).eq("chat_id", chat_id).execute()
        )
    except Exception as e:
        print(f"Error updating user state: {e}")


async def should_skip_reminder(chat_id: str, reminder_type: str, content: str) -> bool:
    """Check if a similar reminder was sent within the last 5 minutes."""
    try:
        five_mins_ago = (datetime.now(ZoneInfo("Africa/Nairobi")) - timedelta(minutes=5)).isoformat()
        res = await run_in_threadpool(
            lambda: supabase.table("sent_reminders")
            .select("*")
            .eq("chat_id", chat_id)
            .eq("reminder_type", reminder_type)
            .gte("sent_at", five_mins_ago)
            .execute()
        )
        # Check for similar content (simple match for now)
        for reminder in res.data:
            if reminder["content"] == content:
                return True
        return False
    except Exception as e:
        print(f"Error checking sent_reminders: {e}")
        return False


async def log_sent_reminder(chat_id: str, reminder_type: str, content: str):
    """Log a sent reminder to Supabase."""
    try:
        payload = {
            "chat_id": chat_id,
            "reminder_type": reminder_type,
            "content": content,
            "sent_at": datetime.now(ZoneInfo("Africa/Nairobi")).isoformat()
        }
        await run_in_threadpool(
            lambda: supabase.table("sent_reminders").insert(payload).execute()
        )
    except Exception as e:
        print(f"Error logging sent reminder: {e}")


async def get_user_context(chat_id: str) -> Dict[str, Any]:
    """Fetch user context from Supabase, creating it if it doesn't exist."""
    try:
        res = await run_in_threadpool(
            lambda: supabase.table("user_context").select("*").eq("chat_id", chat_id).execute()
        )
        if res.data:
            return res.data[0]

        # Create default context if missing
        default_ctx = {"chat_id": chat_id}
        res = await run_in_threadpool(
            lambda: supabase.table("user_context").insert(default_ctx).execute()
        )
        return res.data[0]
    except Exception as e:
        print(f"Error fetching user context: {e}")
        return {"chat_id": chat_id, "current_block_type": None}


async def update_user_context(chat_id: str, **kwargs):
    """Update user context in Supabase."""
    try:
        kwargs["updated_at"] = datetime.now(ZoneInfo("Africa/Nairobi")).isoformat()
        await run_in_threadpool(
            lambda: supabase.table("user_context").update(kwargs).eq("chat_id", chat_id).execute()
        )
    except Exception as e:
        print(f"Error updating user context: {e}")


def get_google_creds():
    try:
        if not SUPABASE_URL or not SUPABASE_KEY:
            print("❌ M-Bot Error: SUPABASE_URL or KEY is missing from Env Vars")
            return None

        res = supabase.table("system_config").select("value").eq("key", "google_token").execute()
        if not res.data:
            print("❌ M-Bot Error: google_token not found in system_config")
            return None

        token_data = res.data[0]["value"]
        if isinstance(token_data, str):
            token_data = json.loads(token_data)

        # Parse expiry so creds.expired works correctly
        expiry_str = token_data.get("expiry")
        expiry = datetime.fromisoformat(expiry_str) if expiry_str else None

        creds = Credentials(
            token=token_data.get("token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri=token_data.get("token_uri"),
            client_id=token_data.get("client_id") or os.getenv("GOOGLE_CLIENT_ID"),
            client_secret=token_data.get("client_secret") or os.getenv("GOOGLE_CLIENT_SECRET"),
            scopes=token_data.get("scopes"),
            expiry=expiry  # ← critical: without this, creds.expired is always False
        )

        if creds and creds.expired and creds.refresh_token:
            print("🔄 Refreshing Google OAuth token...")
            creds.refresh(GoogleRequest())

            # Persist the refreshed token back to Supabase
            updated_token_data = {
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": list(creds.scopes),
                "expiry": creds.expiry.isoformat() if creds.expiry else None  # ← persist expiry too
            }
            supabase.table("system_config").update({"value": updated_token_data}).eq("key", "google_token").execute()
            print("✅ Google token refreshed and saved to Supabase.")

        return creds
    except Exception as e:
        print(f"⚠️ M-Bot Auth Crash Detail: {type(e).__name__} - {str(e)}")
        return None


SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']


def get_calendar_events(max_results: int = 5) -> str:
    """Queries Google Calendar API directly."""
    try:
        creds = get_google_creds()
        if not creds:
            return "Authentication failed. Check your Google Calendar connection."

        service = build('calendar', 'v3', credentials=creds)

        # Sync to Africa/Nairobi
        now_nairobi = datetime.now(ZoneInfo("Africa/Nairobi"))
        now_iso = now_nairobi.isoformat()

        events_result = service.events().list(
            calendarId='primary',
            timeMin=now_iso,
            maxResults=max_results,
            singleEvents=True,
            orderBy='startTime'
        ).execute()

        events = events_result.get('items', [])

        if not events:
            return "No upcoming events found."

        event_list = []
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            display_time = start.split('T')[1][:5] if 'T' in start else "All Day"
            event_list.append(f"- {display_time}: {event.get('summary')}")

        return "\n".join(event_list)

    except Exception as e:
        print(f"Calendar API Error: {e}")
        return "Couldn't fetch your schedule right now, bro."


async def get_scannable_briefing(chat_id: str) -> str:
    """Pull the day's events from the calendar_items table and format as a Scannable List."""
    try:
        now = datetime.now(ZoneInfo("Africa/Nairobi"))
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()

        res = await run_in_threadpool(
            lambda: supabase.table("calendar_items")
            .select("*")
            .eq("chat_id", chat_id)
            .gte("start_time", start_of_day)
            .lte("start_time", end_of_day)
            .order("start_time")
            .execute()
        )

        if not res.data:
            return "No events scheduled for today, bro."

        lines = ["Today's Schedule:"]
        for item in res.data:
            start_dt = datetime.fromisoformat(item["start_time"].replace('Z', '+00:00')).astimezone(ZoneInfo("Africa/Nairobi"))
            time_str = start_dt.strftime("%H:%M")
            lines.append(f"- {time_str}: {item['summary']}")

        return "\n".join(lines)
    except Exception as e:
        print(f"Error fetching scannable briefing: {e}")
        return "Couldn't pull your scannable briefing right now."


def get_schedule(max_results: int = 5) -> str:
    """Retrieves the user's upcoming calendar events."""
    return get_calendar_events(max_results)


class NudgeRequest(BaseModel):
    message: Optional[str] = "Yo Muchiri, just checking in!"


class BrainDump(BaseModel):
    raw_content: str
    tags: List[str] = []
    metadata: Dict[str, Any] = {}


class FunctionDispatcher:
    def __init__(self):
        self.tools: Dict[str, Callable] = {}

    def register(self, name: str, func: Callable):
        self.tools[name] = func

    async def dispatch(self, tool_call) -> Any:
        func_name = tool_call.name
        args = tool_call.args
        if func_name in self.tools:
            if asyncio.iscoroutinefunction(self.tools[func_name]):
                return await self.tools[func_name](**args)
            else:
                return await run_in_threadpool(lambda: self.tools[func_name](**args))
        raise ValueError(f"Unknown tool: {func_name}")


dispatcher = FunctionDispatcher()
dispatcher.register("get_calendar_events", get_calendar_events)


class NudgeEngine:
    def __init__(self, supabase_client: Client):
        self.supabase = supabase_client

    async def check_alerts(self):
        """
        Job that queries user_tasks and user_alarms.
        Handles proactive messaging and the Escalation Policy.
        """
        try:
            now = datetime.now(ZoneInfo("Africa/Nairobi"))
            escalation_time = now - timedelta(minutes=5)

            # 1. Handle Alarms (Always processed regardless of Silent Mode)
            try:
                alarms_resp = await run_in_threadpool(
                    lambda: self.supabase.table("user_alarms")
                    .select("*")
                    .eq("status", "pending")
                    .lte("alarm_time", now.isoformat())
                    .execute()
                )
                for alarm in alarms_resp.data:
                    chat_id = alarm["chat_id"]
                    await send_telegram_message(chat_id, f"🚨 ALARM: {alarm['message']}", reminder_type="alarm")
                    await run_in_threadpool(
                        lambda: self.supabase.table("user_alarms")
                        .update({"status": "triggered", "triggered_at": now.isoformat()})
                        .eq("id", alarm["id"])
                        .execute()
                    )
            except Exception as e:
                print(f"Nudge Engine Alarms Error: {e}")

            # 2. Handle Escalation Policy (5-minute rule)
            try:
                escalation_resp = await run_in_threadpool(
                    lambda: self.supabase.table("user_alarms")
                    .select("*")
                    .eq("status", "triggered")
                    .lte("triggered_at", escalation_time.isoformat())
                    .is_("acknowledged_at", "null")
                    .execute()
                )
                for alarm in escalation_resp.data:
                    chat_id = alarm["chat_id"]
                    await send_telegram_message(
                        chat_id,
                        f"⚠️ ESCALATION: You haven't acknowledged your alarm: {alarm['message']}. "
                        "Your 'Probability of Outage' is increasing. Action required.",
                        reminder_type="alarm_escalation"
                    )
                    await run_in_threadpool(
                        lambda: self.supabase.table("user_alarms")
                        .update({"triggered_at": now.isoformat()})
                        .eq("id", alarm["id"])
                        .execute()
                    )
            except Exception as e:
                print(f"Nudge Engine Alarm Escalation Error: {e}")

            # --- PROACTIVE NUDGES BELOW (Subject to Silent Mode) ---

            # 3. Handle Tasks due for nudge
            try:
                tasks_resp = await run_in_threadpool(
                    lambda: self.supabase.table("user_tasks")
                    .select("*")
                    .eq("status", "pending")
                    .lte("due_date", now.isoformat())
                    .is_("triggered_at", "null")
                    .is_("acknowledged_at", "null")
                    .execute()
                )
                vetted_ps_cache = {}
                for task in tasks_resp.data:
                    chat_id = task["chat_id"]
                    ctx = await get_user_context(chat_id)
                    state = await get_user_state(chat_id)

                    # Suppression Logic
                    last_interaction_str = state.get("last_user_interaction_at")
                    if last_interaction_str:
                        last_interaction = datetime.fromisoformat(last_interaction_str)
                        if (now - last_interaction) < timedelta(minutes=10):
                            print(f"🤫 Nag Kill-Switch: Recent interaction ({now - last_interaction}). Skipping task nudge.")
                            continue
                        if (now - last_interaction) < timedelta(hours=3):
                            # 3-hour suppression for non-escalation nudges
                            # Check if it's a "Deep Work" reminder context
                            if ctx.get("current_block_type") in ["calendar_focus", "pomodoro"]:
                                print(f"🤫 Suppression: Under 3h since interaction. Skipping Deep Work reminder.")
                                continue

                    if state.get("pomodoro_active"):
                        print(f"🤫 Pomodoro Lock active. Skipping non-essential nudge.")
                        continue

                    if ctx.get("current_block_type"):
                        print(f"🤫 Silent Mode active ({ctx['current_block_type']}). Skipping task nudge.")
                        continue

                    if chat_id not in vetted_ps_cache:
                        vetted_ps_cache[chat_id] = await executive_sync_service._get_vetted_ps(chat_id)

                    await send_telegram_message(chat_id, f"🕒 TASK DUE: {task['title']}{vetted_ps_cache[chat_id]}", reminder_type="task_nudge")
                    await run_in_threadpool(
                        lambda: self.supabase.table("user_tasks")
                        .update({"triggered_at": now.isoformat()})
                        .eq("id", task["id"])
                        .execute()
                    )
            except Exception as e:
                print(f"Nudge Engine Tasks Error: {e}")

            # Tasks escalation
            try:
                task_escalation_resp = await run_in_threadpool(
                    lambda: self.supabase.table("user_tasks")
                    .select("*")
                    .eq("status", "pending")
                    .lte("triggered_at", escalation_time.isoformat())
                    .is_("acknowledged_at", "null")
                    .execute()
                )
                for task in task_escalation_resp.data:
                    chat_id = task["chat_id"]
                    ctx = await get_user_context(chat_id)
                    state = await get_user_state(chat_id)

                    # Escalations bypass the 10m/3h suppression but respect Pomodoro Lock if not an alarm
                    if state.get("pomodoro_active"):
                        print(f"🤫 Pomodoro Lock active. Skipping task escalation.")
                        continue

                    if ctx.get("current_block_type"):
                        print(f"🤫 Silent Mode active ({ctx['current_block_type']}). Skipping task escalation.")
                        continue

                    await send_telegram_message(
                        chat_id,
                        f"⚠️ ESCALATION: Task '{task['title']}' is still pending! "
                        "This is impacting your 'Probability of Outage'. Bro, get it done.",
                        reminder_type="task_escalation"
                    )
                    await run_in_threadpool(
                        lambda: self.supabase.table("user_tasks")
                        .update({"triggered_at": now.isoformat()})
                        .eq("id", task["id"])
                        .execute()
                    )
            except Exception as e:
                print(f"Nudge Engine Task Escalation Error: {e}")

            # 4. Handle Pomodoro sessions (System alerts, not subject to Silent Mode)
            try:
                pomodoro_resp = await run_in_threadpool(
                    lambda: self.supabase.table("pomodoro_sessions")
                    .select("*")
                    .eq("status", "active")
                    .lte("end_time", now.isoformat())
                    .execute()
                )
                for session in pomodoro_resp.data:
                    # Use chat_id if available, fallback to user_id
                    target_chat_id = session.get("chat_id") or session["user_id"]
                    session_type = session["type"]
                    msg = "🔔 Time's up! Pomodoro session completed. Take a break, G." if session_type == "work" else "🔔 Break's over! Let's get back to it."
                    await send_telegram_message(target_chat_id, msg, reminder_type="pomodoro_alert")
                    await update_user_state(target_chat_id, pomodoro_active=False)
                    await run_in_threadpool(
                        lambda: self.supabase.table("pomodoro_sessions")
                        .update({"status": "completed"})
                        .eq("id", session["id"])
                        .execute()
                    )
            except Exception as e:
                print(f"Nudge Engine Pomodoro Error: {e}")

        except Exception as e:
            print(f"Nudge Engine Error: {e}")


nudge_engine_service = NudgeEngine(supabase)

# Initialize Telegram Client
telegram_client = TelegramClient(TELEGRAM_TOKEN)


class ExecutiveSyncService:
    def __init__(self, supabase_client: Client, pomodoro_svc: PomodoroService):
        self.supabase = supabase_client
        self.pomodoro_service = pomodoro_svc
        self.chat_id = MUCHIRI_CHAT_ID

    async def _get_vetted_ps(self, chat_id: Optional[str] = None) -> str:
        """Helper to get pending Vetted tickets count as a P.S. string."""
        try:
            query = self.supabase.table("user_tasks").select("*").eq("status", "pending").ilike("title", "%Vetted%")
            if chat_id:
                query = query.eq("chat_id", chat_id)

            res = await run_in_threadpool(lambda: query.execute())
            if res.data:
                count = len(res.data)
                return f"\n\nP.S. You have {count} pending Vetted ticket{'s' if count > 1 else ''}."
        except Exception as e:
            print(f"Error fetching Vetted count: {e}")
        return ""

    async def sync_executive_state(self):
        """
        Job for the Executive PA logic.
        """
        if not self.chat_id:
            return

        try:
            now = datetime.now(ZoneInfo("Africa/Nairobi"))
            ctx = await get_user_context(self.chat_id)

            # 1. The 8 AM Briefing
            is_briefing_time = (now.hour == 8 and now.minute == 0)
            is_retry_time = (now.hour == 8 and now.minute == 15)

            if is_briefing_time or is_retry_time:
                last_briefing = ctx.get("last_briefing_at")
                is_sent_today = False
                if last_briefing:
                    lb_dt = datetime.fromisoformat(last_briefing)
                    if lb_dt.date() == now.date():
                        is_sent_today = True

                if not is_sent_today:
                    briefing_content = await get_scannable_briefing(self.chat_id)

                    msg = f"Morning Muchiri. Here's your scannable list for today:\n\n{briefing_content}"

                    # Use a dedicated reminder type for duplicate prevention if needed,
                    # though briefing has its own state tracking.
                    await send_telegram_message(self.chat_id, msg, reminder_type="morning_briefing")
                    await update_user_context(self.chat_id, last_briefing_at=now.isoformat())
                    print(f"✅ 8 AM Briefing sent (Time: {now.strftime('%H:%M')}).")

            # 2. Elastic Deep Work Sync
            active_pomodoro = await self.pomodoro_service.get_active_session(self.chat_id)

            # Fetch calendar for current/upcoming events
            creds = await run_in_threadpool(get_google_creds)
            current_block_id = None
            current_block_type = None

            if active_pomodoro:
                current_block_id = active_pomodoro["id"]
                current_block_type = "pomodoro"
            elif creds:
                # Use Google Calendar API directly to see if we are currently in a block
                service = build('calendar', 'v3', credentials=creds)
                now_iso = now.isoformat()
                events_result = await run_in_threadpool(
                    lambda: service.events().list(
                        calendarId='primary',
                        timeMin=now_iso,
                        maxResults=5,
                        singleEvents=True,
                        orderBy='startTime'
                    ).execute()
                )
                events = events_result.get('items', [])
                for event in events:
                    start_str = event['start'].get('dateTime', event['start'].get('date'))
                    end_str = event['end'].get('dateTime', event['end'].get('date'))
                    start_dt = datetime.fromisoformat(start_str.replace('Z', '+00:00')).astimezone(ZoneInfo("Africa/Nairobi"))
                    end_dt = datetime.fromisoformat(end_str.replace('Z', '+00:00')).astimezone(ZoneInfo("Africa/Nairobi"))

                    if start_dt <= now <= end_dt:
                        summary = event.get('summary', '').lower()
                        if "deep work" in summary or "ai engineering" in summary:
                            current_block_id = event['id']
                            current_block_type = "calendar_focus"
                            break

            # Update context state
            previous_block_type = ctx.get("current_block_type")
            previous_block_id = ctx.get("current_block_id")

            if current_block_type != previous_block_type or current_block_id != previous_block_id:
                await update_user_context(
                    self.chat_id,
                    current_block_type=current_block_type,
                    current_block_id=current_block_id
                )
                print(f"🔄 Block state updated: {current_block_type} ({current_block_id})")

            # 3. The 'Suspicious Silence' Nudge
            if not current_block_type:
                # Check if a block ended recently (more than 15 mins ago)
                # We use updated_at to see when current_block_type became None
                updated_at_str = ctx.get("updated_at")
                if updated_at_str:
                    updated_at = datetime.fromisoformat(updated_at_str)
                    # If block ended more than 15 mins ago
                    if (now - updated_at) >= timedelta(minutes=15):
                        # Check if we already nudged for this silence
                        last_silence_nudge_str = ctx.get("last_suspicious_silence_at")
                        last_silence_nudge = datetime.fromisoformat(last_silence_nudge_str) if last_silence_nudge_str else datetime.min.replace(tzinfo=ZoneInfo("Africa/Nairobi"))

                        if last_silence_nudge < updated_at:
                            # Check last interaction
                            state = await get_user_state(self.chat_id)
                            last_interaction_str = state.get("last_user_interaction_at")
                            last_interaction = datetime.fromisoformat(last_interaction_str) if last_interaction_str else datetime.min.replace(tzinfo=ZoneInfo("Africa/Nairobi"))

                            if last_interaction < updated_at:
                                # 3-hour suppression check for Suspicious Silence
                                if (now - last_interaction) >= timedelta(hours=3):
                                    # User hasn't messaged since block ended and it's been > 3 hours
                                    msg = f"Block ended at {updated_at.strftime('%H:%M')}. How did it go? Send an update to stay on track."
                                    msg += await self._get_vetted_ps()
                                    await send_telegram_message(self.chat_id, msg, reminder_type="suspicious_silence")
                                    await update_user_context(self.chat_id, last_suspicious_silence_at=now.isoformat())
                                    print("🧐 Suspicious Silence Nudge sent.")

            # 4. General Inactivity Nudge (MIA for 6+ hours)
            if 9 <= now.hour <= 21:
                state = await get_user_state(self.chat_id)
                last_interaction_str = state.get("last_user_interaction_at")
                last_interaction = datetime.fromisoformat(last_interaction_str) if last_interaction_str else datetime.min.replace(tzinfo=ZoneInfo("Africa/Nairobi"))

                if (now - last_interaction) >= timedelta(hours=6):
                    last_inactivity_nudge_str = ctx.get("last_inactivity_nudge_at")
                    last_inactivity_nudge = datetime.fromisoformat(last_inactivity_nudge_str) if last_inactivity_nudge_str else datetime.min.replace(tzinfo=ZoneInfo("Africa/Nairobi"))

                    if last_inactivity_nudge.date() < now.date():
                        # Check if there are pending tasks
                        tasks_resp = await run_in_threadpool(
                            lambda: self.supabase.table("user_tasks")
                            .select("id")
                            .eq("chat_id", self.chat_id)
                            .eq("status", "pending")
                            .limit(1)
                            .execute()
                        )
                        if tasks_resp.data:
                            msg = "Yo Muchiri, you've been off the radar for a bit. Hope the day's going well! Just a reminder that you've still got some pending tasks when you're back in the zone."
                            msg += await self._get_vetted_ps()
                            await send_telegram_message(self.chat_id, msg, reminder_type="inactivity_nudge")
                            await update_user_context(self.chat_id, last_inactivity_nudge_at=now.isoformat())
                            print("🧐 General Inactivity Nudge sent.")

        except Exception as e:
            print(f"❌ ExecutiveSyncService Error: {e}")


executive_sync_service = ExecutiveSyncService(supabase, pomodoro_service)


async def outbound_telemetry_loop():
    """
    Native non-blocking event loop for proactive outbound messages.
    Replaces APScheduler with a lightweight asyncio.sleep polling model.
    """
    print("🚀 Outbound Telemetry Loop started.")
    last_4h_check = datetime.min.replace(tzinfo=ZoneInfo("Africa/Nairobi"))
    last_8am_check_day = None

    while True:
        try:
            now = datetime.now(ZoneInfo("Africa/Nairobi"))

            # 1. 1-Minute Polling Tasks
            await nudge_engine_service.check_alerts()
            await executive_sync_service.sync_executive_state()

            # 2. 4-Hour Polling Tasks
            if (now - last_4h_check).total_seconds() >= 14400:  # 4 hours
                await evaluate_project_velocity()
                await evaluate_habit_velocity()
                last_4h_check = now

            # 3. Daily 8 AM Task (Nairobi Time)
            if now.hour == 8 and last_8am_check_day != now.date():
                await morning_routine_check()
                last_8am_check_day = now.date()

        except asyncio.CancelledError:
            print("🛑 Outbound Telemetry Loop stopping...")
            break
        except Exception as e:
            print(f"❌ Error in Outbound Telemetry Loop: {e}")

        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize Telegram Client
    await telegram_client.start()

    # Configure APScheduler
    scheduler.add_job(check_upcoming_events, 'interval', seconds=60)
    scheduler.add_job(morning_briefing_task, CronTrigger(hour=9, minute=0))
    scheduler.add_job(evening_review_task, CronTrigger(hour=22, minute=0))
    scheduler.add_job(cleanup_old_records, CronTrigger(hour=3, minute=0))

    # Start APScheduler
    scheduler.start()

    # Launch non-blocking background task (legacy or for things not yet in APScheduler)
    telemetry_task = asyncio.create_task(outbound_telemetry_loop())

    yield

    # Clean up
    scheduler.shutdown()
    telemetry_task.cancel()
    try:
        await telemetry_task
    except asyncio.CancelledError:
        pass
    await telegram_client.stop()
    print("🛑 Background tasks and Telegram client shut down.")


app = FastAPI(title="M-bot", lifespan=lifespan)
# Global clients




async def store_project_zayn(data: Dict[str, Any]):
    """Upsert into 'project_zayn' table."""
    try:
        payload = {
            "content": data.get("content"),
            "skincare_done": data.get("skincare_done", False),
            "workout_done": data.get("workout_done", False)
        }
        await run_in_threadpool(
            supabase.table("project_zayn").upsert(payload).execute
        )
    except Exception as e:
        print(f"Error storing Project Zayn data: {e}")


async def store_dev_milestone(category: str, data: Dict[str, Any]):
    """Insert into 'dev_milestones' table."""
    try:
        payload = {
            "category": category,
            "content": data.get("content")
        }
        await run_in_threadpool(
            supabase.table("dev_milestones").insert(payload).execute
        )
    except Exception as e:
        print(f"Error storing dev milestone: {e}")


async def fetch_chat_history(chat_id: str) -> List[Dict[str, str]]:
    """Fetch the last 15 messages for the current chat_id from Supabase."""
    try:
        response = await run_in_threadpool(
            lambda: supabase.table("messages")
            .select("role", "content")
            .eq("chat_id", chat_id)
            .order("created_at", desc=True)
            .limit(30)
            .execute()
        )
        return response.data[::-1]  # chronological order
    except Exception as e:
        print(f"Error fetching chat history: {e}")
        return []


async def store_message(chat_id: str, role: str, content: str):
    """Store a message in the 'messages' table."""
    try:
        payload = {"chat_id": chat_id, "role": role, "content": content}
        await run_in_threadpool(
            supabase.table("messages").insert(payload).execute
        )
    except Exception as e:
        print(f"Error storing message: {e}")


async def send_telegram_message(chat_id: str, text: str, reply_to_message_id: Optional[int] = None, reminder_type: Optional[str] = None):
    """Send a message back to the Telegram chat."""
    global last_nudge_sent_at
    try:
        # Proactive Nudge Guard: Check for Cool-down and Mute
        if reminder_type and chat_id == MUCHIRI_CHAT_ID:
            # Alarms, Morning Briefing, and Pomodoro Alerts bypass mute and cooldown
            if reminder_type not in ["alarm", "alarm_escalation", "morning_briefing", "pomodoro_alert", "task_escalation", "suspicious_silence", "manual_nudge_request"]:
                now = datetime.now(ZoneInfo("Africa/Nairobi"))

                # 1. 1-hour Cool-down check
                if last_nudge_sent_at:
                    if (now - last_nudge_sent_at) < timedelta(hours=1):
                        print(f"🤫 Cool-down active. Last nudge was {(now - last_nudge_sent_at).total_seconds()/60:.1f}m ago. Skipping {reminder_type}.")
                        return

                # 2. Global Mute check
                state = await get_user_state(chat_id)
                if state.get("is_muted"):
                    muted_until_str = state.get("muted_until")
                    if muted_until_str:
                        muted_until = datetime.fromisoformat(muted_until_str)
                        if now < muted_until:
                            print(f"🤫 User is muted until {muted_until_str}. Skipping {reminder_type}.")
                            return

        # Check for duplicate reminders
        if reminder_type:
            if await should_skip_reminder(chat_id, reminder_type, text):
                print(f"🚫 Skipping duplicate reminder: {reminder_type}")
                return

        success = await telegram_client.send_message(
            chat_id=chat_id,
            text=text,
            reply_to_message_id=reply_to_message_id
        )

        if success:
            # Log sent reminder
            if reminder_type:
                await log_sent_reminder(chat_id, reminder_type, text)

            # Update last_nudge_sent_at if it's a proactive nudge for Muchiri
            if chat_id == MUCHIRI_CHAT_ID and reminder_type:
                last_nudge_sent_at = datetime.now(ZoneInfo("Africa/Nairobi"))
    except Exception as e:
        print(f"Error sending Telegram message: {e}")


async def generate_personalized_nudge(project_name: str, status: str) -> str:
    """Generate a personalized nudge using Gemini."""
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = f"Given this project ({project_name}) status: {status} and the 25th deadline, write a concise, sharp 1-sentence reminder for a high-performance engineer."
        response = await model.generate_content_async(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Error generating personalized nudge: {e}")
        return f"Yo Muchiri, don't forget to push updates for {project_name}. Time's ticking!"


async def evaluate_project_velocity():
    """
    Checks active projects and triggers nudges if velocity is low.
    Runs every 4 hours via APScheduler.
    """
    try:
        now = datetime.now(ZoneInfo("Africa/Nairobi"))
        twelve_hours_ago = now - timedelta(hours=12)

        res = await run_in_threadpool(
            lambda: supabase.table("goals").select("*").eq("status", "active").execute()
        )
        projects = res.data

        for project in projects:
            project_id = project["id"]
            project_name = project["name"]
            priority = project.get("priority", 1)

            log_res = await run_in_threadpool(
                lambda: supabase.table("activity_logs")
                .select("created_at")
                .eq("project_id", project_id)
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )

            last_update = None
            if log_res.data:
                last_update = datetime.fromisoformat(log_res.data[0]["created_at"])

            is_stalled = False
            if not last_update or last_update < twelve_hours_ago:
                if priority >= 7:
                    is_stalled = True
                elif "Portfolio" in project_name and now.day <= 25:
                    is_stalled = True

            if is_stalled:
                status_desc = f"No updates in over 12 hours."
                if not last_update:
                    status_desc = "No activity logs found."

                nudge = await generate_personalized_nudge(project_name, status_desc)
                await send_telegram_message(MUCHIRI_CHAT_ID, nudge, reminder_type="velocity_nudge")
                print(f"🚀 Velocity nudge sent for project: {project_name}")

    except Exception as e:
        print(f"Error in evaluate_project_velocity: {e}")


async def evaluate_habit_velocity():
    """
    Checks habit streaks and triggers alerts for breaks.
    Specifically handles the Dopamine Discipline pillar.
    """
    try:
        now = datetime.now(ZoneInfo("Africa/Nairobi"))
        # Habits are checked every 4 hours, but we mainly care if they weren't completed yesterday/today
        # For simplicity, we check if streak was recently reset or if broken
        res = await run_in_threadpool(
            lambda: supabase.table("habits").select("*").execute()
        )
        habits = res.data

        for habit in habits:
            name = habit["name"]
            streak = habit.get("streak", 0)
            last_completed = habit.get("last_completed_at")

            if name == "dopamine_integrity" and streak == 0 and last_completed:
                last_dt = datetime.fromisoformat(last_completed)
                if (now - last_dt) > timedelta(days=1):
                    # Streak broken logic (simplified)
                    msg = "🚨 Systems Failure Alert: Chief, we just hit a bottleneck in the reward center. Resetting counter. Let's get back to the GNN research to stabilize the dopamine loop."
                    await send_telegram_message(MUCHIRI_CHAT_ID, msg, reminder_type="habit_alert")
                    print("⚠️ Dopamine Discipline alert sent.")

    except Exception as e:
        print(f"Error in evaluate_habit_velocity: {e}")


async def morning_routine_check():
    """Daily 8 AM routine check."""
    msg = "Morning Routine Check: Skincare and Scent. Look like the Engineer you're building."
    await send_telegram_message(MUCHIRI_CHAT_ID, msg, reminder_type="morning_check")
    print("🌅 Morning routine check sent.")


async def generate_event_briefing(event: Dict[str, Any], window_mins: int) -> str:
    """Generate an AI briefing for an upcoming event."""
    chat_id = event.get("chat_id")
    now_nairobi = datetime.now(ZoneInfo("Africa/Nairobi")).strftime("%Y-%m-%d %H:%M:%S")

    start_dt = datetime.fromisoformat(event["start_time"].replace('Z', '+00:00')).astimezone(ZoneInfo("Africa/Nairobi"))
    time_str = start_dt.strftime("%I:%M %p")

    window_text = f"in {window_mins} minutes" if window_mins > 0 else "now"
    location_text = f"\n📍 Location: {event['location']}" if event.get("location") else ""

    prompt = (
        f"Context: It's {now_nairobi}. An event is starting {window_text}.\n\n"
        f"Event Title: {event['summary']}\n"
        f"Start Time: {time_str}\n"
        f"Description: {event.get('description', 'No description provided.')}\n"
        f"{location_text}\n\n"
        "Generate a sharp, proactive briefing in this exact format:\n"
        "🚨 Upcoming Event: [Title]\n"
        "Starts: [Time] ([Window Text])\n"
        "[Location if available]\n\n"
        "Objectives:\n"
        "• [Extracted or inferred objective 1]\n"
        "• [Extracted or inferred objective 2]\n\n"
        "Suggested Focus:\n"
        "[A concise, high-impact suggestion on what to prioritize first.]"
    )

    try:
        # We use Gemini directly for proactive briefings to keep it sharp
        model = genai.GenerativeModel("gemini-1.5-flash")
        response = await model.generate_content_async(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Error generating event briefing: {e}")
        # Fallback to a basic template
        return (
            f"🚨 Upcoming Event: {event['summary']}\n"
            f"Starts: {time_str} ({window_text}){location_text}\n\n"
            "Bro, get ready for this event. Time to focus."
        )


async def generate_morning_briefing(chat_id: str) -> str:
    """Generate a comprehensive morning briefing."""
    now = datetime.now(ZoneInfo("Africa/Nairobi"))
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()

    # 1. Fetch events
    events_res = await run_in_threadpool(
        lambda: supabase.table("user_schedules")
        .select("*")
        .eq("chat_id", chat_id)
        .gte("start_time", start_of_day)
        .lte("start_time", end_of_day)
        .order("start_time")
        .execute()
    )

    # 2. Fetch high-priority tasks
    tasks_res = await run_in_threadpool(
        lambda: supabase.table("user_tasks")
        .select("*")
        .eq("chat_id", chat_id)
        .eq("status", "pending")
        .gte("impact_score", 7)
        .execute()
    )

    events_data = events_res.data or []
    tasks_data = tasks_res.data or []

    prompt = (
        f"Context: It's {now.strftime('%A, %B %d, %Y')} at 9:00 AM. Time for the morning briefing.\n\n"
        f"Today's Events: {json.dumps(events_data)}\n"
        f"High-Priority Pending Tasks: {json.dumps(tasks_data)}\n\n"
        "Generate a morning briefing with these sections:\n"
        "1. Summary of today's schedule (scannable list).\n"
        "2. Highlight 2-3 high-priority tasks to crush.\n"
        "3. Identify free time blocks for deep work.\n\n"
        "Tone: Senior executive assistant. Sharp, encouraging, focused on productivity."
    )

    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        response = await model.generate_content_async(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Error generating morning briefing: {e}")
        return "Morning! You've got a busy day ahead. Check your calendar and crush those tasks."


async def morning_briefing_task():
    """APScheduler task: Send daily morning briefing at 9:00 AM."""
    if MUCHIRI_CHAT_ID:
        briefing = await generate_morning_briefing(MUCHIRI_CHAT_ID)
        await send_telegram_message(MUCHIRI_CHAT_ID, briefing, reminder_type="morning_briefing_proactive")


async def generate_evening_review(chat_id: str) -> str:
    """Generate an evening review and productivity summary."""
    now = datetime.now(ZoneInfo("Africa/Nairobi"))
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    end_of_day = now.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()

    # 1. Fetch today's events
    events_res = await run_in_threadpool(
        lambda: supabase.table("user_schedules")
        .select("*")
        .eq("chat_id", chat_id)
        .gte("start_time", start_of_day)
        .lte("start_time", end_of_day)
        .execute()
    )

    # 2. Fetch tasks completed today or still pending
    tasks_res = await run_in_threadpool(
        lambda: supabase.table("user_tasks")
        .select("*")
        .eq("chat_id", chat_id)
        .gte("created_at", start_of_day)
        .execute()
    )

    prompt = (
        f"Context: It's {now.strftime('%A, %B %d, %Y')} at 10:00 PM. Time for the evening review.\n\n"
        f"Today's Events: {json.dumps(events_res.data)}\n"
        f"Tasks Interaction: {json.dumps(tasks_res.data)}\n\n"
        "Generate an evening review that:\n"
        "1. Summarizes the day's achievements based on the schedule and tasks.\n"
        "2. Asks Elvis what he completed from his goals today.\n"
        "3. Provides a concise 'Productivity Summary' and a score (1-10) for the day.\n\n"
        "Tone: Reflective, senior executive assistant, encouraging growth."
    )

    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        response = await model.generate_content_async(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Error generating evening review: {e}")
        return "Evening, Elvis! How was your day? Tell me what you crushed today so I can log it."


async def evening_review_task():
    """APScheduler task: Send daily evening review at 10:00 PM."""
    if MUCHIRI_CHAT_ID:
        review = await generate_evening_review(MUCHIRI_CHAT_ID)
        await send_telegram_message(MUCHIRI_CHAT_ID, review, reminder_type="evening_review_proactive")


async def cleanup_old_records():
    """APScheduler task: Remove old calendar events and sent reminders for events that have passed."""
    try:
        now = datetime.now(ZoneInfo("Africa/Nairobi")).isoformat()

        # 1. Delete old events from user_schedules
        await run_in_threadpool(
            lambda: supabase.table("user_schedules")
            .delete()
            .lt("end_time", now)
            .execute()
        )

        # 2. Delete old event reminders from sent_reminders
        # We keep them for 24 hours just in case, then purge.
        one_day_ago = (datetime.now(ZoneInfo("Africa/Nairobi")) - timedelta(days=1)).isoformat()
        await run_in_threadpool(
            lambda: supabase.table("sent_reminders")
            .delete()
            .lt("event_start_time", one_day_ago)
            .execute()
        )

        print("🧹 Cleanup of old records completed.")
    except Exception as e:
        print(f"Error in cleanup_old_records: {e}")


async def check_upcoming_events():
    try:
        now = datetime.now(ZoneInfo("Africa/Nairobi"))
        windows = [15, 5, 0]

        for window in windows:
            target_time = now + timedelta(minutes=window)
            # Query events starting within a 1-minute window around the target_time
            start_range = (target_time - timedelta(seconds=30)).isoformat()
            end_range = (target_time + timedelta(seconds=30)).isoformat()

            res = await run_in_threadpool(
                lambda: supabase.table("user_schedules")
                .select("*")
                .gte("start_time", start_range)
                .lte("start_time", end_range)
                .execute()
            )

            for event in res.data:
                chat_id = event.get("chat_id")
                if not chat_id: continue

                event_id = event["event_id"]
                reminder_type = f"event_reminder_{window}m"

                # Check if already sent
                sent_res = await run_in_threadpool(
                    lambda: supabase.table("sent_reminders")
                    .select("id")
                    .eq("chat_id", chat_id)
                    .eq("event_id", event_id)
                    .eq("reminder_type", reminder_type)
                    .execute()
                )

                if not sent_res.data:
                    briefing = await generate_event_briefing(event, window)
                    await send_telegram_message(chat_id, briefing, reminder_type=reminder_type)
                    # Log with event_id
                    await run_in_threadpool(
                        lambda: supabase.table("sent_reminders").insert({
                            "chat_id": chat_id,
                            "reminder_type": reminder_type,
                            "content": briefing,
                            "event_id": event_id,
                            "event_start_time": event["start_time"],
                            "sent_at": datetime.now(ZoneInfo("Africa/Nairobi")).isoformat()
                        }).execute()
                    )

    except Exception as e:
        print(f"Error in check_upcoming_events: {e}")


async def get_llm_response(prompt: str) -> str:
    """Get response from Gemini with tool calling support, fallback to Groq."""
    chat_id = current_chat_id.get()
    history = await fetch_chat_history(chat_id)
    now_nairobi = datetime.now(ZoneInfo("Africa/Nairobi")).strftime("%Y-%m-%d %H:%M:%S")

    if GEMINI_API_KEY:
        try:
            system_instruction = (
                f"Current time: {now_nairobi} (Africa/Nairobi)\n\n"
                "You are M-bot, the personal AI assistant of Elvis Muchiri — a QA Engineer, "
                "AI builder, and all-round ambitious guy based in Kenya. "
                "You're basically that one brilliant friend who actually has their life together "
                "and happens to know everything. Smart, casual, occasionally witty, never robotic. "
                "You talk like a real person — no bullet point overload, no corporate speak, "
                "no fake enthusiasm. Just straight up helpful with a personality. "
                "\n\n"
                "How you address Elvis: mix it up naturally. Sometimes 'Elvis', sometimes 'bro', "
                "'chief', 'man', 'G' — read the room based on the message vibe. "
                "If he's logging health stuff, keep it encouraging but not cheesy. "
                "If he's in work mode, be sharp and focused. "
                "If he's just chatting, match that energy. "
                "\n\n"
                "You track these areas of his life:\n"
                "- Project Zayn: his health, fitness, skincare and workout streak\n"
                "- Build Mode: his technical work, coding and engineering notes\n"
                "- AI Roadmap: his journey to becoming an AI Engineering expert\n"
                "- Kijiji: his side hustles and marketplace activities\n"
                "\n\n"
                "You have access to tools to fetch his schedule.\n"
                "\n\n"
                "Rules:\n"
                "- Never make up data or stats you don't have. If you don't know, say so plainly.\n"
                "- Keep responses conversational and natural. Match his energy.\n"
                "- Feel free to use markdown (like bold or lists) to make information scannable when appropriate.\n"
                "- Don't start every message the same way. Vary your openers.\n"
                "- If he seems stressed or tired, acknowledge it like a friend would.\n"
                "- If he just added a task without providing an effort/impact score, ask him for it.\n"
                "- If he provides scores, acknowledge it and suggest a focus order based on impact/effort."
            )

            model = genai.GenerativeModel(
                model_name="gemini-1.5-flash",
                system_instruction=system_instruction,
                tools=[get_calendar_events]
            )

            gemini_history = []
            for msg in history:
                role = "user" if msg["role"] == "user" else "model"
                gemini_history.append({"role": role, "parts": [msg["content"]]})

            chat = model.start_chat(history=gemini_history)
            response = await chat.send_message_async(prompt)

            while response.candidates[0].content.parts[0].function_call:
                tool_call = response.candidates[0].content.parts[0].function_call
                result = await dispatcher.dispatch(tool_call)
                response = await chat.send_message_async(
                    genai.types.Content(
                        parts=[
                            genai.types.Part.from_function_response(
                                name=tool_call.name,
                                response={"result": result}
                            )
                        ]
                    )
                )

            return response.text
        except Exception as e:
            print(f"Gemini error: {e}. Falling back to Groq.")
            return await get_groq_response(prompt, history)
    else:
        return await get_groq_response(prompt, history)


async def get_groq_response(prompt: str, history: List[Dict[str, str]]) -> str:
    """Fallback response from Groq llama-3.1-8b-instant."""
    now_nairobi = datetime.now(ZoneInfo("Africa/Nairobi")).strftime("%Y-%m-%d %H:%M:%S")
    try:
        calendar_context = await run_in_threadpool(get_calendar_events)
        # Don't inject error strings into the prompt — keep it clean
        if any(phrase in calendar_context.lower() for phrase in ["authentication failed", "couldn't fetch", "check your"]):
            calendar_section = "Calendar is unavailable right now."
        else:
            calendar_section = f"His upcoming calendar events:\n{calendar_context}"
    except Exception as e:
        print(f"Calendar fetch error in Groq fallback: {e}")
        calendar_section = "Calendar is unavailable right now."

    system_message = {
        "role": "system",
        "content": (
            f"Current time: {now_nairobi} (Africa/Nairobi)\n\n"
            "You are M-bot, the personal AI assistant of Elvis Muchiri — a QA Engineer, "
            "AI builder, and all-round ambitious guy based in Kenya. "
            "You're basically that one brilliant friend who actually has their life together "
            "and happens to know everything. Smart, casual, occasionally witty, never robotic. "
            "You talk like a real person — no bullet point overload, no corporate speak, "
            "no fake enthusiasm. Just straight up helpful with a personality. "
            "\n\n"
            "How you address Elvis: mix it up naturally. Sometimes 'Elvis', sometimes 'bro', "
            "'chief', 'man', 'G' — read the room based on the message vibe. "
            "If he's logging health stuff, keep it encouraging but not cheesy. "
            "If he's in work mode, be sharp and focused. "
            "If he's just chatting, match that energy. "
            "\n\n"
            "You track these areas of his life:\n"
            "- Project Zayn: his health, fitness, skincare and workout streak\n"
            "- Build Mode: his technical work, coding and engineering notes\n"
            "- AI Roadmap: his journey to becoming an AI Engineering expert\n"
            "- Kijiji: his side hustles and marketplace activities\n"
            "\n\n"
            f"{calendar_section}\n"
            "\n\n"
            "Rules:\n"
            "- Never make up data or stats you don't have. If you don't know, say so plainly.\n"
            "- Keep responses conversational and natural. Match his energy.\n"
            "- Feel free to use markdown (like bold or lists) to make information scannable when appropriate.\n"
            "- Don't start every message the same way. Vary your openers.\n"
            "- If he seems stressed or tired, acknowledge it like a friend would.\n"
            "- If he just added a task without providing an effort/impact score, ask him for it.\n"
            "- If he provides scores, acknowledge it and suggest a focus order based on impact/effort."
        )
    }

    messages = [system_message]
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": prompt})

    response = await groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=messages
    )

    return response.choices[0].message.content


async def acknowledge_most_recent(chat_id: str):
    """Mark the most recently triggered alarm or task as acknowledged."""
    try:
        now = datetime.now(ZoneInfo("Africa/Nairobi"))
        now_iso = now.isoformat()

        # Update user state
        await update_user_state(chat_id, last_user_interaction_at=now_iso)

        await run_in_threadpool(
            lambda: supabase.table("user_alarms")
            .update({"status": "acknowledged", "acknowledged_at": now_iso})
            .eq("chat_id", chat_id)
            .eq("status", "triggered")
            .execute()
        )
        await run_in_threadpool(
            lambda: supabase.table("user_tasks")
            .update({"status": "completed", "acknowledged_at": now_iso})
            .eq("chat_id", chat_id)
            .neq("triggered_at", None)
            .execute()
        )
    except Exception as e:
        print(f"Error acknowledging: {e}")


async def store_task_or_alarm(chat_id: str, data: Dict[str, Any]):
    """Insert into 'user_tasks' or 'user_alarms' table."""
    try:
        task_type = data.get("task_type", "task")
        if task_type == "alarm":
            payload = {
                "chat_id": chat_id,
                "alarm_time": data.get("due_date", datetime.now(ZoneInfo("Africa/Nairobi")).isoformat()),
                "message": data.get("title") or data.get("content")
            }
            await run_in_threadpool(
                supabase.table("user_alarms").insert(payload).execute
            )
        else:
            payload = {
                "chat_id": chat_id,
                "title": data.get("title") or data.get("content"),
                "due_date": data.get("due_date"),
                "effort_score": data.get("effort_score"),
                "impact_score": data.get("impact_score")
            }
            await run_in_threadpool(
                supabase.table("user_tasks").insert(payload).execute
            )
    except Exception as e:
        print(f"Error storing task/alarm: {e}")


@app.post("/webhook")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    """Handle incoming Telegram updates."""
    try:
        update = await request.json()
        message = update.get("message")
        if not message or "text" not in message:
            return {"status": "No text message to process"}

        chat_id = str(message["chat"]["id"])
        user_id = str(message.get("from", {}).get("id", chat_id))
        text = message["text"]

        current_chat_id.set(chat_id)

        # Update last interaction in background
        now_iso = datetime.now(ZoneInfo("Africa/Nairobi")).isoformat()
        background_tasks.add_task(update_user_context, chat_id, last_interaction_at=now_iso)
        background_tasks.add_task(update_user_state, chat_id, last_user_interaction_at=now_iso)

        # 0. Brain Dump Pipeline
        if text.startswith(".") or "#" in text:
            hashtags = re.findall(r"#(\w+)", text.lower())
            if text.startswith(".") or hashtags:
                try:
                    brain_dump = BrainDump(
                        raw_content=text,
                        tags=hashtags,
                        metadata={
                            "message_id": message.get("message_id"),
                            "chat_id": chat_id,
                            "user_id": user_id
                        }
                    )
                    await run_in_threadpool(
                        lambda: supabase.table("brain_dumps").insert(brain_dump.model_dump()).execute()
                    )
                    # Use plain text confirmation to avoid markdown errors with hashtags
                    await send_telegram_message(
                        chat_id,
                        f"🧠 Brain Dump captured. Tags: {', '.join(hashtags) if hashtags else 'none'}. I've got this filed, chief."
                    )
                    return {"status": "success", "type": "brain_dump"}
                except Exception as e:
                    print(f"Error in Brain Dump pipeline: {e}")

        # 1. Handle Commands
        if text.startswith("/"):
            message_id = message.get("message_id")
            if text.startswith("/pomodoro"):
                try:
                    await pomodoro_service.start_session(user_id, chat_id=chat_id)
                    await update_user_state(chat_id, pomodoro_active=True)
                    await send_telegram_message(chat_id, "🚀 Pomodoro started! 25 minutes of deep work begins now. Focus, bro.")
                except Exception as e:
                    await send_telegram_message(chat_id, f"❌ Pomodoro failed: {str(e)}", reply_to_message_id=message_id)
                return {"status": "success", "command": "pomodoro"}
            elif text.startswith("/p_stop"):
                await pomodoro_service.stop_session(user_id)
                await update_user_state(chat_id, pomodoro_active=False)
                await send_telegram_message(chat_id, "🛑 Pomodoro stopped. Rest up.")
                return {"status": "success", "command": "p_stop"}
            elif text.startswith("/p_status"):
                session = await pomodoro_service.get_active_session(user_id)
                if session:
                    end_time = datetime.fromisoformat(session["end_time"].replace('Z', '+00:00')).astimezone(ZoneInfo("Africa/Nairobi"))
                    remaining = end_time - datetime.now(ZoneInfo("Africa/Nairobi"))
                    minutes = int(remaining.total_seconds() // 60)
                    await send_telegram_message(chat_id, f"⏳ {minutes} minutes remaining in your current session. Keep pushing!")
                else:
                    await send_telegram_message(chat_id, "No active Pomodoro session. Use /pomodoro to start one.")
                return {"status": "success", "command": "p_status"}
            elif text.startswith("/mute"):
                muted_until = (datetime.now(ZoneInfo("Africa/Nairobi")) + timedelta(hours=8)).isoformat()
                await update_user_state(chat_id, is_muted=True, muted_until=muted_until)
                await send_telegram_message(chat_id, "🔇 Global Mute active. I'll stay quiet for the next 8 hours, chief. Only alarms will get through.")
                return {"status": "success", "command": "mute"}
            elif text.startswith("/research"):
                topic = text.replace("/research", "").strip()
                if topic:
                    await run_in_threadpool(
                        lambda: supabase.table("knowledge_graph").insert({"topic": topic}).execute()
                    )
                    await send_telegram_message(chat_id, "Node added. Your intellectual latent space is expanding.")
                else:
                    await send_telegram_message(chat_id, "Chief, what topic are we expanding into? Usage: /research [topic]")
                return {"status": "success", "command": "research"}

        # 2. Classify Intent
        try:
            classification = await intent_classifier.classify(text)
            category = classification.get("category")

            # 2. Specialized storage based on category
            if category == "Project Zayn":
                background_tasks.add_task(store_project_zayn, classification)
            elif category in ["Build Mode", "AI Roadmap"]:
                background_tasks.add_task(store_dev_milestone, category, classification)
            elif category == "Task":
                background_tasks.add_task(store_task_or_alarm, chat_id, classification)
            elif category == "Acknowledge":
                background_tasks.add_task(acknowledge_most_recent, chat_id)
            elif category == "Nudge":
                # Intent Guard: Disable automated nudge if message contains question mark or inquiry words
                if re.search(r"\?|how|why|what", text, re.IGNORECASE):
                    print(f"🛡️ Intent Guard: Inquiry detected in nudge request. Skipping automated nudge for '{text}'.")
                else:
                    nudge_msg = await intent_classifier.get_nudge_message(chat_id)
                    # Pass the nudge message as a hint to the LLM instead of returning early
                    text = f"[Nudge Hint: {nudge_msg}] {text}"
        except Exception as e:
            print(f"Classification or specialized storage error: {e}")
            category = "Unknown"

        # 3. Generate LLM response
        try:
            full_response = await get_llm_response(text)
            await send_telegram_message(chat_id, full_response)
        except Exception as e:
            print(f"LLM or Telegram error: {e}")
            full_response = "Sorry bro, I'm having some trouble processing that right now."
            await send_telegram_message(chat_id, full_response)

        # 4. Store messages in background
        background_tasks.add_task(store_message, chat_id, "user", text)
        background_tasks.add_task(store_message, chat_id, "assistant", full_response)

        return {"status": "success", "category": category}
    except Exception as e:
        print(f"Error processing webhook: {e}")
        return {"status": "error", "message": str(e)}


@app.post("/nudge")
async def manual_nudge(request: NudgeRequest, background_tasks: BackgroundTasks):
    """
    Explicitly trigger a nudge message to Muchiri.
    """
    if not MUCHIRI_CHAT_ID:
        raise HTTPException(status_code=500, detail="MUCHIRI_CHAT_ID not configured")

    background_tasks.add_task(send_telegram_message, MUCHIRI_CHAT_ID, request.message)
    return {"status": "nudge_enqueued", "message": request.message}


@app.get("/")
async def root():
    return {"status": "M-bot is running"}