from fastapi import FastAPI, HTTPException, Depends, Request, BackgroundTasks
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

class CommunityPost(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1, max_length=5000)
    image_url: Optional[str] = None

class Comment(BaseModel):
    content: str = Field(..., min_length=1, max_length=1000)
    movie_id: Optional[int] = None
    post_id: Optional[int] = None

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
            "avatar_url": f"https://ui-avatars.com/api/?name={user.username}&background=random&color=fff&size=128",
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
# MOVIE ROUTES
# ============================================

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
            "comments": comments.data if comments.data else [],
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
# COMMUNITY ROUTES (FULLY IMPLEMENTED)
# ============================================

@app.get("/community/posts")
async def get_posts(limit: int = 20, offset: int = 0, sort: str = "newest"):
    """Get all community posts with pagination"""
    try:
        query = supabase_client.table('community_posts')\
            .select('*, profiles!inner(username, avatar_url)')
        
        # Apply sorting
        if sort == "newest":
            query = query.order('created_at', desc=True)
        elif sort == "oldest":
            query = query.order('created_at', asc=True)
        elif sort == "popular":
            # This would need a likes count - simplified for now
            query = query.order('created_at', desc=True)
        
        # Get total count
        count_result = supabase_client.table('community_posts').select('*', count='exact').execute()
        total_count = len(count_result.data) if count_result.data else 0
        
        # Apply pagination
        posts = query.range(offset, offset + limit - 1).execute()
        
        # Add comments count and likes count for each post
        for post in posts.data:
            # Get comments count
            comments = supabase_client.table('comments')\
                .select('*', count='exact')\
                .eq('post_id', post['id'])\
                .execute()
            post['comments_count'] = len(comments.data) if comments.data else 0
            
            # Get likes count
            likes = supabase_client.table('likes')\
                .select('*', count='exact')\
                .eq('post_id', post['id'])\
                .execute()
            post['likes_count'] = len(likes.data) if likes.data else 0
        
        return {
            "posts": posts.data,
            "total": total_count,
            "limit": limit,
            "offset": offset
        }
    except Exception as e:
        print(f"Error fetching posts: {e}")
        return {"posts": [], "total": 0}

