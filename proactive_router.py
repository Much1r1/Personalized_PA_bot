import os
from fastapi import APIRouter, Depends, HTTPException, Header, BackgroundTasks
from typing import Optional
from supabase import create_client, Client
from telegram_client import TelegramClient
from proactive_engine import ProactiveEngine

router = APIRouter(prefix="/api/v1/proactive", tags=["proactive"])

# Environment
INTERNAL_CRON_SECRET = os.getenv("INTERNAL_CRON_SECRET")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
MUCHIRI_CHAT_ID = os.getenv("MUCHIRI_CHAT_ID")

# Clients
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
telegram = TelegramClient(TELEGRAM_TOKEN)
engine = ProactiveEngine(supabase, telegram, GEMINI_API_KEY)

async def verify_cron_secret(authorization: Optional[str] = Header(None)):
    if not INTERNAL_CRON_SECRET:
        # If not set, we might be in dev, but for safety in "production-ready" code:
        raise HTTPException(status_code=500, detail="INTERNAL_CRON_SECRET not configured")

    if authorization != f"Bearer {INTERNAL_CRON_SECRET}":
        raise HTTPException(status_code=403, detail="Unauthorized: Invalid Cron Secret")

@router.post("/morning-brief", dependencies=[Depends(verify_cron_secret)])
async def trigger_morning_brief(background_tasks: BackgroundTasks):
    if not MUCHIRI_CHAT_ID:
        raise HTTPException(status_code=500, detail="MUCHIRI_CHAT_ID not configured")

    background_tasks.add_task(engine.generate_morning_brief, MUCHIRI_CHAT_ID)
    return {"status": "accepted", "message": "Morning brief generation enqueued"}

@router.post("/evaluate-nudges", dependencies=[Depends(verify_cron_secret)])
async def trigger_nudge_evaluation(background_tasks: BackgroundTasks):
    background_tasks.add_task(engine.evaluate_nudges)
    return {"status": "accepted", "message": "Nudge evaluation enqueued"}
