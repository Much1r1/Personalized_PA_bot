import os
import asyncio
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
import time
from supabase import create_client, Client, ClientOptions
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

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_KEY,
    options=ClientOptions(postgrest_client_timeout=5, storage_client_timeout=5)
)
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

            start_time = time.perf_counter()
            try:
                # Telemetry: Log connection duration for Supabase query
                await run_in_threadpool(
                    lambda: supabase.table("user_schedules").select("id").limit(1).execute()
                )
                duration = time.perf_counter() - start_time
                print(f"Supabase telemetry: Connection successful. Duration: {duration:.4f}s")

                # 1. Fetch user credentials from Supabase (Placeholder)
                # 2. Initialize Google Calendar service
                # 3. Fetch events
                # 4. Upsert into 'user_schedules'

            except Exception as db_err:
                duration = time.perf_counter() - start_time
                error_type = type(db_err).__name__
                print(f"Supabase telemetry: Failure after {duration:.4f}s. Error: {error_type}")

                # Specifically log TimeoutError or ConnectionError
                err_msg = str(db_err).lower()
                if "timeout" in err_msg or "connection" in err_msg:
                    print(f"CRITICAL ERROR: Supabase {error_type} - {db_err}")

                # Re-raise to be caught by the outer loop for retry
                raise db_err

            await asyncio.sleep(600)  # Sync every 10 minutes
        except Exception as e:
            print(f"Sync loop error: {e}")
            await asyncio.sleep(60)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(sync_google_calendar())

@app.get("/health")
async def health_check():
    """Verifies connection to Supabase and returns status."""
    try:
        # Simple query to check connectivity
        await run_in_threadpool(
            lambda: supabase.table("user_schedules").select("id").limit(1).execute()
        )
        return {"status": "SUCCESS", "message": "Connection to Supabase is active."}
    except Exception as e:
        return {"status": "FAILURE", "error": str(e)}

@app.get("/")
async def root():
    return {"status": "Sync Service is running"}
