from fastapi import FastAPI, HTTPException, Depends, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer
from pydantic import BaseModel, Field
from typing import Optional
import supabase
import os
from datetime import datetime

app = FastAPI()

# >>> FIX 1: CORS - Allow your frontend origin explicitly <<<
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://xtournyf.vercel.app"], # Your frontend URL
    allow_credentials=True,
    allow_methods=["*"], # Allows all methods including OPTIONS, GET, POST
    allow_headers=["*"], # Allows all headers
)

# Supabase setup (ensure these are set in your Render environment variables)
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")
supabase_client = supabase.create_client(supabase_url, supabase_key)

# Owner credentials (from env vars)
OWNER_USERNAME = os.getenv("OWNER_USERNAME")
OWNER_PASSWORD = os.getenv("OWNER_PASSWORD")

# Pydantic models for requests
class UserLogin(BaseModel):
    username: str
    password: str

class UserSignup(BaseModel):
    username: str
    email: str
    password: str

# >>> FIX 2: Login Route - Path is now '/auth/login' (no /api) <<<
@app.post("/auth/login")
async def login(login_data: UserLogin):
    try:
        # 1. Find user by username in your 'profiles' table
        profile_response = supabase_client.table('profiles').select('*').eq('username', login_data.username).execute()
        if not profile_response.data:
            raise HTTPException(status_code=401, detail="Invalid username or password")

        user_profile = profile_response.data[0]

        # 2. Attempt login with Supabase Auth using email and password
        auth_response = supabase_client.auth.sign_in_with_password({
            "email": user_profile['email'],
            "password": login_data.password
        })

        # 3. Return the token and user data (frontend expects 'token' and 'user')
        return {
            "token": auth_response.session.access_token,
            "user": user_profile
        }

    except Exception as e:
        # Log the detailed error on your server for debugging
        print(f"Login error: {e}")
        raise HTTPException(status_code=401, detail="Invalid username or password")

# >>> FIX 3: Signup Route - Path is now '/auth/register' <<<
@app.post("/auth/register")
async def register(user: UserSignup):
    try:
        # 1. Check if username exists in your 'profiles' table
        existing = supabase_client.table('profiles').select('*').eq('username', user.username).execute()
        if existing.data:
            raise HTTPException(status_code=400, detail="Username already taken")

        # 2. Determine if this is the owner account
        role = 'owner' if (user.username == OWNER_USERNAME and user.password == OWNER_PASSWORD) else 'user'

        # 3. Sign up with Supabase Auth using email
        auth_response = supabase_client.auth.sign_up({
            "email": user.email,
            "password": user.password
        })

        if not auth_response.user:
            raise HTTPException(status_code=400, detail="Signup failed")

        # 4. Create profile entry in your 'profiles' table
        profile_data = {
            "id": auth_response.user.id,
            "username": user.username,
            "email": user.email,
            "role": role,
            "created_at": datetime.now().isoformat()
        }
        supabase_client.table('profiles').insert(profile_data).execute()

        return {"message": "Signup successful", "role": role}

    except Exception as e:
        print(f"Signup error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# >>> FIX 4: Movie Route - Example of path without /api <<<
@app.get("/movies")
async def get_movies(type: Optional[str] = None):
    try:
        query = supabase_client.table('movies').select('*')
        if type:
            query = query.eq('type', type)
        movies = query.execute()
        return {"movies": movies.data}
    except Exception as e:
        print(f"Error fetching movies: {e}")
        return {"movies": []} # Return empty list on error

# Health check (optional but good)
@app.get("/health")
async def health_check():
    return {"status": "ok"}
