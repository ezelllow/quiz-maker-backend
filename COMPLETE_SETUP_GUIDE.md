# Complete Quiz Maker Setup - Step by Step

Follow each step carefully and don't skip any.

---

## **PART 1: Get Your Google Sheet Name**

1. Open your Google Sheet: https://docs.google.com/spreadsheets/d/1TOmLo9UNpzOggeX27j1p6Q2NdAnCWpRJ1ErYAEJ-sZU

2. Look at the **bottom left** where you see the sheet tabs

3. Find your questions sheet tab (the one with all the data)

4. **Right-click on that tab** → Click "View more sheet actions" (⋮ menu) → "Copy sheet name"

5. **Paste it somewhere** - this is your EXACT sheet name (copy the whole thing, with spaces and capitals)

   **Example:** If it says "Physics Paper1", that's your sheet name

6. **Write it down:**
   ```
   MY SHEET NAME: ___________________________
   ```

---

## **PART 2: Get Your Google Drive Folder ID**

1. Open Google Drive: https://drive.google.com

2. Find the folder where your question images are (PyshicsP1_Qimages)

3. Click on it to open it

4. Look at the URL in the address bar - it will look like:
   ```
   https://drive.google.com/drive/folders/10TtAVgxTsczSFxIrkwSSy_KFQlebWCiX
   ```

5. **Copy the ID part** (the long string after `/folders/`)

6. **Write it down:**
   ```
   MY FOLDER ID: 10TtAVgxTsczSFxIrkwSSy_KFQlebWCiX
   ```

---

## **PART 3: Create a Service Account in Google Cloud**

1. Go to [Google Cloud Console](https://console.cloud.google.com/)

2. At the top, make sure you're in the right project. If you need to select one:
   - Click the project dropdown
   - Select your project (or create a new one)

3. In the left sidebar, search for **"Service Accounts"** and click it

4. Click **"Create Service Account"** button (blue button at top)

5. Fill in:
   - **Service Account Name:** `quiz-maker`
   - **Service Account ID:** (auto-fills, leave it)
   - Click **"Create and Continue"**

6. Skip the optional permissions steps:
   - Click **"Continue"**
   - Click **"Done"**

---

## **PART 4: Create Service Account Key (Credentials)**

1. You should see your service account "quiz-maker" in the list

2. Click on **"quiz-maker"** to open it

3. Go to the **"Keys"** tab (top navigation)

4. Click **"Add Key"** → **"Create new key"**

5. Select **"JSON"** (NOT P12!)

6. Click **"Create"**

7. A file will download: **`celestial-brand-449415-e5-...json`**

8. **Rename it to `credentials.json`**

9. **Move it to `C:\School\quizMaker\`** folder

---

## **PART 5: Share Sheet with Service Account**

1. From the Google Cloud Console, go back to your service account

2. Copy the **"Service Account Email"** (looks like: `quiz-maker@celestial-brand-449415-e5.iam.gserviceaccount.com`)

3. Go to your Google Sheet: https://docs.google.com/spreadsheets/d/1TOmLo9UNpzOggeX27j1p6Q2NdAnCWpRJ1ErYAEJ-sZU

4. Click **"Share"** button (top right)

5. Paste the service account email

6. Select **"Editor"** (not Viewer)

7. **Uncheck** "Notify people" 

8. Click **"Share"**

---

## **PART 6: Share Drive Folder with Service Account**

1. Go to Google Drive: https://drive.google.com

2. Open the **PyshicsP1_Qimages** folder

3. Right-click → **"Share"**

4. Paste the same service account email

5. Select **"Editor"**

6. **Uncheck** "Notify people"

7. Click **"Share"**

---

## **PART 7: Update Backend Configuration**

1. Open `C:\School\quizMaker\quiz_backend.py` in a text editor

2. Go to **Line 22-24** and update:

```python
SPREADSHEET_ID = '1TOmLo9UNpzOggeX27j1p6Q2NdAnCWpRJ1ErYAEJ-sZU'
QUESTION_FOLDER_ID = '10TtAVgxTsczSFxIrkwSSy_KFQlebWCiX'
SHEET_NAME = 'Physics Paper1'  # <-- REPLACE WITH YOUR EXACT SHEET NAME
```

**Important:** Replace `'Physics Paper1'` with your actual sheet name from Step 1 (keep the single quotes!)

3. **Save the file**

---

## **PART 8: Verify Everything**

1. Open command prompt in `C:\School\quizMaker\`

2. Run:
```bash
python list_sheets.py
```

3. This should show you the exact sheet names in your spreadsheet

4. **Copy the exact name** and update it in `quiz_backend.py` if different

---

## **PART 9: Run the Backend**

1. In command prompt, run:
```bash
python quiz_backend.py
```

2. You should see:
```
✅ Loaded X questions from sheet
📊 Available subtopics: [...]
📊 Available difficulties: [...]

💡 API will be available at http://localhost:8000
```

3. If you see this, **you're done!** 🎉

---

## **Troubleshooting**

**If you get a credentials error:**
- Make sure `credentials.json` is in `C:\School\quizMaker\`
- Make sure it starts with `{ "type": "service_account"`

**If you get a permission error (403):**
- Wait 2-3 minutes for sharing to propagate
- Make sure you shared with the exact email from Google Cloud

**If you get a sheet name error:**
- Run `python list_sheets.py` 
- Copy the exact sheet name it shows
- Update it in `quiz_backend.py`

---

## **Your Configuration**

Fill this in as you go:

```
Sheet Name: ___________________________
Folder ID: 10TtAVgxTsczSFxIrkwSSy_KFQlebWCiX
Service Account Email: quiz-maker@celestial-brand-449415-e5.iam.gserviceaccount.com
Spreadsheet ID: 1TOmLo9UNpzOggeX27j1p6Q2NdAnCWpRJ1ErYAEJ-sZU
```

---

Done! Now go to Part 1 and start. 👇
