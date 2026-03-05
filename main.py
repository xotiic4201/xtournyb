import asyncio
import threading
import os
import uuid
import math
import random
import bcrypt
import jwt
import secrets
import json
import qrcode
import io
import base64
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Tuple
from dotenv import load_dotenv

# FastAPI imports
from fastapi import FastAPI, HTTPException, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, validator

# Discord imports
import discord
from discord.ext import commands, tasks
from discord import app_commands
from discord.ui import Button, View, Modal, TextInput

# Supabase
from supabase import create_client, Client

# PDF/Image export
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.utils import ImageReader
from PIL import Image, ImageDraw, ImageFont

load_dotenv()

# ==================== Configuration ====================
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
JWT_SECRET = os.getenv("JWT_SECRET", secrets.token_urlsafe(32))
API_PORT = int(os.getenv("API_PORT", 8000))
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:8000")
HOST_ROLE_NAME = os.getenv("HOST_ROLE_NAME", "Tournament Host")

# ==================== Database Layer ====================
class Database:
    def __init__(self):
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set")
        
        self.supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        self.init_tables()
    
    def init_tables(self):
        """Initialize database tables if they don't exist"""
        try:
            # Create tournaments table
            self.supabase.table("tournaments").select("*").limit(1).execute()
        except:
            # Tables will be created via Supabase migrations
            pass
    
    # Tournament operations
    def create_tournament(self, data: Dict) -> Dict:
        """Create a new tournament"""
        data["id"] = str(uuid.uuid4())
        data["join_code"] = self.generate_join_code()
        data["created_at"] = datetime.utcnow().isoformat()
        data["updated_at"] = datetime.utcnow().isoformat()
        
        result = self.supabase.table("tournaments").insert(data).execute()
        return result.data[0]
    
    def generate_join_code(self) -> str:
        """Generate unique join code"""
        while True:
            code = ''.join(random.choices('ABCDEFGHJKLMNPQRSTUVWXYZ23456789', k=6))
            existing = self.get_tournament_by_code(code)
            if not existing:
                return code
    
    def get_tournament(self, tournament_id: str) -> Optional[Dict]:
        """Get tournament by ID"""
        result = self.supabase.table("tournaments")\
            .select("*")\
            .eq("id", tournament_id)\
            .execute()
        
        return result.data[0] if result.data else None
    
    def get_tournament_by_code(self, join_code: str) -> Optional[Dict]:
        """Get tournament by join code"""
        result = self.supabase.table("tournaments")\
            .select("*")\
            .eq("join_code", join_code)\
            .execute()
        
        return result.data[0] if result.data else None
    
    def get_tournaments(self, visibility: str = None, status: str = None) -> List[Dict]:
        """Get all tournaments with optional filters"""
        query = self.supabase.table("tournaments").select("*")
        
        if visibility:
            query = query.eq("visibility", visibility)
        
        if status:
            query = query.eq("status", status)
        
        result = query.order("created_at", desc=True).execute()
        return result.data
    
    def get_tournaments_by_host(self, host_discord_id: str) -> List[Dict]:
        """Get tournaments hosted by a specific Discord user"""
        result = self.supabase.table("tournaments")\
            .select("*")\
            .eq("host_discord_id", host_discord_id)\
            .order("created_at", desc=True)\
            .execute()
        
        return result.data
    
    def update_tournament(self, tournament_id: str, updates: Dict) -> Dict:
        """Update tournament"""
        updates["updated_at"] = datetime.utcnow().isoformat()
        
        result = self.supabase.table("tournaments")\
            .update(updates)\
            .eq("id", tournament_id)\
            .execute()
        
        return result.data[0]
    
    def delete_tournament(self, tournament_id: str):
        """Delete tournament and all related data"""
        # Delete matches
        self.supabase.table("matches")\
            .delete()\
            .eq("tournament_id", tournament_id)\
            .execute()
        
        # Delete participants
        self.supabase.table("participants")\
            .delete()\
            .eq("tournament_id", tournament_id)\
            .execute()
        
        # Delete standings
        self.supabase.table("round_robin_standings")\
            .delete()\
            .eq("tournament_id", tournament_id)\
            .execute()
        
        # Delete tournament
        self.supabase.table("tournaments")\
            .delete()\
            .eq("id", tournament_id)\
            .execute()
    
    # Participant operations
    def add_participant(self, tournament_id: str, name: str, discord_id: str = None) -> Dict:
        """Add participant to tournament"""
        data = {
            "id": str(uuid.uuid4()),
            "tournament_id": tournament_id,
            "name": name,
            "discord_id": discord_id,
            "seed": self.get_next_seed(tournament_id),
            "created_at": datetime.utcnow().isoformat()
        }
        
        result = self.supabase.table("participants").insert(data).execute()
        return result.data[0]
    
    def get_next_seed(self, tournament_id: str) -> int:
        """Get next available seed number"""
        result = self.supabase.table("participants")\
            .select("seed")\
            .eq("tournament_id", tournament_id)\
            .order("seed", desc=True)\
            .limit(1)\
            .execute()
        
        return (result.data[0]["seed"] + 1) if result.data else 1
    
    def add_participants_bulk(self, tournament_id: str, names: List[str]):
        """Add multiple participants at once"""
        seed = self.get_next_seed(tournament_id)
        data = []
        for i, name in enumerate(names):
            data.append({
                "id": str(uuid.uuid4()),
                "tournament_id": tournament_id,
                "name": name,
                "seed": seed + i,
                "created_at": datetime.utcnow().isoformat()
            })
        
        self.supabase.table("participants").insert(data).execute()
    
    def get_participants(self, tournament_id: str) -> List[Dict]:
        """Get all participants for a tournament"""
        result = self.supabase.table("participants")\
            .select("*")\
            .eq("tournament_id", tournament_id)\
            .order("seed")\
            .execute()
        
        return result.data
    
    def get_participant_names(self, tournament_id: str) -> List[str]:
        """Get all participant names for a tournament"""
        result = self.supabase.table("participants")\
            .select("name")\
            .eq("tournament_id", tournament_id)\
            .order("seed")\
            .execute()
        
        return [p["name"] for p in result.data]
    
    def remove_participant(self, tournament_id: str, participant_id: str):
        """Remove participant by ID"""
        self.supabase.table("participants")\
            .delete()\
            .eq("id", participant_id)\
            .execute()
    
    def remove_participant_by_name(self, tournament_id: str, name: str):
        """Remove participant by name"""
        self.supabase.table("participants")\
            .delete()\
            .eq("tournament_id", tournament_id)\
            .eq("name", name)\
            .execute()
    
    def swap_participants(self, tournament_id: str, id1: str, id2: str):
        """Swap two participants by ID"""
        # Get both participants
        p1 = self.supabase.table("participants")\
            .select("*")\
            .eq("id", id1)\
            .execute().data[0]
        
        p2 = self.supabase.table("participants")\
            .select("*")\
            .eq("id", id2)\
            .execute().data[0]
        
        # Swap seeds
        seed1, seed2 = p1["seed"], p2["seed"]
        
        self.supabase.table("participants")\
            .update({"seed": seed2})\
            .eq("id", id1)\
            .execute()
        
        self.supabase.table("participants")\
            .update({"seed": seed1})\
            .eq("id", id2)\
            .execute()
    
    # Match operations
    def create_match(self, tournament_id: str, match_data: Dict) -> Dict:
        """Create a new match"""
        data = {
            "id": str(uuid.uuid4()),
            "tournament_id": tournament_id,
            "round": match_data.get("round", 1),
            "match_number": match_data.get("match_number", 1),
            "participant1_id": match_data.get("participant1_id"),
            "participant2_id": match_data.get("participant2_id"),
            "participant1_name": match_data.get("participant1_name"),
            "participant2_name": match_data.get("participant2_name"),
            "score1": None,
            "score2": None,
            "winner_id": None,
            "next_match_id": match_data.get("next_match_id"),
            "bracket": match_data.get("bracket", "winners"),
            "status": "pending",
            "created_at": datetime.utcnow().isoformat()
        }
        
        result = self.supabase.table("matches").insert(data).execute()
        return result.data[0]
    
    def create_matches_bulk(self, tournament_id: str, matches_data: List[Dict]):
        """Create multiple matches at once"""
        data = []
        for i, match in enumerate(matches_data):
            data.append({
                "id": str(uuid.uuid4()),
                "tournament_id": tournament_id,
                "round": match.get("round", 1),
                "match_number": match.get("match_number", i + 1),
                "participant1_id": match.get("participant1_id"),
                "participant2_id": match.get("participant2_id"),
                "participant1_name": match.get("participant1_name"),
                "participant2_name": match.get("participant2_name"),
                "score1": None,
                "score2": None,
                "winner_id": None,
                "next_match_id": match.get("next_match_id"),
                "bracket": match.get("bracket", "winners"),
                "status": "pending",
                "created_at": datetime.utcnow().isoformat()
            })
        
        self.supabase.table("matches").insert(data).execute()
    
    def get_matches(self, tournament_id: str) -> List[Dict]:
        """Get all matches for a tournament"""
        result = self.supabase.table("matches")\
            .select("*")\
            .eq("tournament_id", tournament_id)\
            .order("round")\
            .order("match_number")\
            .execute()
        
        return result.data
    
    def get_match(self, match_id: str) -> Optional[Dict]:
        """Get match by ID"""
        result = self.supabase.table("matches")\
            .select("*")\
            .eq("id", match_id)\
            .execute()
        
        return result.data[0] if result.data else None
    
    def get_pending_matches(self, tournament_id: str) -> List[Dict]:
        """Get pending matches for a tournament"""
        result = self.supabase.table("matches")\
            .select("*")\
            .eq("tournament_id", tournament_id)\
            .eq("status", "pending")\
            .order("round")\
            .order("match_number")\
            .execute()
        
        return result.data
    
    def get_matches_by_round(self, tournament_id: str, round_num: int) -> List[Dict]:
        """Get matches for a specific round"""
        result = self.supabase.table("matches")\
            .select("*")\
            .eq("tournament_id", tournament_id)\
            .eq("round", round_num)\
            .order("match_number")\
            .execute()
        
        return result.data
    
    def update_match_score(self, match_id: str, score1: int, score2: int) -> Dict:
        """Update match scores"""
        # Determine winner
        winner_id = None
        if score1 > score2:
            winner_idx = 0
        elif score2 > score1:
            winner_idx = 1
        else:
            winner_idx = -1  # Tie
        
        match = self.get_match(match_id)
        if winner_idx == 0:
            winner_id = match["participant1_id"]
        elif winner_idx == 1:
            winner_id = match["participant2_id"]
        
        result = self.supabase.table("matches")\
            .update({
                "score1": score1,
                "score2": score2,
                "winner_id": winner_id,
                "status": "completed"
            })\
            .eq("id", match_id)\
            .execute()
        
        # Advance winner if there's a next match
        if winner_id and match.get("next_match_id"):
            self.advance_to_next_match(match["next_match_id"], winner_id)
        
        return result.data[0]
    
    def advance_to_next_match(self, next_match_id: str, participant_id: str):
        """Advance participant to next match"""
        next_match = self.get_match(next_match_id)
        if next_match:
            if not next_match["participant1_id"]:
                # Get participant name
                participant = self.get_participant(participant_id)
                
                self.supabase.table("matches")\
                    .update({
                        "participant1_id": participant_id,
                        "participant1_name": participant["name"] if participant else None
                    })\
                    .eq("id", next_match_id)\
                    .execute()
            elif not next_match["participant2_id"]:
                participant = self.get_participant(participant_id)
                
                self.supabase.table("matches")\
                    .update({
                        "participant2_id": participant_id,
                        "participant2_name": participant["name"] if participant else None
                    })\
                    .eq("id", next_match_id)\
                    .execute()
    
    def get_participant(self, participant_id: str) -> Optional[Dict]:
        """Get participant by ID"""
        if not participant_id:
            return None
        
        result = self.supabase.table("participants")\
            .select("*")\
            .eq("id", participant_id)\
            .execute()
        
        return result.data[0] if result.data else None
    
    def forfeit_match(self, match_id: str, forfeiting_participant_id: str):
        """Handle match forfeit"""
        match = self.get_match(match_id)
        if match:
            # Determine winner (the other participant)
            winner_id = match["participant2_id"] if match["participant1_id"] == forfeiting_participant_id else match["participant1_id"]
            
            # Update match with forfeit scores
            self.supabase.table("matches")\
                .update({
                    "score1": 0,
                    "score2": 0,
                    "winner_id": winner_id,
                    "status": "completed",
                    "notes": f"Forfeit by {forfeiting_participant_id}"
                })\
                .eq("id", match_id)\
                .execute()
            
            # Advance winner
            if winner_id and match.get("next_match_id"):
                self.advance_to_next_match(match["next_match_id"], winner_id)
    
    def delete_matches(self, tournament_id: str):
        """Delete all matches for a tournament"""
        self.supabase.table("matches")\
            .delete()\
            .eq("tournament_id", tournament_id)\
            .execute()
    
    # Round robin standings
    def update_standings(self, tournament_id: str, standings: List[Dict]):
        """Update round robin standings"""
        # Delete existing standings
        self.supabase.table("round_robin_standings")\
            .delete()\
            .eq("tournament_id", tournament_id)\
            .execute()
        
        # Insert new standings
        data = [
            {
                "id": str(uuid.uuid4()),
                "tournament_id": tournament_id,
                "participant_id": standing["participant_id"],
                "participant_name": standing["participant_name"],
                "wins": standing["wins"],
                "losses": standing["losses"],
                "draws": standing.get("draws", 0),
                "points": standing["points"],
                "games_played": standing["games_played"],
                "created_at": datetime.utcnow().isoformat()
            }
            for standing in standings
        ]
        
        self.supabase.table("round_robin_standings").insert(data).execute()
    
    def get_standings(self, tournament_id: str) -> List[Dict]:
        """Get round robin standings"""
        result = self.supabase.table("round_robin_standings")\
            .select("*")\
            .eq("tournament_id", tournament_id)\
            .order("points", desc=True)\
            .order("wins", desc=True)\
            .execute()
        
        return result.data
    
    # Tournament hosts
    def add_host(self, tournament_id: str, discord_id: str, discord_name: str) -> Dict:
        """Add a host to tournament"""
        data = {
            "id": str(uuid.uuid4()),
            "tournament_id": tournament_id,
            "discord_id": discord_id,
            "discord_name": discord_name,
            "added_at": datetime.utcnow().isoformat()
        }
        
        result = self.supabase.table("tournament_hosts").insert(data).execute()
        return result.data[0]
    
    def remove_host(self, tournament_id: str, discord_id: str):
        """Remove a host from tournament"""
        self.supabase.table("tournament_hosts")\
            .delete()\
            .eq("tournament_id", tournament_id)\
            .eq("discord_id", discord_id)\
            .execute()
    
    def get_hosts(self, tournament_id: str) -> List[Dict]:
        """Get all hosts for a tournament"""
        result = self.supabase.table("tournament_hosts")\
            .select("*")\
            .eq("tournament_id", tournament_id)\
            .execute()
        
        return result.data
    
    def is_host(self, tournament_id: str, discord_id: str) -> bool:
        """Check if user is a host"""
        result = self.supabase.table("tournament_hosts")\
            .select("*")\
            .eq("tournament_id", tournament_id)\
            .eq("discord_id", discord_id)\
            .execute()
        
        return len(result.data) > 0

