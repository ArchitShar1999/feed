from fastapi import FastAPI, HTTPException, APIRouter
import uvicorn
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr
from typing import Optional, Dict, Any
import smtplib
import random
import string
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os
from motor.motor_asyncio import AsyncIOMotorClient


# Load environment variables
# Check root first, then backend folder for local dev
load_dotenv()
if not os.getenv("MONGO_USER") and not os.getenv("MONGODB_URL"):
    load_dotenv("backend/.env")

app = FastAPI(title="Beumer Feedback API")

# Enable CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Determine the absolute path to the static directory
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")

# Route for the main UI
@app.get("/")
async def read_index():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"error": f"index.html not found at {index_path}. Check your directory structure."}

# Mount the static directory for other assets (CSS, JS)
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
else:
    print(f"WARNING: Static directory not found at {STATIC_DIR}")

# In-memory OTP store: { email: { otp: str, expires_at: datetime } }
otp_store: Dict[str, Dict] = {}

# SMTP Config from .env
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER)

import urllib.parse

# MongoDB Connection
MONGO_USER = os.getenv("MONGO_USER")
MONGO_PASS = os.getenv("MONGO_PASS")
MONGO_HOST = os.getenv("MONGO_HOST")
MONGO_DB = os.getenv("MONGO_DB", "beumer_feedback")

# Construct URL if individual variables are provided, otherwise use MONGODB_URL
if MONGO_USER and MONGO_PASS and MONGO_HOST:
    # Safely escape username and password for the URI
    user_escaped = urllib.parse.quote_plus(MONGO_USER)
    pass_escaped = urllib.parse.quote_plus(MONGO_PASS)
    MONGODB_URL = f"mongodb+srv://{user_escaped}:{pass_escaped}@{MONGO_HOST}/?retryWrites=true&w=majority"
    DATABASE_NAME = MONGO_DB
    print(f"Connecting to MongoDB Atlas: {MONGO_HOST}")
else:
    MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    DATABASE_NAME = os.getenv("DATABASE_NAME", MONGO_DB)
    if "localhost" in MONGODB_URL:
        print(f"Connecting to Local MongoDB: {MONGODB_URL}")
    else:
        print(f"Connecting to MongoDB via MONGODB_URL: {MONGODB_URL[:20]}...")

# Initializing Client
try:
    client = AsyncIOMotorClient(MONGODB_URL)
    db = client[DATABASE_NAME]
    feedback_collection = db["feedback"]
    print(f"Successfully connected to database: {DATABASE_NAME}")
except Exception as e:
    print(f"Database connection failed: {e}")


# ─── Models ───────────────────────────────────────────────────────────────────

class OtpRequest(BaseModel):
    email: EmailStr

class OtpVerify(BaseModel):
    email: EmailStr
    otp: str

class FeedbackData(BaseModel):
    sectionA: Dict[str, Any]
    sectionB: Dict[str, Any]
    sectionC: Dict[str, Any]
    sectionD_FillPac: Optional[Dict[str, Any]] = None
    sectionD_BucketElevator: Optional[Dict[str, Any]] = None


# ─── OTP Helpers ──────────────────────────────────────────────────────────────

def generate_otp(length: int = 6) -> str:
    return ''.join(random.choices(string.digits, k=length))

def send_otp_email(to_email: str, otp: str) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Your Beumer Feedback Verification Code"
    msg["From"] = SMTP_FROM
    msg["To"] = to_email

    html = f"""
    <html><body style="font-family:Arial,sans-serif;background:#f4f4f4;padding:30px;">
      <div style="background:white;border-radius:8px;padding:30px;max-width:480px;margin:auto;">
        <h2 style="color:#003399;">Beumer Digitalization</h2>
        <p>Your email verification code is:</p>
        <div style="font-size:2.5rem;font-weight:bold;letter-spacing:10px;color:#003399;
                    background:#f0f4ff;padding:20px;border-radius:8px;text-align:center;">
          {otp}
        </div>
        <p style="color:#888;margin-top:20px;">This code expires in <strong>10 minutes</strong>.</p>
        <p style="color:#888;font-size:0.85rem;">If you didn't request this, please ignore this email.</p>
      </div>
    </body></html>
    """
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_FROM, to_email, msg.as_string())


# ─── Endpoints ────────────────────────────────────────────────────────────────

# API Router for namespaced endpoints
api_router = APIRouter(prefix="/api")

@api_router.post("/send-otp")
async def send_otp(request: OtpRequest):
    print(f"DEBUG: Received OTP request for {request.email}")
    try:
        otp = generate_otp()
        print(f"DEBUG: Generated OTP: {otp}")
        otp_store[request.email] = {
            "otp": otp,
            "expires_at": datetime.utcnow() + timedelta(minutes=10)
        }
        print(f"DEBUG: Attempting to send email via {SMTP_HOST}...")
        send_otp_email(str(request.email), otp)
        print(f"DEBUG: OTP email sent successfully to {request.email}")
        return {"status": "success", "message": f"OTP sent to {request.email}"}
    except smtplib.SMTPAuthenticationError:
        print("DEBUG: SMTP Authentication Failed!")
        raise HTTPException(status_code=500, detail="SMTP authentication failed. Check your credentials in .env")
    except Exception as e:
        print(f"DEBUG: Unexpected error in send_otp: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@api_router.post("/verify-otp")
async def verify_otp(request: OtpVerify):
    record = otp_store.get(request.email)
    if not record:
        raise HTTPException(status_code=400, detail="No OTP found for this email. Please request a new one.")

    if datetime.utcnow() > record["expires_at"]:
        del otp_store[request.email]
        raise HTTPException(status_code=400, detail="OTP has expired. Please request a new one.")

    if request.otp != record["otp"]:
        raise HTTPException(status_code=400, detail="Incorrect OTP. Please try again.")

    del otp_store[request.email]
    return {"status": "success", "message": "Email verified successfully!"}


@api_router.post("/submit-feedback")
async def submit_feedback(data: FeedbackData):
    try:
        feedback_dict = data.model_dump()
        feedback_dict["created_at"] = datetime.utcnow()
        
        # Insert into MongoDB
        result = await feedback_collection.insert_one(feedback_dict)
        
        print(f"Received and saved feedback: {result.inserted_id}")
        return {"status": "success", "message": "Feedback saved successfully", "id": str(result.inserted_id)}
    except Exception as e:
        print(f"Error saving feedback: {e}")
        raise HTTPException(status_code=500, detail="Internal server error while saving feedback")


@api_router.get("/health")
async def health_check():
    return {"status": "ok"}

# Include the router in the app
app.include_router(api_router)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
