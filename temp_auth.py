import os.path
import json
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials

# Use the same scopes as your main app
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

def generate_token():
    # Make sure your credentials.json (the one from Google Console) is in this folder!
    flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
    creds = flow.run_local_server(port=0)
    
    # Print it out so you can copy it straight to Supabase
    token_json = creds.to_json()
    print("\n--- COPY EVERYTHING BELOW THIS LINE ---")
    print(token_json)
    print("--- END OF TOKEN ---")
    
    with open('token.json', 'w') as token:
        token.write(token_json)

if __name__ == '__main__':
    generate_token()