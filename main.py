import uvicorn
import bcrypt
import os
import urllib.parse
import requests
import base64
import time
from datetime import datetime
from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from groq import Groq
from sqlalchemy import create_engine, Column, Integer, String, Text, ForeignKey, DateTime
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

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

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
        return {"user_id": user.id, "username": user.username, "full_name": user.full_name}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Google Login Failed: {str(e)}")

@app.post("/process")
async def process_content(request: ChatRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == request.user_id).first()
    if not user: return {"error": "User missing!"}

    now = datetime.utcnow()
    if not user.last_reset_time: user.last_reset_time = now
    if (now - user.last_reset_time).total_seconds() > 86400: 
        user.response_count = 0
        user.last_reset_time = now
        db.commit()

    if user.response_count >= 50: return {"error": "LIMIT_REACHED"}

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

    model_to_use = "llama-3.3-70b-versatile" if request.task in ["coding", "study"] else "llama-3.1-8b-instant"
    
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
        
    messages.append({"role": "user", "content": request.transcript})
    
    try:
        res = client.chat.completions.create(model=model_to_use, messages=messages)
        ai_msg = res.choices[0].message.content
        new_chat = Chat(user_id=request.user_id, session_id=request.session_id, message=request.transcript, response=ai_msg)
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

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
