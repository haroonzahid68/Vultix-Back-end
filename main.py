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

# =========================================================================
# 📦 EXTERNAL LIBRARIES & MODULE CHECKOUT (RAG & WEB SEARCH)
# =========================================================================
try:
    import PyPDF2
    import io
    import numpy as np
    import faiss
    RAG_ENABLED = True
except ImportError:
    RAG_ENABLED = False
    print("WARNING: RAG modules (PyPDF2, numpy, faiss) not found. Document upload disabled.")

try:
    from duckduckgo_search import DDGS
    DDGS_ENABLED = True
except ImportError:
    DDGS_ENABLED = False
    print("WARNING: duckduckgo-search module not found. Tier 1 Web Search disabled.")

import wikipedia 

# =========================================================================
# ⚙️ LOGGING & ENVIRONMENT SETUP
# =========================================================================
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("VultixCore")

GOOGLE_CLIENT_ID = "1040604821889-nlp7drjmimem7p2ldh1bkhkepp9f1hii.apps.googleusercontent.com"
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
HF_API_KEY = os.getenv("HF_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./vultix_core.db")
ADMIN_MASTER_KEY = os.getenv("ADMIN_MASTER_KEY", "ceo123")

# 🚀 MULTI-LLM & MONETIZATION KEYS
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY") 
if OPENROUTER_API_KEY:
    logger.info("OpenRouter Engine configured successfully for Coding Mode.")
else:
    logger.warning("OPENROUTER_API_KEY is not set. Coding mode will fail.")

LEMON_API_KEY = os.getenv("LEMON_API_KEY")
LEMON_WEBHOOK_SECRET = os.getenv("LEMON_WEBHOOK_SECRET")
LEMON_STORE_ID = os.getenv("LEMON_STORE_ID")
LEMON_VARIANT_ID = os.getenv("LEMON_VARIANT_ID")

GOOGLE_SEARCH_API_KEY = os.getenv("GOOGLE_SEARCH_API_KEY")
GOOGLE_SEARCH_CX = os.getenv("GOOGLE_SEARCH_CX")

# =========================================================================
# 🗄️ DATABASE ARCHITECTURE & MODELS
# =========================================================================
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

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

class Feedback(Base):
    __tablename__ = "feedbacks"
    id = Column(Integer, primary_key=True, index=True)
    chat_id = Column(Integer, ForeignKey("chats.id"))
    user_id = Column(Integer, ForeignKey("users.id"))
    is_positive = Column(Boolean) 
    timestamp = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

# =========================================================================
# 🚀 FASTAPI APP INITIALIZATION
# =========================================================================
app = FastAPI(title="Vultix AI Enterprise Engine", version="5.0.0", description="Developed by Muhammad Haroon Zahid")

app.add_middleware(
    CORSMiddleware, 
    allow_origins=["*"], 
    allow_credentials=True, 
    allow_methods=["*"], 
    allow_headers=["*"]
)

if GROQ_API_KEY:
    client = Groq(api_key=GROQ_API_KEY)
    logger.info("Groq client initialized securely.")
else:
    logger.error("Critical: GROQ_API_KEY is missing! Core chat functions will fail.")

if TAVILY_API_KEY:
    tavily = TavilyClient(api_key=TAVILY_API_KEY)

# =========================================================================
# 🧩 PYDANTIC DATA VALIDATION MODELS
# =========================================================================
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

class FeedbackRequest(BaseModel):
    chat_id: int
    user_id: int
    is_positive: bool

class EnhancePromptRequest(BaseModel):
    prompt: str

# =========================================================================
# 🛡️ SECURITY & DEPENDENCY INJECTION
# =========================================================================
def get_db():
    db = SessionLocal()
    try: 
        yield db
    finally: 
        db.close()

def verify_admin(x_admin_key: str = Header(...)):
    if x_admin_key != ADMIN_MASTER_KEY:
        logger.warning("Unauthorized access attempt to Admin API")
        raise HTTPException(status_code=403, detail="ACCESS_DENIED")

