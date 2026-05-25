# M-bot 🤖

A personal AI assistant that lives in your Telegram. Built for Elvis Muchiri — QA Engineer, AI builder, and all-round ambitious guy based in Kenya.

M-bot knows your schedule, tracks your health streak, logs your dev progress, and actually talks like a real person. No bullet walls. No corporate speak. It operates in the **Africa/Nairobi (UTC+3)** timezone.

---

## What it does

**Project Z** — Tracks your 72-90 day health and skincare streak. Tell it "I did my skincare today" and it logs it to Supabase automatically.

**Chief of Staff Mode** — Proactive monitoring of your projects and habits.
- **4 Pillars**: Physical Rigor, Aesthetic Maintenance, Dopamine Discipline, and Intellectual Expansion.
- **Project Velocity**: M-bot pings you if high-priority projects (like the Portfolio) haven't been updated in 12 hours.
- **Habit Integrity**: 'Systems Failure' alerts if your Dopamine Discipline streak is broken.
- **Proactive Nudges**: 8 AM morning briefings, suspicious silence checks after deep work blocks, and general inactivity nudges.
- **Escalation Policy**: 5-minute rule for alarms and critical tasks. If you don't acknowledge, M-bot gets louder.

**Pomodoro System** — Built-in focus timer with `/pomodoro`. It automatically silences non-essential notifications (Pomodoro Lock) while you're in the zone.

**Brain Dump Pipeline** — Intercepts messages starting with `.` or containing `#hashtags`. Automatically extracts tags and files them into your brain dump for later processing.

**Intellect Growth Tracker** — Use `/research [topic]` to log entries into your knowledge graph. M-bot expands your latent space.

**Build Mode** — Captures technical notes from your QA work at VettedAI. Brain dumps, ticket notes, code decisions — all stored.

**AI Roadmap** — Tracks your progress on your AI Engineering journey. Log milestones, breakthroughs, and next steps.

**Calendar Awareness** — M-bot synchronizes with your Google Calendar, respecting "Deep Work" and "AI Engineering" blocks as silent modes.

---

## Tech Stack

- **FastAPI** — Backend framework
- **Gemini 1.5 Flash** — Primary LLM for conversational intelligence and tool calling
- **Groq (llama-3.1-8b-instant)** — Fallback LLM and high-speed intent classification
- **Supabase** — Postgres database for persistent state, message history, and logs
- **Google Calendar API** — Real-time schedule context and sync
- **Telegram Bot API** — The interface
- **Render** — Hosting

---

## Project Structure

```
Personalized_PA_bot/
├── main.py              # Core FastAPI app and proactive nudge loops
├── intent_classifier.py # Groq-powered intent classification logic
├── pomodoro_service.py  # Pomodoro session management
├── sync_service.py      # Background Google Calendar sync service
├── telegram_client.py   # Robust Async Telegram client with rate-limiting
├── requirements.txt     # Python dependencies
├── schema.sql           # Database schema
└── migration_*.sql      # Schema evolution scripts
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
MUCHIRI_CHAT_ID=your_telegram_chat_id
GROQ_API_KEY=your_groq_api_key
GEMINI_API_KEY=your_gemini_api_key
SUPABASE_URL=your_supabase_project_url
SUPABASE_KEY=your_supabase_service_role_key
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
# Optional/Legacy
GOOGLE_CREDENTIALS=base64_encoded_credentials_json
GOOGLE_TOKEN=base64_encoded_token_json
```

### 4. Set up Supabase

M-bot uses a complex schema to track state. Run `schema.sql` and all `migration_*.sql` files in your Supabase SQL editor to set up the following tables:
- `user_state` & `user_context`: Executive state management
- `messages`: Conversation history
- `user_tasks` & `user_alarms`: Nudge engine targets
- `pomodoro_sessions`: Focus tracking
- `brain_dumps`: Knowledge capture
- `habits` & `goals`: Chief of Staff tracking
- `knowledge_graph`: Intellectual expansion
- `system_config`: Stores refreshed Google OAuth tokens

### 5. Google Calendar Sync

M-bot expects Google OAuth tokens to be stored in the `system_config` table under the key `google_token`.
1. Use `sync_service.py` to handle the initial OAuth flow via `/auth/google`.
2. Once authorized, the tokens are persisted and automatically refreshed by the main application.

---

## Telegram Commands

- `/pomodoro` — Start a 25-minute work session.
- `/p_stop` — Cancel the active session.
- `/p_status` — Check remaining time.
- `/mute` — Silence proactive nudges for 8 hours (Alarms still pass through).
- `/research [topic]` — Add a node to your knowledge graph.

---

## Intent Classification

Every message is classified into categories to trigger specific logic:
- `Project Zayn` → Health/Skincare logs.
- `Build Mode` → Engineering milestones.
- `AI Roadmap` → AI Engineering progress.
- `Kijiji` → Side hustle logs.
- `Task` → Creates a pending task or alarm.
- `Acknowledge` → Marks the most recent alarm/task as completed.
- `Nudge` → Manually requests the next priority task.

---

Built by [Elvis Muchiri](https://github.com/Much1r1)
