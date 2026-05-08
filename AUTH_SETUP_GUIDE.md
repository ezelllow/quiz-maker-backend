# Quiz Maker Authentication Setup Guide

Complete step-by-step guide to add authentication with MySQL and Google OAuth to your quiz maker.

---

## ✅ QUICK CHECKLIST

- [ ] Step 1: Set up MySQL database
- [ ] Step 2: Install backend dependencies
- [ ] Step 3: Create `.env` file with credentials
- [ ] Step 4: Get Google OAuth credentials
- [ ] Step 5: Update backend code
- [ ] Step 6: Install frontend dependencies
- [ ] Step 7: Update frontend code (already done ✓)
- [ ] Step 8: Test the application

---

## STEP 1: Set Up MySQL Database

### 1a. Install MySQL

**Windows:**
```bash
# Option 1: Using Chocolatey
choco install mysql

# Option 2: Download from https://dev.mysql.com/downloads/mysql/
```

**macOS:**
```bash
brew install mysql
```

**Linux:**
```bash
sudo apt-get install mysql-server
```

### 1b. Start MySQL Service

**Windows (PowerShell as Admin):**
```bash
Net start MySQL80
```

**macOS/Linux:**
```bash
mysql.server start
# or
brew services start mysql
```

### 1c. Create Database and User Table

Open terminal and run:
```bash
mysql -u root -p
# Enter your MySQL root password (or leave empty if you haven't set one)
```

Then paste this SQL:
```sql
-- Create database
CREATE DATABASE quiz_maker;
USE quiz_maker;

-- Create users table
CREATE TABLE users (
    id INT PRIMARY KEY AUTO_INCREMENT,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255),
    name VARCHAR(255) NOT NULL,
    google_id VARCHAR(255) UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- Create indexes for faster lookups
CREATE INDEX idx_email ON users(email);
CREATE INDEX idx_google_id ON users(google_id);

-- Verify
SELECT * FROM users;
```

Type `exit` to close MySQL.

---

## STEP 2: Install Backend Dependencies

Open Command Prompt in `C:\School\quizMaker\` and run:

```bash
pip install pymysql --break-system-packages
pip install PyJWT --break-system-packages
pip install python-dotenv --break-system-packages
pip install passlib[bcrypt] --break-system-packages
pip install google-auth-oauthlib --break-system-packages
```

---

## STEP 3: Create `.env` File

Create a new file called `.env` in `C:\School\quizMaker\` with this content:

```
# MySQL Configuration
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=
DB_NAME=quiz_maker

# JWT Secret (use a random string - change this!)
JWT_SECRET=your_super_secret_jwt_key_change_this_12345

# Google OAuth (you'll get these from Google Cloud Console)
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
```

**IMPORTANT:** Replace `DB_PASSWORD` with your MySQL root password if you set one.

---

## STEP 4: Get Google OAuth Credentials

### 4a. Create Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Click **Select a Project** → **New Project**
3. Name it "Quiz Maker" and click **Create**
4. Wait for the project to be created

### 4b. Enable Google+ API

1. In the search bar, search for "Google+ API"
2. Click on it and press **Enable**
3. Wait for it to enable

### 4c. Create OAuth Credentials

1. Go to **Credentials** (left sidebar)
2. Click **Create Credentials** → **OAuth 2.0 Client ID**
3. If prompted, configure the **OAuth consent screen** first:
   - User Type: **External**
   - App name: "Quiz Maker"
   - User support email: Your email
   - Developer contact: Your email
   - Save and continue
4. Back to credentials, select **Web application**
5. Under **Authorized redirect URIs**, add:
   ```
   http://localhost:3000
   http://localhost:8000/api/auth/google-callback
   ```
6. Click **Create**
7. Copy the **Client ID** and **Client Secret**

### 4d. Add to `.env`

Paste your Client ID and Secret into the `.env` file:
```
GOOGLE_CLIENT_ID=your_client_id_here.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your_client_secret_here
```

---

## STEP 5: Update Backend Code

### 5a. Copy AUTH Code to Backend

I've created `AUTH_ADDITIONS.py` with all the authentication code. 

**Now you need to integrate it into `quiz_backend.py`:**

1. Open `AUTH_ADDITIONS.py` (in outputs folder)
2. Copy the imports section from the top
3. Add them to the top of your `quiz_backend.py` (after existing imports)
4. Copy the data models and paste them in the models section of `quiz_backend.py`
5. Copy all the database functions and paste them after the models
6. Copy all the authentication endpoints and paste them before `@app.on_event("startup")`
7. For the `/api/quiz` endpoint, update it to include authentication verification at the top:

```python
@app.post("/api/quiz", response_model=QuizResponse)
async def create_quiz(request: QuizRequest, authorization: str = None):
    """Create a quiz based on filters (requires authentication)"""
    try:
        # Verify token
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="No authorization token")

        token = authorization.replace("Bearer ", "")
        payload = verify_jwt_token(token)

        if not payload:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        user_id = payload.get('user_id')
        print(f"📌 Quiz request from user {user_id}")

        # ... [REST OF YOUR ORIGINAL QUIZ CREATION CODE STAYS THE SAME] ...
