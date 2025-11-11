from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
import re
import json
from typing import Dict, List, Any, Optional
from datetime import datetime

app = FastAPI(
    title="Member Data Q&A System",
    description="A question-answering system for member data",
    version="1.0.1"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Data models
class QuestionRequest(BaseModel):
    question: str

class AnswerResponse(BaseModel):
    answer: str

# Global cache for member data
member_data_cache = {}
cache_last_updated = None

# Configuration
API_BASE_URL = "https://november7-730026606190.europe-west1.run.app"
CACHE_DURATION_MINUTES = 10

class MemberDataAnalyzer:
    def __init__(self):
        self.member_data = {}
    
    async def fetch_member_data(self) -> Dict[str, Any]:
        """Fetch member data from the API with caching"""
        global member_data_cache, cache_last_updated
        
        # Check if cache is still valid
        now = datetime.now()
        if (cache_last_updated and 
            (now - cache_last_updated).total_seconds() < CACHE_DURATION_MINUTES * 60 and
            member_data_cache):
            return member_data_cache
        
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.get(f"{API_BASE_URL}/messages")
                response.raise_for_status()
                data = response.json()
                
                # Process the actual data structure
                processed_data = self.process_member_data(data)
                
                # Update cache
                member_data_cache = processed_data
                cache_last_updated = now
                
                return processed_data
                
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="API timeout - please try again later")
        except httpx.RequestError:
            raise HTTPException(status_code=502, detail="Unable to connect to member data API")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to fetch member data: {str(e)}")
    
    def process_member_data(self, raw_data: Any) -> Dict[str, Any]:
        """Process the actual API data structure"""
        members = {}
        
        # The actual API returns: {"total": 3349, "items": [...]}
        items = []
        if isinstance(raw_data, dict) and "items" in raw_data:
            items = raw_data["items"]
        elif isinstance(raw_data, list):
            items = raw_data
        
        for item in items:
            if isinstance(item, dict) and "user_name" in item and "message" in item:
                user_name = item["user_name"]
                message = item["message"]
                timestamp = item.get("timestamp", "")
                
                if user_name not in members:
                    members[user_name] = {
                        "name": user_name,
                        "messages": [],
                        "requests": [],
                        "preferences": [],
                        "locations": [],
                        "activities": [],
                        "restaurants": [],
                        "travel": [],
                        "other_info": []
                    }
                
                # Store the message
                members[user_name]["messages"].append({
                    "message": message,
                    "timestamp": timestamp
                })
                
                # Extract information from the message
                self.extract_info_from_message(message, members[user_name])
        
        return members
    
    def extract_info_from_message(self, message: str, member_data: Dict):
        """Extract structured information from message content"""
        message_lower = message.lower()
        
        # Extract travel/location information
        travel_patterns = [
            r'trip to (\w+)',
            r'travel to (\w+)', 
            r'visit (\w+)',
            r'going to (\w+)',
            r'tickets to.*in (\w+)',
            r'villa in (\w+)',
            r'tour of .*(\w+)',
            r'weekend in (\w+)'
        ]
        
        for pattern in travel_patterns:
            matches = re.findall(pattern, message, re.IGNORECASE)
            for match in matches:
                if match.lower() not in [loc.lower() for loc in member_data["locations"]]:
                    member_data["locations"].append(match)
                    member_data["travel"].append(f"Trip to {match}")
        
        # Extract restaurant information
        restaurant_patterns = [
            r'dinner.*at ([\w\s]+)',
            r'table.*at ([\w\s]+)',
            r'reservation at ([\w\s]+)',
            r'restaurant ([\w\s]+)'
        ]
        
        for pattern in restaurant_patterns:
            matches = re.findall(pattern, message, re.IGNORECASE)
            for match in matches:
                # Clean up restaurant names
                restaurant = re.sub(r'\b(for|on|tonight|this|next|the)\b', '', match.strip(), flags=re.IGNORECASE).strip()
                if len(restaurant) > 3 and restaurant not in member_data["restaurants"]:
                    member_data["restaurants"].append(restaurant)
        
        # Extract preferences
        preference_patterns = [
            r'prefer ([\w\s]+)',
            r'preference for ([\w\s]+)', 
            r'I.*like ([\w\s]+)',
            r'ensure ([\w\s]+) next time'
        ]
        
        for pattern in preference_patterns:
            matches = re.findall(pattern, message, re.IGNORECASE)
            for match in matches:
                if len(match.strip()) > 3:
                    member_data["preferences"].append(match.strip())
        
        # Extract activities
        activity_patterns = [
            r'tickets to ([\w\s]+)',
            r'passes for ([\w\s]+)',
            r'seats for ([\w\s]+)',
            r'book.*(\w+\s+\w+).*for',
            r'arrange.*(\w+\s+\w+)'
        ]
        
        for pattern in activity_patterns:
            matches = re.findall(pattern, message, re.IGNORECASE)
            for match in matches:
                activity = match.strip()
                if len(activity) > 3 and activity not in member_data["activities"]:
                    member_data["activities"].append(activity)
        
        # Store general request type
        if any(word in message_lower for word in ['book', 'reserve', 'arrange', 'need', 'tickets']):
            member_data["requests"].append(message)
    
    def search_member_data(self, question: str, data: Dict[str, Any]) -> str:
        """Search member data to answer natural language questions"""
        question_lower = question.lower()
        
        # Extract the person's name from the question
        target_name = self.find_member_name(question, data)
        
        if not target_name:
            # If no specific member found, provide available members
            available_members = list(data.keys())
            if available_members:
                return f"I couldn't identify which member you're asking about. Available members: {', '.join(available_members[:10])}{'...' if len(available_members) > 10 else ''}"
            return "No member data is currently available."
        
        member_info = data.get(target_name, {})
        
        # Handle different types of questions
        if any(word in question_lower for word in ["restaurant", "restaurants", "dining", "eat", "food", "table"]):
            restaurants = member_info.get("restaurants", [])
            if restaurants:
                return f"{target_name} has made reservations at: {', '.join(restaurants[:5])}."
            else:
                return f"I don't have restaurant reservation information for {target_name}."
        
        elif any(word in question_lower for word in ["trip", "travel", "visit", "vacation", "where", "location"]):
            locations = member_info.get("locations", [])
            travel = member_info.get("travel", [])
            if locations:
                return f"{target_name} has traveled to or mentioned: {', '.join(locations[:5])}."
            elif travel:
                return f"{target_name}'s travel activities: {', '.join(travel[:3])}."
            else:
                return f"I don't have travel information for {target_name}."
        
        elif any(word in question_lower for word in ["prefer", "preference", "like", "favorite"]):
            preferences = member_info.get("preferences", [])
            if preferences:
                return f"{target_name}'s preferences: {', '.join(preferences[:3])}."
            else:
                return f"I don't have preference information for {target_name}."
        
        elif any(word in question_lower for word in ["activity", "activities", "tickets", "event", "show"]):
            activities = member_info.get("activities", [])
            if activities:
                return f"{target_name} has requested tickets/activities for: {', '.join(activities[:3])}."
            else:
                return f"I don't have activity information for {target_name}."
        
        elif any(word in question_lower for word in ["car", "cars", "vehicle", "how many"]):
            # This specific data doesn't seem to contain car ownership info
            return f"I don't have vehicle ownership information for {target_name} in the current dataset."
        
        else:
            # General information search
            all_info = []
            
            if member_info.get("restaurants"):
                all_info.append(f"restaurants: {', '.join(member_info['restaurants'][:2])}")
            if member_info.get("locations"):
                all_info.append(f"locations: {', '.join(member_info['locations'][:2])}")
            if member_info.get("preferences"):
                all_info.append(f"preferences: {', '.join(member_info['preferences'][:2])}")
            
            if all_info:
                return f"Here's what I know about {target_name}: {'; '.join(all_info[:3])}."
            else:
                message_count = len(member_info.get("messages", []))
                return f"I have {message_count} messages from {target_name}, but no specific categorized information extracted yet."
    
    def find_member_name(self, question: str, data: Dict[str, Any]) -> Optional[str]:
        """Find the member name mentioned in the question"""
        question_lower = question.lower()
        
        # Enhanced name extraction patterns
        name_patterns = [
            r"\b([A-Z][a-z]+ [A-Z][a-z-']+)\b",  # Full name (including hyphenated)
            r"\b([A-Z][a-z]+)\s+(?:planning|has|have|owns?|likes?|is|'s)\b",
            r"\b(?:is|does)\s+([A-Z][a-z]+ [A-Z][a-z-']+)\b",
            r"\b(?:is|does)\s+([A-Z][a-z]+)\b",
            r"\b([A-Z][a-z]+)'s\b",
            r"(?:about|of)\s+([A-Z][a-z]+ [A-Z][a-z-']+)",
            r"(?:about|of)\s+([A-Z][a-z]+)",
        ]
        
        for pattern in name_patterns:
            match = re.search(pattern, question)
            if match:
                potential_name = match.group(1).replace("'s", "")
                
                # Exact match first
                if potential_name in data:
                    return potential_name
                
                # Fuzzy matching
                for member_name in data.keys():
                    # Check if first name or last name matches
                    potential_parts = potential_name.split()
                    member_parts = member_name.split()
                    
                    for p_part in potential_parts:
                        for m_part in member_parts:
                            if (p_part.lower() == m_part.lower() or
                                p_part.lower() in m_part.lower() or
                                m_part.lower() in p_part.lower()):
                                return member_name
        
        return None

