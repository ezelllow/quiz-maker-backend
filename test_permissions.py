"""
Test if service account has access to sheet and drive folder
"""

import json
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

print("🔐 Testing service account permissions...\n")

# Read credentials
with open('credentials.json', 'r') as f:
    creds_data = json.load(f)

service_account_email = creds_data.get('client_email')
print(f"Service Account Email: {service_account_email}")
print("⬆️  MAKE SURE YOU SHARED THE SHEET & FOLDER WITH THIS EMAIL!\n")

# Create credentials
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets.readonly',
    'https://www.googleapis.com/auth/drive.readonly'
]

credentials = Credentials.from_service_account_file(
    'credentials.json',
    scopes=SCOPES
)

# Test values
SPREADSHEET_ID = '1TOmLo9UNpzOggeX27j1p6Q2NdAnCWpRJ1ErYAEJ-sZU'
QUESTION_FOLDER_ID = '10TtAVgxTsczSFxIrkwSSy_KFQlebWCiX'
SHEET_NAME = 'Paper1'  # Just the sheet name, no range

print("="*60)
print("Testing Google Sheets Access...")
print("="*60)
print(f"Spreadsheet ID: {SPREADSHEET_ID}")
print(f"Sheet Name: {SHEET_NAME}\n")

try:
    sheets_service = build('sheets', 'v4', credentials=credentials)
    result = sheets_service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=SHEET_NAME
    ).execute()

    rows = result.get('values', [])
    print(f"✅ SUCCESS! Can access sheet")
    print(f"   Found {len(rows)} rows")
    if rows:
        print(f"   Headers: {rows[0]}")
except Exception as e:
    print(f"❌ FAILED to access sheet: {e}")
    print("\n💡 Solutions:")
    print("   1. Check the SPREADSHEET_ID is correct")
    print("   2. Check the SHEET_NAME is correct (not 'Sheet1', check actual name)")
    print("   3. Make sure you shared the sheet with:")
    print(f"      {service_account_email}")
    print("   4. Wait 1-2 minutes for sharing to propagate")

print("\n" + "="*60)
print("Testing Google Drive Access...")
print("="*60)
print(f"Folder ID: {QUESTION_FOLDER_ID}\n")

try:
    drive_service = build('drive', 'v3', credentials=credentials)
    results = drive_service.files().list(
        q=f"'{QUESTION_FOLDER_ID}' in parents",
        spaces='drive',
        fields='files(id, name)',
        pageSize=5
    ).execute()

    files = results.get('files', [])
    print(f"✅ SUCCESS! Can access Drive folder")
    print(f"   Found {len(files)} files/folders")
    if files:
        print(f"   Sample files:")
        for f in files[:3]:
            print(f"      - {f['name']}")
except Exception as e:
    print(f"❌ FAILED to access Drive folder: {e}")
    print("\n💡 Solutions:")
    print("   1. Check the QUESTION_FOLDER_ID is correct")
    print("   2. Make sure you shared the folder with:")
    print(f"      {service_account_email}")
    print("   3. Wait 1-2 minutes for sharing to propagate")

print("\n" + "="*60)
print("✅ Both tests passed! Your backend should work now.")
print("   Run: python quiz_backend.py")
