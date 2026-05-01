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
from datetime import datetime
from fastapi import FastAPI, HTTPException, Depends, Header, Request
from pydantic import BaseModel
from typing import Optional
from fastapi.middleware.cors import CORSMiddleware
from groq import Groq
from sqlalchemy import create_engine, Column, Integer, String, Text, ForeignKey, DateTime, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from tavily import TavilyClient

# === ENVIRONMENT VARIABLES ===
GOOGLE_CLIENT_ID = "1040604821889-nlp7drjmimem7p2ldh1bkhkepp9f1hii.apps.googleusercontent.com"
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
HF_API_KEY = os.getenv("HF_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_MASTER_KEY = os.getenv("ADMIN_MASTER_KEY", "ceo123")

# 🚀 NEW KEYS FOR SAAS MONETIZATION & DEEPSEEK
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
LEMON_API_KEY = os.getenv("LEMON_API_KEY")
LEMON_WEBHOOK_SECRET = os.getenv("LEMON_WEBHOOK_SECRET")
LEMON_STORE_ID = os.getenv("LEMON_STORE_ID")
LEMON_VARIANT_ID = os.getenv("LEMON_VARIANT_ID") # Jo product user buy karega

# === DATABASE SETUP ===
if DATABASE_URL and DATABASE_URL.startswith("sqlite"):
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
    is_pro = Column(Boolean, default=False) # 💎 NEW: Premium Status

class Chat(Base):
    __tablename__ = "chats"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    session_id = Column(String, index=True)
    message = Column(Text)
    response = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

# === FASTAPI APP ===
app = FastAPI(title="Vultix AI Core Engine")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

client = Groq(api_key=GROQ_API_KEY)
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

class CheckoutRequest(BaseModel):
    user_id: int

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

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
        raise HTTPException(status_code=400, detail=f"Google Login Failed: {str(e)}")

# === 💳 SAAS MONETIZATION: LEMON SQUEEZY APIS ===

@app.post("/create-checkout")
async def create_checkout(request: CheckoutRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == request.user_id).first()
    if not user: return {"error": "User not found"}
    
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
                    "custom": { "user_id": str(user.id) } # Ye webhook mein wapas aayega
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
        return {"checkout_url": checkout_url}
    except Exception as e:
        return {"error": str(e)}

@app.post("/webhook")
async def lemon_webhook(request: Request, db: Session = Depends(get_db)):
    # Verify Webhook Signature for Security
    signature = request.headers.get("X-Signature")
    body = await request.body()
    
    mac = hmac.new(LEMON_WEBHOOK_SECRET.encode(), msg=body, digestmod=hashlib.sha256)
    if not hmac.compare_digest(mac.hexdigest(), signature):
        raise HTTPException(status_code=401, detail="Invalid Webhook Signature")
    
    data = json.loads(body)
    event_name = data["meta"]["event_name"]
    
    # Check if payment was successful
    if event_name in ["order_created", "subscription_created"]:
        custom_data = data["data"]["attributes"]["custom_data"]
        user_id = int(custom_data.get("user_id", 0))
        
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            user.is_pro = True # 💎 Upgrade User in DB!
            db.commit()
            
    return {"status": "success"}

# === 🧠 MULTI-LLM CORE ENGINE ===

@app.post("/process")
async def process_content(request: ChatRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == request.user_id).first()
    if not user: return {"error": "User missing!"}
    if user.is_banned: return {"error": "ACCOUNT_BANNED"}

    # Limit Checking (Skip if PRO)
    now = datetime.utcnow()
    if not user.last_reset_time: user.last_reset_time = now
    if (now - user.last_reset_time).total_seconds() > 86400:
        user.response_count = 0
        user.last_reset_time = now
        db.commit()

    if not user.is_pro and user.response_count >= 50: 
        return {"error": "LIMIT_REACHED"}

    # 🎨 IMAGE GENERATION LOGIC (Image Studio)
    if request.task == "image":
        if request.image_engine == "hd":
            if not user.is_pro: return {"error": "PRO_FEATURE"}
            API_URL = "https://api-inference.huggingface.co/models/stabilityai/stable-diffusion-xl-base-1.0"
            headers = {"Authorization": f"Bearer {HF_API_KEY}"}
            response = requests.post(API_URL, headers=headers, json={"inputs": request.transcript})
            if response.status_code == 200:
                img_data = base64.b64encode(response.content).decode("utf-8")
                ai_response = f"![Generated Image](data:image/png;base64,{img_data})"
            else: ai_response = "HF Engine is warming up. Please try again! ⏳"
        else:
            VIP_STYLE_ENHANCERS = ", volumetric lighting, 8k resolution, hyper-detailed, photorealistic, dramatic composition, masterpieces by masterful artists, cinematic quality, no cartoon."
            cleaned_prompt = request.transcript.strip()[:200]
            vip_prompt = cleaned_prompt + VIP_STYLE_ENHANCERS
            encoded_prompt = urllib.parse.quote(vip_prompt)
            seed = int(time.time())
            image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?seed={seed}&width=720&height=1280&nologo=true"
            ai_response = f"![Generated Image]({image_url})"

        new_chat = Chat(user_id=request.user_id, session_id=request.session_id, message=request.transcript, response=ai_response)
        if not user.is_pro: user.response_count += 1
        db.add(new_chat)
        db.commit()
        return {"data": ai_response, "remaining": "Unlimited" if user.is_pro else 50 - user.response_count}

    # 🚀 SMART ROUTER: DEEPSEEK FOR CODING, GROQ FOR REST
    history = db.query(Chat).filter(Chat.session_id == request.session_id).order_by(Chat.id.desc()).limit(4).all()
    
    if request.task == "coding":
        # DEEPSEEK API LOGIC
        system_instr = "ROLE: Senior 10x Software Engineer & Elite Academic Logic Expert. CRITICAL RULE: When writing C++ code or providing solutions, strictly align with academic requirements. You must use precise logic structures and exact naming conventions as required for strict academic integrity. Absolutely NO code comments in generated code unless explicitly needed to explain a required logic structure. Output ONLY raw, clean logic."
        
        messages = [{"role": "system", "content": system_instr}]
        for h in reversed(history):
            messages.append({"role": "user", "content": h.message})
            messages.append({"role": "assistant", "content": h.response})
        messages.append({"role": "user", "content": request.transcript})
        
        try:
            if not DEEPSEEK_API_KEY: return {"error": "DeepSeek API Key is missing on Server"}
            ds_headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
            ds_payload = {"model": "deepseek-chat", "messages": messages}
            
            res = requests.post("https://api.deepseek.com/chat/completions", headers=ds_headers, json=ds_payload)
            res_data = res.json()
            
            if "choices" in res_data: ai_msg = res_data['choices'][0]['message']['content']
            else: return {"error": f"DeepSeek Error: {res_data}"}
            
            new_chat = Chat(user_id=request.user_id, session_id=request.session_id, message=request.transcript, response=ai_msg)
            if not user.is_pro: user.response_count += 1
            db.add(new_chat)
            db.commit()
            return {"data": ai_msg, "remaining": "Unlimited" if user.is_pro else 50 - user.response_count}
            
        except Exception as e:
            return {"error": f"DeepSeek Route Failed: {str(e)}"}

    else:
        # GROQ API LOGIC (Friendly, Study, Viral, Vision)
        model_to_use = "llama-3.3-70b-versatile" if request.selected_model == "llama-3.3-70b-versatile" else "llama-3.1-8b-instant"
        
        # PRO Check for 70B
        if model_to_use == "llama-3.3-70b-versatile" and not user.is_pro:
            return {"error": "PRO_FEATURE"}
        
        # VISION AI OVERRIDE WITH SMART FALLBACK 🧠
        if request.image_data:
            model_to_use = "llama-3.2-90b-vision-instruct"

        task_rules = ""
        if request.task == "viral":
            task_rules = "ROLE: Elite YouTube & Social Media Viral Strategist. FOCUS: US & UK Audiences. Generate highly engaging scripts, 3-second hooks, and storytelling prompts."
        elif request.task == "study":
            task_rules = "ROLE: Academic Speedster & Task Optimizer. CRITICAL RULE: For multiple-choice quizzes or translations, provide ONLY the direct letter answer (e.g., 'a', 'b', 'c') with ZERO detailed analysis."
        else:
            task_rules = "ROLE: Best friend and supportive AI. LANGUAGE RULE: Use casual Pakistani Roman Urdu mixed with English words. Keep technical terms in English. TONE: Sarcastic, use emojis (🔥, 💀, 🚀, 😂)."

        creator_info = "If anyone asks who created you, state that you are Vultix AI, developed by Muhammad Haroon Zahid, an IT entrepreneur from Bahawalpur."
        system_instr = f"You are Vultix AI, a premium SaaS assistant.\n{creator_info}\n{task_rules}"

        messages = [{"role": "system", "content": system_instr}]
        for h in reversed(history):
            messages.append({"role": "user", "content": h.message})
            messages.append({"role": "assistant", "content": h.response})

        if request.image_data:
            messages.append({"role": "user", "content": [{"type": "text", "text": request.transcript}, {"type": "image_url", "image_url": {"url": request.image_data}}]})
        else:
            messages.append({"role": "user", "content": request.transcript})

        try:
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
            if "model_decommissioned" in error_msg or "model_not_found" in error_msg and request.image_data:
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
                    return {"error": f"Vision API Issue: Check active model names. Details: {str(inner_e)}"}
            return {"error": error_msg}

# === ADMIN APIS (Untouched) ===
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

@app.get("/admin/chats/{user_id}")
async def get_admin_user_chats(user_id: int, db: Session = Depends(get_db), _: None = Depends(verify_admin)):
    chats = db.query(Chat).filter(Chat.user_id == user_id).order_by(Chat.timestamp.asc()).all()
    return {"chats": [{"message": c.message, "response": c.response, "timestamp": c.timestamp} for c in chats]}

@app.post("/admin/toggle_ban/{user_id}")
async def toggle_user_ban(user_id: int, db: Session = Depends(get_db), _: None = Depends(verify_admin)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user: raise HTTPException(status_code=404, detail="User not found")
    user.is_banned = not user.is_banned
    db.commit()
    return {"message": "Status updated", "is_banned": user.is_banned}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
