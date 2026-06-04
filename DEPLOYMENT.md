# Render Deployment Instructions

To deploy the M-bot Proactive Assistant on Render:

## 1. Prerequisites
- A Supabase project.
- Google Cloud Console project with Calendar API enabled and OAuth2 credentials.
- Telegram Bot Token from BotFather.
- Gemini API Key and Groq API Key.

## 2. Database Setup
Run the following migration scripts in your Supabase SQL Editor:
1. `schema.sql` (if not already applied)
2. `migration_proactive_assistant.sql`

## 3. Render Setup
1. Create a new **Web Service** on Render.
2. Connect your GitHub repository.
3. Select **Python** as the runtime.
4. Build Command: `pip install -r requirements.txt`
5. Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`

## 4. Environment Variables
Add the following variables in the Render Dashboard:

| Variable | Description |
| --- | --- |
| `TELEGRAM_TOKEN` | Your Telegram bot token. |
| `SUPABASE_URL` | Your Supabase project URL. |
| `SUPABASE_KEY` | Your Supabase service role key (to bypass RLS). |
| `GROQ_API_KEY` | Your Groq API key. |
| `GEMINI_API_KEY` | Your Gemini API key. |
| `MUCHIRI_CHAT_ID` | Your Telegram chat ID (to receive proactive alerts). |
| `GOOGLE_CLIENT_ID` | Google OAuth client ID. |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret. |
| `GOOGLE_REDIRECT_URI` | The callback URL (e.g., `https://your-app.onrender.com/auth/callback`). |

## 5. Sync Service
For reliable background sync, you can deploy `sync_service.py` as a separate **Background Worker** on Render or keep it integrated within the Web Service if using `apscheduler` for polling (as implemented in `main.py`).

## 6. Verification
- Use `/webhook` to confirm the bot is receiving messages.
- Wait for the scheduled tasks (9 AM, 10 PM) or create a calendar event starting in 15 mins to test proactive notifications.
