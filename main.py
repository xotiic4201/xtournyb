from fastapi import FastAPI, HTTPException, Depends, Request, BackgroundTasks, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Optional, List
import supabase
import os
import shutil
from datetime import datetime, timedelta
import uuid
import json
import random
import re

app = FastAPI(title="Xstream API", version="1.0.0")

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://xtournyf.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create upload directory
UPLOAD_DIR = "uploads"
if not os.path.exists(UPLOAD_DIR):
    os.makedirs(UPLOAD_DIR)
    os.makedirs(f"{UPLOAD_DIR}/movies")
    os.makedirs(f"{UPLOAD_DIR}/thumbnails")

# Mount uploads directory
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# Supabase setup
supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")
supabase_client = supabase.create_client(supabase_url, supabase_key)

print(f"✅ Supabase connected")

# ============================================
# PYDANTIC MODELS
# ============================================

class UserLogin(BaseModel):
    username: str
    password: str

class UserSignup(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=3)
    email: Optional[str] = None

class UserProfileUpdate(BaseModel):
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None
    bio: Optional[str] = None

class Movie(BaseModel):
    title: str
    description: str
    genre: str
    release_year: int
    type: str
    thumbnail: str
    video_url: str

class MovieUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    genre: Optional[str] = None
    release_year: Optional[int] = None
    type: Optional[str] = None
    thumbnail: Optional[str] = None
    video_url: Optional[str] = None

class ChatMessage(BaseModel):
    content: str = Field(..., min_length=1, max_length=500)
    tags: List[str] = []

class Comment(BaseModel):
    content: str = Field(..., min_length=1, max_length=1000)
    movie_id: int

# ============================================
# AUTH MIDDLEWARE
# ============================================

security = HTTPBearer(auto_error=False)

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Get current user from token"""
    if not credentials:
        return None
    
    token = credentials.credentials
    
    # Check token format
    if token.startswith("user_"):
        user_id = token.replace("user_", "")
        result = supabase_client.table('profiles').select('*').eq('id', user_id).execute()
        if result.data:
            user = result.data[0]
            user.pop('password', None)
            return user
    
    return None

async def get_optional_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Get user or return None"""
    try:
        return await get_current_user(credentials)
    except:
        return None

async def require_owner(user = Depends(get_current_user)):
    """Require owner role"""
    if not user or user.get('role') != 'owner':
        raise HTTPException(status_code=403, detail="Owner access required")
    return user

async def require_user(user = Depends(get_current_user)):
    """Require any authenticated user"""
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user

# ============================================
# AUTH ROUTES
# ============================================

