#!/usr/bin/env python3
"""
Auto-Categorize Questions in Google Sheet
Automatically updates the Options column with proper prefixes (TABLE:, IMAGE:, or TEXT)
"""

import json
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

# Configuration
SPREADSHEET_ID = '1TOmLo9UNpzOggeX27j1p6Q2NdAnCWpRJ1ErYAEJ-sZU'
SHEET_NAME = 'Paper1'

# Define categorization for each question
CATEGORIZATIONS = {
    'PHY-ACSBR2019-P1-4E5N-001-': {
        'type': 'TABLE',
        'formatted': 'TABLE:\ndistance / m | time / s\nA) 120 000 | 0.004\nB) 120 000 | 0.000004\nC) 120 000 000 | 0.004\nD) 120 000 000 | 0.000004'
    },
    'PHY-ACSBR2019-P1-4E5N-002-': {
        'type': 'IMAGE',
        'formatted': 'IMAGE:'
    },
    'PHY-ACSBR2019-P1-4E5N-005-': {
        'type': 'TABLE',
        'formatted': 'TABLE:\n | mass | weight\nA) decreases | no change\nB) increases | no change\nC) no change | decreases\nD) no change | increases'
    },
    'PHY-ACSBR2019-P1-4E5N-009-': {
        'type': 'TABLE',
        'formatted': 'TABLE:\nX | Y\nA) period | wavefront\nB) wavefront | period\nC) wavelength | wavefront\nD) wavefront | wavelength'
    },
    'PHY-ACSBR2019-P1-4E5N-010-': {
        'type': 'IMAGE',
        'formatted': 'IMAGE:'
    },
    'PHY-ACSBR2019-P1-4E5N-011-': {
        'type': 'IMAGE',
        'formatted': 'IMAGE:'
    },
    'PHY-ACSBR2019-P1-4E5N-012-': {
        'type': 'IMAGE',
        'formatted': 'IMAGE:'
    },
    'PHY-ACSBR2019-P1-4E5N-013-': {
        'type': 'TABLE',
        'formatted': 'TABLE:\nfastest → slowest |  | \nA) air | iron | water\nB) air | water | iron\nC) iron | air | water\nD) iron | water | air'
    },
    'PHY-ACSBR2019-P1-4E5N-015-': {
        'type': 'IMAGE',
        'formatted': 'IMAGE:'
    },
    'PHY-ACSBR2019-P1-4E5N-018-': {
        'type': 'IMAGE',
        'formatted': 'IMAGE:'
    },
    'PHY-ACSBR2019-P1-4E5N-019-': {
        'type': 'TABLE',
        'formatted': 'TABLE:\nplotting compass 1 | plotting compass 2\nA) ← | →\nB) ← | ←\nC) → | →\nD) → | ←'
    },
    'PHY-ACSBR2019-P1-4E5N-020-': {
        'type': 'TABLE',
        'formatted': 'TABLE:\n | iron closed | iron opened | steel closed | steel opened\nA) 0 | 20 | 5 | 10\nB) 5 | 10 | 0 | 20\nC) 10 | 5 | 20 | 0\nD) 20 | 0 | 10 | 5'
    }
}

def main():
    print("🚀 Categorizing Questions in Google Sheet...")
    print("=" * 80)

    # Load credentials
    try:
        with open('credentials.json') as f:
            creds_dict = json.load(f)
    except FileNotFoundError:
        print("❌ Error: credentials.json not found!")
        print("   Make sure credentials.json is in the same directory as this script")
        return

    # Create credentials and build service
    creds = Credentials.from_service_account_info(
        creds_dict,
        scopes=[
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive.readonly'
        ]
    )

    sheets = build('sheets', 'v4', credentials=creds)

    # Get all data
    print("\n📖 Reading sheet...")
    result = sheets.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=SHEET_NAME
    ).execute()

    rows = result.get('values', [])
    headers = rows[0]
    col_map = {h: i for i, h in enumerate(headers)}

    # Build update list
    updates = []
    updated_count = 0

    print("🔄 Processing questions...\n")

    for row_idx, row in enumerate(rows[1:], start=2):
        while len(row) < len(headers):
            row.append('')

        uid = row[col_map.get('UID', 0)].strip() if col_map.get('UID', 0) < len(row) else ''

        if uid in CATEGORIZATIONS:
            cat = CATEGORIZATIONS[uid]
            col_letter = chr(65 + col_map.get('Options', 0))  # Convert to letter (A, B, C, etc.)

            updates.append({
                'range': f'{SHEET_NAME}!{col_letter}{row_idx}',
                'values': [[cat['formatted']]]
            })

            print(f"✅ {uid}")
            print(f"   Type: {cat['type']}")
            print(f"   Row: {row_idx}")
            updated_count += 1

    if not updates:
        print("❌ No questions found to categorize!")
        return

    # Batch update
    print(f"\n📤 Updating {updated_count} questions in sheet...")

    try:
        body = {
            'data': updates,
            'valueInputOption': 'RAW'
        }

        response = sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=SPREADSHEET_ID,
            body=body
        ).execute()

        print(f"\n{'='*80}")
        print(f"✅ SUCCESS! Updated {response.get('totalUpdatedCells', 0)} cells")
        print(f"{'='*80}")
        print("\n✨ Your sheet has been categorized!")
        print("\nNext steps:")
        print("1. Refresh your Google Sheet to see the changes")
        print("2. Restart your backend: python quiz_backend.py")
        print("3. Your questions will now be properly categorized!")

    except Exception as e:
        print(f"\n❌ Error updating sheet: {e}")
        return

if __name__ == '__main__':
    main()