# =========================================================================
# 🔐 AUTHENTICATION ENDPOINTS
# =========================================================================
@app.post("/signup")
async def signup(request: AuthRequest, db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == request.username).first():
        logger.warning(f"Signup failed: Username {request.username} already taken.")
        raise HTTPException(status_code=400, detail="USERNAME_TAKEN")
    
    hashed_pw = bcrypt.hashpw(request.password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    new_user = User(full_name=request.full_name, username=request.username, hashed_password=hashed_pw)
    
    db.add(new_user)
    db.commit()
    logger.info(f"New user signed up successfully: {request.username}")
    return {"message": "Success"}

@app.post("/login")
async def login(request: AuthRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == request.username).first()
    
    if user and bcrypt.checkpw(request.password.encode('utf-8'), user.hashed_password.encode('utf-8')):
        if user.is_banned: 
            logger.warning(f"Banned user attempted login: {request.username}")
            raise HTTPException(status_code=403, detail="ACCOUNT_BANNED")
        logger.info(f"User logged in: {request.username}")
        return {"user_id": user.id, "username": user.username, "full_name": user.full_name, "is_pro": user.is_pro}
        
    logger.warning(f"Failed login attempt for: {request.username}")
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
            logger.info(f"New user created via Google Auth: {email}")
            
        if user.is_banned: 
            raise HTTPException(status_code=403, detail="ACCOUNT_BANNED")
            
        return {"user_id": user.id, "username": user.username, "full_name": user.full_name, "is_pro": user.is_pro}
    except Exception as e:
        logger.error(f"Google Login Failed completely: {str(e)}")
        raise HTTPException(status_code=400, detail=f"Google Login Failed: {str(e)}")

# =========================================================================
# 🎨 UTILITY ENDPOINTS (ENHANCER & FEEDBACK)
# =========================================================================
@app.post("/enhance-prompt")
async def enhance_prompt(request: EnhancePromptRequest):
    if not GROQ_API_KEY:
        return {"error": "Groq API Key missing on server configuration."}
        
    try:
        system_msg = "You are an expert prompt engineer for advanced AI image generators like Midjourney and Stable Diffusion. The user will provide a basic concept. You must transform it into a highly detailed, professional, comma-separated image generation prompt. Include specific art styles, lighting settings, camera angles, and rendering engines. Your response MUST contain ONLY the final enhanced prompt."
        
        res = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": f"Enhance this concept: {request.prompt}"}
            ],
            temperature=0.7,
            max_tokens=200
        )
        enhanced_text = res.choices[0].message.content.strip()
        return {"enhanced_prompt": enhanced_text}
    except Exception as e:
        logger.error(f"Prompt enhancement failed: {str(e)}")
        return {"error": f"Enhancement failed: {str(e)}"}

@app.post("/feedback")
async def submit_feedback(request: FeedbackRequest, db: Session = Depends(get_db)):
    chat = db.query(Chat).filter(Chat.id == request.chat_id).first()
    if not chat:
        raise HTTPException(status_code=404, detail="Chat response not found. Cannot submit feedback.")
    
    new_feedback = Feedback(chat_id=request.chat_id, user_id=request.user_id, is_positive=request.is_positive)
    db.add(new_feedback)
    db.commit()
    return {"message": "Feedback recorded successfully!"}

# =========================================================================
# 💳 SAAS MONETIZATION (LEMON SQUEEZY)
# =========================================================================
@app.post("/create-checkout")
async def create_checkout(request: CheckoutRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == request.user_id).first()
    if not user: 
        return {"error": "User not found in system."}
    
    if not LEMON_API_KEY or not LEMON_STORE_ID or not LEMON_VARIANT_ID:
        return {"error": "Payment Gateway Configuration Missing in Environment Variables."}

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
        logger.info(f"Premium Checkout link generated for user ID: {user.id}")
        return {"checkout_url": checkout_url}
    except Exception as e:
        logger.error(f"Checkout Link Generation Failed: {str(e)}")
        return {"error": str(e)}

@app.post("/webhook")
async def lemon_webhook(request: Request, db: Session = Depends(get_db)):
    signature = request.headers.get("X-Signature")
    body = await request.body()
    
    mac = hmac.new(LEMON_WEBHOOK_SECRET.encode(), msg=body, digestmod=hashlib.sha256)
    if not hmac.compare_digest(mac.hexdigest(), signature):
        logger.critical("Invalid Webhook Signature detected. Possible intrusion attempt.")
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
            logger.info(f"MONETIZATION SUCCESS: User {user_id} upgraded to PRO via Lemon Squeezy Webhook.")
            
    return {"status": "success"}

