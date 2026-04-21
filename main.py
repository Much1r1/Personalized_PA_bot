import os
import base64
import json
from typing import Optional, List, Dict, Any, Callable
from contextvars import ContextVar
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from groq import AsyncGroq
import google.generativeai as genai
from supabase import create_client, Client
import httpx
import asyncio
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from datetime import datetime, timezone, timedelta
from google.auth.transport.requests import Request as GoogleRequest

load_dotenv()

# Environment Variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Initialize Clients
app = FastAPI(title="M-bot")
groq_client = AsyncGroq(api_key=GROQ_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

# Context variable to store chat_id
current_chat_id: ContextVar[int] = ContextVar("current_chat_id")


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

        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        events_result = service.events().list(
            calendarId='primary',
            timeMin=now,
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


def get_schedule(max_results: int = 5) -> str:
    """Retrieves the user's upcoming calendar events."""
    return get_calendar_events(max_results)


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

    async def run(self):
        """
        Background task that queries user_tasks and user_alarms every minute.
        Handles proactive messaging and the Escalation Policy.
        """
        while True:
            try:
                now = datetime.now(timezone.utc)
                escalation_time = now - timedelta(minutes=5)

                # 1. Handle Alarms
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
                        await send_telegram_message(chat_id, f"🚨 ALARM: {alarm['message']}")
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
                            "Your 'Probability of Outage' is increasing. Action required."
                        )
                        await run_in_threadpool(
                            lambda: self.supabase.table("user_alarms")
                            .update({"triggered_at": now.isoformat()})
                            .eq("id", alarm["id"])
                            .execute()
                        )
                except Exception as e:
                    print(f"Nudge Engine Alarm Escalation Error: {e}")

                # 3. Handle Tasks due for nudge
                try:
                    tasks_resp = await run_in_threadpool(
                        lambda: self.supabase.table("user_tasks")
                        .select("*")
                        .eq("status", "pending")
                        .lte("due_date", now.isoformat())
                        .is_("triggered_at", "null")
                        .execute()
                    )
                    for task in tasks_resp.data:
                        chat_id = task["chat_id"]
                        await send_telegram_message(chat_id, f"🕒 TASK DUE: {task['title']}")
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
                        await send_telegram_message(
                            chat_id,
                            f"⚠️ ESCALATION: Task '{task['title']}' is still pending! "
                            "This is impacting your 'Probability of Outage'. Bro, get it done."
                        )
                        await run_in_threadpool(
                            lambda: self.supabase.table("user_tasks")
                            .update({"triggered_at": now.isoformat()})
                            .eq("id", task["id"])
                            .execute()
                        )
                except Exception as e:
                    print(f"Nudge Engine Task Escalation Error: {e}")

                # 4. System Status Report (8:00 AM)
                if now.hour == 8 and now.minute == 0:
                    active_chats = await run_in_threadpool(
                        lambda: self.supabase.table("messages")
                        .select("chat_id")
                        .execute()
                    )
                    chat_ids = list(set([c["chat_id"] for c in active_chats.data]))

                    for chat_id in chat_ids:
                        schedule_str = await run_in_threadpool(get_calendar_events)
                        tasks_resp = await run_in_threadpool(
                            lambda: self.supabase.table("user_tasks")
                            .select("*")
                            .eq("chat_id", chat_id)
                            .eq("status", "pending")
                            .order("impact_score", desc=True)
                            .limit(5)
                            .execute()
                        )
                        task_lines = [f"- {t['title']} (Impact: {t['impact_score']})" for t in tasks_resp.data]
                        tasks_str = "\n".join(task_lines) if task_lines else "No pending tasks."

                        report = (
                            "📋 SYSTEM STATUS REPORT (8:00 AM)\n\n"
                            "🗓 TODAY'S SCHEDULE:\n"
                            f"{schedule_str}\n\n"
                            "🚀 HIGH-PRIORITY FOCUS:\n"
                            f"{tasks_str}\n\n"
                            "Don't let the technical debt pile up. Let's get it today!"
                        )
                        await send_telegram_message(chat_id, report)

            except Exception as e:
                print(f"Nudge Engine Error: {e}")

            await asyncio.sleep(60)


nudge_engine_service = NudgeEngine(supabase)


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(nudge_engine_service.run())


async def classify_intent(text: str) -> Dict[str, Any]:
    """
    Classify the user intent using Groq.
    Categories: 'Project Zayn', 'Build Mode', 'AI Roadmap', 'Task', 'Acknowledge'
    """
    system_prompt = """
    You are an intent classifier for M-bot, a personal AI PA.
    Classify the user's message into one of these five categories:
    1. 'Project Zayn': Health, skincare, or workout logs.
    2. 'Build Mode': Vetted-QA tasks or technical code notes.
    3. 'AI Roadmap': Tracking progress on AI Engineering milestones.
    4. 'Task': User wants to add a new task or alarm.
    5. 'Acknowledge': User is acknowledging an alert, alarm or task (e.g., 'done', 'ack', 'got it').

    For 'Project Zayn', also detect if they mentioned completing skincare or workout.
    For 'Task', extract the 'title', 'due_date' (if any, in ISO format), and 'task_type' (task or alarm).
    Also for 'Task', extract 'effort_score' (1-10) and 'impact_score' (1-10) if mentioned.
    Return ONLY a raw JSON object with no markdown or backticks:
    {
        "category": "Project Zayn" | "Build Mode" | "AI Roadmap" | "Task" | "Acknowledge",
        "skincare_done": boolean (only for Project Zayn),
        "workout_done": boolean (only for Project Zayn),
        "title": "string" (only for Task),
        "due_date": "ISO string" (only for Task),
        "task_type": "task" | "alarm" (only for Task),
        "effort_score": integer (only for Task),
        "impact_score": integer (only for Task),
        "content": "The original or cleaned up message content"
    }
    """

    try:
        response = await groq_client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text}
            ],
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"Error classifying intent: {e}")
        return {"category": "Unknown", "content": text}


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


