from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer
from pydantic import BaseModel
from typing import Optional
import supabase
import os
from datetime import datetime
import uuid

app = FastAPI()

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://xtournyf.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Supabase setup
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")
supabase_client = supabase.create_client(supabase_url, supabase_key)

# Hardcoded owner credentials
OWNER_USERNAME = "xotiic"
OWNER_PASSWORD = "40671Mps19*"

print(f"✅ Owner credentials loaded - Username: {OWNER_USERNAME}")

# Pydantic models
class UserLogin(BaseModel):
    username: str
    password: str

class UserSignup(BaseModel):
    username: str
    password: str

@app.post("/auth/login")
async def login(login_data: UserLogin):
    try:
        print(f"🔐 Login attempt for username: {login_data.username}")
        
        # Hardcoded owner check FIRST (bypass database)
        if login_data.username == OWNER_USERNAME and login_data.password == OWNER_PASSWORD:
            print("✅ Owner login successful (hardcoded)")
            
            # Check if owner profile exists in database
            profile_response = supabase_client.table('profiles').select('*').eq('username', login_data.username).execute()
            
            if profile_response.data:
                user_profile = profile_response.data[0]
            else:
                # Create owner profile if it doesn't exist
                fake_email = f"{login_data.username}@localhost.local"
                user_id = str(uuid.uuid4())
                
                user_profile = {
                    "id": user_id,
                    "username": login_data.username,
                    "email": fake_email,
                    "role": "owner",
                    "created_at": datetime.now().isoformat()
                }
                
                supabase_client.table('profiles').insert(user_profile).execute()
                print("✅ Created owner profile in database")
            
            # Return fake token (since we're bypassing Supabase Auth)
            return {
                "token": "hardcoded_owner_token_123456789",
                "user": user_profile
            }
        
        # For non-owner users, check database
        profile_response = supabase_client.table('profiles').select('*').eq('username', login_data.username).execute()
        
        if not profile_response.data:
            print(f"❌ No profile found for username: {login_data.username}")
            raise HTTPException(status_code=401, detail="Invalid username or password")
        
        user_profile = profile_response.data[0]
        
        # Simple password check (stored in plain text in database)
        if user_profile.get('password') != login_data.password:
            print(f"❌ Password mismatch for {login_data.username}")
            raise HTTPException(status_code=401, detail="Invalid username or password")
        
        print(f"✅ Login successful for {login_data.username}")
        
        # Update last login
        supabase_client.table('profiles').update({
            "last_login": datetime.now().isoformat()
        }).eq('id', user_profile['id']).execute()
        
        return {
            "token": f"user_token_{user_profile['id']}",
            "user": user_profile
        }
            
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Unexpected login error: {str(e)}")
        raise HTTPException(status_code=401, detail="Invalid username or password")

@app.post("/auth/register")
async def register(user: UserSignup):
    try:
        print(f"📝 Registration attempt for username: {user.username}")
        
        # Check if username exists
        existing = supabase_client.table('profiles').select('*').eq('username', user.username).execute()
        if existing.data:
            raise HTTPException(status_code=400, detail="Username already taken")
        
        # Check if this is the owner
        is_owner = (user.username == OWNER_USERNAME and user.password == OWNER_PASSWORD)
        
        # Create fake email (since Supabase Auth requires email)
        fake_email = f"{user.username}@localhost.local"
        user_id = str(uuid.uuid4())
        
        # Create profile with plain text password
        profile_data = {
            "id": user_id,
            "username": user.username,
            "email": fake_email,
            "password": user.password,  # STORED IN PLAIN TEXT (not secure but you asked for it)
            "role": "owner" if is_owner else "user",
            "created_at": datetime.now().isoformat()
        }
        
        supabase_client.table('profiles').insert(profile_data).execute()
        print(f"✅ User {user.username} created with role: {profile_data['role']}")
        
        return {
            "message": "Signup successful",
            "role": profile_data["role"],
            "user": profile_data
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Registration error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

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
        return {"movies": []}

@app.get("/health")
async def health_check():
    return {"status": "ok", "timestamp": datetime.now().isoformat()}
