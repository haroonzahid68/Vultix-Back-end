import uvicorn
import bcrypt
import os
import urllib.parse
import requests
import base64
import time
import hmac
import hashlib
import json
import logging
from datetime import datetime
from fastapi import FastAPI, HTTPException, Depends, Header, Request, UploadFile, File, Form
from pydantic import BaseModel
from typing import Optional, List
from fastapi.middleware.cors import CORSMiddleware
from groq import Groq
from sqlalchemy import create_engine, Column, Integer, String, Text, ForeignKey, DateTime, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from tavily import TavilyClient

# RAG specific imports
try:
    import PyPDF2
    import io
    import numpy as np
    import faiss
    RAG_ENABLED = True
except ImportError:
    RAG_ENABLED = False
    print("WARNING: RAG modules (PyPDF2, numpy, faiss) not found. Document upload will be disabled.")

# Web Search specific imports
try:
    from duckduckgo_search import DDGS
    DDGS_ENABLED = True
except ImportError:
    DDGS_ENABLED = False
    print("WARNING: duckduckgo-search module not found. Tier 1 Web Search disabled.")

import google.generativeai as genai

# === LOGGING SETUP ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("VultixCore")

# === ENVIRONMENT VARIABLES ===
GOOGLE_CLIENT_ID = "1040604821889-nlp7drjmimem7p2ldh1bkhkepp9f1hii.apps.googleusercontent.com"
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
HF_API_KEY = os.getenv("HF_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./vultix_core.db")
ADMIN_MASTER_KEY = os.getenv("ADMIN_MASTER_KEY", "ceo123")

# 🚀 NEW KEYS FOR SAAS MONETIZATION, MULTI-LLM & SEARCH
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") 
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
else:
    logger.warning("GEMINI_API_KEY is not set. Coding mode will fail.")

LEMON_API_KEY = os.getenv("LEMON_API_KEY")
LEMON_WEBHOOK_SECRET = os.getenv("LEMON_WEBHOOK_SECRET")
LEMON_STORE_ID = os.getenv("LEMON_STORE_ID")
LEMON_VARIANT_ID = os.getenv("LEMON_VARIANT_ID")

# Web Search APIs
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY")
GOOGLE_SEARCH_API_KEY = os.getenv("GOOGLE_SEARCH_API_KEY")
GOOGLE_SEARCH_CX = os.getenv("GOOGLE_SEARCH_CX")

# === DATABASE SETUP ===
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# === DATABASE MODELS ===
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    response_count = Column(Integer, default=0)
    last_reset_time = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_banned = Column(Boolean, default=False)
    is_pro = Column(Boolean, default=False)

class Chat(Base):
    __tablename__ = "chats"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    session_id = Column(String, index=True)
    message = Column(Text)
    response = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)

class Document(Base):
    __tablename__ = "documents"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    filename = Column(String)
    content = Column(Text)
    uploaded_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

# === FASTAPI APP ===
app = FastAPI(title="Vultix AI Pro SaaS Engine", version="3.3.0")

# CORS Middleware Setup
app.add_middleware(
    CORSMiddleware, 
    allow_origins=["*"], 
    allow_credentials=True, 
    allow_methods=["*"], 
    allow_headers=["*"]
)

# API Clients Initialization
if GROQ_API_KEY:
    client = Groq(api_key=GROQ_API_KEY)
else:
    logger.error("GROQ_API_KEY is missing!")

if TAVILY_API_KEY:
    tavily = TavilyClient(api_key=TAVILY_API_KEY)

# === PYDANTIC MODELS ===
class AuthRequest(BaseModel):
    full_name: str = "User"
    username: str
    password: str

class GoogleAuthRequest(BaseModel):
    token: str

class ChatRequest(BaseModel):
    user_id: int
    session_id: str
    transcript: str
    task: str = "friendly"
    selected_model: str = "auto"
    image_engine: str = "fast"
    image_data: Optional[str] = None
    use_rag: Optional[bool] = False 

class CheckoutRequest(BaseModel):
    user_id: int

# === DEPENDENCIES ===
def get_db():
    db = SessionLocal()
    try: 
        yield db
    finally: 
        db.close()

def verify_admin(x_admin_key: str = Header(...)):
    if x_admin_key != ADMIN_MASTER_KEY:
        raise HTTPException(status_code=403, detail="ACCESS_DENIED")

