import jwt
import bcrypt
import random
import time
from datetime import datetime, timedelta

import config
import db
import resend


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash or not password_hash.startswith("$2"):
        return False
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_token(user_id: int) -> str:
    payload = {
        "user_id": user_id,
        "exp": datetime.utcnow() + timedelta(hours=config.JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm="HS256")


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"])
    except Exception:
        return None


def get_current_user(authorization: str = "") -> dict | None:
    if not authorization.startswith("Bearer "):
        return None
    token = authorization[len("Bearer "):]
    payload = decode_token(token)
    if not payload:
        return None
    user_id = payload.get("user_id")
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT id, name, email, phone, company, is_verified FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return None
    return dict(row)


def generate_otp() -> str:
    return str(random.randint(100000, 999999))


def save_otp(user_id: int, code: str):
    expires_at = time.time() + config.OTP_EXPIRY_MINUTES * 60
    with db.get_db() as conn:
        conn.execute("INSERT INTO otp_codes (user_id, code, expires_at) VALUES (?, ?, ?)",
                     (user_id, code, expires_at))
        conn.commit()


def send_otp_email(name: str, email: str, code: str):
    resend.api_key = config.RESEND_API_KEY
    greeting = f"Hi {name}, use" if name else "Use"
    resend.Emails.send({
        "from": "Hashtee <no-reply@auth.hashteelab.com>",
        "to": [email],
        "subject": f"Your verification code: {code}",
        "html": (
            '<div style="font-family: sans-serif; max-width: 480px; margin: 0 auto; padding: 40px 20px;">'
            '<h2 style="color: #1a1a1a; margin-bottom: 12px;">Verify your email</h2>'
            '<p style="color: #666; margin-bottom: 24px;">'
            f"{greeting} this code to verify your Hashtee account. It expires in 10 minutes."
            "</p>"
            '<div style="background: #f9f9f9; border-radius: 12px; padding: 24px; text-align: center; margin-bottom: 24px;">'
            f'<span style="font-size: 32px; font-weight: 700; letter-spacing: 6px; color: #1a1a1a;">{code}</span>'
            "</div>"
            '<p style="color: #999; font-size: 12px;">'
            "If you didn't create a Hashtee account, you can safely ignore this email."
            "</p>"
            "</div>"
        ),
    })


def signup(name: str, email: str, password: str, phone: str = "", company: str = "") -> dict:
    with db.get_db() as conn:
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            return {"error": "Email already registered"}
        pw_hash = hash_password(password)
        cursor = conn.execute(
            "INSERT INTO users (name, email, phone, company, password_hash, is_verified) VALUES (?, ?, ?, ?, ?, 0)",
            (name, email, phone, company, pw_hash),
        )
        conn.commit()
        user_id = cursor.lastrowid

    code = generate_otp()
    save_otp(user_id, code)
    try:
        send_otp_email(name, email, code)
    except Exception as e:
        return {"error": f"Failed to send OTP email: {e}"}

    token = create_token(user_id)
    return {"token": token, "user_id": user_id}


def verify_email(user_id: int, code: str) -> dict:
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM otp_codes WHERE user_id = ?", (user_id,)
        ).fetchall()
        if not rows:
            return {"error": "Invalid verification code"}
        row = rows[-1]
        if row["expires_at"] < time.time():
            return {"error": "Invalid or expired code"}
        if row["code"] != code:
            return {"error": "Invalid verification code"}
        conn.execute("UPDATE users SET is_verified = 1 WHERE id = ?", (user_id,))
        conn.execute("DELETE FROM otp_codes WHERE user_id = ?", (user_id,))
        conn.commit()
    return {"ok": True}


def resend_verification(user_id: int) -> dict:
    with db.get_db() as conn:
        row = conn.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return {"error": "User not found"}
        email = row["email"]
    code = generate_otp()
    save_otp(user_id, code)
    try:
        send_otp_email("", email, code)
    except Exception as e:
        return {"error": f"Failed to resend OTP: {e}"}
    return {"ok": True}


def login(email: str, password: str) -> dict:
    with db.get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if not row:
            return {"error": "No account found with this email. Please sign up first.", "status_code": 404}
        user = dict(row)
        if not verify_password(password, user.get("password_hash", "")):
            return {"error": "Invalid credentials", "status_code": 401}
        token = create_token(user["id"])
        return {
            "token": token,
            "user": {
                "id": user["id"],
                "email": user["email"],
                "name": user["name"],
                "is_verified": user.get("is_verified", 0),
            },
        }


def get_me(user_id: int) -> dict | None:
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT id, name, email, phone, company, is_verified FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        return None
    return dict(row)
