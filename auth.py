import jwt
import bcrypt
import random
import time
from datetime import datetime, timedelta
from config import JWT_SECRET, JWT_EXPIRY_HOURS, OTP_EXPIRY_MINUTES
from db import get_db

# ---------------------------
# Password + Token utilities
# ---------------------------

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    # Guard: reject non-bcrypt hashes (e.g. Google OAuth users stored as "google-oauth")
    if not password_hash or not password_hash.startswith("$2"):
        return False
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def create_token(user_id: int, email: str) -> str:
    payload = {
        "user_id": user_id,
        "email": email,
        "exp": datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def decode_token(token: str):
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except Exception:
        return None


def get_current_user(request):
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header[7:]
    payload = decode_token(token)
    if not payload:
        return None
    conn = get_db()
    user = conn.execute(
        "SELECT id, name, email, phone, company, is_verified FROM users WHERE id = ?",
        (payload["user_id"],)
    ).fetchone()
    conn.close()
    return dict(user) if user else None


# ---------------------------
# OTP utilities
# ---------------------------

def generate_otp() -> str:
    return str(random.randint(100000, 999999))


def save_otp(user_id: int, code: str):
    expires_at = time.time() + (OTP_EXPIRY_MINUTES * 60)
    conn = get_db()
    conn.execute("INSERT INTO otp_codes (user_id, code, expires_at) VALUES (?, ?, ?)",
                 (user_id, code, expires_at))
    conn.commit()
    conn.close()


def send_otp_email(email: str, code: str, name: str = ""):
    import resend
    from config import RESEND_API_KEY
    resend.api_key = RESEND_API_KEY
    resend.Emails.send({
        "from": "Hashtee <no-reply@auth.hashteelab.com>",
        "to": [email],
        "subject": f"Your verification code: {code}",
        "html": f"""
        <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto; padding: 40px 20px;">
          <h2 style="color: #1a1a1a; margin-bottom: 12px;">Verify your email</h2>
          <p style="color: #666; margin-bottom: 24px;">
            {f"Hi {name}, use" if name else "Use"} this code to verify your Hashtee account. It expires in 10 minutes.
          </p>
          <div style="background: #f9f9f9; border-radius: 12px; padding: 24px; text-align: center; margin-bottom: 24px;">
            <span style="font-size: 32px; font-weight: 700; letter-spacing: 6px; color: #1a1a1a;">{code}</span>
          </div>
          <p style="color: #999; font-size: 12px;">
            If you didn't create a Hashtee account, you can safely ignore this email.
          </p>
        </div>
        """
    })


# ---------------------------
# Auth endpoints
# ---------------------------

def signup(name: str, email: str, password: str, phone=None, company=None):
    hashed_pw = hash_password(password)
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO users (name, email, phone, company, password_hash, is_verified) VALUES (?, ?, ?, ?, ?, 0)",
        (name, email, phone, company, hashed_pw)
    )
    user_id = cur.lastrowid
    conn.commit()
    conn.close()

    code = generate_otp()
    save_otp(user_id, code)
    send_otp_email(email, code, name)

    token = create_token(user_id, email)
    return {"user_id": user_id, "email": email, "is_verified": False, "token": token}


def verify_email(user_id: int, code: str):
    conn = get_db()
    otp = conn.execute("SELECT * FROM otp_codes WHERE user_id = ?", (user_id,)).fetchone()
    if not otp or otp["expires_at"] < time.time() or otp["code"] != code:
        conn.close()
        return {"error": "Invalid or expired code"}

    conn.execute("UPDATE users SET is_verified = 1 WHERE id = ?", (user_id,))
    conn.execute("DELETE FROM otp_codes WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    return {"ok": True}


def resend_verification(user_id: int):
    conn = get_db()
    conn.execute("DELETE FROM otp_codes WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

    code = generate_otp()
    save_otp(user_id, code)

    conn = get_db()
    user = conn.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    send_otp_email(user["email"], code)
    return {"ok": True}


def login(email: str, password: str):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()

    if not user or not verify_password(password, user["password_hash"]):
        return {"error": "Invalid credentials"}

    token = create_token(user["id"], user["email"])
    return {"user_id": user["id"], "email": user["email"], "is_verified": bool(user["is_verified"]), "token": token}


def get_me(user_id: int):
    conn = get_db()
    user = conn.execute("SELECT id, name, email, phone, company, is_verified FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return {"user": dict(user)} if user else None