```

### 5b. Update Requirements File

Create or update `requirements.txt` in `C:\School\quizMaker\`:

```
fastapi==0.104.1
uvicorn==0.24.0
google-auth-oauthlib==1.2.0
google-auth==2.25.2
google-auth-httplib2==0.2.0
google-api-python-client==1.12.1
pydantic==2.5.0
pymysql==1.1.0
PyJWT==2.8.1
python-dotenv==1.0.0
passlib==1.7.4
bcrypt==4.1.1
```

---

## STEP 6: Install Frontend Dependencies

Open Command Prompt in `C:\School\quiz-maker-frontend\` and run:

```bash
npm install @react-oauth/google
npm install axios
```

---

## STEP 7: Frontend Code Updates (✓ Already Done)

Your frontend has been updated with:
- ✅ `LoginPage.jsx` - Login form with email/password and Google OAuth
- ✅ `SignupPage.jsx` - Signup form with email/password and Google OAuth
- ✅ `AuthPage.css` - Beautiful styling for auth pages
- ✅ Updated `App.jsx` - Routes between login, signup, and quiz based on auth
- ✅ Updated `App.css` - Header with user greeting and logout button
- ✅ Updated `QuizMaker.jsx` - Now sends auth token with requests

---

## STEP 8: Test the Application

### 8a. Start MySQL
```bash
mysql.server start
# or on Windows: Net start MySQL80
```

### 8b. Start Backend

Open Command Prompt in `C:\School\quizMaker\`:
```bash
python quiz_backend.py
```

You should see:
```
🚀 Starting up...
✅ Startup complete!
💡 API will be available at http://localhost:8000
```

### 8c. Start Frontend

Open Command Prompt in `C:\School\quiz-maker-frontend\`:
```bash
npm start
```

It should open `http://localhost:3000` in your browser.

### 8d. Test Sign Up

1. Click "Sign up here" on the login page
2. Enter a name, email, and password
3. Click "Create Account"
4. You should be logged in and see the quiz maker! ✅

### 8e. Test Google Sign Up

1. On signup page, click the Google button
2. Select your Google account
3. You should be logged in! ✅

### 8f. Test Login

1. Logout (button in top right)
2. Try logging in with your email/password
3. Should work! ✅

---

## TROUBLESHOOTING

### "Error: Could not connect to database"
- Make sure MySQL is running: `mysql.server start`
- Check `DB_PASSWORD` in `.env` matches your MySQL root password
- Verify database exists: `mysql -u root -p` then `SHOW DATABASES;`

### "Invalid Google token" error
- Make sure `GOOGLE_CLIENT_ID` in `.env` matches your app's credentials
- Verify authorized redirect URIs include `http://localhost:3000`
- Clear browser cache and try again

### Signup page not showing
- Make sure frontend code is updated (check `App.jsx`)
- Check browser console for errors (F12)
- Verify all files were saved correctly

### "Authorization header missing" when creating quiz
- Make sure auth token is being saved to localStorage
- Check Network tab in browser DevTools to see if `Authorization: Bearer ...` header is being sent
- Try logging out and back in

### Port 3000 or 8000 already in use
```bash
# Find process using port 3000
lsof -i :3000

# Kill the process (replace PID with the number shown)
kill -9 PID

# Or change port in frontend:
PORT=3001 npm start
```

---

## API ENDPOINTS SUMMARY

| Endpoint | Method | Description | Auth Required |
|----------|--------|-------------|---|
| `/api/auth/signup` | POST | Create new account | ❌ No |
| `/api/auth/login` | POST | Login with email/password | ❌ No |
| `/api/auth/google` | POST | Login/signup with Google | ❌ No |
| `/api/auth/me` | GET | Get current user profile | ✅ Yes |
| `/api/quiz` | POST | Create quiz | ✅ Yes |
| `/api/subtopics` | GET | Get available subtopics | ❌ No |
| `/api/difficulties` | GET | Get difficulty levels | ❌ No |
| `/api/image/{file_id}` | GET | Serve quiz images | ❌ No |

---

## NEXT STEPS (Optional)

Once everything is working:

1. **Store quiz attempts**: Track which quizzes each user has taken
2. **Save quiz progress**: Let users resume incomplete quizzes
3. **User statistics**: Show user their average score by topic
4. **Reset password**: Add forgot password functionality
5. **Email verification**: Verify email addresses during signup
6. **Admin dashboard**: Manage users and questions
7. **Deploy**: Move from localhost to production server

---

## NEED HELP?

If something doesn't work:

1. Check the error message carefully
2. Look at browser console (F12) for frontend errors
3. Check backend terminal for server errors
4. Check that all files are saved
5. Restart both frontend and backend
6. Clear browser cache and cookies

---

**You're all set! 🎉**

Your quiz maker now has secure authentication with MySQL and Google OAuth!
