"""
Quick test to verify credentials.json is valid
"""

import json
import os

print("🔍 Testing credentials.json...\n")

# Check if file exists
if not os.path.exists('credentials.json'):
    print("❌ credentials.json NOT found in current directory!")
    print(f"Current directory: {os.getcwd()}")
    exit(1)

print("✅ credentials.json found\n")

# Try to read and parse it
try:
    with open('credentials.json', 'r') as f:
        creds = json.load(f)
    print("✅ JSON is valid\n")
except json.JSONDecodeError as e:
    print(f"❌ JSON parsing error: {e}")
    exit(1)

# Check required fields
required_fields = ['type', 'private_key_id', 'private_key', 'client_email', 'client_id', 'auth_uri', 'token_uri']

print("Checking required fields:")
for field in required_fields:
    if field in creds:
        value = creds[field]
        if field == 'private_key':
            print(f"  ✅ {field}: {value[:30]}... (truncated)")
        else:
            print(f"  ✅ {field}: {value}")
    else:
        print(f"  ❌ MISSING: {field}")

print("\n" + "="*60)

# Try to create credentials object
print("\nAttempting to create Google credentials...\n")

try:
    from google.oauth2.service_account import Credentials

    SCOPES = [
        'https://www.googleapis.com/auth/spreadsheets.readonly',
        'https://www.googleapis.com/auth/drive.readonly'
    ]

    credentials = Credentials.from_service_account_file(
        'credentials.json',
        scopes=SCOPES
    )
    print("✅ Successfully created credentials!")
    print(f"   Service Account Email: {credentials.service_account_email}")

except Exception as e:
    print(f"❌ Error creating credentials: {e}")
    import traceback
    traceback.print_exc()
    exit(1)

print("\n" + "="*60)
print("✅ ALL CHECKS PASSED! Your credentials are valid.\n")