# === AUTHENTICATION API ===
@app.post("/signup")
async def signup(request: AuthRequest, db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == request.username).first():
        raise HTTPException(status_code=400, detail="USERNAME_TAKEN")
    hashed_pw = bcrypt.hashpw(request.password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    new_user = User(full_name=request.full_name, username=request.username, hashed_password=hashed_pw)
    db.add(new_user)
    db.commit()
    logger.info(f"New user signed up: {request.username}")
    return {"message": "Success"}

@app.post("/login")
async def login(request: AuthRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == request.username).first()
    if user and bcrypt.checkpw(request.password.encode('utf-8'), user.hashed_password.encode('utf-8')):
        if user.is_banned:
            raise HTTPException(status_code=403, detail="ACCOUNT_BANNED")
        return {"user_id": user.id, "username": user.username, "full_name": user.full_name, "is_pro": user.is_pro}
    raise HTTPException(status_code=401, detail="Invalid Credentials")

@app.post("/google-auth")
async def google_auth(request: GoogleAuthRequest, db: Session = Depends(get_db)):
    try:
        idinfo = id_token.verify_oauth2_token(request.token, google_requests.Request(), GOOGLE_CLIENT_ID)
        email = idinfo['email']
        name = idinfo.get('name', 'Google User')
        user = db.query(User).filter(User.username == email).first()
        if not user:
            user = User(full_name=name, username=email, hashed_password="GOOGLE_VERIFIED_USER")
            db.add(user)
            db.commit()
            db.refresh(user)
        if user.is_banned:
            raise HTTPException(status_code=403, detail="ACCOUNT_BANNED")
        return {"user_id": user.id, "username": user.username, "full_name": user.full_name, "is_pro": user.is_pro}
    except Exception as e:
        logger.error(f"Google Login Failed: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Google Login Failed: {str(e)}")

# === 💳 SAAS MONETIZATION: LEMON SQUEEZY APIS ===
@app.post("/create-checkout")
async def create_checkout(request: CheckoutRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == request.user_id).first()
    if not user: 
        return {"error": "User not found"}
    
    if not LEMON_API_KEY or not LEMON_STORE_ID or not LEMON_VARIANT_ID:
        return {"error": "Payment Gateway Configuration Missing in Environment"}

    headers = {
        "Authorization": f"Bearer {LEMON_API_KEY}",
        "Accept": "application/vnd.api+json",
        "Content-Type": "application/vnd.api+json"
    }
    
    payload = {
        "data": {
            "type": "checkouts",
            "attributes": {
                "checkout_data": {
                    "email": user.username if "@" in user.username else f"{user.username}@vultix.com",
                    "name": user.full_name,
                    "custom": { "user_id": str(user.id) }
                }
            },
            "relationships": {
                "store": { "data": { "type": "stores", "id": LEMON_STORE_ID } },
                "variant": { "data": { "type": "variants", "id": LEMON_VARIANT_ID } }
            }
        }
    }
    
    try:
        res = requests.post("https://api.lemonsqueezy.com/v1/checkouts", headers=headers, json=payload)
        res_data = res.json()
        checkout_url = res_data["data"]["attributes"]["url"]
        logger.info(f"Checkout created for user {user.id}")
        return {"checkout_url": checkout_url}
    except Exception as e:
        logger.error(f"Checkout Failed: {str(e)}")
        return {"error": str(e)}

@app.post("/webhook")
async def lemon_webhook(request: Request, db: Session = Depends(get_db)):
    signature = request.headers.get("X-Signature")
    body = await request.body()
    
    mac = hmac.new(LEMON_WEBHOOK_SECRET.encode(), msg=body, digestmod=hashlib.sha256)
    if not hmac.compare_digest(mac.hexdigest(), signature):
        raise HTTPException(status_code=401, detail="Invalid Webhook Signature")
    
    data = json.loads(body)
    event_name = data["meta"]["event_name"]
    
    if event_name in ["order_created", "subscription_created"]:
        custom_data = data["data"]["attributes"]["custom_data"]
        user_id = int(custom_data.get("user_id", 0))
        
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            user.is_pro = True 
            db.commit()
            logger.info(f"User {user_id} upgraded to PRO via Webhook.")
            
    return {"status": "success"}

# === 🌐 WATERFALL WEB SEARCH SYSTEM (DDGS -> BRAVE -> GOOGLE) ===
def perform_waterfall_search(query: str) -> str:
    results = []
    
    # Tier 1: DuckDuckGo (Free, Unlimited)
    try:
        if DDGS_ENABLED:
            logger.info("Attempting Web Search via DuckDuckGo (Tier 1)...")
            with DDGS() as ddgs:
                ddgs_results = list(ddgs.text(query, max_results=3))
                if ddgs_results:
                    for r in ddgs_results:
                        results.append(f"Title: {r.get('title')}\nSnippet: {r.get('body')}")
                    return "\n\n[REAL-TIME WEB DATA (DDGS)]:\n" + "\n---\n".join(results)
    except Exception as e:
        logger.warning(f"DDGS Failed: {str(e)}")

    # Tier 2: Brave Search API (Fallback)
    try:
        if BRAVE_API_KEY:
            logger.info("Attempting Web Search via Brave API (Tier 2)...")
            headers = {"Accept": "application/json", "Accept-Encoding": "gzip", "X-Subscription-Token": BRAVE_API_KEY}
            res = requests.get(f"https://api.search.brave.com/res/v1/web/search?q={urllib.parse.quote(query)}&count=3", headers=headers, timeout=5)
            if res.status_code == 200:
                brave_data = res.json()
                for r in brave_data.get("web", {}).get("results", [])[:3]:
                    results.append(f"Title: {r.get('title')}\nSnippet: {r.get('description')}")
                return "\n\n[REAL-TIME WEB DATA (BRAVE)]:\n" + "\n---\n".join(results)
    except Exception as e:
        logger.warning(f"Brave Search Failed: {str(e)}")

    # Tier 3: Google Custom Search (Last Resort)
    try:
        if GOOGLE_SEARCH_API_KEY and GOOGLE_SEARCH_CX:
            logger.info("Attempting Web Search via Google Search API (Tier 3)...")
            res = requests.get(f"https://www.googleapis.com/customsearch/v1?q={urllib.parse.quote(query)}&key={GOOGLE_SEARCH_API_KEY}&cx={GOOGLE_SEARCH_CX}&num=3", timeout=5)
            if res.status_code == 200:
                google_data = res.json()
                for item in google_data.get("items", [])[:3]:
                    results.append(f"Title: {item.get('title')}\nSnippet: {item.get('snippet')}")
                return "\n\n[REAL-TIME WEB DATA (GOOGLE)]:\n" + "\n---\n".join(results)
    except Exception as e:
        logger.warning(f"Google Search Failed: {str(e)}")

    return "" # No results found or all APIs failed

# === 🧠 KNOWLEDGE VAULT: RAG SYSTEM (Retrieval-Augmented Generation) ===
@app.post("/upload-document")
async def upload_document(
    user_id: int = Form(...), 
    file: UploadFile = File(...), 
    db: Session = Depends(get_db)
):
    if not RAG_ENABLED:
        return {"error": "RAG Server Dependencies Missing. Install PyPDF2, numpy, faiss-cpu"}
    
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return {"error": "User not found"}
    
    if not file.filename.endswith('.pdf'):
        return {"error": "Only PDF files are supported currently."}
        
    try:
        # Extract text from PDF
        pdf_content = await file.read()
        pdf_reader = PyPDF2.PdfReader(io.BytesIO(pdf_content))
        
        extracted_text = ""
        for page in pdf_reader.pages:
            text = page.extract_text()
            if text:
                extracted_text += text + "\n"
        
        # Save to Database for context retrieval later
        new_doc = Document(user_id=user_id, filename=file.filename, content=extracted_text)
        db.add(new_doc)
        db.commit()
        
        return {"message": f"Document '{file.filename}' processed successfully and saved to Knowledge Vault.", "doc_id": new_doc.id}
    except Exception as e:
        logger.error(f"Document processing failed: {str(e)}")
        return {"error": f"Failed to process document: {str(e)}"}

def get_rag_context(user_id: int, query: str, db: Session) -> str:
    """Helper function to fetch relevant text from DB based on simple keyword search"""
    docs = db.query(Document).filter(Document.user_id == user_id).order_by(Document.uploaded_at.desc()).limit(2).all()
    if not docs:
        return ""
    
    context_chunks = []
    for doc in docs:
        chunks = [doc.content[i:i+1500] for i in range(0, len(doc.content), 1500)]
        for chunk in chunks:
            keywords = [w.lower() for w in query.split() if len(w) > 4]
            if any(k in chunk.lower() for k in keywords):
                context_chunks.append(chunk)
                if len(context_chunks) > 2: 
                    break
                    
    if context_chunks:
        joined_context = "\n...".join(context_chunks)
        return f"\n\n[RELEVANT VAULT DOCUMENT FRAGMENTS]:\n{joined_context}\n[END FRAGMENTS. Use this context to answer if relevant.]\n"
    return ""

# === 🧠 MULTI-LLM CORE ENGINE ===
@app.post("/process")
async def process_content(request: ChatRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == request.user_id).first()
    if not user: return {"error": "User missing!"}
    if user.is_banned: return {"error": "ACCOUNT_BANNED"}

    # Limit Checking Logic
    now = datetime.utcnow()
    if not user.last_reset_time: user.last_reset_time = now
    if (now - user.last_reset_time).total_seconds() > 86400:
        user.response_count = 0
        user.last_reset_time = now
        db.commit()

    if not user.is_pro and user.response_count >= 50: 
        return {"error": "LIMIT_REACHED"}

    # =========================================================================
    # 🎨 IMAGE GENERATION LOGIC WITH ASPECT RATIO
    # =========================================================================
    if request.task == "image":
        width, height = 1024, 1024 # Default Square
        
        ratio = request.selected_model 
        if ratio == "16:9":
            width, height = 1280, 720
        elif ratio == "9:16":
            width, height = 720, 1280

        if request.image_engine == "hd":
            if not user.is_pro: return {"error": "PRO_FEATURE"}
            API_URL = "https://api-inference.huggingface.co/models/stabilityai/stable-diffusion-xl-base-1.0"
            headers = {"Authorization": f"Bearer {HF_API_KEY}"}
            try:
                response = requests.post(API_URL, headers=headers, json={"inputs": request.transcript}, timeout=30)
                if response.status_code == 200:
                    img_data = base64.b64encode(response.content).decode("utf-8")
                    ai_response = f"![Generated Image](data:image/png;base64,{img_data})"
                else: 
                    ai_response = "HF Engine is warming up or busy. Please try again in a few seconds! ⏳"
            except Exception as e:
                ai_response = f"Image Generation failed: {str(e)}"
        else:
            VIP_STYLE_ENHANCERS = ", volumetric lighting, 8k resolution, photorealistic, cinematic quality."
            cleaned_prompt = request.transcript.strip()[:200]
            encoded_prompt = urllib.parse.quote(cleaned_prompt + VIP_STYLE_ENHANCERS)
            seed = int(time.time())
            image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?seed={seed}&width={width}&height={height}&nologo=true"
            ai_response = f"![Generated Image]({image_url})"

        new_chat = Chat(user_id=request.user_id, session_id=request.session_id, message=request.transcript, response=ai_response)
        if not user.is_pro: user.response_count += 1
        db.add(new_chat)
        db.commit()
        return {"data": ai_response, "remaining": "Unlimited" if user.is_pro else 50 - user.response_count}

    # Retrieve RAG Context if Study Mode is active
    rag_context = ""
    if request.task == "study" and RAG_ENABLED:
        rag_context = get_rag_context(user_id=user.id, query=request.transcript, db=db)

    # 🌐 SMART WEB SEARCH TRIGGER (Expanded Keywords)
    search_keywords = [
        "latest", "today", "news", "price", "current", "update", "2024", "2025", "2026", 
        "aaj", "ab ki", "realtime", "search", "who is", "what is", "weather", "mausam", 
        "score", "match", "bitcoin", "btc", "crypto", "rate", "usd", "dollar"
    ]
    
    web_context = ""
    # Check if user needs real-time info
    if any(word in request.transcript.lower() for word in search_keywords):
        raw_web_data = perform_waterfall_search(request.transcript)
        if raw_web_data:
            # 🔥 THE AI JAILBREAK COMMAND (Forcing AI to act on the data)
            web_context = raw_web_data + "\n[STRICT SYSTEM COMMAND: You have been provided with LIVE real-time web data above. You MUST use this data to answer the user's question directly. Give the EXACT price, weather, score, or news. DO NOT tell the user to visit links. DO NOT say 'As an AI, I don't have real-time access' because the data is right above. Act confident.]\n\n"

    # Get Current Date & Time for System Awareness
    current_time = datetime.utcnow().strftime("%A, %B %d, %Y - %H:%M UTC")

    # =========================================================================
    # 👨‍💻 CODING LOGIC (GOOGLE GEMINI)
    # =========================================================================
    if request.task == "coding":
        try:
            if not GEMINI_API_KEY: 
                return {"error": "Gemini API Key is missing on Server. Configure GEMINI_API_KEY."}

            system_instr = f"Current Time: {current_time}. ROLE: Senior 10x Software Engineer & Elite Academic Logic Expert. CRITICAL RULE: When writing C++ code or providing solutions, strictly align with academic requirements. Absolutely NO code comments in generated code unless explicitly requested. Output ONLY raw, clean logic."
            
            # Trim history to save Tokens
            history = db.query(Chat).filter(Chat.session_id == request.session_id).order_by(Chat.id.desc()).limit(2).all()
            
            gemini_history = []
            for h in reversed(history):
                gemini_history.append({"role": "user", "parts": [h.message]})
                gemini_history.append({"role": "model", "parts": [h.response]})
            
            model = genai.GenerativeModel("gemini-1.5-flash")

            # Final prompt with Web Context if any
            final_prompt = f"[{system_instr}]\n{web_context}\nUser Request: {request.transcript}"
            
            chat_session = model.start_chat(history=gemini_history)
            res = chat_session.send_message(final_prompt)
            ai_msg = res.text
            
            new_chat = Chat(user_id=request.user_id, session_id=request.session_id, message=request.transcript, response=ai_msg)
            if not user.is_pro: user.response_count += 1
            db.add(new_chat)
            db.commit()
            
            return {"data": ai_msg, "remaining": "Unlimited" if user.is_pro else 50 - user.response_count}
            
        except Exception as e:
            logger.error(f"Gemini Engine Failed: {str(e)}")
            return {"error": f"Gemini Route Failed: {str(e)}. Try switching modes."}

    # =========================================================================
    # 🧠 GENERAL/VIRAL/STUDY LOGIC (GROQ API with LIMIT BYPASS)
    # =========================================================================
    else:
        try:
            model_to_use = "llama-3.3-70b-versatile" if request.selected_model == "llama-3.3-70b-versatile" else "llama-3.1-8b-instant"
            
            if model_to_use == "llama-3.3-70b-versatile" and not user.is_pro:
                return {"error": "PRO_FEATURE"}
            
            if request.image_data:
                model_to_use = "llama-3.2-90b-vision-instruct"

            task_rules = ""
            if request.task == "viral":
                task_rules = "ROLE: Elite YouTube & Social Media Viral Strategist. FOCUS: US & UK Audiences. Create highly engaging hooks and content."
            elif request.task == "study":
                task_rules = "ROLE: Academic Speedster. CRITICAL RULE: For MCQs, provide ONLY the direct letter answer (e.g., 'a', 'b', 'c')."
            else:
                task_rules = "ROLE: Best friend and supportive AI. LANGUAGE RULE: Use casual Pakistani Roman Urdu mixed with English words. TONE: Sarcastic, ALWAYS use real Unicode emojis (like 😂🔥). DO NOT use text shortcodes like :smile:."

            creator_info = f"Current System Time: {current_time}. If anyone asks who created you, state that you are Vultix AI, developed by Muhammad Haroon Zahid, an IT entrepreneur from Bahawalpur."
            system_instr = f"You are Vultix AI, a premium SaaS assistant.\n{creator_info}\n{task_rules}"

            messages = [{"role": "system", "content": system_instr}]
            
            history = db.query(Chat).filter(Chat.session_id == request.session_id).order_by(Chat.id.desc()).limit(1).all()
            for h in reversed(history):
                messages.append({"role": "user", "content": h.message})
                messages.append({"role": "assistant", "content": h.response})

            # Append RAG Context and Web Context
            final_user_transcript = request.transcript + "\n" + rag_context + "\n" + web_context

            if request.image_data:
                messages.append({"role": "user", "content": [{"type": "text", "text": final_user_transcript}, {"type": "image_url", "image_url": {"url": request.image_data}}]})
            else:
                messages.append({"role": "user", "content": final_user_transcript})

            # First Attempt with Groq
            res = client.chat.completions.create(model=model_to_use, messages=messages)
            ai_msg = res.choices[0].message.content
            
            db_message = f"[Sent an Image] {request.transcript}" if request.image_data else request.transcript
            new_chat = Chat(user_id=request.user_id, session_id=request.session_id, message=db_message, response=ai_msg)
            
            if not user.is_pro: user.response_count += 1
            db.add(new_chat)
            db.commit()
            
            return {"data": ai_msg, "remaining": "Unlimited" if user.is_pro else 50 - user.response_count}
            
        except Exception as e: 
            error_msg = str(e)
            logger.error(f"Groq Route Error: {error_msg}")
            
            # Vision Model Fallback
            if "model_not_found" in error_msg and request.image_data:
                try:
                    fallback_model = "llama-3.2-11b-vision-instruct"
                    res = client.chat.completions.create(model=fallback_model, messages=messages)
                    ai_msg = res.choices[0].message.content
                    
                    db_message = f"[Sent an Image] {request.transcript}"
                    new_chat = Chat(user_id=request.user_id, session_id=request.session_id, message=db_message, response=ai_msg)
                    if not user.is_pro: user.response_count += 1
                    db.add(new_chat)
                    db.commit()
                    return {"data": ai_msg, "remaining": "Unlimited" if user.is_pro else 50 - user.response_count}
                except Exception as inner_e:
                    return {"error": f"Vision API Issue: {str(inner_e)}"}
            
            # Rate Limit Fallback Bypass
            if "rate_limit_exceeded" in error_msg.lower() or "429" in error_msg:
                return {"error": "Groq Rate Limit Exceeded. System is cooling down. Please use Coding Mode (Gemini Engine) for a few minutes!"}
                
            return {"error": error_msg}

# === ADMIN APIS ===

@app.get("/admin/user_full_history/{user_id}")
async def get_admin_user_history(user_id: int, db: Session = Depends(get_db), x_admin_key: str = Header(...)):
    if x_admin_key != ADMIN_MASTER_KEY:
        raise HTTPException(status_code=403, detail="ACCESS_DENIED")
    chats = db.query(Chat).filter(Chat.user_id == user_id).order_by(Chat.timestamp.desc()).all()
    return {"chats": [{"message": c.message, "response": c.response, "timestamp": c.timestamp} for c in chats]}

@app.get("/history/{user_id}")
async def get_user_history(user_id: int, db: Session = Depends(get_db)):
    chats = db.query(Chat).filter(Chat.user_id == user_id).order_by(Chat.timestamp.desc()).all()
    sessions = []
    seen = set()
    for c in chats:
        if c.session_id not in seen:
            seen.add(c.session_id)
            title = c.message[:25] + "..." if len(c.message) > 25 else c.message
            sessions.append({"session_id": c.session_id, "title": title})
    return {"history": sessions}

@app.get("/history/session/{session_id}")
async def get_session_chat(session_id: str, db: Session = Depends(get_db)):
    chats = db.query(Chat).filter(Chat.session_id == session_id).order_by(Chat.timestamp.asc()).all()
    return {"chats": [{"message": c.message, "response": c.response} for c in chats]}

@app.delete("/history/session/{session_id}")
async def delete_session(session_id: str, db: Session = Depends(get_db)):
    db.query(Chat).filter(Chat.session_id == session_id).delete()
    db.commit()
    return {"message": "Chat deleted successfully"}

@app.get("/admin/stats")
async def get_admin_stats(db: Session = Depends(get_db), _: None = Depends(verify_admin)):
    total_users = db.query(User).count()
    total_pro_users = db.query(User).filter(User.is_pro == True).count()
    total_chats = db.query(Chat).count()
    return {"total_users": total_users, "total_pro_users": total_pro_users, "total_chats": total_chats}

@app.get("/admin/users")
async def get_admin_users(db: Session = Depends(get_db), _: None = Depends(verify_admin)):
    users = db.query(User).all()
    user_list = []
    for u in users:
        chat_count = db.query(Chat).filter(Chat.user_id == u.id).count()
        user_list.append({
            "id": u.id,
            "full_name": u.full_name,
            "username": u.username,
            "chat_count": chat_count,
            "is_pro": u.is_pro,
            "is_banned": u.is_banned
        })
    return {"users": user_list}

@app.post("/admin/toggle_ban/{user_id}")
async def toggle_user_ban(user_id: int, db: Session = Depends(get_db), _: None = Depends(verify_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user: raise HTTPException(status_code=404, detail="User not found")
    user.is_banned = not user.is_banned
    db.commit()
    return {"message": "Status updated", "is_banned": user.is_banned}

@app.post("/admin/toggle_pro/{user_id}")
async def toggle_user_pro(user_id: int, db: Session = Depends(get_db), _: None = Depends(verify_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user: raise HTTPException(status_code=404, detail="User not found")
    user.is_pro = not user.is_pro
    db.commit()
    return {"message": "Premium status updated", "is_pro": user.is_pro}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
