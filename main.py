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
from proactive_router import router as proactive_router
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

# ─── Environment Variables ────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
SUPABASE_URL     = os.getenv("SUPABASE_URL")
SUPABASE_KEY     = os.getenv("SUPABASE_KEY")
GROQ_API_KEY     = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY")
MUCHIRI_CHAT_ID  = os.getenv("MUCHIRI_CHAT_ID")

# ─── Initialize Clients ───────────────────────────────────────────────────────
groq_client      = AsyncGroq(api_key=GROQ_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
scheduler        = AsyncIOScheduler(timezone=ZoneInfo("Africa/Nairobi"))
pomodoro_service = PomodoroService(supabase)
intent_classifier = IntentClassifier(groq_client, supabase)

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# Context variable to store chat_id per request
current_chat_id: ContextVar[str] = ContextVar("current_chat_id")

# ─── Per-type cooldown registry (replaces single global last_nudge_sent_at) ───
# Key: reminder_type  →  Value: last datetime it was sent
nudge_sent_registry: Dict[str, datetime] = {}

# How long each nudge type is suppressed after firing.
# Types NOT listed here fire freely (alarms, pomodoro_alert, etc.)
NUDGE_COOLDOWNS: Dict[str, timedelta] = {
    "task_nudge":          timedelta(hours=2),
    "task_escalation":     timedelta(hours=1),
    "inactivity_nudge":    timedelta(hours=6),
    "velocity_nudge":      timedelta(hours=4),
    "suspicious_silence":  timedelta(hours=3),
    "morning_briefing":    timedelta(hours=20),
    "morning_check":       timedelta(hours=20),
    "evening_review_proactive": timedelta(hours=20),
    "hydration":           timedelta(hours=2, minutes=30),
    "movement":            timedelta(hours=2, minutes=30),
    "habit_alert":         timedelta(hours=12),
}

# Nudge types that always go through regardless of cooldown or mute
BYPASS_ALL = {
    "alarm",
    "alarm_escalation",
    "pomodoro_alert",
    "manual_nudge_request",
}


# ─── User State / Context helpers ─────────────────────────────────────────────

async def get_user_state(chat_id: str) -> Dict[str, Any]:
    try:
        res = await run_in_threadpool(
            lambda: supabase.table("user_state").select("*").eq("chat_id", chat_id).execute()
        )
        if res.data:
            return res.data[0]
        default_state = {
            "chat_id": chat_id,
            "pomodoro_active": False,
            "is_muted": False,
            "muted_until": None,
        }
        res = await run_in_threadpool(
            lambda: supabase.table("user_state").insert(default_state).execute()
        )
        return res.data[0]
    except Exception as e:
        print(f"Error fetching user state: {e}")
        return {"chat_id": chat_id, "pomodoro_active": False, "is_muted": False, "muted_until": None}


async def update_user_state(chat_id: str, **kwargs):
    try:
        kwargs["updated_at"] = datetime.now(ZoneInfo("Africa/Nairobi")).isoformat()
        await run_in_threadpool(
            lambda: supabase.table("user_state").update(kwargs).eq("chat_id", chat_id).execute()
        )
    except Exception as e:
        print(f"Error updating user state: {e}")


async def get_user_context(chat_id: str) -> Dict[str, Any]:
    try:
        res = await run_in_threadpool(
            lambda: supabase.table("user_context").select("*").eq("chat_id", chat_id).execute()
        )
        if res.data:
            return res.data[0]
        default_ctx = {"chat_id": chat_id}
        res = await run_in_threadpool(
            lambda: supabase.table("user_context").insert(default_ctx).execute()
        )
        return res.data[0]
    except Exception as e:
        print(f"Error fetching user context: {e}")
        return {"chat_id": chat_id, "current_block_type": None}


async def update_user_context(chat_id: str, **kwargs):
    try:
        kwargs["updated_at"] = datetime.now(ZoneInfo("Africa/Nairobi")).isoformat()
        await run_in_threadpool(
            lambda: supabase.table("user_context").update(kwargs).eq("chat_id", chat_id).execute()
        )
    except Exception as e:
        print(f"Error updating user context: {e}")


# ─── Duplicate-reminder guard ─────────────────────────────────────────────────

async def should_skip_reminder(chat_id: str, reminder_type: str, content: str) -> bool:
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
        for reminder in res.data:
            if reminder["content"] == content:
                return True
        return False
    except Exception as e:
        print(f"Error checking sent_reminders: {e}")
        return False


async def log_sent_reminder(chat_id: str, reminder_type: str, content: str):
    try:
        payload = {
            "chat_id": chat_id,
            "reminder_type": reminder_type,
            "content": content,
            "sent_at": datetime.now(ZoneInfo("Africa/Nairobi")).isoformat(),
        }
        await run_in_threadpool(
            lambda: supabase.table("sent_reminders").insert(payload).execute()
        )
    except Exception as e:
        print(f"Error logging sent reminder: {e}")


async def log_reminder_timeline(
    chat_id: str,
    reminder_type: str,
    decision: str,
    reason: Optional[str] = None,
    event_id: Optional[str] = None,
    task_id: Optional[int] = None,
    alarm_id: Optional[int] = None,
    event_start_time: Optional[str] = None,
    minutes_until_start: Optional[int] = None,
    matched_window: Optional[str] = None,
    telegram_response: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None
):
    """Log structured timeline data for the reminder pipeline."""
    try:
        payload = {
            "chat_id": chat_id,
            "reminder_type": reminder_type,
            "decision": decision,
            "reason": reason,
            "event_id": event_id,
            "task_id": task_id,
            "alarm_id": alarm_id,
            "event_start_time": event_start_time,
            "minutes_until_start": minutes_until_start,
            "matched_window": matched_window,
            "telegram_response": telegram_response,
            "metadata": metadata or {}
        }
        await run_in_threadpool(
            lambda: supabase.table("reminder_logs").insert(payload).execute()
        )
    except Exception as e:
        print(f"Error logging reminder timeline: {e}")


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
            print("❌ M-Bot Error: SUPABASE_URL or KEY missing")
            return None

        res = supabase.table("system_config").select("value").eq("key", "google_token").execute()
        if not res.data:
            print("❌ M-Bot Error: google_token not found in system_config")
            return None

        token_data = res.data[0]["value"]
        if isinstance(token_data, str):
            token_data = json.loads(token_data)

        expiry_str = token_data.get("expiry")
        expiry = datetime.fromisoformat(expiry_str) if expiry_str else None

        creds = Credentials(
            token=token_data.get("token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri=token_data.get("token_uri"),
            client_id=token_data.get("client_id") or os.getenv("GOOGLE_CLIENT_ID"),
            client_secret=token_data.get("client_secret") or os.getenv("GOOGLE_CLIENT_SECRET"),
            scopes=token_data.get("scopes"),
            expiry=expiry,
        )

        if creds and creds.expired and creds.refresh_token:
            print("🔄 Refreshing Google OAuth token...")
            creds.refresh(GoogleRequest())
            updated = {
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "scopes": list(creds.scopes),
                "expiry": creds.expiry.isoformat() if creds.expiry else None,
            }
            supabase.table("system_config").update({"value": updated}).eq("key", "google_token").execute()
            print("✅ Google token refreshed and saved.")

        return creds
    except Exception as e:
        print(f"⚠️ M-Bot Auth Crash: {type(e).__name__} - {e}")
        return None


SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']


async def get_calendar_events(max_results: int = 5, until_time: Optional[str] = None) -> str:
    """
    Fetch the user's schedule from the database with strict chronological filtering and ordering.
    Standardized to Africa/Nairobi timezone.
    """
    try:
        now_nairobi = datetime.now(ZoneInfo("Africa/Nairobi"))
        now_nairobi_iso = now_nairobi.isoformat()

        # Compute the end-of-day target time localized strictly to 23:00:00 EAT
        if until_time and ":" in until_time:
            try:
                hour, minute = map(int, until_time.split(':'))
                target_until = now_nairobi.replace(hour=hour, minute=minute, second=0, microsecond=0)
            except ValueError:
                target_until = now_nairobi.replace(hour=23, minute=0, second=0, microsecond=0)
        else:
            target_until = now_nairobi.replace(hour=23, minute=0, second=0, microsecond=0)

        # Query Supabase with strict filtering and ordering
        res = await run_in_threadpool(
            lambda: supabase.table("user_schedules").select("*")
            .gte("start_time", now_nairobi_iso)
            .lte("start_time", target_until.isoformat())
            .order("start_time", desc=False)
            .limit(max_results)
            .execute()
        )

        events = res.data
        if not events:
            return "No upcoming events found."

        lines = ["[CHRONOLOGICAL TIMELINE]"]
        for event in events:
            start_str = event["start_time"]
            end_str = event["end_time"]
            summary = event.get("summary", "No Title")

            # Parse times to local Nairobi for display
            start_dt = datetime.fromisoformat(start_str.replace('Z', '+00:00')).astimezone(ZoneInfo("Africa/Nairobi"))
            end_dt = datetime.fromisoformat(end_str.replace('Z', '+00:00')).astimezone(ZoneInfo("Africa/Nairobi"))

            status = "UPCOMING"
            if start_dt <= now_nairobi <= end_dt:
                status = "ACTIVE"

            display_time = start_dt.strftime("%H:%M")
            lines.append(f"• {display_time} - {summary} ({status})")

        return "\n".join(lines)
    except Exception as e:
        print(f"Calendar Database Error: {e}")
        return "Couldn't fetch your schedule right now, bro."


async def get_scannable_briefing(chat_id: str) -> str:
    try:
        now = datetime.now(ZoneInfo("Africa/Nairobi"))
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        end_of_day   = now.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()

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
            start_dt = datetime.fromisoformat(
                item["start_time"].replace('Z', '+00:00')
            ).astimezone(ZoneInfo("Africa/Nairobi"))
            lines.append(f"- {start_dt.strftime('%H:%M')}: {item['summary']}")

        return "\n".join(lines)
    except Exception as e:
        print(f"Error fetching scannable briefing: {e}")
        return "Couldn't pull your scannable briefing right now."


async def get_schedule(max_results: int = 5) -> str:
    return await get_calendar_events(max_results)


# ─── Pydantic models ───────────────────────────────────────────────────────────

class NudgeRequest(BaseModel):
    message: Optional[str] = "Yo Muchiri, just checking in!"


class BrainDump(BaseModel):
    raw_content: str
    tags: List[str] = []
    metadata: Dict[str, Any] = {}


# ─── Function dispatcher for Gemini tool calls ────────────────────────────────

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
            return await run_in_threadpool(lambda: self.tools[func_name](**args))
        raise ValueError(f"Unknown tool: {func_name}")


dispatcher = FunctionDispatcher()
dispatcher.register("get_calendar_events", get_calendar_events)


# ─── Core send function ────────────────────────────────────────────────────────

telegram_client = TelegramClient(TELEGRAM_TOKEN)


async def send_telegram_message(
    chat_id: str,
    text: str,
    reply_to_message_id: Optional[int] = None,
    reminder_type: Optional[str] = None,
):
    """
    Send a Telegram message with unified cooldown + mute logic.

    Guard order:
      1. BYPASS_ALL types skip every guard.
      2. Per-type cooldown check (nudge_sent_registry).
      3. Global mute check (skips scheduled wellness/escalation types).
      4. Duplicate content check (5-min window).
    """
    try:
        now = datetime.now(ZoneInfo("Africa/Nairobi"))

        if reminder_type and reminder_type not in BYPASS_ALL:

            # 1. Per-type cooldown
            if reminder_type in NUDGE_COOLDOWNS:
                last_sent = nudge_sent_registry.get(reminder_type)
                if last_sent and (now - last_sent) < NUDGE_COOLDOWNS[reminder_type]:
                    elapsed = (now - last_sent).total_seconds() / 60
                    print(f"🤫 [{reminder_type}] on cooldown — {elapsed:.1f}m since last send. Skipping.")
                    return

            # 2. Global mute (does not apply to escalations or task alerts)
            MUTE_EXEMPT = {"task_escalation", "alarm_escalation", "suspicious_silence"}
            if reminder_type not in MUTE_EXEMPT and chat_id == MUCHIRI_CHAT_ID:
                state = await get_user_state(chat_id)
                if state.get("is_muted"):
                    muted_until_str = state.get("muted_until")
                    if muted_until_str:
                        muted_until = datetime.fromisoformat(muted_until_str)
                        if now < muted_until:
                            print(f"🤫 Muted until {muted_until_str}. Skipping [{reminder_type}].")
                            return

            # 3. Duplicate content guard
            if await should_skip_reminder(chat_id, reminder_type, text):
                print(f"🚫 Duplicate guard hit for [{reminder_type}]. Skipping.")
                return

        success = await telegram_client.send_message(
            chat_id=chat_id,
            text=text,
            reply_to_message_id=reply_to_message_id,
        )

        if success and reminder_type:
            await log_sent_reminder(chat_id, reminder_type, text)
            # Update per-type cooldown registry
            if reminder_type in NUDGE_COOLDOWNS:
                nudge_sent_registry[reminder_type] = now

    except Exception as e:
        print(f"Error sending Telegram message: {e}")


# ─── Nudge Engine (alarms + task nudges) ──────────────────────────────────────

class NudgeEngine:
    def __init__(self, supabase_client: Client):
        self.supabase = supabase_client

    async def check_alerts(self):
        try:
            now = datetime.now(ZoneInfo("Africa/Nairobi"))
            escalation_time = now - timedelta(minutes=5)

            # 1. Alarms — always fire
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
                    await send_telegram_message(
                        chat_id,
                        f"🚨 ALARM: {alarm['message']}",
                        reminder_type="alarm",
                        alarm_id=alarm["id"]
                    )
                    await run_in_threadpool(
                        lambda: self.supabase.table("user_alarms")
                        .update({"status": "triggered", "triggered_at": now.isoformat()})
                        .eq("id", alarm["id"])
                        .execute()
                    )
            except Exception as e:
                print(f"NudgeEngine Alarms Error: {e}")

            # 2. Alarm escalation
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
                        reminder_type="alarm_escalation",
                        alarm_id=alarm["id"]
                    )
                    await run_in_threadpool(
                        lambda: self.supabase.table("user_alarms")
                        .update({"triggered_at": now.isoformat()})
                        .eq("id", alarm["id"])
                        .execute()
                    )
            except Exception as e:
                print(f"NudgeEngine Alarm Escalation Error: {e}")

            # 3. Task nudges
            # Suppression: skip if Pomodoro active OR in a focus block.
            # Interaction-time suppression removed — the per-type cooldown handles spacing.
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
                    ctx   = await get_user_context(chat_id)
                    state = await get_user_state(chat_id)

                    # Suppression Logic
                    last_interaction_str = state.get("last_user_interaction_at")
                    if last_interaction_str:
                        last_interaction = datetime.fromisoformat(last_interaction_str)
                        if (now - last_interaction) < timedelta(minutes=10):
                            reason = f"Nag Kill-Switch: Recent interaction ({now - last_interaction})."
                            print(f"🤫 {reason} Skipping task nudge.")
                            await log_reminder_timeline(
                                chat_id=chat_id,
                                reminder_type="task_nudge",
                                decision="suppressed",
                                reason=reason,
                                task_id=task["id"]
                            )
                            continue
                        if (now - last_interaction) < timedelta(hours=3):
                            # 3-hour suppression for non-escalation nudges
                            # Check if it's a "Deep Work" reminder context
                            if ctx.get("current_block_type") in ["calendar_focus", "pomodoro"]:
                                reason = f"Suppression: Under 3h since interaction ({now - last_interaction}) and in {ctx['current_block_type']}."
                                print(f"🤫 {reason} Skipping Deep Work reminder.")
                                await log_reminder_timeline(
                                    chat_id=chat_id,
                                    reminder_type="task_nudge",
                                    decision="suppressed",
                                    reason=reason,
                                    task_id=task["id"]
                                )
                                continue

                    if state.get("pomodoro_active"):
                        reason = "Pomodoro Lock active."
                        print(f"🤫 {reason} Skipping non-essential nudge.")
                        await log_reminder_timeline(
                            chat_id=chat_id,
                            reminder_type="task_nudge",
                            decision="suppressed",
                            reason=reason,
                            task_id=task["id"]
                        )
                        continue
                    if ctx.get("current_block_type"):
                        reason = f"Silent Mode active ({ctx['current_block_type']})."
                        print(f"🤫 {reason} Skipping task nudge.")
                        await log_reminder_timeline(
                            chat_id=chat_id,
                            reminder_type="task_nudge",
                            decision="suppressed",
                            reason=reason,
                            task_id=task["id"]
                        )
                        continue

                    if chat_id not in vetted_ps_cache:
                        vetted_ps_cache[chat_id] = await executive_sync_service._get_vetted_ps(chat_id)

                    await send_telegram_message(
                        chat_id,
                        f"🕒 TASK DUE: {task['title']}{vetted_ps_cache[chat_id]}",
                        reminder_type="task_nudge",
                        task_id=task["id"]
                    )
                    await run_in_threadpool(
                        lambda: self.supabase.table("user_tasks")
                        .update({"triggered_at": now.isoformat()})
                        .eq("id", task["id"])
                        .execute()
                    )
            except Exception as e:
                print(f"NudgeEngine Tasks Error: {e}")

            # 4. Task escalation
            try:
                task_esc_resp = await run_in_threadpool(
                    lambda: self.supabase.table("user_tasks")
                    .select("*")
                    .eq("status", "pending")
                    .lte("triggered_at", escalation_time.isoformat())
                    .is_("acknowledged_at", "null")
                    .execute()
                )
                for task in task_esc_resp.data:
                    chat_id = task["chat_id"]
                    state   = await get_user_state(chat_id)
                    ctx     = await get_user_context(chat_id)

                    if state.get("pomodoro_active"):
                        reason = "Pomodoro Lock active during task escalation."
                        print(f"🤫 {reason} Skipping task escalation.")
                        await log_reminder_timeline(
                            chat_id=chat_id,
                            reminder_type="task_escalation",
                            decision="suppressed",
                            reason=reason,
                            task_id=task["id"]
                        )
                        continue
                    if ctx.get("current_block_type"):
                        reason = f"Silent Mode active ({ctx['current_block_type']}) during task escalation."
                        print(f"🤫 {reason} Skipping task escalation.")
                        await log_reminder_timeline(
                            chat_id=chat_id,
                            reminder_type="task_escalation",
                            decision="suppressed",
                            reason=reason,
                            task_id=task["id"]
                        )
                        continue

                    await send_telegram_message(
                        chat_id,
                        f"⚠️ ESCALATION: Task '{task['title']}' is still pending! "
                        "This is impacting your 'Probability of Outage'. Bro, get it done.",
                        reminder_type="task_escalation",
                        task_id=task["id"]
                    )
                    await run_in_threadpool(
                        lambda: self.supabase.table("user_tasks")
                        .update({"triggered_at": now.isoformat()})
                        .eq("id", task["id"])
                        .execute()
                    )
            except Exception as e:
                print(f"NudgeEngine Task Escalation Error: {e}")

            # 5. Pomodoro session completions — system alert, always fires
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
                    await send_telegram_message(
                        target_chat_id,
                        msg,
                        reminder_type="pomodoro_alert",
                        task_id=session.get("task_id")
                    )
                    await update_user_state(target_chat_id, pomodoro_active=False)
                    await run_in_threadpool(
                        lambda: self.supabase.table("pomodoro_sessions")
                        .update({"status": "completed"})
                        .eq("id", session["id"])
                        .execute()
                    )
            except Exception as e:
                print(f"NudgeEngine Pomodoro Error: {e}")

        except Exception as e:
            print(f"NudgeEngine top-level Error: {e}")


nudge_engine_service = NudgeEngine(supabase)


# ─── Executive Sync Service ────────────────────────────────────────────────────

class ExecutiveSyncService:
    def __init__(self, supabase_client: Client, pomodoro_svc: PomodoroService):
        self.supabase = supabase_client
        self.pomodoro_service = pomodoro_svc
        self.chat_id = MUCHIRI_CHAT_ID

    async def _get_vetted_ps(self, chat_id: Optional[str] = None) -> str:
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
        if not self.chat_id:
            return
        try:
            now = datetime.now(ZoneInfo("Africa/Nairobi"))
            ctx = await get_user_context(self.chat_id)

            # 1. 8 AM Briefing (with 15-min retry window)
            if now.hour == 8 and now.minute < 16:
                last_briefing = ctx.get("last_briefing_at")
                already_sent = False
                if last_briefing:
                    lb_dt = datetime.fromisoformat(last_briefing)
                    if lb_dt.date() == now.date():
                        already_sent = True

                if not already_sent:
                    briefing_content = await get_scannable_briefing(self.chat_id)
                    msg = f"Morning Muchiri. Here's your scannable list for today:\n\n{briefing_content}"
                    await send_telegram_message(self.chat_id, msg, reminder_type="morning_briefing")
                    await update_user_context(self.chat_id, last_briefing_at=now.isoformat())
                    print(f"✅ 8 AM Briefing sent ({now.strftime('%H:%M')}).")

            # 2. Elastic Deep Work Sync — update focus block context
            active_pomodoro = await self.pomodoro_service.get_active_session(self.chat_id)
            creds = await run_in_threadpool(get_google_creds)
            current_block_id   = None
            current_block_type = None

            if active_pomodoro:
                current_block_id   = active_pomodoro["id"]
                current_block_type = "pomodoro"
            elif creds:
                service = build('calendar', 'v3', credentials=creds)
                events_result = await run_in_threadpool(
                    lambda: service.events().list(
                        calendarId='primary',
                        timeMin=now.isoformat(),
                        maxResults=5,
                        singleEvents=True,
                        orderBy='startTime',
                    ).execute()
                )
                for event in events_result.get('items', []):
                    start_str = event['start'].get('dateTime', event['start'].get('date'))
                    end_str   = event['end'].get('dateTime', event['end'].get('date'))
                    start_dt  = datetime.fromisoformat(start_str.replace('Z', '+00:00')).astimezone(ZoneInfo("Africa/Nairobi"))
                    end_dt    = datetime.fromisoformat(end_str.replace('Z', '+00:00')).astimezone(ZoneInfo("Africa/Nairobi"))
                    if start_dt <= now <= end_dt:
                        summary = event.get('summary', '').lower()
                        if "deep work" in summary or "ai engineering" in summary:
                            current_block_id   = event['id']
                            current_block_type = "calendar_focus"
                            break

            previous_block_type = ctx.get("current_block_type")
            previous_block_id   = ctx.get("current_block_id")
            if current_block_type != previous_block_type or current_block_id != previous_block_id:
                await update_user_context(
                    self.chat_id,
                    current_block_type=current_block_type,
                    current_block_id=current_block_id,
                )
                reason = f"Block state transition: {previous_block_type} -> {current_block_type}"
                print(f"🔄 {reason} ({current_block_id})")
                await log_reminder_timeline(
                    chat_id=self.chat_id,
                    reminder_type="block_sync",
                    decision="info",
                    reason=reason,
                    metadata={
                        "previous_block_type": previous_block_type,
                        "current_block_type": current_block_type,
                        "previous_block_id": previous_block_id,
                        "current_block_id": current_block_id
                    }
                )

            # 3. Suspicious Silence nudge — block ended > 15 mins ago, no interaction since
            if not current_block_type:
                updated_at_str = ctx.get("updated_at")
                if updated_at_str:
                    updated_at = datetime.fromisoformat(updated_at_str)
                    if (now - updated_at) >= timedelta(minutes=15):
                        last_silence_str = ctx.get("last_suspicious_silence_at")
                        last_silence = (
                            datetime.fromisoformat(last_silence_str)
                            if last_silence_str
                            else datetime.min.replace(tzinfo=ZoneInfo("Africa/Nairobi"))
                        )
                        if last_silence < updated_at:
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
                                else:
                                    reason = f"Suspicious Silence suppressed: Under 3h since last interaction ({now - last_interaction})."
                                    print(f"🤫 {reason}")
                                    await log_reminder_timeline(
                                        chat_id=self.chat_id,
                                        reminder_type="suspicious_silence",
                                        decision="suppressed",
                                        reason=reason
                                    )

            # 4. General Inactivity Nudge (MIA for 6+ hours)
            if 9 <= now.hour <= 21:
                state = await get_user_state(self.chat_id)
                last_int_str = state.get("last_user_interaction_at")
                last_int = (
                    datetime.fromisoformat(last_int_str)
                    if last_int_str
                    else datetime.min.replace(tzinfo=ZoneInfo("Africa/Nairobi"))
                )
                if (now - last_int) >= timedelta(hours=6):
                    last_inact_str = ctx.get("last_inactivity_nudge_at")
                    last_inact = (
                        datetime.fromisoformat(last_inact_str)
                        if last_inact_str
                        else datetime.min.replace(tzinfo=ZoneInfo("Africa/Nairobi"))
                    )
                    if last_inact.date() < now.date():
                        tasks_resp = await run_in_threadpool(
                            lambda: self.supabase.table("user_tasks")
                            .select("id")
                            .eq("chat_id", self.chat_id)
                            .eq("status", "pending")
                            .limit(1)
                            .execute()
                        )
                        if tasks_resp.data:
                            msg  = "Yo Muchiri, you've been off the radar for a bit. Still got pending tasks when you're back."
                            msg += await self._get_vetted_ps()
                            await send_telegram_message(self.chat_id, msg, reminder_type="inactivity_nudge")
                            await update_user_context(self.chat_id, last_inactivity_nudge_at=now.isoformat())
                            print("🧐 General Inactivity Nudge sent.")
                        else:
                            reason = "General Inactivity nudge skipped: No pending tasks."
                            print(f"🤫 {reason}")
                            await log_reminder_timeline(
                                chat_id=self.chat_id,
                                reminder_type="inactivity_nudge",
                                decision="skipped",
                                reason=reason
                            )
                else:
                    reason = f"General Inactivity suppressed: Under 6h since last interaction ({now - last_interaction})."
                    # We don't want to spam logs every minute for this, maybe only log if it was a candidate
                    pass

        except Exception as e:
            print(f"❌ ExecutiveSyncService Error: {e}")


executive_sync_service = ExecutiveSyncService(supabase, pomodoro_service)


# ─── Outbound telemetry loop (1-min polling) ───────────────────────────────────

async def outbound_telemetry_loop():
    print("🚀 Outbound Telemetry Loop started.")
    last_4h_check = datetime.min.replace(tzinfo=ZoneInfo("Africa/Nairobi"))

    while True:
        try:
            await nudge_engine_service.check_alerts()
            await executive_sync_service.sync_executive_state()

            now = datetime.now(ZoneInfo("Africa/Nairobi"))
            if (now - last_4h_check).total_seconds() >= 14400:
                await evaluate_project_velocity()
                await evaluate_habit_velocity()
                last_4h_check = now

        except asyncio.CancelledError:
            print("🛑 Outbound Telemetry Loop stopping.")
            break
        except Exception as e:
            print(f"❌ Telemetry loop error: {e}")

        await asyncio.sleep(60)


# ─── Scheduled wellness nudges ─────────────────────────────────────────────────
# These fire on a fixed schedule and are NOT subject to interaction suppression.
# They are spaced by the per-type cooldowns in NUDGE_COOLDOWNS.

HYDRATION_MESSAGES = [
    "Hydration check. Water, not coffee.",
    "You need water. Not later — now.",
    "Drink water. Your brain runs on it.",
    "Quick break: glass of water. Go.",
]
MOVEMENT_MESSAGES = [
    "Stand up. 5 mins away from the screen.",
    "Screen break. Walk around, stretch.",
    "Eyes off the monitor for 5. Move.",
    "Quick movement break — do it.",
]

_hydration_idx = 0
_movement_idx  = 0


async def hydration_nudge():
    global _hydration_idx
    if not MUCHIRI_CHAT_ID:
        return
    msg = HYDRATION_MESSAGES[_hydration_idx % len(HYDRATION_MESSAGES)]
    _hydration_idx += 1
    await send_telegram_message(MUCHIRI_CHAT_ID, msg, reminder_type="hydration")
    print("💧 Hydration nudge sent.")


async def movement_nudge():
    global _movement_idx
    if not MUCHIRI_CHAT_ID:
        return
    msg = MOVEMENT_MESSAGES[_movement_idx % len(MOVEMENT_MESSAGES)]
    _movement_idx += 1
    await send_telegram_message(MUCHIRI_CHAT_ID, msg, reminder_type="movement")
    print("🏃 Movement nudge sent.")


# ─── APScheduler jobs ──────────────────────────────────────────────────────────

async def morning_routine_check():
    msg = "Morning Routine Check: Skincare and Scent. Look like the engineer you're building."
    await send_telegram_message(MUCHIRI_CHAT_ID, msg, reminder_type="morning_check")
    print("🌅 Morning routine check sent.")


async def evening_review_task():
    if MUCHIRI_CHAT_ID:
        review = await generate_evening_review(MUCHIRI_CHAT_ID)
        await send_telegram_message(MUCHIRI_CHAT_ID, review, reminder_type="evening_review_proactive")


async def cleanup_old_records():
    try:
        now          = datetime.now(ZoneInfo("Africa/Nairobi")).isoformat()
        one_day_ago  = (datetime.now(ZoneInfo("Africa/Nairobi")) - timedelta(days=1)).isoformat()

        await run_in_threadpool(
            lambda: supabase.table("user_schedules").delete().lt("end_time", now).execute()
        )
        await run_in_threadpool(
            lambda: supabase.table("sent_reminders").delete().lt("event_start_time", one_day_ago).execute()
        )
        print("🧹 Cleanup completed.")
    except Exception as e:
        print(f"Error in cleanup_old_records: {e}")


async def check_upcoming_events():
    try:
        now = datetime.now(ZoneInfo("Africa/Nairobi"))
        for window in [15, 5, 0]:
            target_time = now + timedelta(minutes=window)
            start_range = (target_time - timedelta(seconds=30)).isoformat()
            end_range   = (target_time + timedelta(seconds=30)).isoformat()

            res = await run_in_threadpool(
                lambda: supabase.table("user_schedules")
                .select("*")
                .gte("start_time", start_range)
                .lte("start_time", end_range)
                .execute()
            )
            for event in res.data:
                chat_id = event.get("chat_id")
                if not chat_id:
                    continue

                event_id      = event["event_id"]
                reminder_type = f"event_reminder_{window}m"

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
                    await run_in_threadpool(
                        lambda: supabase.table("sent_reminders").insert({
                            "chat_id": chat_id,
                            "reminder_type": reminder_type,
                            "content": briefing,
                            "event_id": event_id,
                            "event_start_time": event["start_time"],
                            "sent_at": datetime.now(ZoneInfo("Africa/Nairobi")).isoformat(),
                        }).execute()
                    )
    except Exception as e:
        print(f"Error in check_upcoming_events: {e}")


# ─── App lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    await telegram_client.start()

    # Event-based jobs
    scheduler.add_job(check_upcoming_events, 'interval', seconds=60)
    scheduler.add_job(evening_review_task,   CronTrigger(hour=22, minute=0))
    scheduler.add_job(cleanup_old_records,   CronTrigger(hour=3,  minute=0))

    # Wellness nudges — fixed schedule, no interaction dependency
    scheduler.add_job(morning_routine_check, CronTrigger(hour=7,  minute=30))
    scheduler.add_job(hydration_nudge,       CronTrigger(hour='10,13,16,19', minute=0))
    scheduler.add_job(movement_nudge,        CronTrigger(hour='11,14,17',    minute=30))

    # NOTE: morning_briefing_task intentionally removed — handled by
    # outbound_telemetry_loop → executive_sync_service.sync_executive_state()
    # to avoid the race condition that was blocking all nudges post-briefing.

    scheduler.start()

    telemetry_task = asyncio.create_task(outbound_telemetry_loop())

    yield

    scheduler.shutdown()
    telemetry_task.cancel()
    try:
        await telemetry_task
    except asyncio.CancelledError:
        pass
    await telegram_client.stop()
    print("🛑 M-bot shut down cleanly.")


app = FastAPI(title="M-bot", lifespan=lifespan)

# Include Proactive Router
app.include_router(proactive_router)


# ─── Utility / storage helpers ─────────────────────────────────────────────────

async def store_project_zayn(data: Dict[str, Any]):
    try:
        payload = {
            "content":       data.get("content"),
            "skincare_done": data.get("skincare_done", False),
            "workout_done":  data.get("workout_done", False),
        }
        await run_in_threadpool(supabase.table("project_zayn").upsert(payload).execute)
    except Exception as e:
        print(f"Error storing Project Zayn: {e}")


async def store_dev_milestone(category: str, data: Dict[str, Any]):
    try:
        payload = {"category": category, "content": data.get("content")}
        await run_in_threadpool(supabase.table("dev_milestones").insert(payload).execute)
    except Exception as e:
        print(f"Error storing dev milestone: {e}")


async def fetch_chat_history(chat_id: str) -> List[Dict[str, str]]:
    try:
        response = await run_in_threadpool(
            lambda: supabase.table("messages")
            .select("role", "content")
            .eq("chat_id", chat_id)
            .order("created_at", desc=True)
            .limit(30)
            .execute()
        )
        return response.data[::-1]
    except Exception as e:
        print(f"Error fetching chat history: {e}")
        return []


async def store_message(chat_id: str, role: str, content: str):
    try:
        await run_in_threadpool(
            supabase.table("messages").insert({"chat_id": chat_id, "role": role, "content": content}).execute
        )
    except Exception as e:
        print(f"Error storing message: {e}")


async def send_telegram_message(
    chat_id: str,
    text: str,
    reply_to_message_id: Optional[int] = None,
    reminder_type: Optional[str] = None,
    event_id: Optional[str] = None,
    task_id: Optional[int] = None,
    alarm_id: Optional[int] = None,
    event_start_time: Optional[str] = None,
    minutes_until_start: Optional[int] = None,
    matched_window: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
):
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
                    diff = now - last_nudge_sent_at
                    if diff < timedelta(hours=1):
                        reason = f"Cool-down active. Last nudge was {diff.total_seconds()/60:.1f}m ago."
                        print(f"🤫 {reason} Skipping {reminder_type}.")
                        await log_reminder_timeline(
                            chat_id=chat_id,
                            reminder_type=reminder_type,
                            decision="suppressed",
                            reason=reason,
                            event_id=event_id,
                            task_id=task_id,
                            alarm_id=alarm_id,
                            event_start_time=event_start_time,
                            minutes_until_start=minutes_until_start,
                            matched_window=matched_window,
                            metadata=metadata
                        )
                        return

                # 2. Global Mute check
                state = await get_user_state(chat_id)
                if state.get("is_muted"):
                    muted_until_str = state.get("muted_until")
                    if muted_until_str:
                        muted_until = datetime.fromisoformat(muted_until_str)
                        if now < muted_until:
                            reason = f"User is muted until {muted_until_str}."
                            print(f"🤫 {reason} Skipping {reminder_type}.")
                            await log_reminder_timeline(
                                chat_id=chat_id,
                                reminder_type=reminder_type,
                                decision="suppressed",
                                reason=reason,
                                event_id=event_id,
                                task_id=task_id,
                                alarm_id=alarm_id,
                                event_start_time=event_start_time,
                                minutes_until_start=minutes_until_start,
                                matched_window=matched_window,
                                metadata=metadata
                            )
                            return

        # Check for duplicate reminders
        if reminder_type:
            if await should_skip_reminder(chat_id, reminder_type, text):
                reason = "Duplicate reminder detected within last 5 minutes."
                print(f"🚫 {reason} {reminder_type}")
                await log_reminder_timeline(
                    chat_id=chat_id,
                    reminder_type=reminder_type,
                    decision="skipped",
                    reason=reason,
                    event_id=event_id,
                    task_id=task_id,
                    alarm_id=alarm_id,
                    event_start_time=event_start_time,
                    minutes_until_start=minutes_until_start,
                    matched_window=matched_window,
                    metadata=metadata
                )
                return

        result = await telegram_client.send_message(
            chat_id=chat_id,
            text=text,
            reply_to_message_id=reply_to_message_id
        )

        success = result.get("success", False)
        response_data = result.get("response")
        error = result.get("error")

        # Log timeline
        await log_reminder_timeline(
            chat_id=chat_id,
            reminder_type=reminder_type or "manual",
            decision="sent" if success else "failed",
            reason=error if not success else None,
            event_id=event_id,
            task_id=task_id,
            alarm_id=alarm_id,
            event_start_time=event_start_time,
            minutes_until_start=minutes_until_start,
            matched_window=matched_window,
            telegram_response=response_data,
            metadata=metadata
        )

        if success:
            # Log sent reminder
            if reminder_type:
                await log_sent_reminder(chat_id, reminder_type, text)

            # Update last_nudge_sent_at if it's a proactive nudge for Muchiri
            if chat_id == MUCHIRI_CHAT_ID and reminder_type:
                last_nudge_sent_at = datetime.now(ZoneInfo("Africa/Nairobi"))
    except Exception as e:
        print(f"Error acknowledging: {e}")


# ─── AI response helpers ───────────────────────────────────────────────────────

async def generate_personalized_nudge(project_name: str, status: str) -> str:
    try:
        model  = genai.GenerativeModel("gemini-1.5-flash")
        prompt = (
            f"Project: {project_name}. Status: {status}. "
            "Write a sharp 1-sentence reminder for a high-performance engineer."
        )
        response = await model.generate_content_async(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Error generating nudge: {e}")
        return f"Yo Muchiri, push an update for {project_name}. Time's ticking."


async def evaluate_project_velocity():
    try:
        now              = datetime.now(ZoneInfo("Africa/Nairobi"))
        twelve_hours_ago = now - timedelta(hours=12)

        res = await run_in_threadpool(
            lambda: supabase.table("goals").select("*").eq("status", "active").execute()
        )
        for project in res.data:
            project_id   = project["id"]
            project_name = project["name"]
            priority     = project.get("priority", 1)

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
                status_desc = "No updates in over 12 hours." if last_update else "No activity logs found."
                nudge = await generate_personalized_nudge(project_name, status_desc)
                await send_telegram_message(MUCHIRI_CHAT_ID, nudge, reminder_type="velocity_nudge")
                print(f"🚀 Velocity nudge: {project_name}")
    except Exception as e:
        print(f"Error in evaluate_project_velocity: {e}")


async def evaluate_habit_velocity():
    try:
        now = datetime.now(ZoneInfo("Africa/Nairobi"))
        res = await run_in_threadpool(lambda: supabase.table("habits").select("*").execute())
        for habit in res.data:
            if habit["name"] == "dopamine_integrity" and habit.get("streak", 0) == 0:
                last_completed = habit.get("last_completed_at")
                if last_completed:
                    last_dt = datetime.fromisoformat(last_completed)
                    if (now - last_dt) > timedelta(days=1):
                        msg = (
                            "🚨 Systems Failure Alert: Streak reset. "
                            "Back to the GNN research to stabilise the dopamine loop."
                        )
                        await send_telegram_message(MUCHIRI_CHAT_ID, msg, reminder_type="habit_alert")
                        print("⚠️ Dopamine discipline alert sent.")
    except Exception as e:
        print(f"Error in evaluate_habit_velocity: {e}")


async def generate_event_briefing(event: Dict[str, Any], window_mins: int) -> str:
    now_str  = datetime.now(ZoneInfo("Africa/Nairobi")).strftime("%Y-%m-%d %H:%M:%S")
    start_dt = datetime.fromisoformat(
        event["start_time"].replace('Z', '+00:00')
    ).astimezone(ZoneInfo("Africa/Nairobi"))
    time_str     = start_dt.strftime("%I:%M %p")
    window_text  = f"in {window_mins} minutes" if window_mins > 0 else "now"
    location_text = f"\n📍 Location: {event['location']}" if event.get("location") else ""

    prompt = (
        f"Context: It's {now_str}. Event starting {window_text}.\n\n"
        f"Title: {event['summary']}\nStart: {time_str}\n"
        f"Description: {event.get('description', 'None.')}\n{location_text}\n\n"
        "Generate a sharp proactive briefing:\n"
        "🚨 Upcoming Event: [Title]\nStarts: [Time] ([Window])\n[Location]\n\n"
        "Objectives:\n• [obj 1]\n• [obj 2]\n\nSuggested Focus:\n[One sharp sentence.]"
    )
    try:
        model    = genai.GenerativeModel("gemini-1.5-flash")
        response = await model.generate_content_async(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Error generating event briefing: {e}")
        return (
            f"🚨 Upcoming Event: {event['summary']}\n"
            f"Starts: {time_str} ({window_text}){location_text}\n\nGet focused."
        )


async def generate_morning_briefing(chat_id: str) -> str:
    now          = datetime.now(ZoneInfo("Africa/Nairobi"))
    start_of_day = now.replace(hour=0,  minute=0,  second=0,  microsecond=0).isoformat()
    end_of_day   = now.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()

    events_res = await run_in_threadpool(
        lambda: supabase.table("user_schedules")
        .select("*")
        .eq("chat_id", chat_id)
        .gte("start_time", start_of_day)
        .lte("start_time", end_of_day)
        .order("start_time")
        .execute()
    )
    tasks_res = await run_in_threadpool(
        lambda: supabase.table("user_tasks")
        .select("*")
        .eq("chat_id", chat_id)
        .eq("status", "pending")
        .gte("impact_score", 7)
        .execute()
    )

    prompt = (
        f"It's {now.strftime('%A, %B %d, %Y')} at 9:00 AM.\n\n"
        f"Events: {json.dumps(events_res.data or [])}\n"
        f"High-priority tasks: {json.dumps(tasks_res.data or [])}\n\n"
        "Morning briefing: scannable schedule, top 2-3 tasks, free deep-work blocks. "
        "Tone: senior executive assistant, sharp, no fluff."
    )
    try:
        model    = genai.GenerativeModel("gemini-1.5-flash")
        response = await model.generate_content_async(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Error generating morning briefing: {e}")
        return "Morning! Busy day ahead. Check the calendar and crush those tasks."


async def generate_evening_review(chat_id: str) -> str:
    now          = datetime.now(ZoneInfo("Africa/Nairobi"))
    start_of_day = now.replace(hour=0,  minute=0,  second=0,  microsecond=0).isoformat()
    end_of_day   = now.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()

    events_res = await run_in_threadpool(
        lambda: supabase.table("user_schedules")
        .select("*")
        .eq("chat_id", chat_id)
        .gte("start_time", start_of_day)
        .lte("start_time", end_of_day)
        .execute()
    )
    tasks_res = await run_in_threadpool(
        lambda: supabase.table("user_tasks")
        .select("*")
        .eq("chat_id", chat_id)
        .gte("created_at", start_of_day)
        .execute()
    )

    prompt = (
        f"It's {now.strftime('%A, %B %d, %Y')} at 10:00 PM.\n\n"
        f"Today's events: {json.dumps(events_res.data)}\n"
        f"Tasks: {json.dumps(tasks_res.data)}\n\n"
        "Evening review: achievements, ask what Elvis completed, productivity score 1-10. "
        "Tone: reflective, encouraging, senior EA."
    )
    try:
        model    = genai.GenerativeModel("gemini-1.5-flash")
        response = await model.generate_content_async(prompt)
        return response.text.strip()
    except Exception as e:
        print(f"Error generating evening review: {e}")
        return "Evening, Elvis! How was your day? Tell me what you crushed."


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

        # Log cycle start for the main user for visibility
        if MUCHIRI_CHAT_ID:
            await log_reminder_timeline(
                chat_id=MUCHIRI_CHAT_ID,
                reminder_type="scheduler_cycle",
                decision="info",
                reason="Starting check_upcoming_events cycle.",
                metadata={"windows": windows, "now": now.isoformat()}
            )

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

            # Log retrieval
            for chat_id_item in {e.get("chat_id") for e in res.data if e.get("chat_id")}:
                await log_reminder_timeline(
                    chat_id=chat_id_item,
                    reminder_type=f"event_reminder_{window}m",
                    decision="attempted",
                    reason=f"Retrieved {len([e for e in res.data if e.get('chat_id') == chat_id_item])} events for {window}m window.",
                    matched_window=f"{window}m",
                    metadata={"retrieved_count": len(res.data), "start_range": start_range, "end_range": end_range}
                )

            if not res.data:
                # Optional: Log empty cycles for a specific chat if we want total visibility
                pass

            for event in res.data:
                chat_id = event.get("chat_id")
                if not chat_id: continue

                event_id = event["event_id"]
                reminder_type = f"event_reminder_{window}m"
                event_start_time = event["start_time"]

                start_dt = datetime.fromisoformat(event_start_time.replace('Z', '+00:00')).astimezone(ZoneInfo("Africa/Nairobi"))
                minutes_until_start = int((start_dt - now).total_seconds() / 60)

                # Check if already sent
                sent_res = await run_in_threadpool(
                    lambda: supabase.table("sent_reminders")
                    .select("id")
                    .eq("chat_id", chat_id)
                    .eq("event_id", event_id)
                    .eq("reminder_type", reminder_type)
                    .execute()
                )

                if sent_res.data:
                    await log_reminder_timeline(
                        chat_id=chat_id,
                        reminder_type=reminder_type,
                        decision="skipped",
                        reason="Event reminder already logged in sent_reminders.",
                        event_id=event_id,
                        event_start_time=event_start_time,
                        minutes_until_start=minutes_until_start,
                        matched_window=f"{window}m"
                    )
                    continue

                briefing = await generate_event_briefing(event, window)
                await send_telegram_message(
                    chat_id,
                    briefing,
                    reminder_type=reminder_type,
                    event_id=event_id,
                    event_start_time=event_start_time,
                    minutes_until_start=minutes_until_start,
                    matched_window=f"{window}m"
                )

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

# ─── LLM response ─────────────────────────────────────────────────────────────

async def get_llm_response(prompt: str) -> str:
    chat_id     = current_chat_id.get()
    history     = await fetch_chat_history(chat_id)
    now_nairobi = datetime.now(ZoneInfo("Africa/Nairobi")).strftime("%Y-%m-%d %H:%M:%S")

    system_instruction = (
        f"Current time: {now_nairobi} (Africa/Nairobi)\n\n"
        "CRITICAL: You must strictly follow the [CHRONOLOGICAL TIMELINE] provided. "
        "Do not suggest or mention events that occurred in the past relative to the 'Current time' provided in this prompt. "
        "Process events only in the forward-moving order shown.\n\n"
        "You are M-bot, the personal AI assistant of Elvis Muchiri — a QA Engineer, "
        "AI builder, and ambitious guy based in Kenya. "
        "You're that one brilliant friend who has their life together and knows everything. "
        "Smart, casual, occasionally witty, never robotic. Talk like a real person — "
        "no bullet-point overload, no corporate speak, no fake enthusiasm.\n\n"
        "Address Elvis naturally: sometimes 'Elvis', sometimes 'bro', 'chief', 'man', 'G' — "
        "read the room. Health stuff: encouraging but not cheesy. Work mode: sharp and focused. "
        "Just chatting: match that energy.\n\n"
        "You track:\n"
        "- Project Zayn: health, fitness, skincare, workout streak\n"
        "- Build Mode: technical work, coding, engineering notes\n"
        "- AI Roadmap: journey to AI Engineering mastery\n"
        "- Kijiji: side hustles and marketplace activities\n\n"
        "Rules:\n"
        "- Never make up data you don't have. Say so plainly.\n"
        "- Keep it conversational. Match his energy.\n"
        "- Use markdown when it aids scannability.\n"
        "- Vary your openers. Don't start the same way twice.\n"
        "- If he seems stressed, acknowledge it like a friend.\n"
        "- If he adds a task without an effort/impact score, ask for it.\n"
        "- If he provides scores, suggest a focus order based on impact/effort."
    )

    if GEMINI_API_KEY:
        try:
            model = genai.GenerativeModel(
                model_name="gemini-1.5-flash",
                system_instruction=system_instruction,
                tools=[get_calendar_events],
            )
            gemini_history = [
                {"role": "user" if m["role"] == "user" else "model", "parts": [m["content"]]}
                for m in history
            ]
            chat     = model.start_chat(history=gemini_history)
            response = await chat.send_message_async(prompt)

            while response.candidates[0].content.parts[0].function_call:
                tool_call = response.candidates[0].content.parts[0].function_call
                result    = await dispatcher.dispatch(tool_call)
                response  = await chat.send_message_async(
                    genai.types.Content(
                        parts=[genai.types.Part.from_function_response(
                            name=tool_call.name, response={"result": result}
                        )]
                    )
                )
            return response.text
        except Exception as e:
            print(f"Gemini error: {e}. Falling back to Groq.")

    return await get_groq_response(prompt, history)


async def get_groq_response(prompt: str, history: List[Dict[str, str]]) -> str:
    now_nairobi = datetime.now(ZoneInfo("Africa/Nairobi")).strftime("%Y-%m-%d %H:%M:%S")
    try:
        calendar_context = await get_calendar_events()
        if any(p in calendar_context.lower() for p in ["authentication failed", "couldn't fetch", "check your"]):
            calendar_section = "Calendar is unavailable right now."
        else:
            calendar_section = f"Upcoming events:\n{calendar_context}"
    except Exception as e:
        print(f"Calendar fetch error in Groq fallback: {e}")
        calendar_section = "Calendar is unavailable right now."

    system_message = {
        "role": "system",
        "content": (
            f"Current time: {now_nairobi} (Africa/Nairobi)\n\n"
            "CRITICAL: You must strictly follow the [CHRONOLOGICAL TIMELINE] provided. "
            "Do not suggest or mention events that occurred in the past relative to the 'Current time' provided in this prompt. "
            "Process events only in the forward-moving order shown.\n\n"
            "You are M-bot, the personal AI assistant of Elvis Muchiri — a QA Engineer, "
            "AI builder, ambitious guy in Kenya. Brilliant friend energy. Smart, casual, witty. "
            "Real talk — no bullet overload, no corporate speak.\n\n"
            f"{calendar_section}\n\n"
            "Rules: Never fabricate data. Conversational tone. Match his energy. "
            "Vary your openers. If he adds a task without scores, ask for them."
        ),
    }
    messages = [system_message] + [{"role": m["role"], "content": m["content"]} for m in history]
    messages.append({"role": "user", "content": prompt})

    response = await groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=messages,
    )
    return response.choices[0].message.content


# ─── Webhook ───────────────────────────────────────────────────────────────────

@app.post("/webhook")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        update  = await request.json()
        message = update.get("message")
        if not message or "text" not in message:
            return {"status": "No text message to process"}

        chat_id = str(message["chat"]["id"])
        user_id = str(message.get("from", {}).get("id", chat_id))
        text    = message["text"]

        current_chat_id.set(chat_id)

        now_iso = datetime.now(ZoneInfo("Africa/Nairobi")).isoformat()
        background_tasks.add_task(update_user_context, chat_id, last_interaction_at=now_iso)
        background_tasks.add_task(update_user_state, chat_id, last_user_interaction_at=now_iso)

        # 0. Brain Dump pipeline
        if text.startswith(".") or "#" in text:
            hashtags = re.findall(r"#(\w+)", text.lower())
            if text.startswith(".") or hashtags:
                try:
                    brain_dump = BrainDump(
                        raw_content=text,
                        tags=hashtags,
                        metadata={"message_id": message.get("message_id"), "chat_id": chat_id, "user_id": user_id},
                    )
                    await run_in_threadpool(
                        lambda: supabase.table("brain_dumps").insert(brain_dump.model_dump()).execute()
                    )
                    await send_telegram_message(
                        chat_id,
                        f"🧠 Brain Dump captured. Tags: {', '.join(hashtags) if hashtags else 'none'}. Filed, chief.",
                    )
                    return {"status": "success", "type": "brain_dump"}
                except Exception as e:
                    print(f"Brain Dump error: {e}")

        # 1. Commands
        if text.startswith("/"):
            message_id = message.get("message_id")
            if text.startswith("/pomodoro"):
                try:
                    await pomodoro_service.start_session(user_id, chat_id=chat_id)
                    await update_user_state(chat_id, pomodoro_active=True)
                    await send_telegram_message(chat_id, "🚀 Pomodoro started! 25 minutes. Focus.")
                except Exception as e:
                    await send_telegram_message(chat_id, f"❌ Pomodoro failed: {e}", reply_to_message_id=message_id)
                return {"status": "success", "command": "pomodoro"}

            elif text.startswith("/p_stop"):
                await pomodoro_service.stop_session(user_id)
                await update_user_state(chat_id, pomodoro_active=False)
                await send_telegram_message(chat_id, "🛑 Pomodoro stopped.")
                return {"status": "success", "command": "p_stop"}

            elif text.startswith("/p_status"):
                session = await pomodoro_service.get_active_session(user_id)
                if session:
                    end_time  = datetime.fromisoformat(session["end_time"].replace('Z', '+00:00')).astimezone(ZoneInfo("Africa/Nairobi"))
                    remaining = end_time - datetime.now(ZoneInfo("Africa/Nairobi"))
                    minutes   = int(remaining.total_seconds() // 60)
                    await send_telegram_message(chat_id, f"⏳ {minutes} minutes remaining. Keep pushing.")
                else:
                    await send_telegram_message(chat_id, "No active session. Use /pomodoro to start.")
                return {"status": "success", "command": "p_status"}

            elif text.startswith("/mute"):
                muted_until = (datetime.now(ZoneInfo("Africa/Nairobi")) + timedelta(hours=8)).isoformat()
                await update_user_state(chat_id, is_muted=True, muted_until=muted_until)
                await send_telegram_message(chat_id, "🔇 Muted for 8 hours. Only alarms get through.")
                return {"status": "success", "command": "mute"}

            elif text.startswith("/unmute"):
                await update_user_state(chat_id, is_muted=False, muted_until=None)
                await send_telegram_message(chat_id, "🔊 Unmuted. I'm back on watch.")
                return {"status": "success", "command": "unmute"}

            elif text.startswith("/status"):
                now = datetime.now(ZoneInfo("Africa/Nairobi"))
                lines = ["📊 Nudge Registry (last fired):"]
                for ntype, cooldown in NUDGE_COOLDOWNS.items():
                    last = nudge_sent_registry.get(ntype)
                    if last:
                        elapsed = (now - last).total_seconds() / 60
                        next_in = max(0, cooldown.total_seconds() / 60 - elapsed)
                        lines.append(f"• {ntype}: {elapsed:.0f}m ago (next in {next_in:.0f}m)")
                    else:
                        lines.append(f"• {ntype}: never fired this session")
                await send_telegram_message(chat_id, "\n".join(lines))
                return {"status": "success", "command": "status"}

            elif text.startswith("/research"):
                topic = text.replace("/research", "").strip()
                if topic:
                    await run_in_threadpool(
                        lambda: supabase.table("knowledge_graph").insert({"topic": topic}).execute()
                    )
                    await send_telegram_message(chat_id, "Node added. Your intellectual latent space is expanding.")
                else:
                    await send_telegram_message(chat_id, "Usage: /research [topic]")
                return {"status": "success", "command": "research"}

        # 2. Intent classification
        category = "Unknown"
        try:
            classification = await intent_classifier.classify(text)
            category       = classification.get("category")

            if category == "Project Zayn":
                background_tasks.add_task(store_project_zayn, classification)
            elif category in ["Build Mode", "AI Roadmap"]:
                background_tasks.add_task(store_dev_milestone, category, classification)
            elif category == "Task":
                background_tasks.add_task(store_task_or_alarm, chat_id, classification)
            elif category == "Acknowledge":
                background_tasks.add_task(acknowledge_most_recent, chat_id)
            elif category == "Nudge":
                if re.search(r"\?|how|why|what", text, re.IGNORECASE):
                    print(f"🛡️ Intent Guard: Inquiry in nudge request — passing to LLM.")
                else:
                    nudge_msg = await intent_classifier.get_nudge_message(chat_id)
                    text = f"[Nudge Hint: {nudge_msg}] {text}"
        except Exception as e:
            print(f"Classification error: {e}")

        # 3. LLM response
        try:
            full_response = await get_llm_response(text)
            await send_telegram_message(chat_id, full_response)
        except Exception as e:
            print(f"LLM error: {e}")
            full_response = "Sorry bro, having trouble with that right now."
            await send_telegram_message(chat_id, full_response)

        background_tasks.add_task(store_message, chat_id, "user", text)
        background_tasks.add_task(store_message, chat_id, "assistant", full_response)

        return {"status": "success", "category": category}

    except Exception as e:
        print(f"Webhook error: {e}")
        return {"status": "error", "message": str(e)}


# ─── Manual nudge endpoint ─────────────────────────────────────────────────────

@app.post("/nudge")
async def manual_nudge(request: NudgeRequest, background_tasks: BackgroundTasks):
    if not MUCHIRI_CHAT_ID:
        raise HTTPException(status_code=500, detail="MUCHIRI_CHAT_ID not configured")
    background_tasks.add_task(
        send_telegram_message, MUCHIRI_CHAT_ID, request.message, None, "manual_nudge_request"
    )
    return {"status": "nudge_enqueued", "message": request.message}


@app.get("/")
async def root():
    return {"status": "M-bot is running"}
