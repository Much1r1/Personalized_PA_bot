# M-bot 🤖

A personal AI assistant that lives in your Telegram. Built for Elvis Muchiri — QA Engineer, AI builder, and all-round ambitious guy based in Kenya.

M-bot knows your schedule, tracks your health streak, logs your dev progress, and actually talks like a real person. No bullet walls. No corporate speak.

---

## What it does

**Project Z** — Tracks your 72-90 day health and skincare streak. Tell it "I did my skincare today" and it logs it to Supabase automatically.

**Build Mode** — Captures technical notes from your QA work at VettedAI. Brain dumps, ticket notes, code decisions — all stored.

**AI Roadmap** — Tracks your progress on your AI Engineering journey. Log milestones, breakthroughs, and next steps.

**Calendar awareness** — M-bot pulls your Google Calendar events on every message, so it can actually help you plan your day based on what's real.

---

## Tech stack

- **FastAPI** — Backend framework
- **Groq (llama-3.1-8b-instant)** — LLM, free and fast
- **Supabase** — Postgres database for message history and logs
- **Google Calendar API** — Real-time schedule context
- **Telegram Bot API** — The interface
- **Render** — Hosting

---

## Project structure

```
Personalized_PA_bot/
├── main.py              # Core FastAPI app and all bot logic
├── requirements.txt     # Python dependencies
├── .env                 # Environment variables (never commit this)
├── .env.example         # Template for env vars
└── .gitignore
```

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/Much1r1/Personalized_PA_bot.git
cd Personalized_PA_bot
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Set up environment variables

Copy `.env.example` to `.env` and fill in your keys:

```
TELEGRAM_TOKEN=your_telegram_bot_token
GROQ_API_KEY=your_groq_api_key
SUPABASE_URL=your_supabase_project_url
SUPABASE_KEY=your_supabase_anon_key
GOOGLE_CREDENTIALS=base64_encoded_credentials_json
GOOGLE_TOKEN=base64_encoded_token_json
```

To encode your Google credentials as base64:

```bash
python -c "import base64; print(base64.b64encode(open('credentials.json','rb').read()).decode())"
python -c "import base64; print(base64.b64encode(open('token.json','rb').read()).decode())"
```

### 4. Set up Supabase tables

Run this in your Supabase SQL editor:

```sql
CREATE TABLE messages (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE project_zayn (
    id BIGSERIAL PRIMARY KEY,
    content TEXT,
    skincare_done BOOLEAN DEFAULT FALSE,
    workout_done BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE dev_milestones (
    id BIGSERIAL PRIMARY KEY,
    category TEXT NOT NULL,
    content TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### 5. Set up Google Calendar

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Enable the Google Calendar API
3. Create OAuth 2.0 credentials (Desktop App)
4. Download `credentials.json`
5. Run the auth script to generate `token.json`:

```bash
python auth_calendar.py
```

6. Encode both files as base64 and add to your `.env`

### 6. Run locally

```bash
uvicorn main:app --reload
```

### 7. Expose with ngrok (for local testing)

```bash
ngrok http 8000
```

Then register the webhook with Telegram:

```
https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://your-ngrok-url/webhook
```

---

## Deployment (Render)

1. Push to GitHub
2. Create a new Web Service on [Render](https://render.com)
3. Set build command: `pip install -r requirements.txt`
4. Set start command: `uvicorn main:app --host 0.0.0.0 --port 10000`
5. Add all environment variables from your `.env`
6. Deploy and update the Telegram webhook to your Render URL

---

## How M-bot classifies messages

Every incoming message is run through Groq's intent classifier before a response is generated. It maps messages to one of three categories:

- `Project Zayn` → logged to the `project_zayn` table
- `Build Mode` → logged to `dev_milestones` as Vetted-QA
- `AI Roadmap` → logged to `dev_milestones` as AI-Roadmap

The last 15 messages are always fetched from Supabase and passed as context to the LLM so M-bot remembers the conversation.

---

## Environment variables reference

| Variable | Description |
|---|---|
| `TELEGRAM_TOKEN` | From @BotFather on Telegram |
| `GROQ_API_KEY` | From console.groq.com |
| `SUPABASE_URL` | Your Supabase project URL |
| `SUPABASE_KEY` | Your Supabase anon/service key |
| `GOOGLE_CREDENTIALS` | Base64 encoded credentials.json |
| `GOOGLE_TOKEN` | Base64 encoded token.json |

---

Built by [Elvis Muchiri](https://github.com/Much1r1)
