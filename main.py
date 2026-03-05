from fastapi import FastAPI, HTTPException, Depends, UploadFile, File, Form, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator
from typing import Optional, List, Dict, Any
import supabase
import os
from datetime import datetime, timedelta
import uuid
import json
import hashlib
import hmac
import re
from email_validator import validate_email, EmailNotValidError
import asyncio
from collections import defaultdict

app = FastAPI(title="Xstream API", version="1.0.0")

# CORS setup with more specific origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, replace with specific origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# Supabase setup
supabase_url = os.getenv("SUPABASE_URL", "your_supabase_url")
supabase_key = os.getenv("SUPABASE_KEY", "your_supabase_key")
supabase_client = supabase.create_client(supabase_url, supabase_key)
security = HTTPBearer()

# Owner credentials
OWNER_USERNAME = os.getenv("USERNAME")
OWNER_PASSWORD = os.getenv("PASSWORD")
JWT_SECRET = os.getenv("JWT_SECRET", "your-secret-key-here")

# Rate limiting
request_counts = defaultdict(list)
RATE_LIMIT = 100  # requests per minute
RATE_LIMIT_WINDOW = 60  # seconds

# Enhanced Models
class UserSignup(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: str = Field(..., max_length=100)
    password: str = Field(..., min_length=6)
    
    @validator('username')
    def validate_username(cls, v):
        if not re.match("^[a-zA-Z0-9_]+$", v):
            raise ValueError('Username must contain only letters, numbers, and underscores')
        return v
    
    @validator('email')
    def validate_email(cls, v):
        try:
            validate_email(v)
        except EmailNotValidError:
            raise ValueError('Invalid email format')
        return v
    
    @validator('password')
    def validate_password(cls, v):
        if not re.search(r"[A-Z]", v):
            raise ValueError('Password must contain at least one uppercase letter')
        if not re.search(r"[0-9]", v):
            raise ValueError('Password must contain at least one number')
        return v

class UserLogin(BaseModel):
    username: str
    password: str

class Movie(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    description: str = Field(..., min_length=10, max_length=2000)
    genre: str
    release_year: int = Field(..., ge=1900, le=datetime.now().year + 5)
    type: str = Field(..., regex="^(Movie|TV Show)$")
    thumbnail: str
    video_url: str
    rating: Optional[float] = Field(None, ge=0, le=10)
    duration: Optional[int] = Field(None, ge=1)  # in minutes
    cast: Optional[List[str]] = []
    director: Optional[str] = None

class MovieUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    description: Optional[str] = Field(None, min_length=10, max_length=2000)
    genre: Optional[str] = None
    release_year: Optional[int] = Field(None, ge=1900, le=datetime.now().year + 5)
    type: Optional[str] = Field(None, regex="^(Movie|TV Show)$")
    thumbnail: Optional[str] = None
    video_url: Optional[str] = None
    rating: Optional[float] = Field(None, ge=0, le=10)
    duration: Optional[int] = Field(None, ge=1)
    cast: Optional[List[str]] = None
    director: Optional[str] = None

class CommunityPost(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1, max_length=5000)
    image_url: Optional[str] = None
    tags: Optional[List[str]] = []

class Comment(BaseModel):
    content: str = Field(..., min_length=1, max_length=1000)
    movie_id: Optional[int] = None
    post_id: Optional[int] = None
    parent_id: Optional[int] = None  # For nested comments

class WatchHistory(BaseModel):
    movie_id: int
    progress: int = Field(..., ge=0)  # progress in seconds
    completed: bool = False

class UserProfileUpdate(BaseModel):
    display_name: Optional[str] = Field(None, min_length=1, max_length=50)
    avatar_url: Optional[str] = None
    bio: Optional[str] = Field(None, max_length=500)
    preferences: Optional[Dict[str, Any]] = None

# Rate limiting middleware
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_ip = request.client.host
    now = datetime.now()
    
    # Clean old requests
    request_counts[client_ip] = [
        req_time for req_time in request_counts[client_ip]
        if (now - req_time).seconds < RATE_LIMIT_WINDOW
    ]
    
    # Check rate limit
    if len(request_counts[client_ip]) >= RATE_LIMIT:
        return JSONResponse(
            status_code=429,
            content={"detail": "Too many requests. Please try again later."}
        )
    
    request_counts[client_ip].append(now)
    response = await call_next(request)
    return response

# Enhanced Auth helper
async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        # Verify token with Supabase
        user = supabase_client.auth.get_user(token)
        
        # Check if user is banned
        profile = supabase_client.table('profiles').select('*').eq('id', user.user.id).execute()
        if profile.data and profile.data[0].get('role') == 'banned':
            raise HTTPException(status_code=403, detail="User account has been banned")
        
        return user.user
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

async def get_user_profile(user = Depends(get_current_user)):
    profile = supabase_client.table('profiles').select('*').eq('id', user.id).execute()
    if profile.data:
        return profile.data[0]
    # Create profile if doesn't exist
    profile_data = {
        "id": user.id,
        "username": user.email.split('@')[0],
        "email": user.email,
        "role": "user",
        "created_at": datetime.now().isoformat()
    }
    supabase_client.table('profiles').insert(profile_data).execute()
    return profile_data

async def is_owner(user = Depends(get_current_user)):
    profile = await get_user_profile(user)
    if profile.get('role') != 'owner':
        raise HTTPException(status_code=403, detail="Owner access required")
    return profile

async def log_action(action: str, username: str, details: dict = None):
    """Background task to log actions"""
    try:
        supabase_client.table('logs').insert({
            "action": action,
            "username": username,
            "details": json.dumps(details) if details else None,
            "timestamp": datetime.now().isoformat()
        }).execute()
    except Exception as e:
        print(f"Failed to log action: {e}")

# Auth Routes
@app.post("/api/auth/signup")
async def signup(user: UserSignup, background_tasks: BackgroundTasks):
    try:
        # Check if username already exists
        existing = supabase_client.table('profiles').select('*').eq('username', user.username).execute()
        if existing.data:
            raise HTTPException(status_code=400, detail="Username already taken")
        
        # Check if email exists
        existing_email = supabase_client.table('profiles').select('*').eq('email', user.email).execute()
        if existing_email.data:
            raise HTTPException(status_code=400, detail="Email already registered")
        
        # Check if owner
        role = 'owner' if user.username == OWNER_USERNAME and user.password == OWNER_PASSWORD else 'user'
        
        # Sign up with Supabase
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
            "created_at": datetime.now().isoformat(),
            "display_name": user.username,
            "avatar_url": f"https://ui-avatars.com/api/?name={user.username}&background=6c5ce7&color=fff&size=128",
            "bio": "",
            "preferences": {
                "notifications": True,
                "language": "en",
                "autoplay": True
            }
        }
        
        supabase_client.table('profiles').insert(profile_data).execute()
        
        # Log action in background
        background_tasks.add_task(log_action, "User Signup", user.username, {"role": role})
        
        # Create welcome notification
        supabase_client.table('notifications').insert({
            "user_id": auth_response.user.id,
            "title": "Welcome to Xstream!",
            "message": "Thanks for joining! Start exploring our vast collection of movies and TV shows.",
            "type": "welcome",
            "created_at": datetime.now().isoformat()
        }).execute()
        
        return {
            "message": "Signup successful",
            "role": role,
            "user": profile_data
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/auth/login")
async def login(login_data: UserLogin, background_tasks: BackgroundTasks):
    try:
        # Get user's email from profiles
        profile = supabase_client.table('profiles').select('*').eq('username', login_data.username).execute()
        
        if not profile.data:
            raise HTTPException(status_code=401, detail="Invalid username or password")
        
        # Check if user is banned
        if profile.data[0].get('role') == 'banned':
            raise HTTPException(status_code=403, detail="Your account has been banned")
        
        # Login with Supabase
        auth_response = supabase_client.auth.sign_in_with_password({
            "email": profile.data[0]['email'],
            "password": login_data.password
        })
        
        # Update last login
        supabase_client.table('profiles').update({
            "last_login": datetime.now().isoformat()
        }).eq('id', profile.data[0]['id']).execute()
        
        # Log action
        background_tasks.add_task(log_action, "User Login", login_data.username)
        
        return {
            "token": auth_response.session.access_token,
            "user": profile.data[0],
            "expires_in": 3600  # 1 hour
        }
    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid username or password")

@app.post("/api/auth/refresh")
async def refresh_token(user = Depends(get_current_user)):
    try:
        supabase_client.auth.refresh_session()
        return {"message": "Token refreshed successfully"}
    except Exception as e:
        raise HTTPException(status_code=401, detail="Failed to refresh token")

@app.post("/api/auth/logout")
async def logout(user = Depends(get_current_user), background_tasks: BackgroundTasks = None):
    try:
        supabase_client.auth.sign_out()
        if background_tasks and user:
            profile = await get_user_profile(user)
            background_tasks.add_task(log_action, "User Logout", profile.get('username'))
        return {"message": "Logged out successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/auth/me")
async def get_current_user_info(user = Depends(get_current_user)):
    try:
        profile = await get_user_profile(user)
        return {"user": profile}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# Enhanced Movie Routes
@app.get("/api/movies")
async def get_movies(
    type: Optional[str] = None,
    genre: Optional[str] = None,
    year: Optional[int] = None,
    sort_by: str = "created_at",
    order: str = "desc",
    limit: int = 50,
    offset: int = 0,
    user = Depends(get_current_user)
):
    try:
        query = supabase_client.table('movies').select('*')
        
        if type:
            query = query.eq('type', type)
        if genre and genre != 'all':
            query = query.contains('genre', genre)
        if year:
            query = query.eq('release_year', year)
        
        # Get total count
        count_query = supabase_client.table('movies').select('*', count='exact')
        if type:
            count_query = count_query.eq('type', type)
        if genre and genre != 'all':
            count_query = count_query.contains('genre', genre)
        if year:
            count_query = count_query.eq('release_year', year)
        
        count_result = count_query.execute()
        total_count = len(count_result.data) if count_result.data else 0
        
        # Apply sorting and pagination
        movies = query.order(sort_by, desc=(order == "desc")).range(offset, offset + limit - 1).execute()
        
        # Get user's watch history for progress
        if user:
            history = supabase_client.table('watch_history').select('*').eq('user_id', user.id).execute()
            history_dict = {item['movie_id']: item for item in history.data}
            
            for movie in movies.data:
                if movie['id'] in history_dict:
                    movie['progress'] = history_dict[movie['id']]['progress']
                    movie['completed'] = history_dict[movie['id']]['completed']
                else:
                    movie['progress'] = 0
                    movie['completed'] = False
        
        return {
            "movies": movies.data,
            "total": total_count,
            "limit": limit,
            "offset": offset
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/movies/search")
async def search_movies(
    q: str,
    type: Optional[str] = None,
    genre: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
):
    try:
        if len(q) < 2:
            return {"movies": [], "total": 0}
        
        query = supabase_client.table('movies').select('*').ilike('title', f'%{q}%')
        
        if type:
            query = query.eq('type', type)
        if genre and genre != 'all':
            query = query.contains('genre', genre)
        
        # Get total count
        count_query = supabase_client.table('movies').select('*', count='exact').ilike('title', f'%{q}%')
        if type:
            count_query = count_query.eq('type', type)
        if genre and genre != 'all':
            count_query = count_query.contains('genre', genre)
        
        count_result = count_query.execute()
        total_count = len(count_result.data) if count_result.data else 0
        
        # Apply pagination
        movies = query.range(offset, offset + limit - 1).execute()
        
        return {
            "movies": movies.data,
            "total": total_count,
            "query": q,
            "limit": limit,
            "offset": offset
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/movies/genres")
async def get_genres():
    try:
        movies = supabase_client.table('movies').select('genre').execute()
        genres = set()
        for movie in movies.data:
            for genre in movie['genre'].split(','):
                genres.add(genre.strip())
        return {"genres": sorted(list(genres))}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/movies/trending")
async def get_trending_movies(limit: int = 10):
    try:
        # Get movies with most views in last 7 days
        week_ago = (datetime.now() - timedelta(days=7)).isoformat()
        
        # This is a simplified version - in production, use a proper analytics system
        movies = supabase_client.table('movies').select('*').order('views', desc=True).limit(limit).execute()
        
        return {"movies": movies.data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/movies/recommended")
async def get_recommended_movies(user = Depends(get_current_user), limit: int = 10):
    try:
        # Get user's watch history
        history = supabase_client.table('watch_history').select('*, movies(*)').eq('user_id', user.id).execute()
        
        if not history.data:
            # Return popular movies if no history
            return await get_trending_movies(limit)
        
        # Get genres from watched movies
        genres = set()
        for item in history.data:
            if item['movies']:
                for genre in item['movies']['genre'].split(','):
                    genres.add(genre.strip())
        
        # Find similar movies
        recommended = []
        for genre in genres:
            similar = supabase_client.table('movies').select('*').contains('genre', genre).limit(5).execute()
            recommended.extend(similar.data)
        
        # Remove duplicates and already watched
        watched_ids = [item['movie_id'] for item in history.data]
        recommended = [m for m in recommended if m['id'] not in watched_ids]
        
        # Sort by rating and limit
        recommended.sort(key=lambda x: x.get('rating', 0), reverse=True)
        
        return {"movies": recommended[:limit]}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/movies/{movie_id}")
async def get_movie(movie_id: int, user = Depends(get_current_user), background_tasks: BackgroundTasks = None):
    try:
        # Get movie details
        movie = supabase_client.table('movies').select('*').eq('id', movie_id).execute()
        if not movie.data:
            raise HTTPException(status_code=404, detail="Movie not found")
        
        # Increment view count
        supabase_client.table('movies').update({
            "views": movie.data[0].get('views', 0) + 1
        }).eq('id', movie_id).execute()
        
        # Get comments with replies
        comments = supabase_client.table('comments').select('*, profiles(username, avatar_url)').eq('movie_id', movie_id).is_('parent_id', 'null').order('created_at', desc=True).execute()
        
        # Get replies for each comment
        for comment in comments.data:
            replies = supabase_client.table('comments').select('*, profiles(username, avatar_url)').eq('parent_id', comment['id']).order('created_at', asc=True).execute()
            comment['replies'] = replies.data
        
        # Get user's watch progress
        progress = None
        if user:
            watch = supabase_client.table('watch_history').select('*').eq('user_id', user.id).eq('movie_id', movie_id).execute()
            if watch.data:
                progress = watch.data[0]
        
        # Log view
        if background_tasks and user:
            profile = await get_user_profile(user)
            background_tasks.add_task(log_action, "Movie Viewed", profile.get('username'), {"movie_id": movie_id, "title": movie.data[0]['title']})
        
        # Get similar movies
        similar = supabase_client.table('movies').select('*').contains('genre', movie.data[0]['genre'].split(',')[0]).neq('id', movie_id).limit(6).execute()
        
        return {
            "movie": movie.data[0],
            "comments": comments.data,
            "progress": progress,
            "similar": similar.data if similar.data else []
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/movies/{movie_id}/progress")
async def update_watch_progress(
    movie_id: int,
    progress: WatchHistory,
    user = Depends(get_current_user)
):
    try:
        # Check if movie exists
        movie = supabase_client.table('movies').select('*').eq('id', movie_id).execute()
        if not movie.data:
            raise HTTPException(status_code=404, detail="Movie not found")
        
        # Update or insert watch history
        existing = supabase_client.table('watch_history').select('*').eq('user_id', user.id).eq('movie_id', movie_id).execute()
        
        if existing.data:
            supabase_client.table('watch_history').update({
                "progress": progress.progress,
                "completed": progress.completed,
                "last_watched": datetime.now().isoformat()
            }).eq('id', existing.data[0]['id']).execute()
        else:
            supabase_client.table('watch_history').insert({
                "user_id": user.id,
                "movie_id": movie_id,
                "progress": progress.progress,
                "completed": progress.completed,
                "last_watched": datetime.now().isoformat()
            }).execute()
        
        return {"message": "Progress updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/user/history")
async def get_watch_history(user = Depends(get_current_user), limit: int = 50, offset: int = 0):
    try:
        history = supabase_client.table('watch_history').select('*, movies(*)').eq('user_id', user.id).order('last_watched', desc=True).range(offset, offset + limit - 1).execute()
        
        return {
            "history": history.data,
            "total": len(history.data),
            "limit": limit,
            "offset": offset
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/movies/{movie_id}/rate")
async def rate_movie(movie_id: int, rating: float = Field(..., ge=0, le=10), user = Depends(get_current_user)):
    try:
        # Check if movie exists
        movie = supabase_client.table('movies').select('*').eq('id', movie_id).execute()
        if not movie.data:
            raise HTTPException(status_code=404, detail="Movie not found")
        
        # Add or update rating
        existing = supabase_client.table('ratings').select('*').eq('user_id', user.id).eq('movie_id', movie_id).execute()
        
        if existing.data:
            supabase_client.table('ratings').update({
                "rating": rating,
                "updated_at": datetime.now().isoformat()
            }).eq('id', existing.data[0]['id']).execute()
        else:
            supabase_client.table('ratings').insert({
                "user_id": user.id,
                "movie_id": movie_id,
                "rating": rating,
                "created_at": datetime.now().isoformat()
            }).execute()
        
        # Update movie average rating
        ratings = supabase_client.table('ratings').select('rating').eq('movie_id', movie_id).execute()
        if ratings.data:
            avg_rating = sum(r['rating'] for r in ratings.data) / len(ratings.data)
            supabase_client.table('movies').update({
                "rating": round(avg_rating, 1)
            }).eq('id', movie_id).execute()
        
        return {"message": "Rating added successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/user/watchlist")
async def get_watchlist(user = Depends(get_current_user)):
    try:
        watchlist = supabase_client.table('watchlist').select('*, movies(*)').eq('user_id', user.id).execute()
        return {"watchlist": [item['movies'] for item in watchlist.data]}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/user/watchlist/{movie_id}")
async def add_to_watchlist(movie_id: int, user = Depends(get_current_user)):
    try:
        # Check if already in watchlist
        existing = supabase_client.table('watchlist').select('*').eq('user_id', user.id).eq('movie_id', movie_id).execute()
        
        if existing.data:
            raise HTTPException(status_code=400, detail="Movie already in watchlist")
        
        supabase_client.table('watchlist').insert({
            "user_id": user.id,
            "movie_id": movie_id,
            "added_at": datetime.now().isoformat()
        }).execute()
        
        return {"message": "Added to watchlist"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/user/watchlist/{movie_id}")
async def remove_from_watchlist(movie_id: int, user = Depends(get_current_user)):
    try:
        supabase_client.table('watchlist').delete().eq('user_id', user.id).eq('movie_id', movie_id).execute()
        return {"message": "Removed from watchlist"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# Enhanced Community Routes
@app.get("/api/community/posts")
async def get_posts(
    sort_by: str = "created_at",
    order: str = "desc",
    limit: int = 20,
    offset: int = 0,
    tag: Optional[str] = None
):
    try:
        query = supabase_client.table('community_posts').select('*, profiles!inner(username, avatar_url)')
        
        if tag:
            query = query.contains('tags', tag)
        
        # Get total count
        count_query = supabase_client.table('community_posts').select('*', count='exact')
        if tag:
            count_query = count_query.contains('tags', tag)
        
        count_result = count_query.execute()
        total_count = len(count_result.data) if count_result.data else 0
        
        # Get posts with likes and comments count
        posts = query.order(sort_by, desc=(order == "desc")).range(offset, offset + limit - 1).execute()
        
        for post in posts.data:
            # Get comments count
            comments = supabase_client.table('comments').select('*', count='exact').eq('post_id', post['id']).execute()
            post['comments_count'] = len(comments.data) if comments.data else 0
            
            # Get likes count
            likes = supabase_client.table('likes').select('*', count='exact').eq('post_id', post['id']).execute()
            post['likes_count'] = len(likes.data) if likes.data else 0
        
        return {
            "posts": posts.data,
            "total": total_count,
            "limit": limit,
            "offset": offset
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/community/posts")
async def create_post(
    post: CommunityPost,
    user = Depends(get_current_user),
    profile = Depends(get_user_profile),
    background_tasks: BackgroundTasks = None
):
    try:
        post_data = {
            "user_id": user.id,
            "username": profile['username'],
            "avatar_url": profile.get('avatar_url'),
            "title": post.title,
            "content": post.content,
            "image_url": post.image_url,
            "tags": post.tags,
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat()
        }
        
        result = supabase_client.table('community_posts').insert(post_data).execute()
        
        # Log action
        if background_tasks:
            background_tasks.add_task(log_action, "Post Created", profile['username'], {"post_id": result.data[0]['id']})
        
        return {"message": "Post created successfully", "post": result.data[0]}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/community/posts/{post_id}")
async def get_post(post_id: int):
    try:
        post = supabase_client.table('community_posts').select('*, profiles!inner(username, avatar_url)').eq('id', post_id).execute()
        if not post.data:
            raise HTTPException(status_code=404, detail="Post not found")
        
        # Get comments with replies
        comments = supabase_client.table('comments').select('*, profiles(username, avatar_url)').eq('post_id', post_id).is_('parent_id', 'null').order('created_at', desc=True).execute()
        
        for comment in comments.data:
            replies = supabase_client.table('comments').select('*, profiles(username, avatar_url)').eq('parent_id', comment['id']).order('created_at', asc=True).execute()
            comment['replies'] = replies.data
        
        # Get likes count
        likes = supabase_client.table('likes').select('*', count='exact').eq('post_id', post_id).execute()
        post.data[0]['likes_count'] = len(likes.data) if likes.data else 0
        
        return {
            "post": post.data[0],
            "comments": comments.data
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.put("/api/community/posts/{post_id}")
async def update_post(
    post_id: int,
    post_update: CommunityPost,
    user = Depends(get_current_user),
    profile = Depends(get_user_profile)
):
    try:
        # Check if post exists and user owns it
        post = supabase_client.table('community_posts').select('*').eq('id', post_id).execute()
        if not post.data:
            raise HTTPException(status_code=404, detail="Post not found")
        
        if post.data[0]['user_id'] != user.id and profile.get('role') != 'owner':
            raise HTTPException(status_code=403, detail="You can only edit your own posts")
        
        update_data = {
            "title": post_update.title,
            "content": post_update.content,
            "image_url": post_update.image_url,
            "tags": post_update.tags,
            "updated_at": datetime.now().isoformat()
        }
        
        result = supabase_client.table('community_posts').update(update_data).eq('id', post_id).execute()
        
        return {"message": "Post updated successfully", "post": result.data[0]}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/community/posts/{post_id}")
async def delete_post(
    post_id: int,
    user = Depends(get_current_user),
    profile = Depends(get_user_profile),
    background_tasks: BackgroundTasks = None
):
    try:
        # Check if post exists
        post = supabase_client.table('community_posts').select('*').eq('id', post_id).execute()
        if not post.data:
            raise HTTPException(status_code=404, detail="Post not found")
        
        # Check permissions
        if post.data[0]['user_id'] != user.id and profile.get('role') != 'owner':
            raise HTTPException(status_code=403, detail="You can only delete your own posts")
        
        # Delete post and related data
        supabase_client.table('comments').delete().eq('post_id', post_id).execute()
        supabase_client.table('likes').delete().eq('post_id', post_id).execute()
        supabase_client.table('community_posts').delete().eq('id', post_id).execute()
        
        # Log action
        if background_tasks:
            background_tasks.add_task(log_action, "Post Deleted", profile['username'], {"post_id": post_id})
        
        return {"message": "Post deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/community/posts/{post_id}/like")
async def like_post(post_id: int, user = Depends(get_current_user)):
    try:
        # Check if already liked
        existing = supabase_client.table('likes').select('*').eq('user_id', user.id).eq('post_id', post_id).execute()
        
        if existing.data:
            # Unlike
            supabase_client.table('likes').delete().eq('user_id', user.id).eq('post_id', post_id).execute()
            return {"message": "Post unliked", "liked": False}
        else:
            # Like
            supabase_client.table('likes').insert({
                "user_id": user.id,
                "post_id": post_id,
                "created_at": datetime.now().isoformat()
            }).execute()
            return {"message": "Post liked", "liked": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# Enhanced Comment Routes
@app.post("/api/comments")
async def add_comment(
    comment: Comment,
    user = Depends(get_current_user),
    profile = Depends(get_user_profile),
    background_tasks: BackgroundTasks = None
):
    try:
        # Validate that either movie_id or post_id is provided
        if not comment.movie_id and not comment.post_id:
            raise HTTPException(status_code=400, detail="Either movie_id or post_id must be provided")
        
        comment_data = {
            "user_id": user.id,
            "username": profile['username'],
            "avatar_url": profile.get('avatar_url'),
            "content": comment.content,
            "movie_id": comment.movie_id,
            "post_id": comment.post_id,
            "parent_id": comment.parent_id,
            "created_at": datetime.now().isoformat()
        }
        
        result = supabase_client.table('comments').insert(comment_data).execute()
        
        # Create notification for post owner
        if comment.post_id:
            post = supabase_client.table('community_posts').select('user_id').eq('id', comment.post_id).execute()
            if post.data and post.data[0]['user_id'] != user.id:
                supabase_client.table('notifications').insert({
                    "user_id": post.data[0]['user_id'],
                    "title": "New Comment",
                    "message": f"{profile['username']} commented on your post",
                    "type": "comment",
                    "reference_id": comment.post_id,
                    "created_at": datetime.now().isoformat()
                }).execute()
        
        # Log action
        if background_tasks:
            background_tasks.add_task(log_action, "Comment Added", profile['username'])
        
        return {"message": "Comment added successfully", "comment": result.data[0]}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/comments/{comment_id}")
async def delete_comment(
    comment_id: int,
    user = Depends(get_current_user),
    profile = Depends(get_user_profile)
):
    try:
        # Check if comment exists
        comment = supabase_client.table('comments').select('*').eq('id', comment_id).execute()
        if not comment.data:
            raise HTTPException(status_code=404, detail="Comment not found")
        
        # Check permissions
        if comment.data[0]['user_id'] != user.id and profile.get('role') != 'owner':
            raise HTTPException(status_code=403, detail="You can only delete your own comments")
        
        # Delete replies first
        supabase_client.table('comments').delete().eq('parent_id', comment_id).execute()
        supabase_client.table('comments').delete().eq('id', comment_id).execute()
        
        return {"message": "Comment deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# User Profile Routes
@app.get("/api/user/profile")
async def get_my_profile(user = Depends(get_current_user), profile = Depends(get_user_profile)):
    try:
        # Get additional stats
        posts_count = supabase_client.table('community_posts').select('*', count='exact').eq('user_id', user.id).execute()
        comments_count = supabase_client.table('comments').select('*', count='exact').eq('user_id', user.id).execute()
        watchlist_count = supabase_client.table('watchlist').select('*', count='exact').eq('user_id', user.id).execute()
        
        profile_data = profile.copy()
        profile_data['stats'] = {
            "posts": len(posts_count.data) if posts_count.data else 0,
            "comments": len(comments_count.data) if comments_count.data else 0,
            "watchlist": len(watchlist_count.data) if watchlist_count.data else 0
        }
        
        return {"profile": profile_data}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.put("/api/user/profile")
async def update_profile(
    profile_update: UserProfileUpdate,
    user = Depends(get_current_user),
    background_tasks: BackgroundTasks = None
):
    try:
        update_data = {}
        if profile_update.display_name:
            update_data['display_name'] = profile_update.display_name
        if profile_update.avatar_url:
            update_data['avatar_url'] = profile_update.avatar_url
        if profile_update.bio is not None:
            update_data['bio'] = profile_update.bio
        if profile_update.preferences:
            update_data['preferences'] = json.dumps(profile_update.preferences)
        
        update_data['updated_at'] = datetime.now().isoformat()
        
        supabase_client.table('profiles').update(update_data).eq('id', user.id).execute()
        
        # Log action
        if background_tasks:
            profile = await get_user_profile(user)
            background_tasks.add_task(log_action, "Profile Updated", profile.get('username'))
        
        return {"message": "Profile updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/user/notifications")
async def get_notifications(
    user = Depends(get_current_user),
    unread_only: bool = False,
    limit: int = 50,
    offset: int = 0
):
    try:
        query = supabase_client.table('notifications').select('*').eq('user_id', user.id)
        
        if unread_only:
            query = query.eq('read', False)
        
        notifications = query.order('created_at', desc=True).range(offset, offset + limit - 1).execute()
        
        return {
            "notifications": notifications.data,
            "total": len(notifications.data),
            "limit": limit,
            "offset": offset
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/user/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: int, user = Depends(get_current_user)):
    try:
        supabase_client.table('notifications').update({
            "read": True,
            "read_at": datetime.now().isoformat()
        }).eq('id', notification_id).eq('user_id', user.id).execute()
        
        return {"message": "Notification marked as read"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/user/notifications/read-all")
async def mark_all_notifications_read(user = Depends(get_current_user)):
    try:
        supabase_client.table('notifications').update({
            "read": True,
            "read_at": datetime.now().isoformat()
        }).eq('user_id', user.id).eq('read', False).execute()
        
        return {"message": "All notifications marked as read"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# Enhanced Admin Routes
@app.get("/api/admin/logs")
async def get_logs(
    owner = Depends(is_owner),
    action: Optional[str] = None,
    username: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 100,
    offset: int = 0
):
    try:
        query = supabase_client.table('logs').select('*')
        
        if action:
            query = query.ilike('action', f'%{action}%')
        if username:
            query = query.eq('username', username)
        if start_date:
            query = query.gte('timestamp', start_date)
        if end_date:
            query = query.lte('timestamp', end_date)
        
        logs = query.order('timestamp', desc=True).range(offset, offset + limit - 1).execute()
        
        return {
            "logs": logs.data,
            "total": len(logs.data),
            "limit": limit,
            "offset": offset
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/admin/users")
async def get_users(
    owner = Depends(is_owner),
    role: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
):
    try:
        query = supabase_client.table('profiles').select('*')
        
        if role:
            query = query.eq('role', role)
        if search:
            query = query.or_(f"username.ilike.%{search}%,email.ilike.%{search}%")
        
        # Get total count
        count_query = supabase_client.table('profiles').select('*', count='exact')
        if role:
            count_query = count_query.eq('role', role)
        if search:
            count_query = count_query.or_(f"username.ilike.%{search}%,email.ilike.%{search}%")
        
        count_result = count_query.execute()
        total_count = len(count_result.data) if count_result.data else 0
        
        # Get users with stats
        users = query.range(offset, offset + limit - 1).execute()
        
        for user in users.data:
            # Get user stats
            posts = supabase_client.table('community_posts').select('*', count='exact').eq('user_id', user['id']).execute()
            comments = supabase_client.table('comments').select('*', count='exact').eq('user_id', user['id']).execute()
            watchlist = supabase_client.table('watchlist').select('*', count='exact').eq('user_id', user['id']).execute()
            
            user['stats'] = {
                "posts": len(posts.data) if posts.data else 0,
                "comments": len(comments.data) if comments.data else 0,
                "watchlist": len(watchlist.data) if watchlist.data else 0
            }
        
        return {
            "users": users.data,
            "total": total_count,
            "limit": limit,
            "offset": offset
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/admin/users/{user_id}/toggle-role")
async def toggle_user_role(user_id: str, owner = Depends(is_owner)):
    try:
        user = supabase_client.table('profiles').select('role').eq('id', user_id).execute()
        if not user.data:
            raise HTTPException(status_code=404, detail="User not found")
        
        new_role = 'banned' if user.data[0]['role'] != 'banned' else 'user'
        
        supabase_client.table('profiles').update({
            "role": new_role
        }).eq('id', user_id).execute()
        
        # Create notification for user
        supabase_client.table('notifications').insert({
            "user_id": user_id,
            "title": "Account Status Updated",
            "message": f"Your account role has been changed to: {new_role}",
            "type": "account",
            "created_at": datetime.now().isoformat()
        }).execute()
        
        return {"message": f"User role updated to {new_role}"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/admin/users/{user_id}")
async def delete_user(user_id: str, owner = Depends(is_owner)):
    try:
        # Don't allow deleting owner
        user = supabase_client.table('profiles').select('role').eq('id', user_id).execute()
        if user.data and user.data[0]['role'] == 'owner':
            raise HTTPException(status_code=403, detail="Cannot delete owner account")
        
        # Delete user data
        supabase_client.table('comments').delete().eq('user_id', user_id).execute()
        supabase_client.table('likes').delete().eq('user_id', user_id).execute()
        supabase_client.table('watch_history').delete().eq('user_id', user_id).execute()
        supabase_client.table('watchlist').delete().eq('user_id', user_id).execute()
        supabase_client.table('community_posts').delete().eq('user_id', user_id).execute()
        supabase_client.table('profiles').delete().eq('id', user_id).execute()
        
        # Note: This doesn't delete the auth user - that needs to be done in Supabase dashboard
        
        return {"message": "User deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/admin/stats")
async def get_detailed_stats(owner = Depends(is_owner)):
    try:
        # Basic counts
        movies = supabase_client.table('movies').select('*', count='exact').execute()
        users = supabase_client.table('profiles').select('*', count='exact').execute()
        posts = supabase_client.table('community_posts').select('*', count='exact').execute()
        comments = supabase_client.table('comments').select('*', count='exact').execute()
        
        # Recent activity (last 24 hours)
        yesterday = (datetime.now() - timedelta(days=1)).isoformat()
        recent_logins = supabase_client.table('profiles').select('*', count='exact').gte('last_login', yesterday).execute()
        recent_posts = supabase_client.table('community_posts').select('*', count='exact').gte('created_at', yesterday).execute()
        
        # Genre distribution
        genre_stats = {}
        for movie in movies.data:
            for genre in movie['genre'].split(','):
                genre = genre.strip()
                genre_stats[genre] = genre_stats.get(genre, 0) + 1
        
        # User growth (last 30 days)
        thirty_days_ago = (datetime.now() - timedelta(days=30)).isoformat()
        new_users = supabase_client.table('profiles').select('*', count='exact').gte('created_at', thirty_days_ago).execute()
        
        return {
            "overview": {
                "movies": len(movies.data) if movies.data else 0,
                "users": len(users.data) if users.data else 0,
                "posts": len(posts.data) if posts.data else 0,
                "comments": len(comments.data) if comments.data else 0
            },
            "recent_activity": {
                "logins_24h": len(recent_logins.data) if recent_logins.data else 0,
                "posts_24h": len(recent_posts.data) if recent_posts.data else 0
            },
            "genre_distribution": genre_stats,
            "user_growth_30d": len(new_users.data) if new_users.data else 0
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/admin/movies")
async def add_movie_admin(
    movie: Movie,
    owner = Depends(is_owner),
    background_tasks: BackgroundTasks = None
):
    try:
        movie_data = movie.dict()
        movie_data["created_at"] = datetime.now().isoformat()
        movie_data["updated_at"] = datetime.now().isoformat()
        movie_data["views"] = 0
        
        result = supabase_client.table('movies').insert(movie_data).execute()
        
        # Log action
        if background_tasks:
            background_tasks.add_task(log_action, "Movie Added", owner['username'], {"title": movie.title})
        
        return {"message": "Movie added successfully", "movie": result.data[0]}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.put("/api/admin/movies/{movie_id}")
async def update_movie_admin(
    movie_id: int,
    movie_update: MovieUpdate,
    owner = Depends(is_owner),
    background_tasks: BackgroundTasks = None
):
    try:
        # Check if movie exists
        existing = supabase_client.table('movies').select('*').eq('id', movie_id).execute()
        if not existing.data:
            raise HTTPException(status_code=404, detail="Movie not found")
        
        # Update only provided fields
        update_data = {k: v for k, v in movie_update.dict().items() if v is not None}
        update_data["updated_at"] = datetime.now().isoformat()
        
        result = supabase_client.table('movies').update(update_data).eq('id', movie_id).execute()
        
        # Log action
        if background_tasks:
            background_tasks.add_task(log_action, "Movie Updated", owner['username'], {"movie_id": movie_id})
        
        return {"message": "Movie updated successfully", "movie": result.data[0]}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/admin/movies/{movie_id}")
async def delete_movie_admin(
    movie_id: int,
    owner = Depends(is_owner),
    background_tasks: BackgroundTasks = None
):
    try:
        # Get movie title for log
        movie = supabase_client.table('movies').select('title').eq('id', movie_id).execute()
        if not movie.data:
            raise HTTPException(status_code=404, detail="Movie not found")
        
        # Delete related data
        supabase_client.table('comments').delete().eq('movie_id', movie_id).execute()
        supabase_client.table('ratings').delete().eq('movie_id', movie_id).execute()
        supabase_client.table('watch_history').delete().eq('movie_id', movie_id).execute()
        supabase_client.table('watchlist').delete().eq('movie_id', movie_id).execute()
        supabase_client.table('movies').delete().eq('id', movie_id).execute()
        
        # Log action
        if background_tasks:
            background_tasks.add_task(log_action, "Movie Deleted", owner['username'], {"title": movie.data[0]['title']})
        
        return {"message": "Movie deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# Health check
@app.get("/api/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "version": "1.0.0"
    }

@app.on_event("startup")
async def startup_event():
    print("✨ Xstream API started successfully")
    print(f"📅 Server time: {datetime.now().isoformat()}")
    print("🚀 Ready to handle requests")