async def fetch_chat_history(chat_id: int) -> List[Dict[str, str]]:
    """Fetch the last 15 messages for the current chat_id from Supabase."""
    try:
        response = await run_in_threadpool(
            lambda: supabase.table("messages")
            .select("role", "content")
            .eq("chat_id", chat_id)
            .order("created_at", desc=True)
            .limit(15)
            .execute()
        )
        return response.data[::-1]  # chronological order
    except Exception as e:
        print(f"Error fetching chat history: {e}")
        return []


async def store_message(chat_id: int, role: str, content: str):
    """Store a message in the 'messages' table."""
    try:
        payload = {"chat_id": chat_id, "role": role, "content": content}
        await run_in_threadpool(
            supabase.table("messages").insert(payload).execute
        )
    except Exception as e:
        print(f"Error storing message: {e}")


async def send_telegram_message(chat_id: int, text: str):
    """Send a message back to the Telegram chat."""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
    except Exception as e:
        print(f"Error sending Telegram message: {e}")


async def get_llm_response(prompt: str) -> str:
    """Get response from Gemini with tool calling support, fallback to Groq."""
    chat_id = current_chat_id.get()
    history = await fetch_chat_history(chat_id)

    if GEMINI_API_KEY:
        try:
            system_instruction = (
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
                "You track three areas of his life:\n"
                "- Project Zayn: his 72-90 day health, skincare and workout streak\n"
                "- Build Mode: his QA work at VettedAI and any technical notes\n"
                "- AI Roadmap: his journey to becoming an AI Engineer\n"
                "\n\n"
                "You have access to tools to fetch his schedule.\n"
                "\n\n"
                "Rules:\n"
                "- Never make up data or stats you don't have. If you don't know, say so plainly.\n"
                "- Keep responses conversational and concise — no essays unless he asks.\n"
                "- No markdown formatting like **bold** or bullet walls. Just clean natural text.\n"
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
            "You track three areas of his life:\n"
            "- Project Zayn: his 72-90 day health, skincare and workout streak\n"
            "- Build Mode: his QA work at VettedAI and any technical notes\n"
            "- AI Roadmap: his journey to becoming an AI Engineer\n"
            "\n\n"
            f"{calendar_section}\n"
            "\n\n"
            "Rules:\n"
            "- Never make up data or stats you don't have. If you don't know, say so plainly.\n"
            "- Keep responses conversational and concise — no essays unless he asks.\n"
            "- No markdown formatting like **bold** or bullet walls. Just clean natural text.\n"
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


async def acknowledge_most_recent(chat_id: int):
    """Mark the most recently triggered alarm or task as acknowledged."""
    try:
        now = datetime.now(timezone.utc).isoformat()
        await run_in_threadpool(
            lambda: supabase.table("user_alarms")
            .update({"status": "acknowledged", "acknowledged_at": now})
            .eq("chat_id", chat_id)
            .eq("status", "triggered")
            .execute()
        )
        await run_in_threadpool(
            lambda: supabase.table("user_tasks")
            .update({"status": "completed", "acknowledged_at": now})
            .eq("chat_id", chat_id)
            .neq("triggered_at", None)
            .execute()
        )
    except Exception as e:
        print(f"Error acknowledging: {e}")


async def store_task_or_alarm(chat_id: int, data: Dict[str, Any]):
    """Insert into 'user_tasks' or 'user_alarms' table."""
    try:
        task_type = data.get("task_type", "task")
        if task_type == "alarm":
            payload = {
                "chat_id": chat_id,
                "alarm_time": data.get("due_date", datetime.now(timezone.utc).isoformat()),
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

        chat_id = message["chat"]["id"]
        text = message["text"]

        current_chat_id.set(chat_id)

        # 1. Classify Intent
        try:
            classification = await classify_intent(text)
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


@app.get("/")
async def root():
    return {"status": "M-bot is running"}