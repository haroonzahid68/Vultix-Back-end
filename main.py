import uvicorn
import bcrypt
import os
import urllib.parse
import requests
import base64
import time
from typing import Optional
from datetime import datetime
from fastapi import FastAPI, HTTPException, Depends, Header
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from groq import Groq
from sqlalchemy import create_engine, Column, Integer, String, Text, ForeignKey, DateTime, Boolean, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from tavily import TavilyClient

GOOGLE_CLIENT_ID = "1040604821889-nlp7drjmimem7p2ldh1bkhkepp9f1hii.apps.googleusercontent.com"
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
HF_API_KEY = os.getenv("HF_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
ADMIN_MASTER_KEY = os.getenv("ADMIN_MASTER_KEY", "ceo123")

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

class Chat(Base):
    __tablename__ = "chats"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    session_id = Column(String, index=True)
    message = Column(Text)
    response = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Vultix AI Core Engine")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

client = Groq(api_key=GROQ_API_KEY)
tavily = TavilyClient(api_key=TAVILY_API_KEY)

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
    image_data: Optional[str] = None  # Ab ye null values ko block nahi karega! new vision ai feature

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

def verify_admin(x_admin_key: str = Header(...)):
    if x_admin_key != ADMIN_MASTER_KEY:
        raise HTTPException(status_code=403, detail="ACCESS_DENIED")

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
        return {"user_id": user.id, "username": user.username, "full_name": user.full_name}
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
        return {"user_id": user.id, "username": user.username, "full_name": user.full_name}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Google Login Failed: {str(e)}")

@app.post("/process")
async def process_content(request: ChatRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == request.user_id).first()
    if not user: return {"error": "User missing!"}
    if user.is_banned: return {"error": "ACCOUNT_BANNED"}

    now = datetime.utcnow()
    if not user.last_reset_time: user.last_reset_time = now
    if (now - user.last_reset_time).total_seconds() > 86400:
        user.response_count = 0
        user.last_reset_time = now
        db.commit()

    if user.response_count >= 50: return {"error": "LIMIT_REACHED"}

    # IMAGE GENERATION LOGIC (Image Studio)
    if request.task == "image":
        if request.image_engine == "hd":
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
        user.response_count += 1
        db.add(new_chat)
        db.commit()
        return {"data": ai_response, "remaining": 50 - user.response_count}

    # TEXT & VISION AI ENGINE LOGIC
    model_to_use = "llama-3.3-70b-versatile" if request.task in ["coding", "study"] else "llama-3.1-8b-instant"
    
    # VISION AI OVERRIDE
    if request.image_data:
        model_to_use = "llama-3.2-90b-vision-preview"  # Naya aur zyada powerful model!

    task_rules = ""
    if request.task == "viral":
        task_rules = "ROLE: Elite YouTube & Social Media Viral Strategist. FOCUS: US & UK Audiences. Generate highly engaging scripts, 3-second hooks, and ASMR/Survival storytelling prompts. Maximize retention."
    elif request.task == "coding":
        task_rules = "ROLE: Senior 10x Software Engineer. Generate advanced, highly optimized code. CRITICAL RULE: Absolutely NO code comments in generated code. Output ONLY raw, clean logic."
    elif request.task == "study":
        task_rules = "ROLE: Academic Speedster & Task Optimizer. CRITICAL RULE: For multiple-choice quizzes or translations, provide ONLY the direct letter answer (e.g., 'a', 'b', 'c') with ZERO detailed analysis."
    else:
        task_rules = "ROLE: Best friend and supportive AI. LANGUAGE RULE: Use casual Pakistani Roman Urdu mixed with English words. Keep spellings simple (e.g., 'karna', 'masla'). Keep technical terms in English. TONE: Sarcastic, use emojis (🔥, 💀, 🚀, 😂)."

    creator_info = "If anyone asks who created, made, or developed you, proudly state that you are Vultix AI, developed by Muhammad Haroon Zahid, a 20-year-old tech entrepreneur and IT & Software agency owner from Bahawalpur, currently living in Lahore and studying BS IET at the University of Lahore."

    system_instr = f"You are Vultix AI, a premium SaaS assistant.\n{creator_info}\n{task_rules}"

    messages = [{"role": "system", "content": system_instr}]

    history = db.query(Chat).filter(Chat.session_id == request.session_id).order_by(Chat.id.desc()).limit(4).all()
    for h in reversed(history):
        messages.append({"role": "user", "content": h.message})
        messages.append({"role": "assistant", "content": h.response})

    # DYNAMIC MESSAGE FORMATTING FOR VISION AI
    if request.image_data:
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": request.transcript},
                {"type": "image_url", "image_url": {"url": request.image_data}}
            ]
        })
    else:
        messages.append({"role": "user", "content": request.transcript})

    try:
        res = client.chat.completions.create(model=model_to_use, messages=messages)
        ai_msg = res.choices[0].message.content
        
        # Save transcript to DB (We DO NOT save Base64 image to DB to prevent storage crash)
        db_message = f"[Sent an Image] {request.transcript}" if request.image_data else request.transcript
        
        new_chat = Chat(user_id=request.user_id, session_id=request.session_id, message=db_message, response=ai_msg)
        user.response_count += 1
        db.add(new_chat)
        db.commit()
        return {"data": ai_msg, "remaining": 50 - user.response_count}
    except Exception as e: return {"error": str(e)}

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
    total_chats = db.query(Chat).count()
    return {"total_users": total_users, "total_chats": total_chats}

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
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.is_banned = not user.is_banned
    db.commit()
    return {"message": "Status updated", "is_banned": user.is_banned}

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
