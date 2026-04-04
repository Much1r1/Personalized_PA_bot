import os
import base64
import json
from typing import Optional, List, Dict, Any
from contextvars import ContextVar
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from groq import AsyncGroq
from supabase import create_client, Client
import httpx
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from datetime import datetime, timezone

load_dotenv()

# Environment Variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# Initialize Clients
app = FastAPI(title="M-bot")
groq_client = AsyncGroq(api_key=GROQ_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Context variable to store chat_id
current_chat_id: ContextVar[int] = ContextVar("current_chat_id")


async def classify_intent(text: str) -> Dict[str, Any]:
    """
    Classify the user intent using Groq.
    Categories: 'Project Zayn', 'Build Mode', 'AI Roadmap'
    """
    system_prompt = """
    You are an intent classifier for M-bot, a personal AI PA.
    Classify the user's message into one of these three categories:
    1. 'Project Zayn': Health, skincare, or workout logs.
    2. 'Build Mode': Vetted-QA tasks or technical code notes.
    3. 'AI Roadmap': Tracking progress on AI Engineering milestones.

    For 'Project Zayn', also detect if they mentioned completing skincare or workout.
    Return ONLY a raw JSON object with no markdown or backticks:
    {
        "category": "Project Zayn" | "Build Mode" | "AI Roadmap",
        "skincare_done": boolean (only for Project Zayn),
        "workout_done": boolean (only for Project Zayn),
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
    """Get response from Groq llama-3.1-8b-instant with chat history."""
    chat_id = current_chat_id.get()
    history = await fetch_chat_history(chat_id)
    calendar_context = await run_in_threadpool(get_calendar_events)
    print(f"DEBUG CALENDAR: {calendar_context}")

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
        f"His upcoming calendar events:\n{calendar_context}\n"
        "\n\n"
        "Rules:\n"
        "- Never make up data or stats you don't have. If you don't know, say so plainly.\n"
        "- Keep responses conversational and concise — no essays unless he asks.\n"
        "- No markdown formatting like **bold** or bullet walls. Just clean natural text.\n"
        "- Don't start every message the same way. Vary your openers.\n"
        "- If he seems stressed or tired, acknowledge it like a friend would."
    )
   }

    # Build messages: system + history + new user prompt
    messages = [system_message]
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": prompt})

    response = await groq_client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=messages
    )

    full_response = response.choices[0].message.content
    await send_telegram_message(chat_id, full_response)
    return full_response

def get_calendar_events(max_results: int = 3) -> str:
    try:
        # Load credentials from environment variables
        token_b64 = os.getenv("GOOGLE_TOKEN")
        
        if not token_b64:
            return "Calendar not configured."
        
        token_json = base64.b64decode(token_b64).decode()
        token_data = json.loads(token_json)
        
        # Load client secrets for refresh
        creds_b64 = os.getenv("GOOGLE_CREDENTIALS")
        creds_json = base64.b64decode(creds_b64).decode()
        creds_data = json.loads(creds_json)
        
        client_config = creds_data.get("installed") or creds_data.get("web")
        
        creds = Credentials(
            token=token_data.get("token"),
            refresh_token=token_data.get("refresh_token"),
            token_uri=token_data.get("token_uri"),
            client_id=client_config.get("client_id"),
            client_secret=client_config.get("client_secret"),
            scopes=token_data.get("scopes")
        )

        service = build('calendar', 'v3', credentials=creds)

        now = datetime.now(timezone.utc).isoformat()

        events_result = service.events().list(
            calendarId='primary',
            timeMin=now,
            maxResults=max_results,
            singleEvents=True,
            orderBy='startTime'
        ).execute()

        events = events_result.get('items', [])

        if not events:
            return "No upcoming events."

        lines = []
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            lines.append(f"- {start}: {event['summary']}")

        return "\n".join(lines)

    except Exception as e:
        print(f"Calendar error: {e}")
        return "Couldn't fetch calendar events."

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
        classification = await classify_intent(text)
        category = classification.get("category")

        # 2. Specialized storage based on category
        if category == "Project Zayn":
            await store_project_zayn(classification)
        elif category in ["Build Mode", "AI Roadmap"]:
            await store_dev_milestone(category, classification)

        # 3. Generate LLM response
        full_response = await get_llm_response(text)

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