@app.post("/auth/login")
async def login(login_data: UserLogin):
    """Login user - checks Supabase profiles table"""
    try:
        print(f"🔐 Login attempt: {login_data.username}")
        
        # Find user in database
        result = supabase_client.table('profiles')\
            .select('*')\
            .eq('username', login_data.username)\
            .execute()
        
        if not result.data:
            print(f"❌ User not found: {login_data.username}")
            raise HTTPException(status_code=401, detail="Invalid username or password")
        
        user = result.data[0]
        
        # Check password (plain text as per your DB)
        if user['password'] != login_data.password:
            print(f"❌ Password mismatch for: {login_data.username}")
            raise HTTPException(status_code=401, detail="Invalid username or password")
        
        # Update last login
        supabase_client.table('profiles')\
            .update({'last_login': datetime.now().isoformat()})\
            .eq('id', user['id'])\
            .execute()
        
        print(f"✅ Login successful: {login_data.username} (Role: {user['role']})")
        
        # Don't send password back
        user_copy = user.copy()
        user_copy.pop('password', None)
        
        return {
            "token": f"user_{user['id']}",
            "user": user_copy
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Login error: {str(e)}")
        raise HTTPException(status_code=401, detail="Invalid username or password")

@app.post("/auth/register")
async def register(user: UserSignup):
    """Register a new user"""
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
            "password": user.password,
            "role": "user",
            "avatar_url": f"https://ui-avatars.com/api/?name={user.username}&background=ff0000&color=fff&size=128",
            "bio": "",
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

@app.get("/auth/me")
async def get_current_user_info(user = Depends(get_optional_user)):
    """Get current user info"""
    return {"user": user}

@app.post("/auth/logout")
async def logout():
    """Logout user"""
    return {"message": "Logged out successfully"}

# ============================================
# MOVIE ROUTES (WITH UPLOADS)
# ============================================

@app.post("/admin/movies/upload", dependencies=[Depends(require_owner)])
async def upload_movie(
    title: str = Form(...),
    description: str = Form(...),
    genre: str = Form(...),
    release_year: int = Form(...),
    type: str = Form(...),
    thumbnail: UploadFile = File(...),
    video: UploadFile = File(...)
):
    """Upload a movie with thumbnail and video file (owner only)"""
    try:
        # Validate file types
        if not thumbnail.content_type.startswith('image/'):
            raise HTTPException(status_code=400, detail="Thumbnail must be an image")
        
        if not video.content_type.startswith('video/'):
            raise HTTPException(status_code=400, detail="Video must be a video file")
        
        # Generate unique filenames
        thumbnail_filename = f"thumb_{uuid.uuid4()}_{thumbnail.filename}"
        video_filename = f"video_{uuid.uuid4()}_{video.filename}"
        
        thumbnail_path = f"{UPLOAD_DIR}/thumbnails/{thumbnail_filename}"
        video_path = f"{UPLOAD_DIR}/movies/{video_filename}"
        
        # Save files
        with open(thumbnail_path, "wb") as buffer:
            shutil.copyfileobj(thumbnail.file, buffer)
        
        with open(video_path, "wb") as buffer:
            shutil.copyfileobj(video.file, buffer)
        
        # Create URLs
        thumbnail_url = f"/uploads/thumbnails/{thumbnail_filename}"
        video_url = f"/uploads/movies/{video_filename}"
        
        # Insert into database
        movie_data = {
            "title": title,
            "description": description,
            "genre": genre,
            "release_year": release_year,
            "type": type,
            "thumbnail": thumbnail_url,
            "video_url": video_url,
            "views": 0,
            "rating": 0,
            "created_at": datetime.now().isoformat()
        }
        
        result = supabase_client.table('movies').insert(movie_data).execute()
        
        return {
            "message": "Movie uploaded successfully",
            "movie": result.data[0]
        }
        
    except Exception as e:
        print(f"Error uploading movie: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/admin/movies", dependencies=[Depends(require_owner)])
async def add_movie(movie: Movie):
    """Add a movie with URL (owner only)"""
    try:
        movie_data = movie.dict()
        movie_data["views"] = 0
        movie_data["rating"] = 0
        movie_data["created_at"] = datetime.now().isoformat()
        
        result = supabase_client.table('movies').insert(movie_data).execute()
        
        return {"message": "Movie added", "movie": result.data[0]}
    except Exception as e:
        print(f"Error adding movie: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.put("/admin/movies/{movie_id}", dependencies=[Depends(require_owner)])
async def update_movie(movie_id: int, movie: MovieUpdate):
    """Update a movie (owner only)"""
    try:
        # Filter out None values
        update_data = {k: v for k, v in movie.dict().items() if v is not None}
        
        result = supabase_client.table('movies')\
            .update(update_data)\
            .eq('id', movie_id)\
            .execute()
        
        return {"message": "Movie updated", "movie": result.data[0]}
    except Exception as e:
        print(f"Error updating movie: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/admin/movies/{movie_id}", dependencies=[Depends(require_owner)])
async def delete_movie(movie_id: int):
    """Delete a movie (owner only)"""
    try:
        # Get movie to delete files
        movie = supabase_client.table('movies').select('*').eq('id', movie_id).execute()
        if movie.data:
            # Delete video file if it exists in uploads
            video_url = movie.data[0].get('video_url')
            if video_url and video_url.startswith('/uploads/'):
                file_path = f".{video_url}"
                if os.path.exists(file_path):
                    os.remove(file_path)
            
            # Delete thumbnail file
            thumbnail_url = movie.data[0].get('thumbnail')
            if thumbnail_url and thumbnail_url.startswith('/uploads/'):
                file_path = f".{thumbnail_url}"
                if os.path.exists(file_path):
                    os.remove(file_path)
        
        # Delete related data
        supabase_client.table('comments').delete().eq('movie_id', movie_id).execute()
        supabase_client.table('ratings').delete().eq('movie_id', movie_id).execute()
        supabase_client.table('watch_history').delete().eq('movie_id', movie_id).execute()
        supabase_client.table('watchlist').delete().eq('movie_id', movie_id).execute()
        supabase_client.table('movies').delete().eq('id', movie_id).execute()
        
        return {"message": "Movie deleted"}
    except Exception as e:
        print(f"Error deleting movie: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/movies")
async def get_movies(type: Optional[str] = None, genre: Optional[str] = None, limit: int = 50, offset: int = 0):
    """Get all movies with filters"""
    try:
        query = supabase_client.table('movies').select('*')
        
        if type:
            query = query.eq('type', type)
        if genre and genre != 'all':
            query = query.contains('genre', genre)
        
        # Get total count
        count_query = supabase_client.table('movies').select('*', count='exact')
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
            "limit": limit,
            "offset": offset
        }
    except Exception as e:
        print(f"Error fetching movies: {e}")
        return {"movies": [], "total": 0}

@app.get("/movies/search")
async def search_movies(q: str, limit: int = 50):
    """Search movies by title"""
    try:
        if len(q) < 2:
            return {"movies": []}
        
        result = supabase_client.table('movies')\
            .select('*')\
            .ilike('title', f'%{q}%')\
            .limit(limit)\
            .execute()
        
        return {"movies": result.data}
    except Exception as e:
        print(f"Error searching movies: {e}")
        return {"movies": []}

@app.get("/movies/genres")
async def get_genres():
    """Get all unique genres"""
    try:
        result = supabase_client.table('movies').select('genre').execute()
        genres = set()
        for movie in result.data:
            if movie.get('genre'):
                for g in movie['genre'].split(','):
                    genres.add(g.strip())
        return {"genres": sorted(list(genres))}
    except Exception as e:
        print(f"Error fetching genres: {e}")
        return {"genres": []}

@app.get("/movies/trending")
async def get_trending_movies(limit: int = 10):
    """Get trending movies (by views)"""
    try:
        result = supabase_client.table('movies')\
            .select('*')\
            .order('views', desc=True)\
            .limit(limit)\
            .execute()
        return {"movies": result.data}
    except Exception as e:
        print(f"Error fetching trending: {e}")
        return {"movies": []}

@app.get("/movies/{movie_id}")
async def get_movie(movie_id: int):
    """Get movie details by ID"""
    try:
        result = supabase_client.table('movies')\
            .select('*')\
            .eq('id', movie_id)\
            .execute()
        
        if not result.data:
            raise HTTPException(status_code=404, detail="Movie not found")
        
        movie = result.data[0]
        
        # Increment view count
        supabase_client.table('movies')\
            .update({"views": movie.get('views', 0) + 1})\
            .eq('id', movie_id)\
            .execute()
        
        # Get comments for this movie
        comments = supabase_client.table('comments')\
            .select('*, profiles!inner(username, avatar_url)')\
            .eq('movie_id', movie_id)\
            .order('created_at', desc=True)\
            .execute()
        
        # Format comments
        formatted_comments = []
        for comment in comments.data:
            formatted_comments.append({
                "id": comment['id'],
                "user_id": comment['user_id'],
                "username": comment['profiles']['username'],
                "avatar_url": comment['profiles'].get('avatar_url'),
                "content": comment['content'],
                "created_at": comment['created_at']
            })
        
        # Get similar movies (by genre)
        similar = []
        if movie.get('genre'):
            first_genre = movie['genre'].split(',')[0].strip()
            similar_result = supabase_client.table('movies')\
                .select('*')\
                .contains('genre', first_genre)\
                .neq('id', movie_id)\
                .limit(6)\
                .execute()
            similar = similar_result.data
        
        return {
            "movie": movie,
            "comments": formatted_comments,
            "similar": similar
        }
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error fetching movie: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/movies/{movie_id}/rate")
async def rate_movie(movie_id: int, rating: float, user = Depends(require_user)):
    """Rate a movie"""
    try:
        # Validate rating
        if rating < 0 or rating > 10:
            raise HTTPException(status_code=400, detail="Rating must be between 0 and 10")
        
        # Check if already rated
        existing = supabase_client.table('ratings')\
            .select('*')\
            .eq('user_id', user['id'])\
            .eq('movie_id', movie_id)\
            .execute()
        
        if existing.data:
            # Update
            supabase_client.table('ratings')\
                .update({"rating": rating, "updated_at": datetime.now().isoformat()})\
                .eq('id', existing.data[0]['id'])\
                .execute()
        else:
            # Insert
            supabase_client.table('ratings')\
                .insert({
                    "user_id": user['id'],
                    "movie_id": movie_id,
                    "rating": rating,
                    "created_at": datetime.now().isoformat()
                })\
                .execute()
        
        # Update movie average rating
        ratings = supabase_client.table('ratings')\
            .select('rating')\
            .eq('movie_id', movie_id)\
            .execute()
        
        if ratings.data:
            avg_rating = sum(r['rating'] for r in ratings.data) / len(ratings.data)
            supabase_client.table('movies')\
                .update({"rating": round(avg_rating, 1)})\
                .eq('id', movie_id)\
                .execute()
        
        return {"message": "Rating added successfully"}
    except Exception as e:
        print(f"Error rating movie: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# ============================================
# GLOBAL CHAT ROOM WITH TAGS
# ============================================

@app.get("/chat/messages")
async def get_chat_messages(limit: int = 50, tag: Optional[str] = None):
    """Get chat messages, optionally filtered by tag"""
    try:
        query = supabase_client.table('chat_messages')\
            .select('*, profiles!inner(username, avatar_url)')\
            .order('created_at', desc=True)
        
        if tag:
            # Get all messages and filter by tag
            all_messages = query.execute()
            filtered = [m for m in all_messages.data if tag in m.get('tags', [])]
            
            messages = []
            for msg in filtered[:limit]:
                messages.append({
                    "id": msg['id'],
                    "user_id": msg['user_id'],
                    "username": msg['profiles']['username'],
                    "avatar_url": msg['profiles'].get('avatar_url'),
                    "content": msg['content'],
                    "tags": msg.get('tags', []),
                    "created_at": msg['created_at']
                })
            
            return {"messages": messages}
        
        result = query.limit(limit).execute()
        
        messages = []
        for msg in result.data:
            messages.append({
                "id": msg['id'],
                "user_id": msg['user_id'],
                "username": msg['profiles']['username'],
                "avatar_url": msg['profiles'].get('avatar_url'),
                "content": msg['content'],
                "tags": msg.get('tags', []),
                "created_at": msg['created_at']
            })
        
        return {"messages": messages}
    except Exception as e:
        print(f"Error fetching chat messages: {e}")
        return {"messages": []}

@app.post("/chat/messages")
async def send_chat_message(message: ChatMessage, user = Depends(require_user)):
    """Send a chat message"""
    try:
        # Generate ID
        message_id = random.randint(100000, 999999)
        
        message_data = {
            "id": message_id,
            "user_id": user['id'],
            "content": message.content,
            "tags": message.tags,
            "created_at": datetime.now().isoformat()
        }
        
        result = supabase_client.table('chat_messages')\
            .insert(message_data)\
            .execute()
        
        return {
            "message": "Message sent",
            "chat_message": {
                "id": result.data[0]['id'],
                "user_id": user['id'],
                "username": user['username'],
                "avatar_url": user.get('avatar_url'),
                "content": result.data[0]['content'],
                "tags": result.data[0].get('tags', []),
                "created_at": result.data[0]['created_at']
            }
        }
    except Exception as e:
        print(f"Error sending chat message: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/chat/tags")
async def get_popular_tags(limit: int = 20):
    """Get popular chat tags"""
    try:
        result = supabase_client.table('chat_messages')\
            .select('tags')\
            .execute()
        
        tag_counts = {}
        for msg in result.data:
            for tag in msg.get('tags', []):
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        
        # Sort by count and return top tags
        sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
        return {"tags": [{"name": t[0], "count": t[1]} for t in sorted_tags[:limit]]}
    except Exception as e:
        print(f"Error fetching tags: {e}")
        return {"tags": []}

@app.delete("/chat/messages/{message_id}")
async def delete_chat_message(message_id: int, user = Depends(require_user)):
    """Delete a chat message (only by author or owner)"""
    try:
        # Check if message exists
        message = supabase_client.table('chat_messages')\
            .select('*')\
            .eq('id', message_id)\
            .execute()
        
        if not message.data:
            raise HTTPException(status_code=404, detail="Message not found")
        
        # Check permissions
        if message.data[0]['user_id'] != user['id'] and user.get('role') != 'owner':
            raise HTTPException(status_code=403, detail="You can only delete your own messages")
        
        supabase_client.table('chat_messages')\
            .delete()\
            .eq('id', message_id)\
            .execute()
        
        return {"message": "Message deleted"}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error deleting message: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# ============================================
# COMMENT ROUTES (FOR MOVIES)
# ============================================

@app.post("/comments")
async def add_comment(comment: Comment, user = Depends(require_user)):
    """Add a comment to a movie"""
    try:
        # Check if movie exists
        movie = supabase_client.table('movies').select('*').eq('id', comment.movie_id).execute()
        if not movie.data:
            raise HTTPException(status_code=404, detail="Movie not found")
        
        # Generate ID
        comment_id = random.randint(10000, 99999)
        
        comment_data = {
            "id": comment_id,
            "user_id": user['id'],
            "username": user['username'],
            "avatar_url": user.get('avatar_url'),
            "content": comment.content,
            "movie_id": comment.movie_id,
            "created_at": datetime.now().isoformat()
        }
        
        result = supabase_client.table('comments')\
            .insert(comment_data)\
            .execute()
        
        return {
            "message": "Comment added successfully",
            "comment": result.data[0]
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error adding comment: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/comments/{comment_id}")
async def delete_comment(comment_id: int, user = Depends(require_user)):
    """Delete a comment (only by author or owner)"""
    try:
        # Check if comment exists
        comment = supabase_client.table('comments')\
            .select('*')\
            .eq('id', comment_id)\
            .execute()
        
        if not comment.data:
            raise HTTPException(status_code=404, detail="Comment not found")
        
        # Check permissions
        if comment.data[0]['user_id'] != user['id'] and user.get('role') != 'owner':
            raise HTTPException(status_code=403, detail="You can only delete your own comments")
        
        supabase_client.table('comments')\
            .delete()\
            .eq('id', comment_id)\
            .execute()
        
        return {"message": "Comment deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error deleting comment: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# ============================================
# USER PROFILE ROUTES
# ============================================

@app.get("/user/profile")
async def get_my_profile(user = Depends(get_optional_user)):
    """Get current user profile"""
    if not user:
        return {"profile": None}
    
    # Get additional stats
    if user.get('role') == 'owner':
        # Get counts from database
        messages_count = supabase_client.table('chat_messages')\
            .select('*', count='exact')\
            .execute()
        users_count = supabase_client.table('profiles')\
            .select('*', count='exact')\
            .execute()
        movies_count = supabase_client.table('movies')\
            .select('*', count='exact')\
            .execute()
        
        user['stats'] = {
            "messages": len(messages_count.data) if messages_count.data else 0,
            "users": len(users_count.data) if users_count.data else 0,
            "movies": len(movies_count.data) if movies_count.data else 0
        }
    else:
        # Get user stats
        messages = supabase_client.table('chat_messages')\
            .select('*', count='exact')\
            .eq('user_id', user['id'])\
            .execute()
        comments = supabase_client.table('comments')\
            .select('*', count='exact')\
            .eq('user_id', user['id'])\
            .execute()
        
        user['stats'] = {
            "messages": len(messages.data) if messages.data else 0,
            "comments": len(comments.data) if comments.data else 0
        }
    
    return {"profile": user}

@app.put("/user/profile")
async def update_profile(update: UserProfileUpdate, user = Depends(require_user)):
    """Update user profile"""
    update_data = {}
    if update.display_name:
        update_data['display_name'] = update.display_name
    if update.avatar_url:
        update_data['avatar_url'] = update.avatar_url
    if update.bio is not None:
        update_data['bio'] = update.bio
    
    update_data['updated_at'] = datetime.now().isoformat()
    
    result = supabase_client.table('profiles')\
        .update(update_data)\
        .eq('id', user['id'])\
        .execute()
    
    updated = result.data[0]
    updated.pop('password', None)
    
    return {"message": "Profile updated", "profile": updated}

@app.get("/user/history")
async def get_watch_history(user = Depends(require_user)):
    """Get user's watch history"""
    result = supabase_client.table('watch_history')\
        .select('*, movies(*)')\
        .eq('user_id', user['id'])\
        .order('last_watched', desc=True)\
        .execute()
    
    history = []
    for item in result.data:
        if item.get('movies'):
            movie = item['movies']
            history.append({
                "id": item['id'],
                "movie_id": item['movie_id'],
                "progress": item['progress'],
                "completed": item['completed'],
                "last_watched": item['last_watched'],
                "movie": movie
            })
    
    return {"history": history}

@app.post("/user/history/{movie_id}")
async def add_to_history(movie_id: int, progress: int = 0, user = Depends(require_user)):
    """Add movie to watch history"""
    # Check if movie exists
    movie = supabase_client.table('movies').select('*').eq('id', movie_id).execute()
    if not movie.data:
        raise HTTPException(status_code=404, detail="Movie not found")
    
    # Check if exists
    existing = supabase_client.table('watch_history')\
        .select('*')\
        .eq('user_id', user['id'])\
        .eq('movie_id', movie_id)\
        .execute()
    
    if existing.data:
        # Update
        supabase_client.table('watch_history')\
            .update({
                "progress": progress,
                "completed": progress >= 90,  # Mark as completed if progress > 90%
                "last_watched": datetime.now().isoformat()
            })\
            .eq('id', existing.data[0]['id'])\
            .execute()
    else:
        # Insert
        supabase_client.table('watch_history')\
            .insert({
                "user_id": user['id'],
                "movie_id": movie_id,
                "progress": progress,
                "completed": progress >= 90,
                "last_watched": datetime.now().isoformat()
            })\
            .execute()
    
    return {"message": "History updated"}

@app.get("/user/watchlist")
async def get_watchlist(user = Depends(require_user)):
    """Get user's watchlist"""
    result = supabase_client.table('watchlist')\
        .select('*, movies(*)')\
        .eq('user_id', user['id'])\
        .execute()
    
    watchlist = []
    for item in result.data:
        if item.get('movies'):
            watchlist.append(item['movies'])
    
    return {"watchlist": watchlist}

@app.post("/user/watchlist/{movie_id}")
async def add_to_watchlist(movie_id: int, user = Depends(require_user)):
    """Add movie to watchlist"""
    # Check if movie exists
    movie = supabase_client.table('movies').select('*').eq('id', movie_id).execute()
    if not movie.data:
        raise HTTPException(status_code=404, detail="Movie not found")
    
    # Check if already in watchlist
    existing = supabase_client.table('watchlist')\
        .select('*')\
        .eq('user_id', user['id'])\
        .eq('movie_id', movie_id)\
        .execute()
    
    if existing.data:
        return {"message": "Already in watchlist"}
    
    supabase_client.table('watchlist')\
        .insert({
            "user_id": user['id'],
            "movie_id": movie_id,
            "added_at": datetime.now().isoformat()
        })\
        .execute()
    
    return {"message": "Added to watchlist"}

@app.delete("/user/watchlist/{movie_id}")
async def remove_from_watchlist(movie_id: int, user = Depends(require_user)):
    """Remove movie from watchlist"""
    supabase_client.table('watchlist')\
        .delete()\
        .eq('user_id', user['id'])\
        .eq('movie_id', movie_id)\
        .execute()
    
    return {"message": "Removed from watchlist"}

# ============================================
# ADMIN ROUTES (OWNER ONLY)
# ============================================

@app.get("/admin/stats")
async def get_admin_stats(owner = Depends(require_owner)):
    """Get platform statistics"""
    try:
        # Get counts
        users = supabase_client.table('profiles').select('*', count='exact').execute()
        movies = supabase_client.table('movies').select('*', count='exact').execute()
        messages = supabase_client.table('chat_messages').select('*', count='exact').execute()
        comments = supabase_client.table('comments').select('*', count='exact').execute()
        
        # Get recent activity (last 24h)
        yesterday = (datetime.now() - timedelta(days=1)).isoformat()
        recent_users = supabase_client.table('profiles')\
            .select('*', count='exact')\
            .gte('created_at', yesterday)\
            .execute()
        recent_messages = supabase_client.table('chat_messages')\
            .select('*', count='exact')\
            .gte('created_at', yesterday)\
            .execute()
        
        # Get storage info
        total_video_size = 0
        video_count = 0
        if os.path.exists(f"{UPLOAD_DIR}/movies"):
            for file in os.listdir(f"{UPLOAD_DIR}/movies"):
                file_path = os.path.join(f"{UPLOAD_DIR}/movies", file)
                if os.path.isfile(file_path):
                    total_video_size += os.path.getsize(file_path)
                    video_count += 1
        
        return {
            "movies": len(movies.data) if movies.data else 0,
            "users": len(users.data) if users.data else 0,
            "messages": len(messages.data) if messages.data else 0,
            "comments": len(comments.data) if comments.data else 0,
            "recent_users": len(recent_users.data) if recent_users.data else 0,
            "recent_messages": len(recent_messages.data) if recent_messages.data else 0,
            "storage_used_gb": round(total_video_size / (1024**3), 2),
            "video_files": video_count
        }
    except Exception as e:
        print(f"Error getting stats: {e}")
        return {
            "movies": 0, "users": 0, "messages": 0, 
            "comments": 0, "recent_users": 0, "recent_messages": 0,
            "storage_used_gb": 0, "video_files": 0
        }

@app.get("/admin/users")
async def get_all_users(owner = Depends(require_owner)):
    """Get all users with details"""
    try:
        result = supabase_client.table('profiles')\
            .select('id, username, email, role, avatar_url, created_at, last_login')\
            .execute()
        
        # Add stats for each user
        for user in result.data:
            messages = supabase_client.table('chat_messages')\
                .select('*', count='exact')\
                .eq('user_id', user['id'])\
                .execute()
            comments = supabase_client.table('comments')\
                .select('*', count='exact')\
                .eq('user_id', user['id'])\
                .execute()
            
            user['stats'] = {
                "messages": len(messages.data) if messages.data else 0,
                "comments": len(comments.data) if comments.data else 0
            }
        
        return {"users": result.data}
    except Exception as e:
        print(f"Error fetching users: {e}")
        return {"users": []}

@app.get("/admin/movies")
async def get_all_movies_admin(owner = Depends(require_owner)):
    """Get all movies with details for admin"""
    try:
        result = supabase_client.table('movies')\
            .select('*')\
            .order('created_at', desc=True)\
            .execute()
        
        return {"movies": result.data}
    except Exception as e:
        print(f"Error fetching movies: {e}")
        return {"movies": []}

@app.get("/admin/chat")
async def get_all_chat_messages(owner = Depends(require_owner), limit: int = 100):
    """Get all chat messages for admin moderation"""
    try:
        result = supabase_client.table('chat_messages')\
            .select('*, profiles!inner(username, avatar_url)')\
            .order('created_at', desc=True)\
            .limit(limit)\
            .execute()
        
        messages = []
        for msg in result.data:
            messages.append({
                "id": msg['id'],
                "user_id": msg['user_id'],
                "username": msg['profiles']['username'],
                "avatar_url": msg['profiles'].get('avatar_url'),
                "content": msg['content'],
                "tags": msg.get('tags', []),
                "created_at": msg['created_at']
            })
        
        return {"messages": messages}
    except Exception as e:
        print(f"Error fetching chat messages: {e}")
        return {"messages": []}

@app.post("/admin/users/{user_id}/toggle-role")
async def toggle_user_role(user_id: str, owner = Depends(require_owner)):
    """Toggle user role (ban/unban)"""
    try:
        # Get current user
        user = supabase_client.table('profiles')\
            .select('role')\
            .eq('id', user_id)\
            .execute()
        
        if not user.data:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Don't allow toggling owner
        if user.data[0]['role'] == 'owner':
            raise HTTPException(status_code=403, detail="Cannot modify owner")
        
        new_role = 'banned' if user.data[0]['role'] != 'banned' else 'user'
        
        supabase_client.table('profiles')\
            .update({"role": new_role})\
            .eq('id', user_id)\
            .execute()
        
        return {"message": f"User role updated to {new_role}"}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error toggling user role: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/admin/chat/{message_id}")
async def admin_delete_chat_message(message_id: int, owner = Depends(require_owner)):
    """Delete any chat message (admin only)"""
    try:
        supabase_client.table('chat_messages')\
            .delete()\
            .eq('id', message_id)\
            .execute()
        
        return {"message": "Message deleted by admin"}
    except Exception as e:
        print(f"Error deleting message: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# ============================================
# HEALTH CHECK
# ============================================

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "database": "connected",
        "uploads": os.path.exists(UPLOAD_DIR),
        "upload_dirs": {
            "movies": os.path.exists(f"{UPLOAD_DIR}/movies"),
            "thumbnails": os.path.exists(f"{UPLOAD_DIR}/thumbnails")
        }
    }
