from fastapi import FastAPI, HTTPException, Depends, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer
from pydantic import BaseModel, Field
from typing import Optional
import supabase
import os
from datetime import datetime
import time

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

# Owner credentials from Render env
OWNER_USERNAME = os.getenv("OWNER_USERNAME")
OWNER_PASSWORD = os.getenv("OWNER_PASSWORD")
OWNER_EMAIL = os.getenv("OWNER_EMAIL")

print(f"Owner credentials loaded - Username: {OWNER_USERNAME}, Email: {OWNER_EMAIL}")

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
        
        # Find user by username in profiles table
        profile_response = supabase_client.table('profiles').select('*').eq('username', login_data.username).execute()
        
        if not profile_response.data:
            print(f"No profile found for username: {login_data.username}")
            raise HTTPException(status_code=401, detail="Invalid username or password")
        
        user_profile = profile_response.data[0]
        print(f"Found profile for email: {user_profile['email']}")
        
        # Attempt login with Supabase Auth
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
        print(f"Registration attempt for username: {user.username}, email: {user.email}")
        
        # Check if username exists in profiles
        existing = supabase_client.table('profiles').select('*').eq('username', user.username).execute()
        if existing.data:
            raise HTTPException(status_code=400, detail="Username already taken")
        
        # Check if email exists in profiles
        existing_email = supabase_client.table('profiles').select('*').eq('email', user.email).execute()
        if existing_email.data:
            raise HTTPException(status_code=400, detail="Email already registered")
        
        # Check if this is the owner account
        is_owner = (user.username == OWNER_USERNAME and user.password == OWNER_PASSWORD)
        print(f"User is owner: {is_owner}")
        
        # Sign up with Supabase Auth - THIS WILL TRIGGER YOUR SQL TRIGGER
        try:
            auth_response = supabase_client.auth.sign_up({
                "email": user.email,
                "password": user.password,
                "options": {
                    "data": {
                        "username": user.username
                    }
                }
            })
            
            if not auth_response.user:
                raise HTTPException(status_code=400, detail="Signup failed")
                
            print(f"Supabase Auth user created with ID: {auth_response.user.id}")
            
            # Wait a moment for the trigger to complete
            time.sleep(2)
            
            # Check if profile was created by trigger
            profile_response = supabase_client.table('profiles').select('*').eq('id', auth_response.user.id).execute()
            
            if profile_response.data:
                # Profile exists from trigger
                profile = profile_response.data[0]
                print(f"Profile created by trigger with role: {profile.get('role')}")
                
                # Update role if this is owner
                if is_owner and profile.get('role') != 'owner':
                    supabase_client.table('profiles').update({
                        "role": "owner"
                    }).eq('id', auth_response.user.id).execute()
                    profile['role'] = 'owner'
                    print("Updated profile to owner role")
                
                return {
                    "message": "Signup successful",
                    "role": profile['role'],
                    "user": profile
                }
            else:
                # Trigger failed - try to create profile manually with service role
                # Note: This might still fail due to RLS
                print("Trigger didn't create profile, attempting manual creation")
                
                # For manual creation, we need to use a different approach
                # Let's return success but note that profile needs to be created
                return {
                    "message": "User created but profile pending. Please try logging in.",
                    "user": {
                        "id": auth_response.user.id,
                        "username": user.username,
                        "email": user.email,
                        "role": "owner" if is_owner else "user"
                    }
                }
            
        except Exception as auth_error:
            print(f"Supabase Auth signup error: {str(auth_error)}")
            
            # Check if user already exists
            if "User already registered" in str(auth_error):
                # Try to login to get the user
                try:
                    auth_response = supabase_client.auth.sign_in_with_password({
                        "email": user.email,
                        "password": user.password
                    })
                    
                    if auth_response.user:
                        # Check if profile exists
                        profile_response = supabase_client.table('profiles').select('*').eq('id', auth_response.user.id).execute()
                        
                        if profile_response.data:
                            return {
                                "message": "User already exists",
                                "user": profile_response.data[0]
                            }
                except:
                    pass
                
                raise HTTPException(status_code=400, detail="User already registered")
            else:
                raise HTTPException(status_code=400, detail=f"Signup failed: {str(auth_error)}")
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Unexpected registration error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/auth/setup-owner")
async def setup_owner():
    """Set up the owner account"""
    try:
        print(f"Setting up owner with username: {OWNER_USERNAME}, email: {OWNER_EMAIL}")
        
        # First, check if owner profile exists
        profile_response = supabase_client.table('profiles').select('*').eq('username', OWNER_USERNAME).execute()
        
        if profile_response.data:
            # Profile exists, update to owner if needed
            profile = profile_response.data[0]
            if profile.get('role') != 'owner':
                supabase_client.table('profiles').update({
                    "role": "owner"
                }).eq('id', profile['id']).execute()
                return {"message": "Owner role updated", "user": profile}
            else:
                return {"message": "Owner already exists", "user": profile}
        
        # Try to find by email
        profile_by_email = supabase_client.table('profiles').select('*').eq('email', OWNER_EMAIL).execute()
        if profile_by_email.data:
            profile = profile_by_email.data[0]
            supabase_client.table('profiles').update({
                "role": "owner",
                "username": OWNER_USERNAME
            }).eq('id', profile['id']).execute()
            return {"message": "Owner role updated", "user": profile}
        
        # No profile found, need to create auth user first
        try:
            auth_response = supabase_client.auth.sign_up({
                "email": OWNER_EMAIL,
                "password": OWNER_PASSWORD,
                "options": {
                    "data": {
                        "username": OWNER_USERNAME
                    }
                }
            })
            
            time.sleep(2)
            
            # Check if profile was created
            profile_response = supabase_client.table('profiles').select('*').eq('id', auth_response.user.id).execute()
            
            if profile_response.data:
                supabase_client.table('profiles').update({
                    "role": "owner"
                }).eq('id', auth_response.user.id).execute()
                return {"message": "Owner created", "user": profile_response.data[0]}
            else:
                return {"message": "Auth user created but profile pending"}
                
        except Exception as e:
            if "User already registered" in str(e):
                return {"message": "User exists in Auth. Please check Supabase dashboard."}
            raise e
        
    except Exception as e:
        print(f"Setup owner error: {str(e)}")
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
