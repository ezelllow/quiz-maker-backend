"""
Diagnostic script — verifies every IMAGE: reference in the spreadsheet has a
matching file in the Drive folder the backend scans.

Updates vs the original:
  1. Auto-discovers ALL sheet tabs in the spreadsheet (no more hard-coded "Paper1")
  2. Scans BOTH the Options column AND the Diagram column (setup rows put
     their IMAGE: marker in Diagram, not Options)
  3. Prints a side-by-side comparison: UID → expected filenames → match?
"""

import os
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SPREADSHEET_ID = '1TOmLo9UNpzOggeX27j1p6Q2NdAnCWpRJ1ErYAEJ-sZU'
QUESTION_FOLDER_ID = '10TtAVgxTsczSFxIrkwSSy_KFQlebWCiX'

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


def main():
    creds = get_credentials()
    sheets_service = build('sheets', 'v4', credentials=creds)
    drive_service = build('drive', 'v3', credentials=creds)

    # ── 1. Discover every sheet (tab) name in the spreadsheet ────────────
    print("=" * 80)
    print("📋 SHEET DISCOVERY")
    print("=" * 80)
    meta = sheets_service.spreadsheets().get(spreadsheetId=SPREADSHEET_ID).execute()
    sheet_names = [s['properties']['title'] for s in meta.get('sheets', [])]
    print(f"\n🗂️  Found {len(sheet_names)} sheet(s): {sheet_names}\n")

    # ── 2. Pull every row, harvest IMAGE: refs from Options AND Diagram ─
    print("=" * 80)
    print("🔍 SCANNING SHEETS FOR IMAGE: REFERENCES")
    print("=" * 80)
    image_uids = set()  # {(sheet, row, source_col, uid)}
    uid_log = []        # ordered log for printout

    for sheet_name in sheet_names:
        # Quoted name in case it contains spaces / dashes / em-dashes.
        rng = f"'{sheet_name}'"
        try:
            res = sheets_service.spreadsheets().values().get(
                spreadsheetId=SPREADSHEET_ID,
                range=rng,
            ).execute()
        except Exception as e:
            print(f"  ⚠️  Skipping '{sheet_name}' — {e}")
            continue

        rows = res.get('values', [])
        if not rows:
            print(f"  📭 '{sheet_name}' is empty")
            continue

        headers = rows[0]
        col_map = {h.strip(): idx for idx, h in enumerate(headers)}

        options_col = col_map.get('Options', -1)
        diagram_col = col_map.get('Diagram', -1)
        uid_col     = col_map.get('UID', -1)

        print(f"\n📄 '{sheet_name}' — {len(rows)-1} data rows; "
              f"UID col={uid_col} Options col={options_col} Diagram col={diagram_col}")

        sheet_hits = 0
        for r_idx, row in enumerate(rows[1:], start=2):
            row_uid = row[uid_col].strip() if 0 <= uid_col < len(row) else ''

            for source_name, c_idx in (('Options', options_col), ('Diagram', diagram_col)):
                if c_idx < 0 or c_idx >= len(row):
                    continue
                cell = row[c_idx].strip()
                if cell.startswith('IMAGE:'):
                    uid = cell.split(':', 1)[1].strip()
                    if uid:
                        image_uids.add(uid)
                        uid_log.append((sheet_name, r_idx, source_name, row_uid, uid))
                        sheet_hits += 1

        if sheet_hits:
            print(f"  ✅ {sheet_hits} IMAGE: reference(s) found")
        else:
            print(f"  · no IMAGE: references")

    print(f"\n📌 Total: {len(image_uids)} unique UIDs referenced across all sheets")

    # ── 3. List every file in the Drive folder ───────────────────────────
    print("\n" + "=" * 80)
    print(f"📁 LISTING FILES IN DRIVE FOLDER {QUESTION_FOLDER_ID}")
    print("=" * 80)

    files = []
    page_token = None
    while True:
        q = f"'{QUESTION_FOLDER_ID}' in parents and trashed=false"
        resp = drive_service.files().list(
            q=q,
            spaces='drive',
            fields='nextPageToken, files(id, name, mimeType)',
            pageToken=page_token,
            pageSize=1000,
        ).execute()
        files.extend(resp.get('files', []))
        page_token = resp.get('nextPageToken')
        if not page_token:
            break

    print(f"\n📂 Drive folder contains {len(files)} file(s)")
    # Build a case-insensitive map: {lowercased name: original name}
    drive_names = {f['name'].lower(): f['name'] for f in files}

    # ── 4. Match each UID against the Drive folder ───────────────────────
    print("\n" + "=" * 80)
    print("🎯 UID → FILE MATCH REPORT")
    print("=" * 80)

    matched = []
    unmatched = []
    for uid in sorted(image_uids):
        candidates = [
            f"{uid}.png",
            f"{uid}.jpg",
            f"{uid}.jpeg",
            f"{uid}.gif",
            uid,
        ]
        hit = None
        for cand in candidates:
            if cand.lower() in drive_names:
                hit = drive_names[cand.lower()]
                break

        if hit:
            matched.append((uid, hit))
        else:
            unmatched.append(uid)

    print(f"\n✅ Matched: {len(matched)} / {len(image_uids)}")
    print(f"❌ Unmatched: {len(unmatched)} / {len(image_uids)}\n")

    if unmatched:
        print("─── UIDs with NO matching file in Drive ───")
        for uid in unmatched:
            # Show which sheet/row referenced this UID for context
            refs = [(s, r, c, ru) for (s, r, c, ru, u) in uid_log if u == uid]
            print(f"\n  ❌ {uid}")
            for s, r, c, ru in refs[:3]:
                print(f"       referenced from sheet '{s}' row {r}, "
                      f"column '{c}', row UID='{ru}'")
            if len(refs) > 3:
                print(f"       … and {len(refs)-3} more reference(s)")
            print(f"       expected filename: {uid}.png  (or .jpg / .jpeg / .gif / no ext)")
        print()

    # ── 5. Show files in Drive that no UID references (orphans) ─────────
    referenced_lower = set()
    for uid in image_uids:
        for ext in ('.png', '.jpg', '.jpeg', '.gif', ''):
            referenced_lower.add((uid + ext).lower())
    orphans = [f for f in files if f['name'].lower() not in referenced_lower]
    if orphans:
        print(f"─── Orphan files in Drive (no UID references them, first 15) ───")
        for f in orphans[:15]:
            print(f"  · {f['name']}")
        if len(orphans) > 15:
            print(f"  … and {len(orphans)-15} more")


if __name__ == '__main__':
    main()
