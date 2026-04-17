import os
import asyncio
from fastapi import FastAPI, Request
from supabase import create_client, Client
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from datetime import datetime, timezone

load_dotenv()

# Environment Variables
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GOOGLE_CLIENT_CONFIG = {
    "web": {
        "client_id": os.getenv("GOOGLE_CLIENT_ID"),
        "project_id": os.getenv("GOOGLE_PROJECT_ID"),
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
        "redirect_uris": [os.getenv("GOOGLE_REDIRECT_URI")]
    }
}

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
app = FastAPI(title="M-bot Sync Service")

@app.get("/auth/google")
async def google_auth():
    """Starts the Google OAuth flow."""
    flow = Flow.from_client_config(
        GOOGLE_CLIENT_CONFIG,
        scopes=['https://www.googleapis.com/auth/calendar.readonly']
    )
    flow.redirect_uri = GOOGLE_CLIENT_CONFIG["web"]["redirect_uris"][0]
    auth_url, _ = flow.authorization_url(prompt='consent')
    return {"auth_url": auth_url}

@app.get("/auth/callback")
async def google_callback(code: str):
    """Handles the Google OAuth callback."""
    flow = Flow.from_client_config(
        GOOGLE_CLIENT_CONFIG,
        scopes=['https://www.googleapis.com/auth/calendar.readonly']
    )
    flow.redirect_uri = GOOGLE_CLIENT_CONFIG["web"]["redirect_uris"][0]
    flow.fetch_token(code=code)

    credentials = flow.credentials
    # In a real app, store these credentials in Supabase associated with the user
    # For boilerplate, we'll just return them
    return {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes
    }

async def sync_google_calendar():
    """Polls Google Calendar and upserts into Supabase user_schedules."""
    while True:
        try:
            print(f"[{datetime.now()}] Starting Google Calendar Sync...")

            # 1. Fetch user credentials from Supabase (Placeholder)
            # 2. Initialize Google Calendar service
            # 3. Fetch events
            # 4. Upsert into 'user_schedules'

            # Example Upsert:
            # payload = {
            #     "event_id": event['id'],
            #     "summary": event['summary'],
            #     "start_time": event['start'].get('dateTime'),
            #     "end_time": event['end'].get('dateTime')
            # }
            # supabase.table("user_schedules").upsert(payload).execute()

            await asyncio.sleep(600)  # Sync every 10 minutes
        except Exception as e:
            print(f"Sync error: {e}")
            await asyncio.sleep(60)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(sync_google_calendar())

@app.get("/")
async def root():
    return {"status": "Sync Service is running"}
