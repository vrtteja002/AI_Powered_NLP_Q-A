from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
import json
import os
from typing import Dict, List, Any, Optional
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

app = FastAPI(
    title="AI-Powered Member Data Q&A System",
    description="An intelligent question-answering system for member data using AI",
    version="2.0.1"
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
    confidence: Optional[float] = None
    sources_used: Optional[List[str]] = None

# Global cache for member data
member_data_cache = {}
cache_last_updated = None

# Configuration
API_BASE_URL = "https://november7-730026606190.europe-west1.run.app"
CACHE_DURATION_MINUTES = 10
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
AI_MODEL = "gpt-4o"  # Can be changed to gpt-4 for better results

class AIQuestionAnswering:
    def __init__(self):
        self.openai_headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
    
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
                
                # Store raw data for AI processing
                member_data_cache = data
                cache_last_updated = now
                
                return data
                
        except httpx.TimeoutException:
            raise HTTPException(status_code=504, detail="API timeout - please try again later")
        except httpx.RequestError:
            raise HTTPException(status_code=502, detail="Unable to connect to member data API")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to fetch member data: {str(e)}")
    
    def prepare_context_for_ai(self, member_data: Any) -> str:
        """Convert member data into a properly formatted context string for AI"""
        # Handle the actual API structure: {"total": 3349, "items": [...]}
        items = []
        if isinstance(member_data, dict) and "items" in member_data:
            items = member_data["items"]
            total_count = member_data.get("total", len(items))
        elif isinstance(member_data, list):
            items = member_data
            total_count = len(items)
        else:
            return "No valid member data available."
        
        context_parts = []
        context_parts.append(f"Member Data System - {total_count} total messages")
        context_parts.append("=" * 50)
        
        # Group messages by member
        members_data = {}
        for item in items:
            if isinstance(item, dict) and "user_name" in item and "message" in item:
                user_name = item["user_name"]
                if user_name not in members_data:
                    members_data[user_name] = []
                
                members_data[user_name].append({
                    "message": item["message"],
                    "timestamp": item.get("timestamp", ""),
                    "id": item.get("id", "")
                })
        
        # Format for AI consumption
        for member_name, messages in members_data.items():
            context_parts.append(f"\nMEMBER: {member_name}")
            context_parts.append("-" * 30)
            
            for i, msg_data in enumerate(messages[:10]):  # Limit to avoid token limits
                timestamp = msg_data["timestamp"][:10] if msg_data["timestamp"] else "Unknown date"
                context_parts.append(f"[{timestamp}] {msg_data['message']}")
            
            if len(messages) > 10:
                context_parts.append(f"... and {len(messages) - 10} more messages")
            
            context_parts.append("")  # Add spacing
        
        return "\n".join(context_parts)
    
    async def ask_ai(self, question: str, context: str) -> Dict[str, Any]:
        """Send question and context to AI for processing"""
        if not OPENAI_API_KEY:
            raise HTTPException(
                status_code=500, 
                detail="AI service not configured. Please set OPENAI_API_KEY environment variable."
            )
        
        # Create a comprehensive prompt for the AI
        system_prompt = """You are a precise data analyst for a luxury concierge service. Your job is to extract and report specific information from member service requests.

ANALYSIS RULES:
1. Answer ONLY with information explicitly stated in the member messages
2. Quote specific dates, locations, restaurant names, and request details exactly as written
3. If asked about preferences, extract them word-for-word from the messages
4. For "how many" questions, count actual occurrences in the data
5. For comparison questions, analyze all relevant members' data
6. If information doesn't exist in the messages, state: "No information available in the data"

RESPONSE FORMAT:
- Give direct, factual answers without speculation
- Include specific dates, numbers, and names when available
- Use bullet points or numbered lists for multiple items
- Cite member names exactly as they appear in the data
- For trends/patterns, only mention what can be directly observed

WHAT TO EXTRACT:
- Restaurant reservations (name, date, party size)
- Travel requests (destinations, dates, accommodation types)
- Event tickets (venue, event type, quantity, dates)
- Personal preferences (exact wording from messages)
- Service feedback (positive/negative, specific comments)
- Contact updates (phone numbers, addresses)
- Special requirements (dietary, accessibility, room preferences)

DO NOT:
- Make assumptions beyond what's explicitly stated
- Generalize from limited data
- Add interpretive language like "seems to prefer" - use "requested" or "stated preference for"
- Include information not present in the member messages"""

        user_prompt = f"""Member Service Data:
{context}

Question: {question}

Please analyze the member messages above and answer the question. Focus on extracting relevant information from the actual messages and requests made by the members."""

        payload = {
            "model": AI_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "max_tokens": 800,
            "temperature": 0.5,  # Lower temperature for more focused responses
        }
        
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                response = await client.post(
                    "https://api.openai.com/v1/chat/completions",
                    headers=self.openai_headers,
                    json=payload
                )
                response.raise_for_status()
                result = response.json()
                
                if "choices" in result and len(result["choices"]) > 0:
                    answer = result["choices"][0]["message"]["content"].strip()
                    
                    # Try to estimate confidence based on the response
                    confidence = self.estimate_confidence(answer)
                    
                    return {
                        "answer": answer,
                        "confidence": confidence,
                        "usage": result.get("usage", {}),
                        "model_used": AI_MODEL
                    }
                else:
                    raise HTTPException(status_code=500, detail="Invalid response from AI service")
                    
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise HTTPException(status_code=500, detail="AI service authentication failed")
            elif e.response.status_code == 429:
                raise HTTPException(status_code=503, detail="AI service rate limit exceeded")
            else:
                raise HTTPException(status_code=500, detail=f"AI service error: {e.response.status_code}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Error calling AI service: {str(e)}")
    
    def estimate_confidence(self, answer: str) -> float:
        """Estimate confidence based on response characteristics"""
        confidence = 0.8  # Base confidence
        
        # Lower confidence for uncertain language
        uncertainty_phrases = [
            "i don't have", "not available", "unclear", "uncertain", 
            "might be", "possibly", "seems like", "appears to",
            "no information", "can't find", "don't see"
        ]
        
        answer_lower = answer.lower()
        for phrase in uncertainty_phrases:
            if phrase in answer_lower:
                confidence = 0.4
                break
        
        # Higher confidence for specific information
        if any(char.isdigit() for char in answer) or any(word.istitle() for word in answer.split()):
            confidence = min(1.0, confidence + 0.15)
        
        # Higher confidence if answer contains dates or specific details
        if any(pattern in answer for pattern in ['2024', '2025', ':', '-', 'reservation', 'requested']):
            confidence = min(1.0, confidence + 0.1)
        
        return max(0.1, min(1.0, confidence))

# Initialize the AI system
ai_qa = AIQuestionAnswering()

@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "message": "AI-Powered Member Data Q&A System is running",
        "version": "2.0.1",
        "ai_model": AI_MODEL,
        "ai_configured": bool(OPENAI_API_KEY)
    }

