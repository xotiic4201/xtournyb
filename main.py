from fastapi import FastAPI, HTTPException, Depends, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer
from pydantic import BaseModel, Field
from typing import Optional
import supabase
import os
from datetime import datetime
import bcrypt

app = FastAPI()

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://xtournyf.vercel.app"],  # Your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Supabase setup
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")
supabase_client = supabase.create_client(supabase_url, supabase_key)

# Owner credentials
OWNER_USERNAME = os.getenv("OWNER_USERNAME")
OWNER_PASSWORD = os.getenv("OWNER_PASSWORD")
OWNER_EMAIL = os.getenv("OWNER_EMAIL")  # Add this to your env vars

# Pydantic models
class UserLogin(BaseModel):
    username: str
    password: str

class UserSignup(BaseModel):
    username: str
    email: str
    password: str

@app.post("/auth/login")
async def login(login_data: UserLogin):
    try:
        print(f"Login attempt for username: {login_data.username}")
        
        # 1. Find user by username in profiles table
        profile_response = supabase_client.table('profiles').select('*').eq('username', login_data.username).execute()
        
        if not profile_response.data:
            print(f"No profile found for username: {login_data.username}")
            raise HTTPException(status_code=401, detail="Invalid username or password")
        
        user_profile = profile_response.data[0]
        print(f"Found profile for email: {user_profile['email']}")
        
        # 2. Attempt login with Supabase Auth
        try:
            auth_response = supabase_client.auth.sign_in_with_password({
                "email": user_profile['email'],
                "password": login_data.password
            })
            
            print("Login successful with Supabase Auth")
            
            # Update last login
            supabase_client.table('profiles').update({
                "last_login": datetime.now().isoformat()
            }).eq('id', user_profile['id']).execute()
            
            return {
                "token": auth_response.session.access_token,
                "user": user_profile
            }
            
        except Exception as auth_error:
            print(f"Supabase Auth error: {str(auth_error)}")
            raise HTTPException(status_code=401, detail="Invalid username or password")
            
    except HTTPException:
        raise
    except Exception as e:
        print(f"Unexpected login error: {str(e)}")
        raise HTTPException(status_code=401, detail="Invalid username or password")

@app.post("/auth/register")
async def register(user: UserSignup):
    try:
        print(f"Registration attempt for username: {user.username}")
        
        # Check if username exists
        existing = supabase_client.table('profiles').select('*').eq('username', user.username).execute()
        if existing.data:
            raise HTTPException(status_code=400, detail="Username already taken")
        
        # Check if email exists
        existing_email = supabase_client.table('profiles').select('*').eq('email', user.email).execute()
        if existing_email.data:
            raise HTTPException(status_code=400, detail="Email already registered")
        
        # Check if this is the owner account
        role = 'owner' if (user.username == OWNER_USERNAME and user.password == OWNER_PASSWORD) else 'user'
        print(f"User role will be: {role}")
        
        # Sign up with Supabase Auth
        try:
            auth_response = supabase_client.auth.sign_up({
                "email": user.email,
                "password": user.password
            })
            
            if not auth_response.user:
                raise HTTPException(status_code=400, detail="Signup failed")
                
            print(f"Supabase Auth user created with ID: {auth_response.user.id}")
            
        except Exception as auth_error:
            print(f"Supabase Auth signup error: {str(auth_error)}")
            raise HTTPException(status_code=400, detail=f"Auth signup failed: {str(auth_error)}")
        
        # Create profile in profiles table
        profile_data = {
            "id": auth_response.user.id,
            "username": user.username,
            "email": user.email,
            "role": role,
            "display_name": user.username,
            "created_at": datetime.now().isoformat(),
            "last_login": None
        }
        
        profile_result = supabase_client.table('profiles').insert(profile_data).execute()
        print("Profile created successfully")
        
        return {
            "message": "Signup successful",
            "role": role,
            "user": profile_data
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Unexpected registration error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/auth/setup-owner")
async def setup_owner():
    """One-time endpoint to set up the owner account"""
    try:
        # Check if owner already exists
        existing = supabase_client.table('profiles').select('*').eq('username', OWNER_USERNAME).execute()
        if existing.data:
            return {"message": "Owner already exists"}
        
        # Create owner in Supabase Auth
        try:
            auth_response = supabase_client.auth.sign_up({
                "email": OWNER_EMAIL,
                "password": OWNER_PASSWORD
            })
        except Exception as e:
            # If user already exists in Auth, try to get them
            if "User already registered" in str(e):
                # Try to find the user by email
                # Note: This is a workaround - in production, you'd handle this differently
                return {"message": "Owner may already exist in Auth. Please check Supabase dashboard."}
            raise e
        
        # Create owner profile
        profile_data = {
            "id": auth_response.user.id,
            "username": OWNER_USERNAME,
            "email": OWNER_EMAIL,
            "role": "owner",
            "display_name": OWNER_USERNAME,
            "created_at": datetime.now().isoformat()
        }
        
        supabase_client.table('profiles').insert(profile_data).execute()
        
        return {"message": "Owner created successfully"}
        
    except Exception as e:
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