# =========================================================================
# 🌐 WATERFALL WEB SEARCH SYSTEM (Tier 1: DDGS -> Tier 2: Wiki -> Tier 3: Google)
# =========================================================================
def perform_waterfall_search(query: str) -> str:
    results = []
    
    try:
        if DDGS_ENABLED:
            logger.info("Attempting Web Search via DuckDuckGo (Tier 1)...")
            with DDGS() as ddgs:
                time.sleep(1) 
                ddgs_results = list(ddgs.text(query, max_results=3))
                if ddgs_results:
                    for r in ddgs_results:
                        results.append(f"Title: {r.get('title')}\nSnippet: {r.get('body')}")
                    logger.info("Tier 1 Web Search Successful.")
                    return "\n\n[REAL-TIME WEB DATA (DDGS)]:\n" + "\n---\n".join(results)
    except Exception as e:
        logger.warning(f"Tier 1 (DDGS) Failed or Rate Limited: {str(e)}")

    try:
        logger.info("Attempting Web Search via Wikipedia API (Tier 2)...")
        wiki_search = wikipedia.search(query, results=1)
        if wiki_search:
            page = wikipedia.page(wiki_search[0])
            snippet = page.summary[:600] 
            results.append(f"Title: {page.title}\nSnippet: {snippet}...")
            logger.info("Tier 2 Wikipedia Search Successful.")
            return "\n\n[REAL-TIME WEB DATA (WIKIPEDIA)]:\n" + "\n---\n".join(results)
    except Exception as e:
        logger.warning(f"Tier 2 (Wikipedia) Search Failed: {str(e)}")

    try:
        if GOOGLE_SEARCH_API_KEY and GOOGLE_SEARCH_CX:
            logger.info("Attempting Web Search via Google Search API (Tier 3)...")
            res = requests.get(f"https://www.googleapis.com/customsearch/v1?q={urllib.parse.quote(query)}&key={GOOGLE_SEARCH_API_KEY}&cx={GOOGLE_SEARCH_CX}&num=3", timeout=5)
            if res.status_code == 200:
                google_data = res.json()
                for item in google_data.get("items", [])[:3]:
                    results.append(f"Title: {item.get('title')}\nSnippet: {item.get('snippet')}")
                logger.info("Tier 3 Google Search Successful.")
                return "\n\n[REAL-TIME WEB DATA (GOOGLE)]:\n" + "\n---\n".join(results)
    except Exception as e:
        logger.error(f"Tier 3 (Google) Search completely failed: {str(e)}")

    return "" 

# =========================================================================
# 🧠 KNOWLEDGE VAULT: RAG SYSTEM
# =========================================================================
@app.post("/upload-document")
async def upload_document(user_id: int = Form(...), file: UploadFile = File(...), db: Session = Depends(get_db)):
    if not RAG_ENABLED: 
        return {"error": "RAG Server Dependencies Missing. Ensure PyPDF2, numpy, and faiss are installed."}
        
    user = db.query(User).filter(User.id == user_id).first()
    if not user: 
        return {"error": "User not found in system."}
        
    if not file.filename.endswith('.pdf'): 
        return {"error": "Invalid format. Only PDF files are supported currently."}
        
    try:
        pdf_content = await file.read()
        pdf_reader = PyPDF2.PdfReader(io.BytesIO(pdf_content))
        
        extracted_text = ""
        for page_num in range(len(pdf_reader.pages)):
            text = pdf_reader.pages[page_num].extract_text()
            if text:
                extracted_text += text + "\n"
                
        new_doc = Document(user_id=user_id, filename=file.filename, content=extracted_text)
        db.add(new_doc)
        db.commit()
        
        logger.info(f"Knowledge Vault updated. Document '{file.filename}' processed for User {user_id}")
        return {"message": f"Document '{file.filename}' processed and saved to your Knowledge Vault.", "doc_id": new_doc.id}
    except Exception as e:
        logger.error(f"Document processing failed: {str(e)}")
        return {"error": f"Failed to read and process document: {str(e)}"}

def get_rag_context(user_id: int, query: str, db: Session) -> str:
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
        return f"\n\n[RELEVANT VAULT DOCUMENTS]:\n" + "\n...".join(context_chunks) + "\n[END VAULT CONTEXT]\n"
    return ""

