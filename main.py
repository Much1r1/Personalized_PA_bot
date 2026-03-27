import os
import json
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, Request, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from openai import AsyncOpenAI
from supabase import create_client, Client
import httpx
from dotenv import load_dotenv

load_dotenv()

# Environment Variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Initialize Clients
app = FastAPI(title="M-bot")
openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

async def classify_intent(text: str) -> Dict[str, Any]:
    """
    Classify the user intent using OpenAI.
    Categories: 'Project Zayn', 'Build Mode', 'AI Roadmap'
    """
    system_prompt = """
    You are an intent classifier for M-bot, a personal AI PA.
    Classify the user's message into one of these three categories:
    1. 'Project Zayn': Health, skincare, or workout logs.
    2. 'Build Mode': Vetted-QA tasks or technical code notes.
    3. 'AI Roadmap': Tracking progress on AI Engineering milestones.

    For 'Project Zayn', also detect if they mentioned completing skincare or workout.
    Return a JSON object:
    {
        "category": "Project Zayn" | "Build Mode" | "AI Roadmap",
        "skincare_done": boolean (only for Project Zayn),
        "workout_done": boolean (only for Project Zayn),
        "content": "The original or cleaned up message content"
    }
    """

    response = await openai_client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text}
        ],
        response_format={"type": "json_object"}
    )

    return json.loads(response.choices[0].message.content)

async def store_project_zayn(data: Dict[str, Any]):
    """
    Upsert into 'project_zayn' table.
    """
    # Assuming the table structure is:
    # id, created_at, content, skincare_done, workout_done
    payload = {
        "content": data.get("content"),
        "skincare_done": data.get("skincare_done", False),
        "workout_done": data.get("workout_done", False)
    }
    # Requirement: Upsert to project_zayn
    # supabase-py execute() is not inherently async unless using an async provider or if we wrap it.
    # However, the requirement asks for the entire app to be async.
    # For supabase-py, if we want true async, we should use the postgrest-py async client or
    # run_in_threadpool if it's blocking.
    # But usually supabase-py's .execute() is blocking.
    # To keep the app performing well and strictly follow "entire app must be async",
    # we should ideally use an async-compatible way to call it.
    # Let's use run_in_threadpool to make it async-friendly
    response = await run_in_threadpool(
        supabase.table("project_zayn").upsert(payload).execute
    )
    return response

async def store_dev_milestone(category: str, data: Dict[str, Any]):
    """
    Insert into 'dev_milestones' table.
    """
    payload = {
        "category": category,
        "content": data.get("content")
    }
    response = await run_in_threadpool(
        supabase.table("dev_milestones").insert(payload).execute
    )
    return response

@app.post("/webhook")
async def telegram_webhook(request: Request):
    """
    Handle incoming Telegram updates.
    """
    try:
        update = await request.json()
        message = update.get("message")
        if not message or "text" not in message:
            return {"status": "No text message to process"}

        chat_id = message["chat"]["id"]
        text = message["text"]

        # 1. Classify Intent
        classification = await classify_intent(text)
        category = classification.get("category")

        # 2. Database Integration
        if category == "Project Zayn":
            await store_project_zayn(classification)
        elif category in ["Build Mode", "AI Roadmap"]:
            await store_dev_milestone(category, classification)
        else:
            # Handle unknown category if necessary
            pass

        # 3. Confirmation (Optional but good UX)
        await send_telegram_message(chat_id, f"Logged to {category}: {classification.get('content')}")

        return {"status": "success", "category": category}

    except Exception as e:
        # In production, use better logging
        print(f"Error processing webhook: {e}")
        return {"status": "error", "message": str(e)}

async def send_telegram_message(chat_id: int, text: str):
    """
    Send a message back to the Telegram chat.
    """
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    async with httpx.AsyncClient() as client:
        await client.post(url, json=payload)

@app.get("/")
async def root():
    return {"status": "M-bot is running"}