# ==================== Authentication ====================
class AuthHandler:
    def __init__(self):
        self.secret_key = JWT_SECRET
        self.algorithm = "HS256"
        self.token_expiry = 24  # hours
    
    def hash_password(self, password: str) -> str:
        """Hash a password using bcrypt"""
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
        return hashed.decode('utf-8')
    
    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash"""
        return bcrypt.checkpw(
            plain_password.encode('utf-8'),
            hashed_password.encode('utf-8')
        )
    
    def create_token(self, tournament_id: str, discord_id: str = None) -> str:
        """Create a JWT token for host authentication"""
        payload = {
            "tournament_id": tournament_id,
            "discord_id": discord_id,
            "exp": datetime.utcnow() + timedelta(hours=self.token_expiry),
            "iat": datetime.utcnow()
        }
        return jwt.encode(payload, self.secret_key, algorithm=self.algorithm)
    
    def verify_token(self, token: str) -> Dict:
        """Verify and decode a JWT token"""
        try:
            return jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token expired")
        except jwt.InvalidTokenError:
            raise HTTPException(status_code=401, detail="Invalid token")

# ==================== Bracket Logic ====================
class BracketGenerator:
    def __init__(self):
        self.match_counter = 1
    
    def generate_bracket(self, tournament_type: str, participants: List[Dict]) -> List[Dict]:
        """Generate initial bracket based on tournament type"""
        if tournament_type == "single":
            return self.generate_single_elimination(participants)
        elif tournament_type == "double":
            return self.generate_double_elimination(participants)
        elif tournament_type == "round_robin":
            return self.generate_round_robin(participants)
        else:
            raise ValueError(f"Unknown tournament type: {tournament_type}")
    
    def seed_participants(self, participants: List[Dict]) -> List[Dict]:
        """Seed participants in standard tournament seeding"""
        # Sort by seed
        seeded = sorted(participants, key=lambda x: x.get("seed", 999))
        
        # Standard tournament seeding (1 vs 16, 8 vs 9, etc.)
        n = len(seeded)
        if n <= 2:
            return seeded
        
        # Find next power of 2
        next_pow2 = 2 ** math.ceil(math.log2(n))
        
        # Create optimal seeding
        result = [None] * next_pow2
        positions = self.get_seed_positions(next_pow2)
        
        for i, pos in enumerate(positions):
            if i < n:
                result[pos] = seeded[i]
        
        return result
    
    def get_seed_positions(self, n: int) -> List[int]:
        """Get optimal seed positions for bracket"""
        if n == 2:
            return [0, 1]
        
        half = n // 2
        first_half = self.get_seed_positions(half)
        second_half = [x + half for x in first_half]
        
        result = []
        for i in range(half):
            result.append(first_half[i])
            result.append(second_half[i])
        
        return result
    
    def generate_single_elimination(self, participants: List[Dict]) -> List[Dict]:
        """Generate single elimination bracket"""
        matches = []
        seeded_participants = self.seed_participants(participants)
        num_participants = len(seeded_participants)
        num_rounds = math.ceil(math.log2(num_participants))
        
        # Create matches mapping
        matches_by_round = {}
        
        # Create first round matches
        first_round_matches = []
        for i in range(0, num_participants, 2):
            p1 = seeded_participants[i] if i < len(seeded_participants) else None
            p2 = seeded_participants[i + 1] if i + 1 < len(seeded_participants) else None
            
            match = {
                "round": 1,
                "match_number": len(first_round_matches) + 1,
                "participant1_id": p1["id"] if p1 else None,
                "participant2_id": p2["id"] if p2 else None,
                "participant1_name": p1["name"] if p1 else "TBD",
                "participant2_name": p2["name"] if p2 else "TBD",
                "next_match_id": None,
                "bracket": "winners"
            }
            first_round_matches.append(match)
            matches.append(match)
        
        matches_by_round[1] = first_round_matches
        
        # Create subsequent rounds
        matches_per_round = len(first_round_matches)
        for round_num in range(2, num_rounds + 1):
            matches_per_round //= 2
            round_matches = []
            
            for match_num in range(matches_per_round):
                match = {
                    "round": round_num,
                    "match_number": match_num + 1,
                    "participant1_id": None,
                    "participant2_id": None,
                    "participant1_name": "TBD",
                    "participant2_name": "TBD",
                    "next_match_id": None,
                    "bracket": "winners"
                }
                round_matches.append(match)
                matches.append(match)
            
            matches_by_round[round_num] = round_matches
            
            # Link previous round matches to this round
            prev_round_matches = matches_by_round[round_num - 1]
            for i, match in enumerate(round_matches):
                if i * 2 < len(prev_round_matches):
                    prev_round_matches[i * 2]["next_match_id"] = match["id"] if "id" in match else f"match_{round_num}_{i*2}"
                if i * 2 + 1 < len(prev_round_matches):
                    prev_round_matches[i * 2 + 1]["next_match_id"] = match["id"] if "id" in match else f"match_{round_num}_{i*2}"
        
        return matches
    
    def generate_double_elimination(self, participants: List[Dict]) -> List[Dict]:
        """Generate double elimination bracket"""
        matches = []
        seeded_participants = self.seed_participants(participants)
        num_participants = len(seeded_participants)
        num_rounds = math.ceil(math.log2(num_participants))
        
        # Winners bracket
        winners_matches = self.generate_single_elimination(participants)
        for match in winners_matches:
            match["bracket"] = "winners"
            matches.append(match)
        
        # Losers bracket - simplified version
        losers_rounds = num_rounds * 2 - 1
        for round_num in range(1, losers_rounds + 1):
            match = {
                "round": round_num,
                "bracket": "losers",
                "match_number": 1,
                "participant1_id": None,
                "participant2_id": None,
                "participant1_name": "TBD",
                "participant2_name": "TBD",
                "next_match_id": None
            }
            matches.append(match)
        
        # Grand finals
        grand_finals = {
            "round": num_rounds + 1,
            "bracket": "grand_finals",
            "match_number": 1,
            "participant1_id": None,
            "participant2_id": None,
            "participant1_name": "Winners Bracket Champion",
            "participant2_name": "Losers Bracket Champion",
            "next_match_id": None,
            "is_final": True
        }
        matches.append(grand_finals)
        
        # Optional second grand finals match
        grand_finals_second = {
            "round": num_rounds + 2,
            "bracket": "grand_finals",
            "match_number": 2,
            "participant1_id": None,
            "participant2_id": None,
            "participant1_name": "Grand Finals Winner",
            "participant2_name": "Grand Finals Loser",
            "next_match_id": None,
            "is_final": True
        }
        matches.append(grand_finals_second)
        
        return matches
    
    def generate_round_robin(self, participants: List[Dict]) -> List[Dict]:
        """Generate round robin matches"""
        matches = []
        match_number = 1
        
        for i in range(len(participants)):
            for j in range(i + 1, len(participants)):
                match = {
                    "round": 1,
                    "match_number": match_number,
                    "participant1_id": participants[i]["id"],
                    "participant2_id": participants[j]["id"],
                    "participant1_name": participants[i]["name"],
                    "participant2_name": participants[j]["name"],
                    "next_match_id": None,
                    "bracket": "round_robin"
                }
                matches.append(match)
                match_number += 1
        
        return matches
    
    def calculate_round_robin_standings(self, tournament_id: str, participants: List[Dict], matches: List[Dict]) -> List[Dict]:
        """Calculate round robin standings"""
        standings = {}
        
        # Initialize standings
        for p in participants:
            standings[p["id"]] = {
                "participant_id": p["id"],
                "participant_name": p["name"],
                "wins": 0,
                "losses": 0,
                "draws": 0,
                "points": 0,
                "games_played": 0
            }
        
        # Calculate from matches
        for match in matches:
            if match["score1"] is not None and match["score2"] is not None:
                p1_id = match["participant1_id"]
                p2_id = match["participant2_id"]
                
                if p1_id and p2_id:
                    standings[p1_id]["games_played"] += 1
                    standings[p2_id]["games_played"] += 1
                    
                    if match["score1"] > match["score2"]:
                        standings[p1_id]["wins"] += 1
                        standings[p1_id]["points"] += 3
                        standings[p2_id]["losses"] += 1
                    elif match["score2"] > match["score1"]:
                        standings[p2_id]["wins"] += 1
                        standings[p2_id]["points"] += 3
                        standings[p1_id]["losses"] += 1
                    else:
                        standings[p1_id]["draws"] += 1
                        standings[p2_id]["draws"] += 1
                        standings[p1_id]["points"] += 1
                        standings[p2_id]["points"] += 1
        
        return list(standings.values())

# ==================== Export Functions ====================
class ExportHandler:
    @staticmethod
    def generate_qr_code(tournament_id: str, join_code: str) -> str:
        """Generate QR code for tournament join"""
        url = f"{FRONTEND_URL}/tournament/{join_code}"
        qr = qrcode.QRCode(version=1, box_size=10, border=5)
        qr.add_data(url)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white")
        
        # Convert to base64
        buffered = io.BytesIO()
        img.save(buffered, format="PNG")
        img_str = base64.b64encode(buffered.getvalue()).decode()
        
        return f"data:image/png;base64,{img_str}"
    
    @staticmethod
    def export_as_image(tournament: Dict, matches: List[Dict], participants: List[Dict]) -> str:
        """Export bracket as image"""
        # Create image
        img_width = 1200
        img_height = 800
        img = Image.new('RGB', (img_width, img_height), color='white')
        draw = ImageDraw.Draw(img)
        
        # Try to load font, fallback to default
        try:
            font = ImageFont.truetype("arial.ttf", 16)
            title_font = ImageFont.truetype("arial.ttf", 24)
        except:
            font = ImageFont.load_default()
            title_font = ImageFont.load_default()
        
        # Draw title
        draw.text((50, 30), f"Xtourny - {tournament['name']}", fill='black', font=title_font)
        draw.text((50, 60), f"Type: {tournament['type']} | Status: {tournament['status']}", fill='gray', font=font)
        
        # Draw bracket
        y_offset = 100
        matches_by_round = {}
        
        for match in matches:
            round_num = match["round"]
            if round_num not in matches_by_round:
                matches_by_round[round_num] = []
            matches_by_round[round_num].append(match)
        
        x = 50
        for round_num in sorted(matches_by_round.keys()):
            round_matches = matches_by_round[round_num]
            y = y_offset
            
            draw.text((x, y - 20), f"Round {round_num}", fill='darkblue', font=font)
            
            for match in round_matches:
                # Draw match box
                draw.rectangle([x, y, x + 200, y + 60], outline='black', width=1)
                
                # Participant 1
                p1_name = match.get("participant1_name", "TBD")[:20]
                score1 = match.get("score1", "")
                draw.text((x + 5, y + 5), f"{p1_name}", fill='black', font=font)
                if score1 is not None:
                    draw.text((x + 170, y + 5), str(score1), fill='black', font=font)
                
                # Participant 2
                p2_name = match.get("participant2_name", "TBD")[:20]
                score2 = match.get("score2", "")
                draw.text((x + 5, y + 35), f"{p2_name}", fill='black', font=font)
                if score2 is not None:
                    draw.text((x + 170, y + 35), str(score2), fill='black', font=font)
                
                # Highlight winner
                if match.get("winner_id"):
                    if match["winner_id"] == match.get("participant1_id"):
                        draw.rectangle([x, y, x + 200, y + 30], outline='green', width=2)
                    else:
                        draw.rectangle([x, y + 30, x + 200, y + 60], outline='green', width=2)
                
                y += 70
            
            x += 250
        
        # Convert to base64
        buffered = io.BytesIO()
        img.save(buffered, format="PNG")
        img_str = base64.b64encode(buffered.getvalue()).decode()
        
        return f"data:image/png;base64,{img_str}"
    
    @staticmethod
    def export_as_pdf(tournament: Dict, matches: List[Dict], participants: List[Dict]) -> bytes:
        """Export bracket as PDF"""
        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=landscape(letter))
        width, height = landscape(letter)
        
        # Title
        c.setFont("Helvetica-Bold", 20)
        c.drawString(50, height - 50, f"Xtourny - {tournament['name']}")
        
        c.setFont("Helvetica", 12)
        c.drawString(50, height - 70, f"Type: {tournament['type']} | Status: {tournament['status']}")
        c.drawString(50, height - 85, f"Created: {tournament['created_at'][:10]}")
        
        # Participants list
        c.setFont("Helvetica-Bold", 14)
        c.drawString(50, height - 120, "Participants:")
        
        c.setFont("Helvetica", 10)
        y = height - 140
        for i, p in enumerate(participants[:20]):  # Limit to first 20
            c.drawString(70, y, f"{i+1}. {p['name']}")
            y -= 15
        
        # Matches
        c.setFont("Helvetica-Bold", 14)
        c.drawString(300, height - 120, "Bracket:")
        
        c.setFont("Helvetica", 10)
        y = height - 140
        matches_by_round = {}
        
        for match in matches[:30]:  # Limit to first 30 matches
            round_num = match["round"]
            if round_num not in matches_by_round:
                matches_by_round[round_num] = []
            matches_by_round[round_num].append(match)
        
        for round_num in sorted(matches_by_round.keys()):
            c.setFont("Helvetica-Bold", 12)
            c.drawString(300, y, f"Round {round_num}:")
            y -= 15
            
            c.setFont("Helvetica", 10)
            for match in matches_by_round[round_num][:5]:  # Limit per round
                p1 = match.get("participant1_name", "TBD")[:15]
                p2 = match.get("participant2_name", "TBD")[:15]
                s1 = match.get("score1", "")
                s2 = match.get("score2", "")
                
                line = f"{p1} vs {p2}"
                if s1 is not None and s2 is not None:
                    line += f" ({s1}-{s2})"
                
                c.drawString(320, y, line)
                y -= 15
                
                if y < 50:  # New page
                    c.showPage()
                    y = height - 50
        
        c.save()
        buffer.seek(0)
        return buffer.getvalue()

# ==================== FastAPI Models ====================
class TournamentCreate(BaseModel):
    name: str
    type: str = Field(..., regex="^(single|double|round_robin)$")
    participants: List[str]
    host_password: Optional[str] = None
    host_discord_id: Optional[str] = None

class TournamentUpdate(BaseModel):
    name: Optional[str] = None
    status: Optional[str] = Field(None, regex="^(pending|active|paused|completed)$")
    visibility: Optional[str] = Field(None, regex="^(public|private)$")

class AuthRequest(BaseModel):
    password: Optional[str] = None
    discord_id: Optional[str] = None

class AuthResponse(BaseModel):
    token: str
    expires: datetime
    is_host: bool

class ScoreSubmit(BaseModel):
    score1: int
    score2: int

class ParticipantAdd(BaseModel):
    name: str
    discord_id: Optional[str] = None

class PasswordUpdate(BaseModel):
    password: str

class VisibilityUpdate(BaseModel):
    visibility: str

class ForfeitRequest(BaseModel):
    participant_id: str

class SwapRequest(BaseModel):
    participant1_id: str
    participant2_id: str

# ==================== FastAPI App ====================
app = FastAPI(title="Xtourny API", description="Tournament Bracket System", version="1.0.0")
security = HTTPBearer()
auth_handler = AuthHandler()
db = Database()
bracket_gen = BracketGenerator()
export_handler = ExportHandler()

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# WebSocket connections for real-time updates
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, tournament_id: str, websocket: WebSocket):
        await websocket.accept()
        if tournament_id not in self.active_connections:
            self.active_connections[tournament_id] = []
        self.active_connections[tournament_id].append(websocket)

    def disconnect(self, tournament_id: str, websocket: WebSocket):
        if tournament_id in self.active_connections:
            self.active_connections[tournament_id].remove(websocket)

    async def broadcast(self, tournament_id: str, message: dict):
        if tournament_id in self.active_connections:
            for connection in self.active_connections[tournament_id]:
                try:
                    await connection.send_json(message)
                except:
                    pass

manager = ConnectionManager()

# ==================== FastAPI Endpoints ====================
@app.get("/")
async def root():
    return {
        "name": "Xtourny API",
        "version": "1.0.0",
        "description": "Tournament Bracket System",
        "endpoints": {
            "tournaments": "/tournaments",
            "docs": "/docs",
            "redoc": "/redoc"
        }
    }

@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat()}

@app.get("/api/tournaments", response_model=List[Dict])
async def get_tournaments():
    """Get all public tournaments"""
    try:
        tournaments = db.get_tournaments(visibility="public")
        
        # Add participant counts
        for t in tournaments:
            participants = db.get_participant_names(t["id"])
            t["participant_count"] = len(participants)
        
        return tournaments
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/tournaments/{tournament_id}")
async def get_tournament(tournament_id: str):
    """Get specific tournament details"""
    try:
        tournament = db.get_tournament(tournament_id)
        if not tournament:
            raise HTTPException(status_code=404, detail="Tournament not found")
        
        # Add matches and participants
        tournament["matches"] = db.get_matches(tournament_id)
        tournament["participants"] = db.get_participants(tournament_id)
        tournament["participant_names"] = [p["name"] for p in tournament["participants"]]
        
        # Add standings for round robin
        if tournament["type"] == "round_robin":
            tournament["standings"] = db.get_standings(tournament_id)
        
        # Generate QR code
        tournament["qr_code"] = export_handler.generate_qr_code(tournament_id, tournament["join_code"])
        
        return tournament
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/tournament/code/{join_code}")
async def get_tournament_by_code(join_code: str):
    """Get tournament by join code"""
    try:
        tournament = db.get_tournament_by_code(join_code)
        if not tournament:
            raise HTTPException(status_code=404, detail="Tournament not found")
        
        # Redirect to tournament ID endpoint
        tournament["matches"] = db.get_matches(tournament["id"])
        tournament["participants"] = db.get_participants(tournament["id"])
        tournament["participant_names"] = [p["name"] for p in tournament["participants"]]
        
        if tournament["type"] == "round_robin":
            tournament["standings"] = db.get_standings(tournament["id"])
        
        tournament["qr_code"] = export_handler.generate_qr_code(tournament["id"], join_code)
        
        return tournament
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/tournaments")
async def create_tournament(tournament: TournamentCreate):
    """Create a new tournament"""
    try:
        # Validate participant count
        if len(tournament.participants) < 2 or len(tournament.participants) > 64:
            raise HTTPException(status_code=400, detail="Participants must be between 2 and 64")
        
        # Hash password if provided
        password_hash = None
        if tournament.host_password:
            password_hash = auth_handler.hash_password(tournament.host_password)
        
        # Create tournament
        tournament_data = {
            "name": tournament.name,
            "type": tournament.type,
            "status": "pending",
            "visibility": "public",
            "host_password_hash": password_hash,
            "host_discord_id": tournament.host_discord_id,
            "settings": {
                "best_of": 1,
                "auto_advance": False,
                "allow_draws": tournament.type == "round_robin"
            }
        }
        
        result = db.create_tournament(tournament_data)
        tournament_id = result["id"]
        
        # Add participants
        db.add_participants_bulk(tournament_id, tournament.participants)
        
        # Get participants with IDs
        participants = db.get_participants(tournament_id)
        
        # Generate initial bracket
        matches = bracket_gen.generate_bracket(tournament.type, participants)
        
        # Save matches
        db.create_matches_bulk(tournament_id, matches)
        
        # If host Discord ID provided, add as host
        if tournament.host_discord_id:
            db.add_host(tournament_id, tournament.host_discord_id, "Discord Host")
        
        # Get full tournament data
        full_tournament = db.get_tournament(tournament_id)
        full_tournament["participants"] = participants
        full_tournament["matches"] = db.get_matches(tournament_id)
        full_tournament["qr_code"] = export_handler.generate_qr_code(tournament_id, full_tournament["join_code"])
        
        return full_tournament
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/tournaments/{tournament_id}/auth")
async def authenticate_host(tournament_id: str, auth: AuthRequest):
    """Authenticate tournament host"""
    try:
        tournament = db.get_tournament(tournament_id)
        if not tournament:
            raise HTTPException(status_code=404, detail="Tournament not found")
        
        is_host = False
        
        # Check Discord host role
        if auth.discord_id:
            if db.is_host(tournament_id, auth.discord_id):
                is_host = True
        
        # Check password
        if auth.password and tournament.get("host_password_hash"):
            if auth_handler.verify_password(auth.password, tournament["host_password_hash"]):
                is_host = True
        
        if not is_host:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        
        # Generate token
        token = auth_handler.create_token(tournament_id, auth.discord_id)
        expires = datetime.utcnow() + timedelta(hours=24)
        
        return AuthResponse(token=token, expires=expires, is_host=True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/tournaments/{tournament_id}/host-data")
async def get_host_data(
    tournament_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Get host-specific tournament data"""
    try:
        # Verify token
        token_data = auth_handler.verify_token(credentials.credentials)
        if token_data["tournament_id"] != tournament_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        tournament = db.get_tournament(tournament_id)
        if not tournament:
            raise HTTPException(status_code=404, detail="Tournament not found")
        
        # Get all data
        matches = db.get_matches(tournament_id)
        participants = db.get_participants(tournament_id)
        
        # Calculate stats
        completed_matches = len([m for m in matches if m["status"] == "completed"])
        total_matches = len(matches)
        
        # Get hosts
        hosts = db.get_hosts(tournament_id)
        
        return {
            **tournament,
            "matches": matches,
            "participants": participants,
            "completed_matches": completed_matches,
            "total_matches": total_matches,
            "progress": (completed_matches / total_matches * 100) if total_matches > 0 else 0,
            "hosts": hosts
        }
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/matches/{match_id}/score")
async def submit_match_score(
    match_id: str,
    score: ScoreSubmit,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Submit match scores"""
    try:
        # Get match details
        match = db.get_match(match_id)
        if not match:
            raise HTTPException(status_code=404, detail="Match not found")
        
        # Verify host access
        token_data = auth_handler.verify_token(credentials.credentials)
        if token_data["tournament_id"] != match["tournament_id"]:
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Update match scores
        updated_match = db.update_match_score(match_id, score.score1, score.score2)
        
        # For round robin, update standings
        tournament = db.get_tournament(match["tournament_id"])
        if tournament["type"] == "round_robin":
            participants = db.get_participants(match["tournament_id"])
            all_matches = db.get_matches(match["tournament_id"])
            standings = bracket_gen.calculate_round_robin_standings(
                match["tournament_id"],
                participants,
                all_matches
            )
            db.update_standings(match["tournament_id"], standings)
        
        # Broadcast update
        await manager.broadcast(match["tournament_id"], {
            "type": "match_update",
            "match_id": match_id,
            "scores": [score.score1, score.score2]
        })
        
        return {"message": "Score submitted successfully", "match": updated_match}
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/matches/{match_id}/advance")
async def advance_match(
    match_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Force advance a match"""
    try:
        match = db.get_match(match_id)
        if not match:
            raise HTTPException(status_code=404, detail="Match not found")
        
        token_data = auth_handler.verify_token(credentials.credentials)
        if token_data["tournament_id"] != match["tournament_id"]:
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Auto-advance with default winner (first participant)
        if match["participant1_id"]:
            winner_id = match["participant1_id"]
            db.update_match_score(match_id, 1, 0)  # Default scores
        elif match["participant2_id"]:
            winner_id = match["participant2_id"]
            db.update_match_score(match_id, 0, 1)  # Default scores
        else:
            raise HTTPException(status_code=400, detail="No participants to advance")
        
        # Broadcast update
        await manager.broadcast(match["tournament_id"], {
            "type": "match_advanced",
            "match_id": match_id,
            "winner_id": winner_id
        })
        
        return {"message": "Match advanced"}
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/matches/{match_id}/forfeit")
async def forfeit_match(
    match_id: str,
    forfeit: ForfeitRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Declare forfeit for a match"""
    try:
        match = db.get_match(match_id)
        if not match:
            raise HTTPException(status_code=404, detail="Match not found")
        
        token_data = auth_handler.verify_token(credentials.credentials)
        if token_data["tournament_id"] != match["tournament_id"]:
            raise HTTPException(status_code=403, detail="Access denied")
        
        db.forfeit_match(match_id, forfeit.participant_id)
        
        # Broadcast update
        await manager.broadcast(match["tournament_id"], {
            "type": "match_forfeit",
            "match_id": match_id,
            "forfeiting_participant": forfeit.participant_id
        })
        
        return {"message": "Forfeit declared"}
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/tournaments/{tournament_id}/next-round")
async def start_next_round(
    tournament_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Start the next round"""
    try:
        token_data = auth_handler.verify_token(credentials.credentials)
        if token_data["tournament_id"] != tournament_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        tournament = db.get_tournament(tournament_id)
        if not tournament:
            raise HTTPException(status_code=404, detail="Tournament not found")
        
        if tournament["type"] == "round_robin":
            raise HTTPException(status_code=400, detail="Round robin doesn't have rounds")
        
        # Get current matches
        matches = db.get_matches(tournament_id)
        current_round = max([m["round"] for m in matches]) if matches else 0
        
        # Check if all matches in current round are completed
        current_round_matches = [m for m in matches if m["round"] == current_round]
        if not all(m["status"] == "completed" for m in current_round_matches):
            raise HTTPException(status_code=400, detail="Complete all matches in current round first")
        
        # Generate next round matches
        participants = db.get_participants(tournament_id)
        new_matches = bracket_gen.generate_bracket(tournament["type"], participants)
        
        # Filter for next round
        next_round_matches = [m for m in new_matches if m["round"] == current_round + 1]
        
        if next_round_matches:
            db.create_matches_bulk(tournament_id, next_round_matches)
        
        # Update tournament status
        db.update_tournament(tournament_id, {"status": "active"})
        
        # Broadcast update
        await manager.broadcast(tournament_id, {
            "type": "new_round",
            "round": current_round + 1,
            "matches": next_round_matches
        })
        
        return {"message": f"Round {current_round + 1} started", "matches": next_round_matches}
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/tournaments/{tournament_id}/pause")
async def pause_tournament(
    tournament_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Pause tournament"""
    try:
        token_data = auth_handler.verify_token(credentials.credentials)
        if token_data["tournament_id"] != tournament_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        db.update_tournament(tournament_id, {"status": "paused"})
        
        # Broadcast update
        await manager.broadcast(tournament_id, {"type": "tournament_paused"})
        
        return {"message": "Tournament paused"}
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/tournaments/{tournament_id}/resume")
async def resume_tournament(
    tournament_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Resume tournament"""
    try:
        token_data = auth_handler.verify_token(credentials.credentials)
        if token_data["tournament_id"] != tournament_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        db.update_tournament(tournament_id, {"status": "active"})
        
        # Broadcast update
        await manager.broadcast(tournament_id, {"type": "tournament_resumed"})
        
        return {"message": "Tournament resumed"}
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/tournaments/{tournament_id}/participants")
async def add_participant(
    tournament_id: str,
    participant: ParticipantAdd,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Add participant to tournament"""
    try:
        token_data = auth_handler.verify_token(credentials.credentials)
        if token_data["tournament_id"] != tournament_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        tournament = db.get_tournament(tournament_id)
        if tournament["status"] != "pending":
            raise HTTPException(status_code=400, detail="Can only add participants to pending tournaments")
        
        participants = db.get_participants(tournament_id)
        if len(participants) >= 64:
            raise HTTPException(status_code=400, detail="Maximum participants reached")
        
        new_participant = db.add_participant(tournament_id, participant.name, participant.discord_id)
        
        # Broadcast update
        await manager.broadcast(tournament_id, {
            "type": "participant_added",
            "participant": new_participant
        })
        
        return {"message": "Participant added", "participant": new_participant}
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/tournaments/{tournament_id}/participants/{participant_id}")
async def remove_participant(
    tournament_id: str,
    participant_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Remove participant from tournament"""
    try:
        token_data = auth_handler.verify_token(credentials.credentials)
        if token_data["tournament_id"] != tournament_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        tournament = db.get_tournament(tournament_id)
        if tournament["status"] != "pending":
            raise HTTPException(status_code=400, detail="Can only remove participants from pending tournaments")
        
        db.remove_participant(tournament_id, participant_id)
        
        # Broadcast update
        await manager.broadcast(tournament_id, {
            "type": "participant_removed",
            "participant_id": participant_id
        })
        
        return {"message": "Participant removed"}
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/tournaments/{tournament_id}/participants/swap")
async def swap_participants(
    tournament_id: str,
    swap: SwapRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Swap two participants"""
    try:
        token_data = auth_handler.verify_token(credentials.credentials)
        if token_data["tournament_id"] != tournament_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        tournament = db.get_tournament(tournament_id)
        if tournament["status"] != "pending":
            raise HTTPException(status_code=400, detail="Can only swap participants in pending tournaments")
        
        db.swap_participants(tournament_id, swap.participant1_id, swap.participant2_id)
        
        # Broadcast update
        await manager.broadcast(tournament_id, {
            "type": "participants_swapped",
            "participant1_id": swap.participant1_id,
            "participant2_id": swap.participant2_id
        })
        
        return {"message": "Participants swapped"}
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/tournaments/{tournament_id}")
async def update_tournament(
    tournament_id: str,
    update: TournamentUpdate,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Update tournament settings"""
    try:
        token_data = auth_handler.verify_token(credentials.credentials)
        if token_data["tournament_id"] != tournament_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        update_data = {k: v for k, v in update.dict().items() if v is not None}
        if update_data:
            db.update_tournament(tournament_id, update_data)
            
            # Broadcast update
            await manager.broadcast(tournament_id, {
                "type": "tournament_updated",
                "updates": update_data
            })
        
        return {"message": "Tournament updated"}
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/tournaments/{tournament_id}/password")
async def update_password(
    tournament_id: str,
    password_update: PasswordUpdate,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Update host password"""
    try:
        token_data = auth_handler.verify_token(credentials.credentials)
        if token_data["tournament_id"] != tournament_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        password_hash = auth_handler.hash_password(password_update.password)
        db.update_tournament(tournament_id, {"host_password_hash": password_hash})
        
        return {"message": "Password updated"}
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/tournaments/{tournament_id}/visibility")
async def update_visibility(
    tournament_id: str,
    visibility_update: VisibilityUpdate,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Update tournament visibility"""
    try:
        token_data = auth_handler.verify_token(credentials.credentials)
        if token_data["tournament_id"] != tournament_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        db.update_tournament(tournament_id, {"visibility": visibility_update.visibility})
        
        # Broadcast update
        await manager.broadcast(tournament_id, {
            "type": "visibility_updated",
            "visibility": visibility_update.visibility
        })
        
        return {"message": f"Tournament is now {visibility_update.visibility}"}
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/tournaments/{tournament_id}/reset")
async def reset_tournament(
    tournament_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Reset tournament to initial state"""
    try:
        token_data = auth_handler.verify_token(credentials.credentials)
        if token_data["tournament_id"] != tournament_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        tournament = db.get_tournament(tournament_id)
        participants = db.get_participants(tournament_id)
        
        # Delete existing matches
        db.delete_matches(tournament_id)
        
        # Generate new bracket
        matches = bracket_gen.generate_bracket(tournament["type"], participants)
        
        # Save new matches
        db.create_matches_bulk(tournament_id, matches)
        
        # Update status
        db.update_tournament(tournament_id, {"status": "pending"})
        
        # Broadcast update
        await manager.broadcast(tournament_id, {"type": "tournament_reset"})
        
        return {"message": "Tournament reset"}
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/tournaments/{tournament_id}")
async def delete_tournament(
    tournament_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    """Delete tournament"""
    try:
        token_data = auth_handler.verify_token(credentials.credentials)
        if token_data["tournament_id"] != tournament_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        db.delete_tournament(tournament_id)
        
        return {"message": "Tournament deleted"}
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/tournaments/{tournament_id}/export/image")
async def export_tournament_image(tournament_id: str):
    """Export tournament as image"""
    try:
        tournament = db.get_tournament(tournament_id)
        if not tournament:
            raise HTTPException(status_code=404, detail="Tournament not found")
        
        matches = db.get_matches(tournament_id)
        participants = db.get_participants(tournament_id)
        
        image_data = export_handler.export_as_image(tournament, matches, participants)
        
        return {"image": image_data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/tournaments/{tournament_id}/export/pdf")
async def export_tournament_pdf(tournament_id: str):
    """Export tournament as PDF"""
    try:
        tournament = db.get_tournament(tournament_id)
        if not tournament:
            raise HTTPException(status_code=404, detail="Tournament not found")
        
        matches = db.get_matches(tournament_id)
        participants = db.get_participants(tournament_id)
        
        pdf_data = export_handler.export_as_pdf(tournament, matches, participants)
        
        return FileResponse(
            io.BytesIO(pdf_data),
            media_type='application/pdf',
            filename=f"xtourny_{tournament_id}.pdf"
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.websocket("/ws/{tournament_id}")
async def websocket_endpoint(websocket: WebSocket, tournament_id: str):
    """WebSocket for real-time updates"""
    await manager.connect(tournament_id, websocket)
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(tournament_id, websocket)

# ==================== Discord Bot ====================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

class XtournyBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.db = db
        self.auth_handler = auth_handler
        self.bracket_gen = bracket_gen
        
    async def setup_hook(self):
        await self.tree.sync()
        print(f"Synced commands for {self.user}")

bot = XtournyBot()

# Discord UI Components
class HostPasswordModal(Modal, title="Host Authentication"):
    def __init__(self, tournament_id: str, interaction: discord.Interaction):
        super().__init__()
        self.tournament_id = tournament_id
        self.original_interaction = interaction
    
    password = TextInput(
        label="Tournament Password",
        placeholder="Enter the host password",
        style=discord.TextStyle.short,
        required=True
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Authenticate with API
            tournament = self.bot.db.get_tournament(self.tournament_id)
            if not tournament:
                await interaction.response.send_message("Tournament not found!", ephemeral=True)
                return
            
            password_hash = tournament.get("host_password_hash")
            if not password_hash:
                await interaction.response.send_message("This tournament doesn't have a password set!", ephemeral=True)
                return
            
            if self.bot.auth_handler.verify_password(str(self.password), password_hash):
                # Generate token
                token = self.bot.auth_handler.create_token(self.tournament_id, str(interaction.user.id))
                
                # Store token in user's session (simplified - in production use database)
                embed = discord.Embed(
                    title="✅ Authentication Successful",
                    description="You now have host access to this tournament!",
                    color=discord.Color.green()
                )
                
                view = HostControlView(self.tournament_id, token)
                await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
            else:
                await interaction.response.send_message("❌ Incorrect password!", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Error: {str(e)}", ephemeral=True)

class HostControlView(View):
    def __init__(self, tournament_id: str, token: str):
        super().__init__(timeout=300)
        self.tournament_id = tournament_id
        self.token = token
    
    @discord.ui.button(label="View Matches", style=discord.ButtonStyle.primary)
    async def view_matches(self, interaction: discord.Interaction, button: discord.ui.Button):
        matches = bot.db.get_pending_matches(self.tournament_id)
        if not matches:
            await interaction.response.send_message("No pending matches!", ephemeral=True)
            return
        
        embed = discord.Embed(
            title="Pending Matches",
            color=discord.Color.blue()
        )
        
        for match in matches[:10]:  # Limit to 10
            p1 = match.get("participant1_name", "TBD")
            p2 = match.get("participant2_name", "TBD")
            embed.add_field(
                name=f"Match {match['match_number']} (Round {match['round']})",
                value=f"{p1} vs {p2}\nID: `{match['id']}`",
                inline=False
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)
    
    @discord.ui.button(label="Tournament Status", style=discord.ButtonStyle.secondary)
    async def tournament_status(self, interaction: discord.Interaction, button: discord.ui.Button):
        tournament = bot.db.get_tournament(self.tournament_id)
        if not tournament:
            await interaction.response.send_message("Tournament not found!", ephemeral=True)
            return
        
        matches = bot.db.get_matches(self.tournament_id)
        participants = bot.db.get_participants(self.tournament_id)
        completed = len([m for m in matches if m["status"] == "completed"])
        total = len(matches)
        
        embed = discord.Embed(
            title=tournament["name"],
            description=f"Status: {tournament['status']}\nType: {tournament['type']}",
            color=discord.Color.gold()
        )
        embed.add_field(name="Participants", value=len(participants), inline=True)
        embed.add_field(name="Matches", value=f"{completed}/{total}", inline=True)
        embed.add_field(name="Join Code", value=tournament["join_code"], inline=True)
        
        if completed > 0:
            embed.add_field(name="Progress", value=f"{(completed/total*100):.1f}%", inline=True)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

class ScoreSubmissionModal(Modal, title="Submit Match Score"):
    def __init__(self, match_id: str, token: str):
        super().__init__()
        self.match_id = match_id
        self.token = token
    
    score1 = TextInput(
        label="Participant 1 Score",
        placeholder="Enter score",
        style=discord.TextStyle.short,
        required=True
    )
    
    score2 = TextInput(
        label="Participant 2 Score",
        placeholder="Enter score",
        style=discord.TextStyle.short,
        required=True
    )
    
    async def on_submit(self, interaction: discord.Interaction):
        try:
            # Call API to submit score
            match = bot.db.get_match(self.match_id)
            if not match:
                await interaction.response.send_message("Match not found!", ephemeral=True)
                return
            
            # Update via API (simplified - in production would call the API endpoint)
            bot.db.update_match_score(
                self.match_id,
                int(str(self.score1)),
                int(str(self.score2))
            )
            
            embed = discord.Embed(
                title="✅ Score Submitted",
                description=f"Score: {self.score1} - {self.score2}",
                color=discord.Color.green()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
            # Broadcast update via WebSocket
            # In production, would trigger WebSocket broadcast
        except Exception as e:
            await interaction.response.send_message(f"Error: {str(e)}", ephemeral=True)

# Discord Slash Commands
@bot.tree.command(name="tournament", description="Create a new tournament")
@app_commands.describe(
    name="Tournament name",
    type="Tournament type",
    participants="List of participants (comma-separated or line breaks)"
)
async def tournament_create(
    interaction: discord.Interaction,
    name: str,
    type: str,
    participants: str
):
    """Create a new tournament"""
    await interaction.response.defer(ephemeral=True)
    
    try:
        # Parse participants
        participant_list = [p.strip() for p in participants.replace('\n', ',').split(',') if p.strip()]
        
        if len(participant_list) < 2 or len(participant_list) > 64:
            await interaction.followup.send("Participants must be between 2 and 64!", ephemeral=True)
            return
        
        # Check if user has host role
        host_role = discord.utils.get(interaction.guild.roles, name=HOST_ROLE_NAME)
        is_host = host_role in interaction.user.roles if host_role else False
        
        # Create tournament
        tournament_data = {
            "name": name,
            "type": type,
            "participants": participant_list,
            "host_discord_id": str(interaction.user.id) if is_host else None
        }
        
        # Call API to create tournament
        # In production, would call the FastAPI endpoint
        # For now, create directly
        password_hash = None
        if not is_host:
            # Generate random password for non-hosts
            temp_password = secrets.token_urlsafe(8)
            password_hash = auth_handler.hash_password(temp_password)
        
        tournament = {
            "id": str(uuid.uuid4()),
            "name": name,
            "type": type,
            "status": "pending",
            "join_code": db.generate_join_code(),
            "host_discord_id": str(interaction.user.id) if is_host else None,
            "host_password_hash": password_hash
        }
        
        # Add participants
        for p in participant_list:
            db.add_participant(tournament["id"], p)
        
        # Generate bracket
        participants_with_ids = db.get_participants(tournament["id"])
        matches = bracket_gen.generate_bracket(type, participants_with_ids)
        db.create_matches_bulk(tournament["id"], matches)
        
        # Create embed
        embed = discord.Embed(
            title="🎮 Tournament Created!",
            description=f"**{name}**",
            color=discord.Color.green()
        )
        embed.add_field(name="Type", value=type, inline=True)
        embed.add_field(name="Participants", value=len(participant_list), inline=True)
        embed.add_field(name="Join Code", value=tournament["join_code"], inline=True)
        embed.add_field(name="Tournament ID", value=tournament["id"], inline=False)
        embed.add_field(name="View Online", value=f"{FRONTEND_URL}/tournament/{tournament['join_code']}", inline=False)
        
        if not is_host:
            embed.add_field(
                name="Host Password",
                value=f"`{temp_password}`\n*Save this password to host the tournament*",
                inline=False
            )
        
        # Add view button
        view = View()
        button = Button(
            label="View Tournament",
            style=discord.ButtonStyle.link,
            url=f"{FRONTEND_URL}/tournament/{tournament['join_code']}"
        )
        view.add_item(button)
        
        await interaction.followup.send(embed=embed, view=view)
        
    except Exception as e:
        await interaction.followup.send(f"Error creating tournament: {str(e)}", ephemeral=True)

@bot.tree.command(name="tournaments", description="List active tournaments")
async def tournaments_list(interaction: discord.Interaction):
    """List active tournaments"""
    await interaction.response.defer()
    
    try:
        tournaments = db.get_tournaments(visibility="public", status="active")
        
        if not tournaments:
            await interaction.followup.send("No active tournaments found!")
            return
        
        # Paginate
        pages = []
        current_page = []
        
        for t in tournaments:
            participants = db.get_participant_names(t["id"])
            current_page.append(f"**{t['name']}**\n"
                               f"ID: `{t['id']}` | Type: {t['type']}\n"
                               f"Participants: {len(participants)} | Code: {t['join_code']}\n")
            
            if len(current_page) == 5:
                pages.append(current_page)
                current_page = []
        
        if current_page:
            pages.append(current_page)
        
        for i, page in enumerate(pages):
            embed = discord.Embed(
                title=f"Active Tournaments (Page {i+1}/{len(pages)})",
                description="\n".join(page),
                color=discord.Color.blue()
            )
            await interaction.followup.send(embed=embed)
            
    except Exception as e:
        await interaction.followup.send(f"Error: {str(e)}")

@bot.tree.command(name="tournament_view", description="View tournament details")
@app_commands.describe(
    identifier="Tournament ID or Join Code"
)
async def tournament_view(interaction: discord.Interaction, identifier: str):
    """View tournament details"""
    await interaction.response.defer()
    
    try:
        # Try to find by ID or code
        tournament = db.get_tournament(identifier)
        if not tournament:
            tournament = db.get_tournament_by_code(identifier)
        
        if not tournament:
            await interaction.followup.send("Tournament not found!")
            return
        
        participants = db.get_participant_names(tournament["id"])
        matches = db.get_matches(tournament["id"])
        completed = len([m for m in matches if m["status"] == "completed"])
        total = len(matches)
        
        embed = discord.Embed(
            title=tournament["name"],
            description=f"Status: {tournament['status']} | Type: {tournament['type']}",
            color=discord.Color.gold()
        )
        embed.add_field(name="Participants", value=len(participants), inline=True)
        embed.add_field(name="Matches", value=f"{completed}/{total}", inline=True)
        embed.add_field(name="Join Code", value=tournament["join_code"], inline=True)
        
        if participants:
            embed.add_field(
                name="Participant List",
                value="\n".join(participants[:10]) + ("\n..." if len(participants) > 10 else ""),
                inline=False
            )
        
        # Add view button
        view = View()
        button = Button(
            label="View Online",
            style=discord.ButtonStyle.link,
            url=f"{FRONTEND_URL}/tournament/{tournament['join_code']}"
        )
        view.add_item(button)
        
        await interaction.followup.send(embed=embed, view=view)
        
    except Exception as e:
        await interaction.followup.send(f"Error: {str(e)}")

@bot.tree.command(name="match_list", description="List pending matches")
@app_commands.describe(
    tournament_id="Tournament ID"
)
async def match_list(interaction: discord.Interaction, tournament_id: str):
    """List pending matches"""
    await interaction.response.defer()
    
    try:
        tournament = db.get_tournament(tournament_id)
        if not tournament:
            await interaction.followup.send("Tournament not found!")
            return
        
        matches = db.get_pending_matches(tournament_id)
        
        if not matches:
            await interaction.followup.send("No pending matches!")
            return
        
        embed = discord.Embed(
            title=f"Pending Matches - {tournament['name']}",
            color=discord.Color.blue()
        )
        
        for match in matches[:15]:  # Limit to 15
            p1 = match.get("participant1_name", "TBD")
            p2 = match.get("participant2_name", "TBD")
            embed.add_field(
                name=f"Round {match['round']} - Match {match['match_number']}",
                value=f"{p1} vs {p2}\nID: `{match['id']}`",
                inline=False
            )
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"Error: {str(e)}")

@bot.tree.command(name="standings", description="View tournament standings")
@app_commands.describe(
    tournament_id="Tournament ID"
)
async def standings(interaction: discord.Interaction, tournament_id: str):
    """View tournament standings"""
    await interaction.response.defer()
    
    try:
        tournament = db.get_tournament(tournament_id)
        if not tournament:
            await interaction.followup.send("Tournament not found!")
            return
        
        if tournament["type"] == "round_robin":
            standings = db.get_standings(tournament_id)
            
            if not standings:
                await interaction.followup.send("No standings available yet!")
                return
            
            embed = discord.Embed(
                title=f"Standings - {tournament['name']}",
                color=discord.Color.gold()
            )
            
            standings_text = ""
            for i, s in enumerate(standings[:10]):
                standings_text += f"{i+1}. **{s['participant_name']}** - {s['points']} pts ({s['wins']}-{s['losses']}-{s['draws']})\n"
            
            embed.description = standings_text
            await interaction.followup.send(embed=embed)
        else:
            # For elimination tournaments, show winners bracket
            matches = db.get_matches(tournament_id)
            winners = []
            
            for match in matches:
                if match["status"] == "completed" and match.get("winner_id"):
                    winner = db.get_participant(match["winner_id"])
                    if winner and winner["name"] not in winners:
                        winners.append(winner["name"])
            
            if winners:
                embed = discord.Embed(
                    title=f"Tournament Progress - {tournament['name']}",
                    description="\n".join([f"🏆 {w}" for w in winners[:10]]),
                    color=discord.Color.gold()
                )
                await interaction.followup.send(embed=embed)
            else:
                await interaction.followup.send("No winners yet!")
        
    except Exception as e:
        await interaction.followup.send(f"Error: {str(e)}")

@bot.tree.command(name="host", description="Host commands (requires authentication)")
@app_commands.describe(
    tournament_id="Tournament ID"
)
async def host(interaction: discord.Interaction, tournament_id: str):
    """Open host control panel"""
    
    # Check if user has host role
    host_role = discord.utils.get(interaction.guild.roles, name=HOST_ROLE_NAME)
    if host_role in interaction.user.roles:
        # Auto-authenticate for users with host role
        token = auth_handler.create_token(tournament_id, str(interaction.user.id))
        
        embed = discord.Embed(
            title="Host Control Panel",
            description="You have been automatically authenticated as a tournament host!",
            color=discord.Color.green()
        )
        
        view = HostControlView(tournament_id, token)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    else:
        # Show password modal for non-host role users
        modal = HostPasswordModal(tournament_id, interaction)
        await interaction.response.send_modal(modal)

@bot.tree.command(name="match_submit", description="Submit match score")
@app_commands.describe(
    match_id="Match ID",
    score1="Score for participant 1",
    score2="Score for participant 2"
)
async def match_submit(interaction: discord.Interaction, match_id: str, score1: int, score2: int):
    """Submit match score"""
    
    # Check if user has host role
    host_role = discord.utils.get(interaction.guild.roles, name=HOST_ROLE_NAME)
    if host_role not in interaction.user.roles:
        await interaction.response.send_message("You need the Tournament Host role to submit scores!", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        match = db.get_match(match_id)
        if not match:
            await interaction.followup.send("Match not found!")
            return
        
        db.update_match_score(match_id, score1, score2)
        
        embed = discord.Embed(
            title="✅ Score Submitted",
            description=f"Match updated: {score1} - {score2}",
            color=discord.Color.green()
        )
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"Error: {str(e)}")

@bot.tree.command(name="match_advance", description="Force advance a match")
@app_commands.describe(
    match_id="Match ID"
)
async def match_advance(interaction: discord.Interaction, match_id: str):
    """Force advance a match"""
    
    # Check if user has host role
    host_role = discord.utils.get(interaction.guild.roles, name=HOST_ROLE_NAME)
    if host_role not in interaction.user.roles:
        await interaction.response.send_message("You need the Tournament Host role to advance matches!", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        match = db.get_match(match_id)
        if not match:
            await interaction.followup.send("Match not found!")
            return
        
        # Auto-advance with default winner
        if match["participant1_id"]:
            winner_id = match["participant1_id"]
            db.update_match_score(match_id, 1, 0)
        elif match["participant2_id"]:
            winner_id = match["participant2_id"]
            db.update_match_score(match_id, 0, 1)
        else:
            await interaction.followup.send("No participants to advance!")
            return
        
        embed = discord.Embed(
            title="✅ Match Advanced",
            description="Match has been force-advanced",
            color=discord.Color.green()
        )
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"Error: {str(e)}")

@bot.tree.command(name="round_start", description="Start next round")
@app_commands.describe(
    tournament_id="Tournament ID"
)
async def round_start(interaction: discord.Interaction, tournament_id: str):
    """Start next round"""
    
    # Check if user has host role
    host_role = discord.utils.get(interaction.guild.roles, name=HOST_ROLE_NAME)
    if host_role not in interaction.user.roles:
        await interaction.response.send_message("You need the Tournament Host role to start rounds!", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        tournament = db.get_tournament(tournament_id)
        if not tournament:
            await interaction.followup.send("Tournament not found!")
            return
        
        if tournament["type"] == "round_robin":
            await interaction.followup.send("Round robin tournaments don't have rounds!")
            return
        
        # Get current matches
        matches = db.get_matches(tournament_id)
        current_round = max([m["round"] for m in matches]) if matches else 0
        
        # Check if all matches in current round are completed
        current_round_matches = [m for m in matches if m["round"] == current_round]
        if not all(m["status"] == "completed" for m in current_round_matches):
            await interaction.followup.send("Complete all matches in current round first!")
            return
        
        # Generate next round matches
        participants = db.get_participants(tournament_id)
        new_matches = bracket_gen.generate_bracket(tournament["type"], participants)
        
        # Filter for next round
        next_round_matches = [m for m in new_matches if m["round"] == current_round + 1]
        
        if next_round_matches:
            db.create_matches_bulk(tournament_id, next_round_matches)
            db.update_tournament(tournament_id, {"status": "active"})
            
            embed = discord.Embed(
                title="✅ Round Started",
                description=f"Round {current_round + 1} has been started with {len(next_round_matches)} matches",
                color=discord.Color.green()
            )
        else:
            # Tournament complete
            db.update_tournament(tournament_id, {"status": "completed"})
            
            embed = discord.Embed(
                title="🏆 Tournament Complete!",
                description="The tournament has finished!",
                color=discord.Color.gold()
            )
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"Error: {str(e)}")

@bot.tree.command(name="tournament_reset", description="Reset tournament")
@app_commands.describe(
    tournament_id="Tournament ID"
)
async def tournament_reset(interaction: discord.Interaction, tournament_id: str):
    """Reset tournament"""
    
    # Check if user has host role
    host_role = discord.utils.get(interaction.guild.roles, name=HOST_ROLE_NAME)
    if host_role not in interaction.user.roles:
        await interaction.response.send_message("You need the Tournament Host role to reset tournaments!", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        tournament = db.get_tournament(tournament_id)
        if not tournament:
            await interaction.followup.send("Tournament not found!")
            return
        
        participants = db.get_participants(tournament_id)
        
        # Delete existing matches
        db.delete_matches(tournament_id)
        
        # Generate new bracket
        matches = bracket_gen.generate_bracket(tournament["type"], participants)
        
        # Save new matches
        db.create_matches_bulk(tournament_id, matches)
        
        # Update status
        db.update_tournament(tournament_id, {"status": "pending"})
        
        embed = discord.Embed(
            title="✅ Tournament Reset",
            description="Tournament has been reset to initial state",
            color=discord.Color.green()
        )
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"Error: {str(e)}")

@bot.tree.command(name="tournament_delete", description="Delete tournament")
@app_commands.describe(
    tournament_id="Tournament ID"
)
async def tournament_delete(interaction: discord.Interaction, tournament_id: str):
    """Delete tournament"""
    
    # Check if user has host role
    host_role = discord.utils.get(interaction.guild.roles, name=HOST_ROLE_NAME)
    if host_role not in interaction.user.roles:
        await interaction.response.send_message("You need the Tournament Host role to delete tournaments!", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    try:
        tournament = db.get_tournament(tournament_id)
        if not tournament:
            await interaction.followup.send("Tournament not found!")
            return
        
        db.delete_tournament(tournament_id)
        
        embed = discord.Embed(
            title="✅ Tournament Deleted",
            description="Tournament has been permanently deleted",
            color=discord.Color.green()
        )
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        await interaction.followup.send(f"Error: {str(e)}")

@bot.tree.command(name="help", description="Show available commands")
async def help_command(interaction: discord.Interaction):
    """Show help menu"""
    embed = discord.Embed(
        title="🎮 Xtourny Bot Commands",
        description="Tournament management system for Discord",
        color=discord.Color.blue()
    )
    
    embed.add_field(
        name="Public Commands",
        value=(
            "`/tournament` - Create a new tournament\n"
            "`/tournaments` - List active tournaments\n"
            "`/tournament_view` - View tournament details\n"
            "`/match_list` - List pending matches\n"
            "`/standings` - View tournament standings\n"
            "`/host` - Open host control panel"
        ),
        inline=False
    )
    
    embed.add_field(
        name="Host Commands (Requires Host Role)",
        value=(
            "`/match_submit` - Submit match score\n"
            "`/match_advance` - Force advance match\n"
            "`/round_start` - Start next round\n"
            "`/tournament_reset` - Reset tournament\n"
            "`/tournament_delete` - Delete tournament"
        ),
        inline=False
    )
    
    embed.set_footer(text="Visit xtourny.com for web interface")
    
    await interaction.response.send_message(embed=embed)

# ==================== Run Both Servers ====================
def run_fastapi():
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=API_PORT)

def run_discord_bot():
    bot.run(DISCORD_TOKEN)

if __name__ == "__main__":
    # Run FastAPI in a separate thread
    api_thread = threading.Thread(target=run_fastapi, daemon=True)
    api_thread.start()
    
    # Run Discord bot in main thread
    run_discord_bot()