@app.post("/ask", response_model=AnswerResponse)
async def ask_question(request: QuestionRequest):
    """Main endpoint for asking questions about member data using AI"""
    if not request.question or not request.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")
    
    try:
        # Fetch member data
        member_data = await ai_qa.fetch_member_data()
        
        # Prepare context for AI
        context = ai_qa.prepare_context_for_ai(member_data)
        
        # Get AI response
        ai_response = await ai_qa.ask_ai(request.question, context)
        
        return AnswerResponse(
            answer=ai_response["answer"],
            confidence=ai_response.get("confidence"),
            sources_used=["member_data_api", "ai_processing"]
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing question: {str(e)}")

@app.post("/ask-detailed")
async def ask_question_detailed(request: QuestionRequest):
    """Extended endpoint with detailed AI response information"""
    if not request.question or not request.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")
    
    try:
        # Fetch member data
        member_data = await ai_qa.fetch_member_data()
        
        # Prepare context for AI
        context = ai_qa.prepare_context_for_ai(member_data)
        
        # Get AI response
        ai_response = await ai_qa.ask_ai(request.question, context)
        
        return {
            "answer": ai_response["answer"],
            "confidence": ai_response.get("confidence"),
            "model_used": ai_response.get("model_used"),
            "usage": ai_response.get("usage"),
            "context_length": len(context),
            "sources_used": ["member_data_api", "ai_processing"],
            "timestamp": datetime.now().isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing question: {str(e)}")

@app.get("/members")
async def get_members():
    """Get processed member data for verification"""
    try:
        member_data = await ai_qa.fetch_member_data()
        
        # Process the data to show member summary
        member_summary = {}
        if isinstance(member_data, dict) and "items" in member_data:
            items = member_data["items"]
            total = member_data.get("total", len(items))
            
            # Group by member
            for item in items:
                if isinstance(item, dict) and "user_name" in item:
                    user_name = item["user_name"]
                    if user_name not in member_summary:
                        member_summary[user_name] = {
                            "message_count": 0,
                            "latest_message": "",
                            "latest_timestamp": ""
                        }
                    member_summary[user_name]["message_count"] += 1
                    
                    # Keep track of latest message
                    if item.get("timestamp", "") > member_summary[user_name]["latest_timestamp"]:
                        member_summary[user_name]["latest_message"] = item.get("message", "")[:100]
                        member_summary[user_name]["latest_timestamp"] = item.get("timestamp", "")
        
        return {
            "total_messages": member_data.get("total", 0),
            "unique_members": len(member_summary),
            "members": member_summary,
            "context_preview": ai_qa.prepare_context_for_ai(member_data)[:800] + "..."
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching members: {str(e)}")

@app.get("/health")
async def health_check():
    """Health check endpoint with detailed status"""
    try:
        # Test external API connectivity
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(f"{API_BASE_URL}/messages")
            api_status = "healthy" if response.status_code == 200 else "degraded"
    except:
        api_status = "unhealthy"
    
    # Test AI service
    ai_status = "configured" if OPENAI_API_KEY else "not_configured"
    
    return {
        "status": "healthy",
        "api_status": api_status,
        "ai_status": ai_status,
        "ai_model": AI_MODEL,
        "cache_status": "populated" if member_data_cache else "empty",
        "timestamp": datetime.now().isoformat()
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
