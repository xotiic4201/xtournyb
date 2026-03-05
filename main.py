from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from typing import Optional, List
import supabase
import os
from datetime import datetime
import uuid
import json

app = FastAPI()

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Supabase setup
supabase_url = os.getenv("SUPABASE_URL", "your_supabase_url")
supabase_key = os.getenv("SUPABASE_KEY", "your_supabase_key")
supabase_client = supabase.create_client(supabase_url, supabase_key)
security = HTTPBearer()

# Owner credentials
OWNER_USERNAME = os.getenv("USERNAME")
OWNER_PASSWORD = os.getenv("PASSWORD")

# Models
class UserSignup(BaseModel):
    username: str
    email: str
    password: str

class UserLogin(BaseModel):
    username: str
    password: str

class Movie(BaseModel):
    title: str
    description: str
    genre: str
    release_year: int
    type: str
    thumbnail: str
    video_url: str

class CommunityPost(BaseModel):
    title: str
    content: str
    image_url: Optional[str] = None

class Comment(BaseModel):
    content: str
    movie_id: Optional[int] = None
    post_id: Optional[int] = None

# Auth helper
async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        user = supabase_client.auth.get_user(token)
        return user.user
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid token")

async def get_user_profile(user = Depends(get_current_user)):
    profile = supabase_client.table('profiles').select('*').eq('id', user.id).execute()
    if profile.data:
        return profile.data[0]
    return None

async def is_owner(user = Depends(get_current_user)):
    profile = supabase_client.table('profiles').select('*').eq('id', user.id).execute()
    if not profile.data or profile.data[0]['role'] != 'owner':
        raise HTTPException(status_code=403, detail="Owner access required")
    return profile.data[0]

# Auth Routes
@app.post("/api/auth/signup")
async def signup(user: UserSignup):
    try:
        # Check if username already exists
        existing = supabase_client.table('profiles').select('*').eq('username', user.username).execute()
        if existing.data:
            raise HTTPException(status_code=400, detail="Username already taken")
        
        # Check if owner
        role = 'owner' if user.username == OWNER_USERNAME and user.password == OWNER_PASSWORD else 'user'
        
        # Sign up with Supabase using email
        auth_response = supabase_client.auth.sign_up({
            "email": user.email,
            "password": user.password
        })
        
        if not auth_response.user:
            raise HTTPException(status_code=400, detail="Signup failed")
        
        # Create profile
        profile_data = {
            "id": auth_response.user.id,
            "username": user.username,
            "email": user.email,
            "role": role,
            "created_at": datetime.now().isoformat()
        }
        
        supabase_client.table('profiles').insert(profile_data).execute()
        
        # Log action
        supabase_client.table('logs').insert({
            "action": "User Signup",
            "username": user.username,
            "timestamp": datetime.now().isoformat()
        }).execute()
        
        return {"message": "Signup successful", "role": role}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/auth/login")
async def login(login_data: UserLogin):
    try:
        # First get the user's email from profiles using username
        profile = supabase_client.table('profiles').select('*').eq('username', login_data.username).execute()
        
        if not profile.data:
            raise HTTPException(status_code=401, detail="Invalid username or password")
        
        # Login with Supabase using email
        auth_response = supabase_client.auth.sign_in_with_password({
            "email": profile.data[0]['email'],
            "password": login_data.password
        })
        
        return {
            "token": auth_response.session.access_token,
            "user": profile.data[0]
        }
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid username or password")

