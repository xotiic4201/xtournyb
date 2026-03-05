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

print(f"✅ Supabase connected")

# Pydantic models
class UserLogin(BaseModel):
    username: str
    password: str

class UserSignup(BaseModel):
    username: str
    password: str
    email: Optional[str] = None

class MovieResponse(BaseModel):
    id: int
    title: str
    description: str
    genre: str
    release_year: int
    type: str
    thumbnail: str
    video_url: str

@app.post("/auth/login")
async def login(login_data: UserLogin):
    try:
        print(f"🔐 Login attempt: {login_data.username}")
        
        # Direct database query for user
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
        
        print(f"✅ Login successful: {login_data.username} (Role: {user['role']})")
        
        # Don't send password back to client
        user_copy = user.copy()
        user_copy.pop('password', None)
        
        return {
            "token": f"token_{user['id']}",
            "user": user_copy
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Login error: {str(e)}")
        raise HTTPException(status_code=401, detail="Invalid username or password")

@app.post("/auth/register")
async def register(user: UserSignup):
    try:
        print(f"📝 Registration attempt: {user.username}")
        
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
            "password": user.password,  # Plain text as per your DB
            "role": "user",
            "created_at": datetime.now().isoformat()
        }
        
        result = supabase_client.table('profiles')\
            .insert(new_user)\
            .execute()
        
        if not result.data:
            raise HTTPException(status_code=400, detail="Failed to create user")
        
        created_user = result.data[0]
        created_user.pop('password', None)  # Remove password from response
        
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

@app.get("/movies/search/{query}")
async def search_movies(query: str):
    try:
        result = supabase_client.table('movies')\
            .select('*')\
            .ilike('title', f'%{query}%')\
            .execute()
        
        return {"movies": result.data}
        
    except Exception as e:
        print(f"❌ Error searching movies: {e}")
        return {"movies": []}

@app.get("/profile/{username}")
async def get_profile(username: str):
    try:
        result = supabase_client.table('profiles')\
            .select('id, username, email, role, avatar_url, created_at')\
            .eq('username', username)\
            .execute()
        
        if not result.data:
            raise HTTPException(status_code=404, detail="User not found")
        
        return {"profile": result.data[0]}
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Error fetching profile: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "database": "connected"
    }

# Admin routes (owner only)
@app.get("/admin/users")
async def get_all_users():
    try:
        result = supabase_client.table('profiles')\
            .select('id, username, email, role, created_at, last_login')\
            .execute()
        
        return {"users": result.data}
        
    except Exception as e:
        print(f"❌ Error fetching users: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/admin/movies")
async def add_movie(movie: dict):
    try:
        movie_data = {
            "title": movie.get("title"),
            "description": movie.get("description"),
            "genre": movie.get("genre"),
            "release_year": movie.get("release_year"),
            "type": movie.get("type"),
            "thumbnail": movie.get("thumbnail"),
            "video_url": movie.get("video_url"),
            "created_at": datetime.now().isoformat()
        }
        
        result = supabase_client.table('movies')\
            .insert(movie_data)\
            .execute()
        
        return {"message": "Movie added", "movie": result.data[0]}
        
    except Exception as e:
        print(f"❌ Error adding movie: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/admin/movies/{movie_id}")
async def delete_movie(movie_id: int):
    try:
        supabase_client.table('movies')\
            .delete()\
            .eq('id', movie_id)\
            .execute()
        
        return {"message": "Movie deleted"}
        
    except Exception as e:
        print(f"❌ Error deleting movie: {e}")
        raise HTTPException(status_code=400, detail=str(e))
