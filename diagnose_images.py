"""
Diagnostic script to check:
1. What images are actually in QUESTION_FOLDER_ID
2. What UIDs are being extracted from the sheet
3. Whether filenames match
"""

import os
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SPREADSHEET_ID = '1TOmLo9UNpzOggeX27j1p6Q2NdAnCWpRJ1ErYAEJ-sZU'
QUESTION_FOLDER_ID = '10TtAVgxTsczSFxIrkwSSy_KFQlebWCiX'
SHEET_NAME = 'Paper1'

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets.readonly',
    'https://www.googleapis.com/auth/drive.readonly'
]

def get_credentials():
    if os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'):
        return Credentials.from_service_account_file(
            os.environ.get('GOOGLE_APPLICATION_CREDENTIALS'),
            scopes=SCOPES
        )
    elif os.path.exists('credentials.json'):
        return Credentials.from_service_account_file('credentials.json', scopes=SCOPES)
    else:
        raise FileNotFoundError("Credentials not found!")

try:
    creds = get_credentials()
    sheets_service = build('sheets', 'v4', credentials=creds)
    drive_service = build('drive', 'v3', credentials=creds)

    print("=" * 80)
    print("📋 SHEET DIAGNOSTICS")
    print("=" * 80)

    # Get sheet data
    result = sheets_service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=SHEET_NAME
    ).execute()

    rows = result.get('values', [])
    headers = rows[0] if rows else []
    col_map = {header: idx for idx, header in enumerate(headers)}

    print(f"\n📊 Found {len(rows)-1} data rows")
    print(f"\n🔍 Looking for IMAGE: UIDs in Options column...")

    options_col = col_map.get('Options', -1)
    if options_col < 0:
        print("❌ No 'Options' column found!")
    else:
        image_uids = set()
        for idx, row in enumerate(rows[1:], start=2):
            if len(row) > options_col:
                options = row[options_col].strip()
                if options.startswith('IMAGE:'):
                    uid = options.split(':', 1)[1].strip() if ':' in options else ''
                    if uid:
                        image_uids.add(uid)
                        print(f"  Row {idx}: {uid}")

        print(f"\n📌 Found {len(image_uids)} unique IMAGE UIDs")

    print("\n" + "=" * 80)
    print("📁 FOLDER DIAGNOSTICS")
    print("=" * 80)
    print(f"\nScanning folder: {QUESTION_FOLDER_ID}")

    # List all files in the folder
    query = f"'{QUESTION_FOLDER_ID}' in parents and trashed=false"
    results = drive_service.files().list(
        q=query,
        spaces='drive',
        fields='files(id, name, mimeType)',
        pageSize=1000
    ).execute()

    files = results.get('files', [])
    print(f"\n📂 Found {len(files)} items in folder:")

    # Categorize files
    image_files = []
    folders = []
    other = []

    for f in sorted(files, key=lambda x: x['name']):
        if 'folder' in f['mimeType'].lower():
            folders.append(f)
        elif any(x in f['mimeType'].lower() for x in ['image', 'png', 'jpg', 'jpeg', 'gif']):
            image_files.append(f)
        else:
            other.append(f)

    if image_files:
        print(f"\n  🖼️  IMAGE FILES ({len(image_files)}):")
        for f in image_files:
            print(f"     • {f['name']} (ID: {f['id'][:20]}...)")

    if folders:
        print(f"\n  📁 FOLDERS ({len(folders)}):")
        for f in folders:
            print(f"     • {f['name']} (ID: {f['id'][:20]}...)")

    if other:
        print(f"\n  📄 OTHER FILES ({len(other)}):")
        for f in other[:10]:
            print(f"     • {f['name']}")
        if len(other) > 10:
            print(f"     ... and {len(other)-10} more")

    # Test matching
    if image_uids and image_files:
        print("\n" + "=" * 80)
        print("🔗 MATCHING TEST")
        print("=" * 80)

        for uid in sorted(image_uids)[:5]:  # Test first 5
            print(f"\n  UID: {uid}")
            # Try different extensions
            extensions = ['', '.png', '.jpg', '.jpeg', '.gif']
            found = False
            for ext in extensions:
                filename = uid + ext
                matching = [f for f in image_files if f['name'].lower() == filename.lower()]
                if matching:
                    print(f"    ✅ MATCH: {filename}")
                    found = True
                    break
                else:
                    print(f"    ❌ Not found: {filename}")
            if not found:
                print(f"    ⚠️  No match found for UID in image files")

except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
