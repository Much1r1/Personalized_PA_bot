import os
import json
from typing import Optional, List, Dict, Any
from contextvars import ContextVar
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

# Context variable to store chat_id for LLM streaming response
current_chat_id: ContextVar[int] = ContextVar("current_chat_id")

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

    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4o",
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
    """
    Upsert into 'project_zayn' table.
    """
    try:
        payload = {
            "content": data.get("content"),
            "skincare_done": data.get("skincare_done", False),
            "workout_done": data.get("workout_done", False)
        }
        response = await run_in_threadpool(
            supabase.table("project_zayn").upsert(payload).execute
        )
        return response
    except Exception as e:
        print(f"Error storing Project Zayn data: {e}")
        return None

async def store_dev_milestone(category: str, data: Dict[str, Any]):
    """
    Insert into 'dev_milestones' table.
    """
    try:
        payload = {
            "category": category,
            "content": data.get("content")
        }
        response = await run_in_threadpool(
            supabase.table("dev_milestones").insert(payload).execute
        )
        return response
    except Exception as e:
        print(f"Error storing dev milestone: {e}")
        return None

async def fetch_chat_history(chat_id: int) -> List[Dict[str, str]]:
    """
    Fetch the last 5 messages for the current chat_id from Supabase.
    """
    try:
        response = await run_in_threadpool(
            lambda: supabase.table("messages")
            .select("role", "content")
            .eq("chat_id", chat_id)
            .order("created_at", desc=True)
            .limit(5)
            .execute()
        )
        # The response is in reverse chronological order, LLM needs it in chronological order.
        history = response.data[::-1]
        return history
    except Exception as e:
        print(f"Error fetching chat history: {e}")
        return []

async def store_message(chat_id: int, role: str, content: str):
    """
    Store a message in the 'messages' table.
    """
    try:
        payload = {
            "chat_id": chat_id,
            "role": role,
            "content": content
        }
        await run_in_threadpool(
            supabase.table("messages").insert(payload).execute
        )
    except Exception as e:
        print(f"Error storing message: {e}")

async def send_telegram_draft(chat_id: int, text: str):
    """
    Update the Telegram draft (native streaming animation).
    """
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessageDraft"
        payload = {"chat_id": chat_id, "text": text}
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
    except Exception as e:
        print(f"Error sending Telegram draft: {e}")

async def get_llm_response(prompt: str) -> str:
    """
    Get response from OpenAI with streaming and native Telegram animation.
    """
    chat_id = current_chat_id.get()
    history = await fetch_chat_history(chat_id)

    messages = [{"role": "system", "content": "You are M-bot, a helpful personal AI PA."}]
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": prompt})

    response_stream = await openai_client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        stream=True
    )

    full_response = ""
    last_sent_response = ""

    async for chunk in response_stream:
        if chunk.choices[0].delta.content:
            full_response += chunk.choices[0].delta.content
            # To avoid hitting Telegram rate limits, we could throttle here,
            # but sendMessageDraft is designed for frequent updates.
            # Let's send an update every time we have significant new content or periodically.
            if len(full_response) - len(last_sent_response) > 10:
                await send_telegram_draft(chat_id, full_response)
                last_sent_response = full_response

    # Final message is sent via standard sendMessage to "complete" the draft
    await send_telegram_message(chat_id, full_response)
    return full_response

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

        # Set chat_id context for streaming
        current_chat_id.set(chat_id)

        # 1. Classify Intent
        classification = await classify_intent(text)
        category = classification.get("category")

        # 2. Specialized Logic based on category
        if category == "Project Zayn":
            await store_project_zayn(classification)
        elif category in ["Build Mode", "AI Roadmap"]:
            await store_dev_milestone(category, classification)

        # 3. Generate LLM response (this includes fetching history)
        full_response = await get_llm_response(text)

        # 4. Store user message and assistant response in Supabase
        # As per requirement: "After the LLM responds, store both the user's message and the assistant's response"
        await store_message(chat_id, "user", text)
        await store_message(chat_id, "assistant", full_response)

        return {"status": "success", "category": category}

    except Exception as e:
        # In production, use better logging
        print(f"Error processing webhook: {e}")
        return {"status": "error", "message": str(e)}

async def send_telegram_message(chat_id: int, text: str):
    """
    Send a message back to the Telegram chat.
    """
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        payload = {"chat_id": chat_id, "text": text}
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
    except Exception as e:
        print(f"Error sending Telegram message: {e}")

@app.get("/")
async def root():
    return {"status": "M-bot is running"}
