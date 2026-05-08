"""
List all sheet names in the spreadsheet
"""

import json
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# Read credentials
with open('credentials.json', 'r') as f:
    creds_data = json.load(f)

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets.readonly',
    'https://www.googleapis.com/auth/drive.readonly'
]

credentials = Credentials.from_service_account_file(
    'credentials.json',
    scopes=SCOPES
)

SPREADSHEET_ID = '1TOmLo9UNpzOggeX27j1p6Q2NdAnCWpRJ1ErYAEJ-sZU'

try:
    sheets_service = build('sheets', 'v4', credentials=credentials)
    spreadsheet = sheets_service.spreadsheets().get(
        spreadsheetId=SPREADSHEET_ID,
        fields='sheets.properties'
    ).execute()

    print("📋 Available Sheets in Spreadsheet:\n")
    for idx, sheet in enumerate(spreadsheet['sheets'], 1):
        sheet_name = sheet['properties']['title']
        sheet_id = sheet['properties']['sheetId']
        print(f"{idx}. Sheet Name: '{sheet_name}'")
        print(f"   Sheet ID: {sheet_id}")
        print(f"   Use this in code: '{sheet_name}'!A1:Z1000\n")

except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