# =========================================================================
# 🔥 THE VULTIX CORE ENGINE (Multi-LLM Routing & Processing)
# =========================================================================
@app.post("/process")
async def process_content(request: ChatRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == request.user_id).first()
    if not user: 
        return {"error": "User identity missing or invalid!"}
    if user.is_banned: 
        return {"error": "ACCOUNT_BANNED"}

    now = datetime.utcnow()
    if not user.last_reset_time: 
        user.last_reset_time = now
    if (now - user.last_reset_time).total_seconds() > 86400:
        user.response_count = 0
        user.last_reset_time = now
        db.commit()

    if not user.is_pro and user.response_count >= 50: 
        return {"error": "LIMIT_REACHED. Please upgrade to Pro or wait 24 hours."}

    # -------------------------------------------------------------------------
    # 🎨 IMAGE GENERATION PIPELINE
    # -------------------------------------------------------------------------
    if request.task == "image":
        width, height = (1280, 720) if request.selected_model == "16:9" else (720, 1280) if request.selected_model == "9:16" else (1024, 1024)
        
        if request.image_engine == "hd":
            if not user.is_pro: 
                return {"error": "HD Image Generation is a PRO_FEATURE"}
            API_URL = "https://api-inference.huggingface.co/models/stabilityai/stable-diffusion-xl-base-1.0"
            headers = {"Authorization": f"Bearer {HF_API_KEY}"}
            try:
                response = requests.post(API_URL, headers=headers, json={"inputs": request.transcript}, timeout=30)
                if response.status_code == 200:
                    img_data = base64.b64encode(response.content).decode("utf-8")
                    ai_response = f"![Generated Image](data:image/png;base64,{img_data})"
                else: 
                    ai_response = "HF Engine is warming up or busy. Please try again in 15 seconds! ⏳"
            except Exception as e: 
                ai_response = f"Critical Engine Failure: {str(e)}"
        else:
            cleaned_prompt = request.transcript.strip()[:250]
            encoded_prompt = urllib.parse.quote(cleaned_prompt + ", highly detailed, masterpiece, 8k resolution")
            image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?seed={int(time.time())}&width={width}&height={height}&nologo=true"
            ai_response = f"![Generated Image]({image_url})"

        new_chat = Chat(user_id=request.user_id, session_id=request.session_id, message=request.transcript, response=ai_response)
        if not user.is_pro: 
            user.response_count += 1
        db.add(new_chat)
        db.commit()
        return {"data": ai_response, "chat_id": new_chat.id, "remaining": "Unlimited" if user.is_pro else 50 - user.response_count}

    # -------------------------------------------------------------------------
    # 📚 RAG & WEB CONTEXT GATHERING
    # -------------------------------------------------------------------------
    rag_context = get_rag_context(user_id=user.id, query=request.transcript, db=db) if request.task == "study" and RAG_ENABLED else ""

    search_keywords = ["latest", "today", "news", "price", "current", "update", "2024", "2025", "2026", "weather", "btc", "score", "match", "mausam"]
    web_context = ""
    if any(word in request.transcript.lower() for word in search_keywords):
        raw_web_data = perform_waterfall_search(request.transcript)
        if raw_web_data:
            web_context = raw_web_data + "\n[STRICT SYSTEM COMMAND: You must answer the user's prompt directly using the real-time web data provided above. Be specific, provide numbers, prices, or weather stats. DO NOT tell the user to visit links or claim you don't have real-time access.]\n\n"

    current_time = datetime.utcnow().strftime("%A, %B %d, %Y - %H:%M UTC")

    # -------------------------------------------------------------------------
    # 👨‍💻 CODING ENGINE (OPENROUTER - LIVE UI CANVAS LOGIC)
    # -------------------------------------------------------------------------
    if request.task == "coding":
        try:
            if not OPENROUTER_API_KEY: 
                return {"error": "OpenRouter API Key missing on server."}

            system_instr = """ROLE: Expert UI/UX Developer and Senior Software Engineer.
            CRITICAL INSTRUCTIONS FOR LIVE UI CANVAS:
            1. If the user asks for a UI component, website, dashboard, or frontend design, you MUST output ONLY a single-file raw HTML code.
            2. You MUST use Tailwind CSS via CDN (`<script src="https://cdn.tailwindcss.com"></script>`) in the head.
            3. Include any necessary JavaScript within standard `<script>` tags inside the HTML. Do not reference external local files.
            4. You MUST wrap the ENTIRE HTML code inside a standard markdown block exactly like this: ```html
            ... your code here ...
            ```
            5. DO NOT write any conversational text, explanations, or greetings before or after the code block. ONLY provide the code.
            6. If the user asks for general backend/algorithmic code (Python, C++, etc.), wrap it in the respective markdown block (e.g., ```python) with no extra text."""
            
            history = db.query(Chat).filter(Chat.session_id == request.session_id).order_by(Chat.id.desc()).limit(3).all()
            
            messages = [{"role": "system", "content": system_instr}]
            
            for h in reversed(history):
                messages.append({"role": "user", "content": h.message})
                messages.append({"role": "assistant", "content": h.response})

            final_prompt = f"{web_context}\nUser Request: {request.transcript}"
            messages.append({"role": "user", "content": final_prompt})
            
            logger.info(f"Dispatching Coding Task to OpenRouter (Claude 3.5 Sonnet) for User {user.id}")
            
            headers = {
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "HTTP-Referer": "[https://vultix.ai](https://vultix.ai)", 
                "X-Title": "Vultix AI",
                "Content-Type": "application/json"
            }
            
            payload = {
                "model": "anthropic/claude-3.5-sonnet",
                "messages": messages,
                "temperature": 0.5
            }
            
            res = requests.post("[https://openrouter.ai/api/v1/chat/completions](https://openrouter.ai/api/v1/chat/completions)", headers=headers, json=payload, timeout=60)
            
            if res.status_code == 200:
                ai_msg = res.json()["choices"][0]["message"]["content"]
            else:
                raise Exception(f"OpenRouter Response Error: {res.text}")
            
            new_chat = Chat(user_id=request.user_id, session_id=request.session_id, message=request.transcript, response=ai_msg)
            if not user.is_pro: 
                user.response_count += 1
            db.add(new_chat)
            db.commit()
            
            return {"data": ai_msg, "chat_id": new_chat.id, "remaining": "Unlimited" if user.is_pro else 50 - user.response_count}
        except Exception as e:
            logger.error(f"OpenRouter Engine Complete Failure: {str(e)}")
            return {"error": f"OpenRouter Logic Core Failed: {str(e)}. Please switch to Auto Mode."}

    # -------------------------------------------------------------------------
    # 🧠 GENERAL / VIRAL / STUDY ENGINE (GROQ - LLAMA SERIES)
    # -------------------------------------------------------------------------
    else:
        try:
            model_to_use = "llama-3.3-70b-versatile" if request.selected_model == "llama-3.3-70b-versatile" else "llama-3.1-8b-instant"
            
            if model_to_use == "llama-3.3-70b-versatile" and not user.is_pro: 
                return {"error": "Llama 70B is a PRO_FEATURE"}
                
            if request.image_data: 
                model_to_use = "llama-3.2-90b-vision-instruct"

            intro_keywords = ["who are you", "who made you", "your creator", "who created", "kis ne banaya", "tumhara naam", "who is your developer"]
            creator_info = ""
            if any(word in request.transcript.lower() for word in intro_keywords):
                creator_info = "CRITICAL INSTRUCTION: The user is asking about your identity or creator. You MUST answer EXACTLY with this information: 'I am Vultix AI. My creator is Muhammad Haroon Zahid. He is from Bahawalpur, but currently, he is in Lahore doing his BS IET (Information Engineering Technology) at the University of Lahore.' Do not add any other backstory."

            task_rules = ""
            if request.task == "viral":
                task_rules = "ROLE: Elite YouTube Viral Strategist. FOCUS: US & UK Audiences. Generate massive hook impact."
            elif request.task == "study":
                task_rules = "ROLE: Academic Speedster. For MCQs, provide ONLY the direct letter answer."
            else:
                task_rules = "ROLE: Best friend and supportive AI. LANGUAGE RULE: Speak in casual, highly conversational English. Do not use Roman Urdu. TONE: Friendly, slightly sarcastic. EMOJI RULE: Strictly use real Unicode emojis (max 1 or 2 emojis per full response). Avoid emoji spam."

            system_instr = f"Current Real-Time System Clock: {current_time}.\n{creator_info}\n{task_rules}"

            messages = [{"role": "system", "content": system_instr}]
            
            history = db.query(Chat).filter(Chat.session_id == request.session_id).order_by(Chat.id.desc()).limit(1).all()
            for h in reversed(history):
                messages.append({"role": "user", "content": h.message})
                messages.append({"role": "assistant", "content": h.response})

            final_user_transcript = request.transcript + "\n" + rag_context + "\n" + web_context
            
            if request.image_data:
                messages.append({"role": "user", "content": [{"type": "text", "text": final_user_transcript}, {"type": "image_url", "image_url": {"url": request.image_data}}]})
            else:
                messages.append({"role": "user", "content": final_user_transcript})

            res = client.chat.completions.create(model=model_to_use, messages=messages)
            ai_msg = res.choices[0].message.content
            
            db_message = f"[Sent an Image Payload] {request.transcript}" if request.image_data else request.transcript
            new_chat = Chat(user_id=request.user_id, session_id=request.session_id, message=db_message, response=ai_msg)
            
            if not user.is_pro: 
                user.response_count += 1
            db.add(new_chat)
            db.commit()
            
            return {"data": ai_msg, "chat_id": new_chat.id, "remaining": "Unlimited" if user.is_pro else 50 - user.response_count}
            
        except Exception as e: 
            error_msg = str(e)
            logger.error(f"Groq Route Failed: {error_msg}")
            
            if "model_not_found" in error_msg and request.image_data:
                try:
                    fallback_model = "llama-3.2-11b-vision-instruct"
                    res = client.chat.completions.create(model=fallback_model, messages=messages)
                    ai_msg = res.choices[0].message.content
                    
                    db_message = f"[Sent an Image Payload] {request.transcript}"
                    new_chat = Chat(user_id=request.user_id, session_id=request.session_id, message=db_message, response=ai_msg)
                    if not user.is_pro: 
                        user.response_count += 1
                    db.add(new_chat)
                    db.commit()
                    return {"data": ai_msg, "chat_id": new_chat.id, "remaining": "Unlimited" if user.is_pro else 50 - user.response_count}
                except Exception as inner_e:
                    return {"error": f"Critical Vision API Failure: {str(inner_e)}"}
            
            if "rate_limit_exceeded" in error_msg.lower() or "429" in error_msg:
                return {"error": "Groq Engine Rate Limit Exceeded. The system is cooling down. Please use Coding Mode temporarily!"}
            
            return {"error": error_msg}

