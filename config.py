import os
from pathlib import Path

from dotenv import load_dotenv

VERSION = "0.1.0"

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# Storage
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "events.db"
JSONL_PATH = DATA_DIR / "events.jsonl"

# LLM
LLM_URL = os.getenv("LLM_URL", "http://localhost:1234/v1/chat/completions")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen/qwen3-4b-2507")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")

# CORS
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")

# Thresholds
MIN_EVENTS_FOR_REC = int(os.getenv("MIN_EVENTS_FOR_REC", "3"))
REC_COOLDOWN = int(os.getenv("REC_COOLDOWN", "120"))
INSIGHT_COOLDOWN = int(os.getenv("INSIGHT_COOLDOWN", "10"))

# Admin
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "tastebud")
LLM_CONFIG_PATH = DATA_DIR / "llm_config.json"

# OCR
OCR_WHITELIST_IPS = [ip.strip() for ip in os.getenv("OCR_WHITELIST_IPS", "").split(",") if ip.strip()]

# Auth
JWT_SECRET = os.getenv("JWT_SECRET", "hashtee-secret-change-in-production")
JWT_EXPIRY_HOURS = int(os.getenv("JWT_EXPIRY_HOURS", "72"))
OTP_EXPIRY_MINUTES = int(os.getenv("OTP_EXPIRY_MINUTES", "10"))
# Email
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

# Server
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
