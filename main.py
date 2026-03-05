from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer
from pydantic import BaseModel
from typing import Optional, List
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

# Owner credentials from Render env - NOT from Supabase
OWNER_USERNAME = os.getenv("OWNER_USERNAME", "xotiic")
OWNER_PASSWORD = os.getenv("OWNER_PASSWORD", "40671Mps19*")

print(f"✅ Owner credentials loaded from Render - Username: {OWNER_USERNAME}")

# Hardcoded owner user object (completely separate from Supabase)
OWNER_USER = {
    "id": "owner-001",
    "username": OWNER_USERNAME,
    "email": "owner@xstream.com",
    "role": "owner",
    "avatar_url": None,
    "created_at": datetime.now().isoformat()
}

# Pydantic models
class UserLogin(BaseModel):
    username: str
    password: str

class UserSignup(BaseModel):
    username: str
    password: str
    email: Optional[str] = None

@app.post("/auth/login")
async def login(login_data: UserLogin):
    try:
        print(f"🔐 Login attempt: {login_data.username}")
        
        # OWNER CHECK - Hardcoded from Render env, no database
        if login_data.username == OWNER_USERNAME and login_data.password == OWNER_PASSWORD:
            print(f"✅ Owner login successful from Render env")
            return {
                "token": "owner_token_xyz789",
                "user": OWNER_USER
            }
        
        # Regular user check - Database only
        try:
            result = supabase_client.table('profiles')\
                .select('*')\
                .eq('username', login_data.username)\
                .execute()
            
            if not result.data:
                print(f"❌ User not found: {login_data.username}")
                raise HTTPException(status_code=401, detail="Invalid username or password")
            
            user = result.data[0]
            
            # Plain text password check
            if user['password'] != login_data.password:
                print(f"❌ Password mismatch for: {login_data.username}")
                raise HTTPException(status_code=401, detail="Invalid username or password")
            
            # Update last login
            supabase_client.table('profiles')\
                .update({'last_login': datetime.now().isoformat()})\
                .eq('id', user['id'])\
                .execute()
            
            print(f"✅ Regular user login: {login_data.username}")
            
            # Don't send password back
            user_copy = user.copy()
            user_copy.pop('password', None)
            
            return {
                "token": f"user_{user['id']}",
                "user": user_copy
            }
            
        except HTTPException:
            raise
        except Exception as db_error:
            print(f"❌ Database error: {str(db_error)}")
            raise HTTPException(status_code=401, detail="Invalid username or password")
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Login error: {str(e)}")
        raise HTTPException(status_code=401, detail="Invalid username or password")

@app.post("/auth/register")
async def register(user: UserSignup):
    try:
        print(f"📝 Registration attempt: {user.username}")
        
        # Prevent registering as owner
        if user.username == OWNER_USERNAME:
            if user.password == OWNER_PASSWORD:
                return {
                    "message": "Owner account exists. Please login.",
                    "user": OWNER_USER
                }
            else:
                raise HTTPException(status_code=400, detail="Username taken")
        
        # Check if username exists
        existing = supabase_client.table('profiles')\
            .select('*')\
            .eq('username', user.username)\
            .execute()
        
        if existing.data:
            raise HTTPException(status_code=400, detail="Username already taken")
        
        # Generate UUID for new user
        user_id = str(uuid.uuid4())
        
        # Use provided email or create a fake one
        email = user.email if user.email else f"{user.username}@user.local"
        
        # Create new profile
        new_user = {
            "id": user_id,
            "username": user.username,
            "email": email,
            "password": user.password,
            "role": "user",
            "created_at": datetime.now().isoformat()
        }
        
        result = supabase_client.table('profiles')\
            .insert(new_user)\
            .execute()
        
        if not result.data:
            raise HTTPException(status_code=400, detail="Failed to create user")
        
        created_user = result.data[0]
        created_user.pop('password', None)
        
        print(f"✅ User created: {user.username}")
        
        return {
            "message": "Registration successful",
            "user": created_user
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
        
        result = query.execute()
        return {"movies": result.data}
        
    except Exception as e:
        print(f"❌ Error fetching movies: {e}")
        return {"movies": []}

@app.get("/movies/{movie_id}")
async def get_movie(movie_id: int):
    try:
        result = supabase_client.table('movies')\
            .select('*')\
            .eq('id', movie_id)\
            .execute()
        
        if not result.data:
            raise HTTPException(status_code=404, detail="Movie not found")
        
        return {"movie": result.data[0]}
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error fetching movie: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "owner": OWNER_USERNAME
    }
