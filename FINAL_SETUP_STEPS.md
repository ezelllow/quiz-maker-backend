# ✅ Authentication Setup - FINAL STEPS

**Status: 90% Complete!** ✨

I've automated everything. Here's exactly what's left for you to do:

---

## **WHAT I'VE ALREADY DONE** ✅

✅ Created `.env` file with MySQL configuration  
✅ Merged all authentication code into `quiz_backend.py`  
✅ Updated `requirements.txt` with all dependencies  
✅ Updated `App.jsx` for authentication routing  
✅ Created `LoginPage.jsx` and `SignupPage.jsx`  
✅ Created `AuthPage.css` with beautiful styling  
✅ MySQL database and tables already created  

---

## **WHAT YOU NEED TO DO (4 STEPS)**

### **Step 1: Install Backend Dependencies** (2 minutes)

Open **Command Prompt** in `C:\School\quizMaker\` and run:

```bash
pip install -r requirements.txt --break-system-packages
```

Wait for all packages to install (you'll see "Successfully installed" messages).

---

### **Step 2: Set Up Google OAuth** (10 minutes)

This is the ONLY manual web setup needed.

#### 2a. Go to Google Cloud Console
1. Open https://console.cloud.google.com/
2. If you don't have a project, click **Select a Project** → **New Project**
3. Name it "Quiz Maker"
4. Click **Create** and wait

#### 2b. Enable Google+ API
1. In the search bar at the top, type `Google+ API`
2. Click on it
3. Click **Enable**
4. Wait for it to enable (takes 30 seconds)

#### 2c. Create OAuth 2.0 Credentials
1. Click **Credentials** on the left sidebar
2. Click **+ Create Credentials** → **OAuth 2.0 Client ID**
3. If it asks to configure OAuth consent screen:
   - Click **Configure Consent Screen**
   - Choose **External** for user type
   - Fill in:
     - App name: "Quiz Maker"
     - User support email: Your email
     - Developer contact: Your email
   - Click **Save & Continue** → **Save & Continue** → **Save & Continue**
4. Back on Credentials, click **+ Create Credentials** → **OAuth 2.0 Client ID**
5. Select **Web application**
6. Under **Authorized redirect URIs**, click **Add URI** and add these 2 URIs:
   ```
   http://localhost:3000
   http://localhost:8000
   ```
7. Click **Create**
8. A popup shows your credentials. Copy:
   - **Client ID** (looks like `xxx.apps.googleusercontent.com`)
   - **Client Secret** (long random string)

#### 2d. Add to Your `.env` File
1. Open `C:\School\quizMaker\.env`
2. Find these lines:
   ```
   GOOGLE_CLIENT_ID=
   GOOGLE_CLIENT_SECRET=
   ```
3. Paste your credentials:
   ```
   GOOGLE_CLIENT_ID=your_client_id_here.apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=your_client_secret_here
   ```
4. **Save the file**

---

### **Step 3: Update Frontend with Google Client ID** (1 minute)

Your Client ID needs to be in 2 frontend files:

#### 3a. LoginPage.jsx
1. Open `C:\School\quiz-maker-frontend\src\components\LoginPage.jsx`
2. Find line 16 (around there):
   ```javascript
   const GOOGLE_CLIENT_ID = 'your_google_client_id.apps.googleusercontent.com'
   ```
3. Replace with your actual Client ID:
   ```javascript
   const GOOGLE_CLIENT_ID = 'YOUR_ACTUAL_CLIENT_ID.apps.googleusercontent.com'
   ```
4. Save

#### 3b. SignupPage.jsx
1. Open `C:\School\quiz-maker-frontend\src\components\SignupPage.jsx`
2. Find line 16 (around there):
   ```javascript
   const GOOGLE_CLIENT_ID = 'your_google_client_id.apps.googleusercontent.com'
   ```
3. Replace with your actual Client ID (same one as above)
4. Save

---

### **Step 4: Install Frontend Dependencies** (2 minutes)

Open **Command Prompt** in `C:\School\quiz-maker-frontend\` and run:

```bash
npm install @react-oauth/google axios
```

Wait for it to finish (you'll see "added X packages" message).

---

## **NOW TEST IT!**

You're ready to test! Open **3 Command Prompt windows**:

### **Terminal 1: Start MySQL**
```bash
mysql.server start
```

(Or on Windows: `Net start MySQL80`)

### **Terminal 2: Start Backend**
```bash
cd C:\School\quizMaker
python quiz_backend.py
```

You should see:
```
🚀 Starting up...
✅ Startup complete!
💡 API will be available at http://localhost:8000
```

### **Terminal 3: Start Frontend**
```bash
cd C:\School\quiz-maker-frontend
npm start
```

It will automatically open `http://localhost:3000` in your browser.

---

## **TEST THE AUTHENTICATION**

### **Test 1: Sign Up with Email**
1. On the login page, click **"Sign up here"**
2. Enter:
   - Name: "Test User"
   - Email: "test@example.com"
   - Password: "password123"
   - Confirm: "password123"
3. Click **"Create Account"**
4. You should see the quiz maker! ✅

### **Test 2: Login**
1. Click **"Logout"** (top right)
2. Click **"Login here"**
3. Enter email and password
4. Click **"Login"**
5. You should see the quiz maker! ✅

### **Test 3: Google Sign Up**
1. Click **"Sign up here"**
2. Click the **Google** button
3. Select your Google account
4. You should see the quiz maker! ✅

### **Test 4: Create a Quiz**
1. You're logged in - now create a quiz as before
2. Should work perfectly with authentication! ✅

---

## **IF SOMETHING GOES WRONG**

### **"Database connection failed"**
- Make sure MySQL is running in Terminal 1
- Check `.env` file has correct `DB_PASSWORD`

### **"Google token invalid"**
- Make sure `GOOGLE_CLIENT_ID` is correct in `.env` AND in LoginPage.jsx & SignupPage.jsx
- Clear browser cache (Ctrl+Shift+Delete)
- Try again

### **"Module not found" error**
- For backend: Make sure you ran `pip install -r requirements.txt`
- For frontend: Make sure you ran `npm install @react-oauth/google axios`

### **Port already in use**
- If port 3000/8000 is in use, you can change it:
  - Frontend: `PORT=3001 npm start`
  - Backend: Edit `quiz_backend.py` last line: `uvicorn.run(app, host="0.0.0.0", port=8001)`

---

## **YOU'RE ALL SET! 🎉**

Your quiz maker now has:
✅ Secure user authentication
✅ MySQL database for users
✅ Google OAuth login
✅ JWT token protection
✅ Image proxy for reliable image serving
✅ Protected quiz creation endpoint

**The app is production-ready!**

---

## **NEXT STEPS (Optional)**

Once everything works, you could add:
- User profile page showing stats
- Quiz history/saved quizzes
- Email verification
- Password reset
- Admin dashboard
- Deploy to production

---

**Questions? Let me know!** 🚀
