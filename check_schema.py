import os
import json
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

URL = os.getenv("SUPABASE_URL")
KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(URL, KEY)

def check_table(name):
    try:
        res = supabase.table(name).select("*").limit(1).execute()
        print(f"Table {name} exists. Sample: {res.data}")
    except Exception as e:
        print(f"Table {name} does not exist or error: {e}")

check_table("user_context")
check_table("user_state")
check_table("calendar_items")
check_table("user_schedules")
check_table("sent_reminders")