# =========================================================================
# 🏢 ADMIN DASHBOARD APIS 
# =========================================================================
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
            title = c.message[:30] + "..." if len(c.message) > 30 else c.message
            sessions.append({"session_id": c.session_id, "title": title})
    return {"history": sessions}

@app.get("/history/session/{session_id}")
async def get_session_chat(session_id: str, db: Session = Depends(get_db)):
    chats = db.query(Chat).filter(Chat.session_id == session_id).order_by(Chat.timestamp.asc()).all()
    return {"chats": [{"message": c.message, "response": c.response} for c in chats]}

@app.delete("/history/session/{session_id}")
async def delete_session(session_id: str, db: Session = Depends(get_db)):
    try:
        db.query(Chat).filter(Chat.session_id == session_id).delete()
        db.commit()
        return {"message": "Chat session successfully purged from database."}
    except Exception as e:
        logger.error(f"Failed to delete session {session_id}: {str(e)}")
        raise HTTPException(status_code=500, detail="Database deletion error")

@app.get("/admin/stats")
async def get_admin_stats(db: Session = Depends(get_db), _: None = Depends(verify_admin)):
    total_users = db.query(User).count()
    total_pro_users = db.query(User).filter(User.is_pro == True).count()
    total_chats = db.query(Chat).count()
    total_feedbacks = db.query(Feedback).count()
    
    return {
        "total_users": total_users, 
        "total_pro_users": total_pro_users, 
        "total_chats": total_chats,
        "total_feedbacks_recorded": total_feedbacks
    }

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
            "is_banned": u.is_banned,
            "created_at": u.created_at
        })
    return {"users": user_list}

@app.post("/admin/toggle_ban/{user_id}")
async def toggle_user_ban(user_id: int, db: Session = Depends(get_db), _: None = Depends(verify_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user: 
        raise HTTPException(status_code=404, detail="User not found in system")
        
    user.is_banned = not user.is_banned
    db.commit()
    return {"message": "Status updated successfully", "is_banned": user.is_banned}

@app.post("/admin/toggle_pro/{user_id}")
async def toggle_user_pro(user_id: int, db: Session = Depends(get_db), _: None = Depends(verify_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user: 
        raise HTTPException(status_code=404, detail="User not found in system")
        
    user.is_pro = not user.is_pro
    db.commit()
    return {"message": "Premium status updated successfully", "is_pro": user.is_pro}

# =========================================================================
# 🚦 SERVER EXECUTION BLOCK
# =========================================================================
if __name__ == "__main__":
    logger.info("Starting Vultix AI Enterprise Server on Port 8000...")
    uvicorn.run(app, host="127.0.0.1", port=8000)