# Movie Routes
@app.get("/api/movies")
async def get_movies(type: Optional[str] = None, genre: Optional[str] = None):
    try:
        query = supabase_client.table('movies').select('*')
        
        if type:
            query = query.eq('type', type)
        if genre and genre != 'all':
            query = query.contains('genre', genre)
        
        movies = query.execute()
        return {"movies": movies.data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/movies/search")
async def search_movies(q: str):
    try:
        movies = supabase_client.table('movies').select('*').ilike('title', f'%{q}%').execute()
        return {"movies": movies.data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/movies/{movie_id}")
async def get_movie(movie_id: int):
    try:
        movie = supabase_client.table('movies').select('*').eq('id', movie_id).execute()
        if not movie.data:
            raise HTTPException(status_code=404, detail="Movie not found")
        
        # Get comments
        comments = supabase_client.table('comments').select('*, profiles(username)').eq('movie_id', movie_id).execute()
        
        return {
            "movie": movie.data[0],
            "comments": comments.data
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/movies/add")
async def add_movie(movie: Movie, owner=Depends(is_owner)):
    try:
        movie_data = {
            "title": movie.title,
            "description": movie.description,
            "genre": movie.genre,
            "release_year": movie.release_year,
            "type": movie.type,
            "thumbnail": movie.thumbnail,
            "video_url": movie.video_url,
            "created_at": datetime.now().isoformat()
        }
        
        result = supabase_client.table('movies').insert(movie_data).execute()
        
        # Log action
        supabase_client.table('logs').insert({
            "action": f"Movie Added: {movie.title}",
            "username": owner['username'],
            "timestamp": datetime.now().isoformat()
        }).execute()
        
        return {"message": "Movie added successfully", "movie": result.data[0]}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.put("/api/movies/{movie_id}")
async def update_movie(movie_id: int, movie: Movie, owner=Depends(is_owner)):
    try:
        movie_data = movie.dict()
        result = supabase_client.table('movies').update(movie_data).eq('id', movie_id).execute()
        
        # Log action
        supabase_client.table('logs').insert({
            "action": f"Movie Updated: {movie.title}",
            "username": owner['username'],
            "timestamp": datetime.now().isoformat()
        }).execute()
        
        return {"message": "Movie updated successfully", "movie": result.data[0]}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/movies/{movie_id}")
async def delete_movie(movie_id: int, owner=Depends(is_owner)):
    try:
        # Get movie title for log
        movie = supabase_client.table('movies').select('title').eq('id', movie_id).execute()
        
        supabase_client.table('movies').delete().eq('id', movie_id).execute()
        
        # Log action
        supabase_client.table('logs').insert({
            "action": f"Movie Deleted: {movie.data[0]['title'] if movie.data else 'Unknown'}",
            "username": owner['username'],
            "timestamp": datetime.now().isoformat()
        }).execute()
        
        return {"message": "Movie deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# Community Routes
@app.get("/api/community/posts")
async def get_posts():
    try:
        posts = supabase_client.table('community_posts').select('*, profiles!inner(username)').order('created_at', desc=True).execute()
        return {"posts": posts.data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/community/posts")
async def create_post(post: CommunityPost, user=Depends(get_current_user), profile=Depends(get_user_profile)):
    try:
        post_data = {
            "user_id": user.id,
            "username": profile['username'],
            "title": post.title,
            "content": post.content,
            "image_url": post.image_url,
            "created_at": datetime.now().isoformat()
        }
        
        result = supabase_client.table('community_posts').insert(post_data).execute()
        return {"message": "Post created successfully", "post": result.data[0]}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/community/posts/{post_id}")
async def delete_post(post_id: int, owner=Depends(is_owner)):
    try:
        # Get post for log
        post = supabase_client.table('community_posts').select('title').eq('id', post_id).execute()
        
        supabase_client.table('community_posts').delete().eq('id', post_id).execute()
        
        # Log action
        supabase_client.table('logs').insert({
            "action": f"Community Post Deleted: {post.data[0]['title'] if post.data else 'Unknown'}",
            "username": owner['username'],
            "timestamp": datetime.now().isoformat()
        }).execute()
        
        return {"message": "Post deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# Comment Routes
@app.post("/api/comments")
async def add_comment(comment: Comment, user=Depends(get_current_user), profile=Depends(get_user_profile)):
    try:
        comment_data = {
            "user_id": user.id,
            "username": profile['username'],
            "content": comment.content,
            "movie_id": comment.movie_id,
            "post_id": comment.post_id,
            "created_at": datetime.now().isoformat()
        }
        
        result = supabase_client.table('comments').insert(comment_data).execute()
        return {"message": "Comment added successfully", "comment": result.data[0]}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# Admin Routes
@app.get("/api/admin/logs")
async def get_logs(owner=Depends(is_owner)):
    try:
        logs = supabase_client.table('logs').select('*').order('timestamp', desc=True).limit(100).execute()
        return {"logs": logs.data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/admin/users")
async def get_users(owner=Depends(is_owner)):
    try:
        users = supabase_client.table('profiles').select('*').execute()
        return {"users": users.data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/admin/users/{user_id}/ban")
async def ban_user(user_id: str, owner=Depends(is_owner)):
    try:
        supabase_client.table('profiles').update({"role": "banned"}).eq('id', user_id).execute()
        
        # Log action
        supabase_client.table('logs').insert({
            "action": f"User Banned",
            "username": owner['username'],
            "timestamp": datetime.now().isoformat()
        }).execute()
        
        return {"message": "User banned successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/admin/stats")
async def get_stats(owner=Depends(is_owner)):
    try:
        movies_count = supabase_client.table('movies').select('*', count='exact').execute()
        users_count = supabase_client.table('profiles').select('*', count='exact').execute()
        posts_count = supabase_client.table('community_posts').select('*', count='exact').execute()
        
        return {
            "movies": len(movies_count.data) if movies_count.data else 0,
            "users": len(users_count.data) if users_count.data else 0,
            "posts": len(posts_count.data) if posts_count.data else 0
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.on_event("startup")
async def startup_event():
    print("Xstream API started")
