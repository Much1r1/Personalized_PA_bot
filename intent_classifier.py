import json
from typing import Dict, Any, List
from groq import AsyncGroq
from supabase import Client
from fastapi.concurrency import run_in_threadpool

class IntentClassifier:
    def __init__(self, groq_client: AsyncGroq, supabase_client: Client):
        self.groq = groq_client
        self.supabase = supabase_client

    async def classify(self, text: str) -> Dict[str, Any]:
        """
        Classify the user intent using Groq.
        Categories: 'Project Zayn', 'Build Mode', 'AI Roadmap', 'Task', 'Acknowledge', 'Nudge'
        """
        system_prompt = """
        You are an intent classifier for M-bot, a personal AI PA.
        Classify the user's message into one of these six categories:
        1. 'Project Zayn': Health, skincare, or workout logs.
        2. 'Build Mode': Vetted-QA tasks or technical code notes.
        3. 'AI Roadmap': Tracking progress on AI Engineering milestones.
        4. 'Task': User wants to add a new task or alarm.
        5. 'Acknowledge': User is acknowledging an alert, alarm or task (e.g., 'done', 'ack', 'got it').
        6. 'Nudge': User is asking for a nudge, checking what they should do next, or needs motivation to get back to work.

        For 'Project Zayn', also detect if they mentioned completing skincare or workout.
        For 'Task', extract the 'title', 'due_date' (if any, in ISO format), and 'task_type' (task or alarm).
        Also for 'Task', extract 'effort_score' (1-10) and 'impact_score' (1-10) if mentioned.
        Return ONLY a raw JSON object with no markdown or backticks:
        {
            "category": "Project Zayn" | "Build Mode" | "AI Roadmap" | "Task" | "Acknowledge" | "Nudge",
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
            response = await self.groq.chat.completions.create(
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

    async def get_nudge_message(self, chat_id: int) -> str:
        """
        Fetches the oldest pending task and formats a 'gentle but firm' nudge message.
        """
        try:
            response = await run_in_threadpool(
                lambda: self.supabase.table("user_tasks")
                .select("*")
                .eq("chat_id", chat_id)
                .eq("status", "pending")
                .order("created_at", desc=False)
                .limit(1)
                .execute()
            )

            if not response.data:
                return "You're all clear, chief. No pending tasks on the radar. Maybe it's time to level up and add something new?"

            task = response.data[0]
            title = task["title"]

            system_prompt = (
                "You are M-bot, the personal AI assistant of Elvis Muchiri. "
                "Your persona: Smart, casual, Kenyan vibe, occasionally witty, never robotic. "
                "You talk like a real friend—no bullet points, no fake enthusiasm. "
                "Format a 'gentle but firm' nudge for Elvis about a pending task. "
                "Keep it to 1-2 sentences. No markdown. "
            )

            user_prompt = f"The task is: '{title}'. Give him a nudge, man."

            nudge_res = await self.groq.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]
            )
            return nudge_res.choices[0].message.content.strip()

        except Exception as e:
            print(f"Error generating nudge message: {e}")
            return "Yo Elvis, you've still got some work pending. Let's not let it pile up, bro."