# Initialize analyzer
analyzer = MemberDataAnalyzer()

@app.get("/")
async def root():
    """Health check endpoint"""
    return {"message": "Member Data Q&A System is running", "version": "1.0.1"}

@app.post("/ask", response_model=AnswerResponse)
async def ask_question(request: QuestionRequest):
    """Main endpoint for asking questions about member data"""
    if not request.question or not request.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")
    
    try:
        # Fetch member data
        member_data = await analyzer.fetch_member_data()
        
        # Process the question
        answer = analyzer.search_member_data(request.question, member_data)
        
        return AnswerResponse(answer=answer)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing question: {str(e)}")

@app.get("/members")
async def get_members():
    """Get all members data for verification"""
    try:
        member_data = await analyzer.fetch_member_data()
        return {
            "count": len(member_data),
            "members": {name: {
                "message_count": len(info.get("messages", [])),
                "restaurants": info.get("restaurants", []),
                "locations": info.get("locations", []),
                "preferences": info.get("preferences", []),
                "activities": info.get("activities", [])
            } for name, info in member_data.items()}
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching members: {str(e)}")

@app.get("/health")
async def health_check():
    """Health check endpoint with detailed status"""
    try:
        # Test API connectivity
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(f"{API_BASE_URL}/messages")
            api_status = "healthy" if response.status_code == 200 else "degraded"
    except:
        api_status = "unhealthy"
    
    return {
        "status": "healthy",
        "api_status": api_status,
        "cache_status": "populated" if member_data_cache else "empty",
        "timestamp": datetime.now().isoformat()
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)