@app.get("/community/posts/{post_id}")
async def get_post(post_id: int):
    """Get a single post with comments"""
    try:
        # Get post
        post_result = supabase_client.table('community_posts')\
            .select('*, profiles!inner(username, avatar_url)')\
            .eq('id', post_id)\
            .execute()
        
        if not post_result.data:
            raise HTTPException(status_code=404, detail="Post not found")
        
        post = post_result.data[0]
        
        # Get comments for this post
        comments = supabase_client.table('comments')\
            .select('*, profiles!inner(username, avatar_url)')\
            .eq('post_id', post_id)\
            .order('created_at', asc=True)\
            .execute()
        
        # Get likes count
        likes = supabase_client.table('likes')\
            .select('*', count='exact')\
            .eq('post_id', post_id)\
            .execute()
        post['likes_count'] = len(likes.data) if likes.data else 0
        
        return {
            "post": post,
            "comments": comments.data
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error fetching post: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/community/posts")
async def create_post(post: CommunityPost, user = Depends(require_user)):
    """Create a new community post"""
    try:
        # Generate ID
        post_id = random.randint(1000, 9999)
        
        post_data = {
            "id": post_id,
            "user_id": user['id'],
            "username": user['username'],
            "avatar_url": user.get('avatar_url'),
            "title": post.title,
            "content": post.content,
            "image_url": post.image_url,
            "created_at": datetime.now().isoformat()
        }
        
        result = supabase_client.table('community_posts')\
            .insert(post_data)\
            .execute()
        
        return {
            "message": "Post created successfully",
            "post": result.data[0]
        }
    except Exception as e:
        print(f"Error creating post: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.put("/community/posts/{post_id}")
async def update_post(post_id: int, post_update: CommunityPost, user = Depends(require_user)):
    """Update a post (only by author or owner)"""
    try:
        # Check if post exists
        post = supabase_client.table('community_posts')\
            .select('*')\
            .eq('id', post_id)\
            .execute()
        
        if not post.data:
            raise HTTPException(status_code=404, detail="Post not found")
        
        # Check permissions
        if post.data[0]['user_id'] != user['id'] and user.get('role') != 'owner':
            raise HTTPException(status_code=403, detail="You can only edit your own posts")
        
        update_data = {
            "title": post_update.title,
            "content": post_update.content,
            "image_url": post_update.image_url,
            "updated_at": datetime.now().isoformat()
        }
        
        result = supabase_client.table('community_posts')\
            .update(update_data)\
            .eq('id', post_id)\
            .execute()
        
        return {
            "message": "Post updated successfully",
            "post": result.data[0]
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error updating post: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/community/posts/{post_id}")
async def delete_post(post_id: int, user = Depends(require_user)):
    """Delete a post (only by author or owner)"""
    try:
        # Check if post exists
        post = supabase_client.table('community_posts')\
            .select('*')\
            .eq('id', post_id)\
            .execute()
        
        if not post.data:
            raise HTTPException(status_code=404, detail="Post not found")
        
        # Check permissions
        if post.data[0]['user_id'] != user['id'] and user.get('role') != 'owner':
            raise HTTPException(status_code=403, detail="You can only delete your own posts")
        
        # Delete related comments first
        supabase_client.table('comments').delete().eq('post_id', post_id).execute()
        
        # Delete likes
        supabase_client.table('likes').delete().eq('post_id', post_id).execute()
        
        # Delete post
        supabase_client.table('community_posts').delete().eq('id', post_id).execute()
        
        return {"message": "Post deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error deleting post: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/community/posts/{post_id}/like")
async def like_post(post_id: int, user = Depends(require_user)):
    """Like or unlike a post"""
    try:
        # Check if already liked
        existing = supabase_client.table('likes')\
            .select('*')\
            .eq('user_id', user['id'])\
            .eq('post_id', post_id)\
            .execute()
        
        if existing.data:
            # Unlike
            supabase_client.table('likes')\
                .delete()\
                .eq('user_id', user['id'])\
                .eq('post_id', post_id)\
                .execute()
            return {"message": "Post unliked", "liked": False}
        else:
            # Like
            supabase_client.table('likes')\
                .insert({
                    "user_id": user['id'],
                    "post_id": post_id,
                    "created_at": datetime.now().isoformat()
                })\
                .execute()
            return {"message": "Post liked", "liked": True}
    except Exception as e:
        print(f"Error liking post: {e}")
        raise HTTPException(status_code=400, detail=str(e))

# ============================================
# COMMENT ROUTES
# ============================================

@app.post("/comments")
async def add_comment(comment: Comment, user = Depends(require_user)):
    """Add a comment to a movie or post"""
    try:
        # Validate that either movie_id or post_id is provided
        if not comment.movie_id and not comment.post_id:
            raise HTTPException(status_code=400, detail="Either movie_id or post_id must be provided")
        
        # Generate ID
        comment_id = random.randint(10000, 99999)
        
        comment_data = {
            "id": comment_id,
            "user_id": user['id'],
            "username": user['username'],
            "avatar_url": user.get('avatar_url'),
            "content": comment.content,
            "movie_id": comment.movie_id,
            "post_id": comment.post_id,
            "created_at": datetime.now().isoformat()
        }
        
        result = supabase_client.table('comments')\
            .insert(comment_data)\
            .execute()
        
        return {
            "message": "Comment added successfully",
            "comment": result.data[0]
        }
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
        posts_count = supabase_client.table('community_posts')\
            .select('*', count='exact')\
            .execute()
        users_count = supabase_client.table('profiles')\
            .select('*', count='exact')\
            .execute()
        movies_count = supabase_client.table('movies')\
            .select('*', count='exact')\
            .execute()
        
        user['stats'] = {
            "posts": len(posts_count.data) if posts_count.data else 0,
            "users": len(users_count.data) if users_count.data else 0,
            "movies": len(movies_count.data) if movies_count.data else 0
        }
    else:
        # Get user stats
        posts = supabase_client.table('community_posts')\
            .select('*', count='exact')\
            .eq('user_id', user['id'])\
            .execute()
        comments = supabase_client.table('comments')\
            .select('*', count='exact')\
            .eq('user_id', user['id'])\
            .execute()
        
        user['stats'] = {
            "posts": len(posts.data) if posts.data else 0,
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
    
    return {"history": result.data}

@app.post("/user/history/{movie_id}")
async def add_to_history(movie_id: int, progress: int = 0, user = Depends(require_user)):
    """Add movie to watch history"""
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
    
    return {"watchlist": [item['movies'] for item in result.data]}

@app.post("/user/watchlist/{movie_id}")
async def add_to_watchlist(movie_id: int, user = Depends(require_user)):
    """Add movie to watchlist"""
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
# ADMIN ROUTES (OWNER ONLY) - FULLY IMPLEMENTED
# ============================================

@app.get("/admin/stats")
async def get_admin_stats(owner = Depends(require_owner)):
    """Get platform statistics"""
    try:
        # Get counts
        users = supabase_client.table('profiles').select('*', count='exact').execute()
        movies = supabase_client.table('movies').select('*', count='exact').execute()
        posts = supabase_client.table('community_posts').select('*', count='exact').execute()
        comments = supabase_client.table('comments').select('*', count='exact').execute()
        
        # Get recent activity (last 24h)
        yesterday = (datetime.now() - timedelta(days=1)).isoformat()
        recent_users = supabase_client.table('profiles')\
            .select('*', count='exact')\
            .gte('created_at', yesterday)\
            .execute()
        recent_posts = supabase_client.table('community_posts')\
            .select('*', count='exact')\
            .gte('created_at', yesterday)\
            .execute()
        
        return {
            "movies": len(movies.data) if movies.data else 0,
            "users": len(users.data) if users.data else 0,
            "posts": len(posts.data) if posts.data else 0,
            "comments": len(comments.data) if comments.data else 0,
            "recent_users": len(recent_users.data) if recent_users.data else 0,
            "recent_posts": len(recent_posts.data) if recent_posts.data else 0
        }
    except Exception as e:
        print(f"Error getting stats: {e}")
        return {
            "movies": 0, "users": 0, "posts": 0, 
            "comments": 0, "recent_users": 0, "recent_posts": 0
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
            posts = supabase_client.table('community_posts')\
                .select('*', count='exact')\
                .eq('user_id', user['id'])\
                .execute()
            comments = supabase_client.table('comments')\
                .select('*', count='exact')\
                .eq('user_id', user['id'])\
                .execute()
            
            user['stats'] = {
                "posts": len(posts.data) if posts.data else 0,
                "comments": len(comments.data) if comments.data else 0
            }
        
        return {"users": result.data}
    except Exception as e:
        print(f"Error fetching users: {e}")
        return {"users": []}

@app.get("/admin/posts")
async def get_all_posts(owner = Depends(require_owner)):
    """Get all community posts for moderation"""
    try:
        result = supabase_client.table('community_posts')\
            .select('*, profiles!inner(username)')\
            .order('created_at', desc=True)\
            .execute()
        
        return {"posts": result.data}
    except Exception as e:
        print(f"Error fetching posts: {e}")
        return {"posts": []}

@app.delete("/admin/posts/{post_id}")
async def admin_delete_post(post_id: int, owner = Depends(require_owner)):
    """Delete any post (admin only)"""
    try:
        # Delete related comments first
        supabase_client.table('comments').delete().eq('post_id', post_id).execute()
        # Delete likes
        supabase_client.table('likes').delete().eq('post_id', post_id).execute()
        # Delete post
        supabase_client.table('community_posts').delete().eq('id', post_id).execute()
        
        return {"message": "Post deleted by admin"}
    except Exception as e:
        print(f"Error deleting post: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/admin/movies")
async def add_movie(movie: Movie, owner = Depends(require_owner)):
    """Add a new movie"""
    try:
        movie_data = movie.dict()
        movie_data["views"] = 0
        movie_data["created_at"] = datetime.now().isoformat()
        
        result = supabase_client.table('movies')\
            .insert(movie_data)\
            .execute()
        
        return {"message": "Movie added", "movie": result.data[0]}
    except Exception as e:
        print(f"Error adding movie: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.put("/admin/movies/{movie_id}")
async def update_movie(movie_id: int, movie: Movie, owner = Depends(require_owner)):
    """Update a movie"""
    try:
        movie_data = movie.dict()
        result = supabase_client.table('movies')\
            .update(movie_data)\
            .eq('id', movie_id)\
            .execute()
        
        return {"message": "Movie updated", "movie": result.data[0]}
    except Exception as e:
        print(f"Error updating movie: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/admin/movies/{movie_id}")
async def delete_movie(movie_id: int, owner = Depends(require_owner)):
    """Delete a movie"""
    try:
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

# ============================================
# HEALTH CHECK
# ============================================

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "database": "connected"
    }
