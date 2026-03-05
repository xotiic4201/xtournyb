from fastapi import FastAPI, HTTPException, Depends, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer
from pydantic import BaseModel, Field
from typing import Optional
import supabase
import os
from datetime import datetime

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

# Owner credentials
OWNER_USERNAME = os.getenv("OWNER_USERNAME")
OWNER_PASSWORD = os.getenv("OWNER_PASSWORD")
OWNER_EMAIL = os.getenv("OWNER_EMAIL")

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
        print(f"Registration attempt for username: {user.username}")
        
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
            
        except Exception as auth_error:
            print(f"Supabase Auth signup error: {str(auth_error)}")
            error_msg = str(auth_error)
            if "Database error saving new user" in error_msg:
                # This likely means the trigger failed - let's check if profile was created
                # Try to find the user in auth to get their ID
                try:
                    # Attempt to login to see if user was created
                    check_auth = supabase_client.auth.sign_in_with_password({
                        "email": user.email,
                        "password": user.password
                    })
                    if check_auth.user:
                        # User was created but trigger failed - let's create profile manually
                        profile_data = {
                            "id": check_auth.user.id,
                            "username": user.username,
                            "email": user.email,
                            "role": "owner" if is_owner else "user",
                            "created_at": datetime.now().isoformat()
                        }
                        supabase_client.table('profiles').insert(profile_data).execute()
                        print("Profile created manually")
                        
                        return {
                            "message": "Signup successful",
                            "role": profile_data["role"],
                            "user": profile_data
                        }
                except:
                    pass
            raise HTTPException(status_code=400, detail=f"Auth signup failed")
        
        # If we get here, the trigger should have created the profile
        # But let's verify and update the role if needed
        profile_response = supabase_client.table('profiles').select('*').eq('id', auth_response.user.id).execute()
        
        if profile_response.data:
            profile = profile_response.data[0]
            # Update role if this is owner
            if is_owner and profile.get('role') != 'owner':
                supabase_client.table('profiles').update({
                    "role": "owner"
                }).eq('id', auth_response.user.id).execute()
                profile['role'] = 'owner'
            
            print("Profile exists from trigger")
            return {
                "message": "Signup successful",
                "role": profile['role'],
                "user": profile
            }
        else:
            # Trigger failed - create profile manually
            profile_data = {
                "id": auth_response.user.id,
                "username": user.username,
                "email": user.email,
                "role": "owner" if is_owner else "user",
                "created_at": datetime.now().isoformat()
            }
            supabase_client.table('profiles').insert(profile_data).execute()
            print("Profile created manually (trigger failed)")
            
            return {
                "message": "Signup successful",
                "role": profile_data["role"],
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
        # Check if owner already exists in profiles
        existing = supabase_client.table('profiles').select('*').eq('username', OWNER_USERNAME).execute()
        if existing.data:
            return {"message": "Owner already exists", "user": existing.data[0]}
        
        # Try to create owner in Supabase Auth
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
            
            # Wait a moment for trigger
            import time
            time.sleep(2)
            
            # Check if profile was created by trigger
            profile_response = supabase_client.table('profiles').select('*').eq('id', auth_response.user.id).execute()
            
            if profile_response.data:
                # Update to owner role
                supabase_client.table('profiles').update({
                    "role": "owner"
                }).eq('id', auth_response.user.id).execute()
                return {"message": "Owner created successfully", "user": profile_response.data[0]}
            else:
                # Create profile manually
                profile_data = {
                    "id": auth_response.user.id,
                    "username": OWNER_USERNAME,
                    "email": OWNER_EMAIL,
                    "role": "owner",
                    "created_at": datetime.now().isoformat()
                }
                supabase_client.table('profiles').insert(profile_data).execute()
                return {"message": "Owner created successfully (manual)", "user": profile_data}
                
        except Exception as e:
            if "User already registered" in str(e):
                # Try to find the user by email in auth
                try:
                    # Attempt to login to get user ID
                    auth_response = supabase_client.auth.sign_in_with_password({
                        "email": OWNER_EMAIL,
                        "password": OWNER_PASSWORD
                    })
                    
                    # Check if profile exists
                    profile_response = supabase_client.table('profiles').select('*').eq('id', auth_response.user.id).execute()
                    
                    if not profile_response.data:
                        # Create profile
                        profile_data = {
                            "id": auth_response.user.id,
                            "username": OWNER_USERNAME,
                            "email": OWNER_EMAIL,
                            "role": "owner",
                            "created_at": datetime.now().isoformat()
                        }
                        supabase_client.table('profiles').insert(profile_data).execute()
                        return {"message": "Owner profile created", "user": profile_data}
                    else:
                        # Update existing profile to owner
                        supabase_client.table('profiles').update({
                            "role": "owner"
                        }).eq('id', auth_response.user.id).execute()
                        return {"message": "Owner role updated", "user": profile_response.data[0]}
                        
                except:
                    return {"message": "Owner exists in Auth but cannot access. Check Supabase dashboard."}
            else:
                raise e
        
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
