import os
from pathlib import Path
from dotenv import load_dotenv

VERSION = "0.1.0"

load_dotenv(".env")

BASE_DIR = Path(__file__).resolve().parent

DATA_DIR = Path(os.getenv("DATA_DIR", "data"))
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = str(DATA_DIR / "events.db")
JSONL_PATH = str(DATA_DIR / "events.jsonl")

LLM_URL = os.getenv("LLM_URL", "http://localhost:1234/v1/chat/completions")
LLM_MODEL = os.getenv("LLM_MODEL", "qwen/qwen3-4b-2507")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")

CORS_ORIGINS = os.getenv("CORS_ORIGINS", "").split(",")

MIN_EVENTS_FOR_REC = int(os.getenv("MIN_EVENTS_FOR_REC", "3"))
REC_COOLDOWN = int(os.getenv("REC_COOLDOWN", "120"))
INSIGHT_COOLDOWN = int(os.getenv("INSIGHT_COOLDOWN", "10"))

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "tastebud")

LLM_CONFIG_PATH = str(BASE_DIR / "llm_config.json")

OCR_WHITELIST_IPS = os.getenv("OCR_WHITELIST_IPS", "").strip().split(",")

JWT_SECRET = os.getenv("JWT_SECRET", "hashtee-secret-change-in-production")
JWT_EXPIRY_HOURS = int(os.getenv("JWT_EXPIRY_HOURS", "72"))
OTP_EXPIRY_MINUTES = int(os.getenv("OTP_EXPIRY_MINUTES", "10"))

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
BOOKING_EMAIL = os.getenv("BOOKING_EMAIL", "contact@hashteelab.com")
NANOCLAW_URL = os.getenv("NANOCLAW_URL", "http://localhost:3001")

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))
