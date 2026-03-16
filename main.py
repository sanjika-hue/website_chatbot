"""
Tastebud — intelligence layer for hashteelab.com

Usage:
    pip install -r requirements.txt
    python main.py
"""

import json
import logging
import secrets
import time
from contextlib import asynccontextmanager
from typing import Optional

import requests as http_requests
import resend
from fastapi import BackgroundTasks, FastAPI, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from chat_route import router as chat_router

from config import (
    DB_PATH, JSONL_PATH, LLM_URL, LLM_MODEL,
    MIN_EVENTS_FOR_REC, REC_COOLDOWN, INSIGHT_COOLDOWN,
    CORS_ORIGINS, HOST, PORT, ADMIN_PASSWORD,
    OCR_WHITELIST_IPS, RESEND_API_KEY, FRONTEND_URL,
)
from db import get_db, init_db
from llm import call_llm, format_session_for_llm, get_llm_config, save_llm_config, test_llm_connection
from models import EventBatch, InsightRequest, ForYouRequest, SignupRequest, LoginRequest, SubscriptionRequest
from auth import hash_password, verify_password, create_token, get_current_user, generate_otp, save_otp, send_otp_email
from prompts import RECOMMENDATION_PROMPT, INSIGHT_PROMPT, FOR_YOU_PROMPT

# In-memory reset tokens store
_reset_tokens: dict[str, str] = {}

# Rate limiting: tracks login attempts per email
_login_attempts: dict[str, dict] = {}
MAX_LOGIN_ATTEMPTS = 5
LOGIN_BLOCK_DURATION = 15 * 60  # 15 minutes

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("tastebud")


# ── background tasks ──────────────────────────────────────────────

def maybe_analyze_session(session_id: str):
    """Check if a session qualifies for LLM analysis, then call it."""
    conn = get_db()

    count = conn.execute(
        "SELECT COUNT(*) FROM events WHERE session_id = ?", (session_id,)
    ).fetchone()[0]

    if count < MIN_EVENTS_FOR_REC:
        conn.close()
        return

    last_rec = conn.execute(
        "SELECT created_at FROM recommendations WHERE session_id = ? "
        "ORDER BY created_at DESC LIMIT 1",
        (session_id,),
    ).fetchone()

    if last_rec and time.time() - last_rec["created_at"] < REC_COOLDOWN:
        conn.close()
        return

    rows = conn.execute(
        "SELECT * FROM events WHERE session_id = ? ORDER BY timestamp",
        (session_id,),
    ).fetchall()
    conn.close()

    events = [dict(r) for r in rows]
    summary = format_session_for_llm(events)

    log.info(f"Analyzing session {session_id[:8]}… ({len(events)} events)")

    content = call_llm(
        RECOMMENDATION_PROMPT,
        f"User session ({len(events)} events):\n{summary}",
        max_tokens=200,
    )

    if not content:
        return

    try:
        rec = json.loads(content)
        message = rec.get("message", content)
        page = rec.get("page")
        cta = rec.get("cta")
    except json.JSONDecodeError:
        message = content
        page = None
        cta = None

    conn = get_db()
    conn.execute(
        "INSERT INTO recommendations (session_id, message, page, cta) VALUES (?, ?, ?, ?)",
        (session_id, message, page, cta),
    )
    conn.commit()
    conn.close()

    log.info(f"Recommendation saved for {session_id[:8]}: {message[:60]}…")


# ── app ───────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Tastebud", lifespan=lifespan)
app.include_router(chat_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ── routes: health ────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "ts": int(time.time())}


# ── routes: events ────────────────────────────────────────────────

@app.post("/events")
def receive_events(batch: EventBatch, background_tasks: BackgroundTasks):
    conn = get_db()
    rows_inserted = 0

    with open(JSONL_PATH, "a") as jf:
        for ev in batch.events:
            conn.execute(
                """
                INSERT INTO events
                    (session_id, event, page, target, data,
                     referrer, locale, device, screen, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ev.session_id, ev.event, ev.page, ev.target,
                    json.dumps(ev.data) if ev.data else None,
                    ev.referrer, ev.locale, ev.device, ev.screen, ev.timestamp,
                ),
            )
            jf.write(ev.model_dump_json() + "\n")
            rows_inserted += 1

    conn.commit()
    conn.close()

    session_ids = {ev.session_id for ev in batch.events}
    for sid in session_ids:
        background_tasks.add_task(maybe_analyze_session, sid)

    return {"ok": True, "inserted": rows_inserted}


@app.get("/events")
def query_events(
    session_id: Optional[str] = Query(None),
    event: Optional[str] = Query(None),
    page: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    conn = get_db()
    clauses: list[str] = []
    params: list[str | int] = []

    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)
    if event:
        clauses.append("event = ?")
        params.append(event)
    if page:
        clauses.append("page LIKE ?")
        params.append(f"%{page}%")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT * FROM events {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    rows = conn.execute(sql, params).fetchall()
    conn.close()

    results = []
    for row in rows:
        d = dict(row)
        if d.get("data"):
            try:
                d["data"] = json.loads(d["data"])
            except json.JSONDecodeError:
                pass
        results.append(d)

    return {"events": results, "count": len(results)}


@app.get("/events/sessions")
def list_sessions(limit: int = Query(50, ge=1, le=500)):
    conn = get_db()
    rows = conn.execute(
        """
        SELECT
            session_id,
            MIN(page) AS first_page,
            MIN(referrer) AS referrer,
            MIN(device) AS device,
            MIN(locale) AS locale,
            COUNT(*) AS event_count,
            MIN(timestamp) AS started_at,
            MAX(timestamp) AS last_seen
        FROM events
        GROUP BY session_id
        ORDER BY last_seen DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    conn.close()
    return {"sessions": [dict(r) for r in rows], "count": len(rows)}


# ── routes: recommendation ────────────────────────────────────────

@app.get("/recommendation")
def get_recommendation(session_id: str = Query(...)):
    conn = get_db()
    row = conn.execute(
        "SELECT message, page, cta, created_at FROM recommendations "
        "WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    conn.close()

    if not row:
        return {"recommendation": None}

    return {
        "recommendation": {
            "message": row["message"],
            "page": row["page"],
            "cta": row["cta"],
            "created_at": row["created_at"],
        }
    }


# ── routes: insight ───────────────────────────────────────────────

@app.post("/insight")
def get_insight(req: InsightRequest):
    conn = get_db()

    # Cooldown (skipped for follow-ups)
    if not req.follow_up_question:
        last = conn.execute(
            "SELECT created_at FROM insights WHERE session_id = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (req.session_id,),
        ).fetchone()

        if last and time.time() - last["created_at"] < INSIGHT_COOLDOWN:
            conn.close()
            return {"error": "cooldown", "retry_after": INSIGHT_COOLDOWN}

    conn.close()

    user_msg = (
        f"Product: {req.product_slug}\n"
        f"Page: {req.page}\n"
        f"Highlighted text: \"{req.highlighted_text[:500]}\""
    )
    if req.follow_up_question:
        user_msg += (
            f"\n\nThe user clicked this follow-up question: \"{req.follow_up_question}\"\n"
            f"Answer it specifically and suggest 2-3 new follow-up questions."
        )

    content = call_llm(INSIGHT_PROMPT, user_msg, max_tokens=250, timeout=15.0)

    if not content:
        return {"error": "llm_failed"}

    try:
        parsed = json.loads(content)
        message = parsed.get("message", content)
        questions = parsed.get("questions", [])
    except json.JSONDecodeError:
        message = content
        questions = []

    conn = get_db()
    conn.execute(
        "INSERT INTO insights (session_id, page, highlighted_text, product_slug, locale, message, questions) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (req.session_id, req.page, req.highlighted_text[:500], req.product_slug,
         req.locale, message, json.dumps(questions)),
    )
    conn.commit()
    conn.close()

    log.info(f"Insight for {req.session_id[:8]} on {req.product_slug}: {message[:60]}…")
    return {"message": message, "questions": questions}


# ── routes: for-you ───────────────────────────────────────────────

@app.post("/for-you")
def get_for_you(req: ForYouRequest):
    conn = get_db()

    event_rows = conn.execute(
        "SELECT * FROM events WHERE session_id = ? ORDER BY timestamp",
        (req.session_id,),
    ).fetchall()

    insight_rows = conn.execute(
        "SELECT highlighted_text, product_slug, message, questions FROM insights "
        "WHERE session_id = ? ORDER BY created_at",
        (req.session_id,),
    ).fetchall()

    conn.close()

    events = [dict(r) for r in event_rows]
    insights = [dict(r) for r in insight_rows]

    # Build concise context (keep under ~1500 tokens)
    summary_parts = []

    # Questionnaire answers
    answers = req.answers or {}
    if any(answers.values()):
        summary_parts.append("USER PROFILE:")
        if answers.get("industry"):
            summary_parts.append(f"- Industry: {answers['industry']}")
        if answers.get("challenge"):
            summary_parts.append(f"- Challenge: {answers['challenge']}")
        if answers.get("scale"):
            summary_parts.append(f"- Scale: {answers['scale']}")
        if answers.get("detail"):
            summary_parts.append(f"- Problem: \"{answers['detail'][:150]}\"")

    # Browsing history — summarized by page
    if events:
        pages_seen = {}
        for ev in events:
            page = ev.get("page", "")
            data = ev.get("data")
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except json.JSONDecodeError:
                    data = None
            time_on = data.get("timeOnPage", 0) if data else 0
            scroll = data.get("scrollDepth", 0) if data else 0
            if page not in pages_seen:
                pages_seen[page] = {"time": 0, "scroll": 0}
            pages_seen[page]["time"] = max(pages_seen[page]["time"], time_on)
            pages_seen[page]["scroll"] = max(pages_seen[page]["scroll"], scroll)

        summary_parts.append(f"\nBROWSING ({len(events)} events, {len(pages_seen)} pages):")
        sorted_pages = sorted(pages_seen.items(), key=lambda x: x[1]["time"], reverse=True)
        for page, info in sorted_pages[:8]:
            parts = [f"- {page}"]
            if info["time"]:
                parts.append(f"time:{info['time']}s")
            if info["scroll"]:
                parts.append(f"scroll:{info['scroll']}%")
            summary_parts.append(" ".join(parts))

    # Interest signals (cap at 5)
    if insights:
        summary_parts.append("\nINTEREST SIGNALS:")
        for ins in insights[-5:]:
            summary_parts.append(f"- {ins['product_slug']}: \"{ins['highlighted_text'][:100]}\"")

    # Questions clicked (cap at 5)
    answer_events = [e for e in events if e.get("event") == "insight_answer"]
    if answer_events:
        summary_parts.append("\nQUESTIONS CLICKED:")
        for ae in answer_events[-5:]:
            data = ae.get("data")
            if isinstance(data, str):
                try:
                    data = json.loads(data)
                except json.JSONDecodeError:
                    data = {}
            if data and data.get("question_text"):
                summary_parts.append(f"- \"{data['question_text'][:80]}\"")

    if not summary_parts:
        return {"sections": [], "empty": True}

    full_summary = "\n".join(summary_parts)
    user_msg = (
        f"Generate personalized content for this user.\n\n{full_summary}\n\n"
        f"Remember: creatively match Hashtee products to their needs even if the use case isn't explicitly listed."
    )

    content = call_llm(FOR_YOU_PROMPT, user_msg, max_tokens=800, timeout=40.0)

    if not content:
        return {"sections": [], "error": "llm_failed"}

    try:
        parsed = json.loads(content)
        sections = parsed.get("sections", [])
    except json.JSONDecodeError:
        sections = [{"type": "summary", "title": "For You", "content": content}]

    log.info(f"For-you page for {req.session_id[:8]}: {len(sections)} sections")
    return {"sections": sections, "generated_at": time.time()}


# ── routes: OCR ───────────────────────────────────────────────────

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp"}
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 MB
OCR_RATE_LIMIT = 5  # max uploads per IP

# In-memory IP rate limiter
_ocr_usage: dict[str, int] = {}


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.post("/ocr")
async def ocr_convert(request: Request, file: UploadFile, page: int = 1):
    # Rate limit by IP (whitelisted IPs skip)
    ip = _get_client_ip(request)
    whitelisted = ip in OCR_WHITELIST_IPS
    used = _ocr_usage.get(ip, 0)
    if not whitelisted and used >= OCR_RATE_LIMIT:
        return JSONResponse(
            {"error": "rate_limit", "message": "Demo limit reached (5 conversions). Contact us for full access.", "remaining": 0},
            status_code=429,
        )

    from pathlib import Path as P
    ext = P(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return JSONResponse(
            {"error": f"Unsupported file type: {ext}. Demo supports PDF and images only.", "supported": list(ALLOWED_EXTENSIONS)},
            status_code=400,
        )

    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        return JSONResponse({"error": "File too large (max 20 MB)"}, status_code=400)

    try:
        from ocr import convert_document, get_pdf_page_count

        pages_total = None
        if ext == ".pdf":
            pages_total = get_pdf_page_count(contents)
            if page < 1 or page > pages_total:
                return JSONResponse(
                    {"error": f"Page {page} out of range (document has {pages_total} pages)", "pages": pages_total},
                    status_code=400,
                )
            markdown = convert_document(contents, file.filename or "upload.pdf", page=page)
        else:
            markdown = convert_document(contents, file.filename or "upload" + ext)

    except Exception as e:
        log.error(f"OCR conversion failed: {e}")
        return JSONResponse({"error": f"Conversion failed: {str(e)}"}, status_code=500)

    # Count usage after success (skip for whitelisted)
    if not whitelisted:
        _ocr_usage[ip] = used + 1
    remaining = -1 if whitelisted else OCR_RATE_LIMIT - used - 1

    result = {"markdown": markdown, "filename": file.filename, "remaining": remaining}
    if pages_total is not None:
        result["pages"] = pages_total
        result["page"] = page
    return result


# ── routes: admin ─────────────────────────────────────────────────

ADMIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Tastebud Admin</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,system-ui,sans-serif;background:#0a0a0a;color:#e0e0e0;display:flex;justify-content:center;padding:40px 16px}
.card{background:#151515;border:1px solid #252525;border-radius:12px;padding:32px;max-width:480px;width:100%}
h1{font-size:18px;font-weight:600;margin-bottom:4px;color:#fff}
.sub{font-size:13px;color:#666;margin-bottom:28px}
label{display:block;font-size:12px;font-weight:500;color:#888;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px;margin-top:20px}
input{width:100%;padding:10px 12px;background:#0a0a0a;border:1px solid #303030;border-radius:8px;color:#fff;font-size:14px;outline:none;transition:border .2s}
input:focus{border-color:#4a9eff}
.row{display:flex;gap:8px;margin-top:24px}
button{flex:1;padding:10px;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;transition:opacity .2s}
button:active{opacity:.8}
.btn-save{background:#4a9eff;color:#fff}
.btn-test{background:#252525;color:#ccc;border:1px solid #303030}
.status{margin-top:16px;padding:10px 12px;border-radius:8px;font-size:13px;display:none}
.status.ok{display:block;background:#0a2a1a;border:1px solid #1a4a2a;color:#4ade80}
.status.err{display:block;background:#2a0a0a;border:1px solid #4a1a1a;color:#f87171}
.status.info{display:block;background:#0a1a2a;border:1px solid #1a2a4a;color:#60a5fa}
.current{margin-top:20px;padding:12px;background:#0a0a0a;border-radius:8px;font-size:12px;color:#666}
.current span{color:#999}
.dot{display:inline-block;width:6px;height:6px;border-radius:50%;margin-right:6px}
.dot.on{background:#4ade80}.dot.off{background:#f87171}.dot.unknown{background:#666}
</style>
</head>
<body>
<div class="card">
  <h1>Tastebud Admin</h1>
  <p class="sub">LLM endpoint configuration</p>

  <div class="current" id="current">Enter password to load config</div>

  <label>Admin Password</label>
  <input type="password" id="pw" placeholder="Enter password">
  <div class="row" style="margin-top:12px;margin-bottom:8px">
    <button class="btn-test" onclick="load()" style="flex:none;padding:10px 20px">Unlock</button>
  </div>

  <div id="fields" style="display:none">

  <label>LLM Endpoint URL</label>
  <input type="url" id="url" placeholder="https://xyz.runpod.ai/v1/chat/completions">

  <label>Model Name</label>
  <input type="text" id="model" placeholder="qwen2.5-3b">

  <label>API Key <span style="color:#555;text-transform:none">(optional)</span></label>
  <input type="password" id="apikey" placeholder="sk-...">

  <div class="row">
    <button class="btn-save" onclick="save()">Save</button>
    <button class="btn-test" onclick="test()">Test Connection</button>
  </div>

  </div>

  <div class="status" id="status"></div>
</div>
<script>
const $=id=>document.getElementById(id);
const st=(msg,cls)=>{const s=$('status');s.textContent=msg;s.className='status '+cls};
const base=location.pathname.replace(/\\/$/,'');

async function load(){
  const pw=$('pw').value;
  if(!pw){st('Password required','err');return}
  try{
    const r=await fetch(base+'/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:pw})});
    const d=await r.json();
    if(d.error){st(d.error,'err');return}
    $('url').value=d.llm_url;
    $('model').value=d.llm_model;
    $('apikey').value=d.llm_api_key==='***'?'':d.llm_api_key||'';
    $('current').innerHTML=`<span class="dot unknown"></span>Current: <span>${d.llm_url}</span> &mdash; <span>${d.llm_model}</span>${d.llm_api_key?' &mdash; key set':''}`;
    $('fields').style.display='block';
  }catch(e){st('Failed to load config','err')}
}

async function save(){
  const pw=$('pw').value;
  if(!pw){st('Password required','err');return}
  try{
    const r=await fetch(base+'/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:pw,llm_url:$('url').value,llm_model:$('model').value,llm_api_key:$('apikey').value})});
    const d=await r.json();
    if(d.ok){st('Saved successfully','ok');load()}else{st(d.error||'Failed','err')}
  }catch(e){st('Request failed: '+e.message,'err')}
}

async function test(){
  st('Testing connection...','info');
  try{
    const r=await fetch(base+'/test');
    const d=await r.json();
    if(d.ok){st('Connected — model responded','ok');$('current').innerHTML=`<span class="dot on"></span>Current: <span>${d.url}</span> &mdash; <span>${d.model}</span>`}
    else{st('Failed: '+d.error,'err');$('current').innerHTML=`<span class="dot off"></span>Current: <span>${d.url}</span> &mdash; <span>${d.model}</span>`}
  }catch(e){st('Request failed: '+e.message,'err')}
}

</script>
</body>
</html>"""


@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    return ADMIN_HTML


@app.post("/admin/config")
async def admin_set_config(request: Request):
    body = await request.json()
    if body.get("password") != ADMIN_PASSWORD:
        return JSONResponse({"ok": False, "error": "Invalid password"}, status_code=403)

    # If no url/model provided, treat as a read (authenticated)
    if not body.get("llm_url") and not body.get("llm_model"):
        url, model, api_key = get_llm_config()
        return {"llm_url": url, "llm_model": model, "llm_api_key": "***" if api_key else ""}

    url = body.get("llm_url", "").strip()
    model = body.get("llm_model", "").strip()
    api_key = body.get("llm_api_key", "").strip()
    if not url or not model:
        return JSONResponse({"ok": False, "error": "URL and model are required"}, status_code=400)
    save_llm_config(url, model, api_key)
    log.info(f"LLM config updated: {url} ({model})")
    return {"ok": True}


@app.get("/admin/test")
def admin_test():
    return test_llm_connection()


# ── routes: auth ──────────────────────────────────────────────────

@app.post("/auth/signup")
async def signup(req: SignupRequest):
    conn = get_db()
    existing = conn.execute("SELECT id FROM users WHERE email = ?", (req.email,)).fetchone()
    if existing:
        conn.close()
        return JSONResponse({"error": "Email already registered"}, status_code=400)
    password_hash = hash_password(req.password)
    cursor = conn.execute(
        "INSERT INTO users (name, email, phone, company, password_hash, is_verified) VALUES (?, ?, ?, ?, ?, 0)",
        (req.name, req.email, req.phone, req.company, password_hash),
    )
    user_id = cursor.lastrowid
    conn.commit()
    conn.close()
    token = create_token(user_id, req.email)
    try:
        code = generate_otp()
        save_otp(user_id, code)
        send_otp_email(req.email, code, req.name)
    except Exception as e:
        log.error(f"Failed to send OTP email: {e}")
    return {"token": token, "user": {"id": user_id, "name": req.name, "email": req.email, "is_verified": False}}


@app.post("/auth/login")
async def login(req: LoginRequest, request: Request):
    ip = _get_client_ip(request)
    now = time.time()
    email_key = req.email.lower().strip()
    attempt_data = _login_attempts.get(email_key, {"count": 0, "blocked_until": 0})
    if attempt_data["blocked_until"] > now:
        remaining_secs = int(attempt_data["blocked_until"] - now)
        remaining_mins = (remaining_secs // 60) + 1
        return JSONResponse({"error": f"Too many failed attempts. Try again in {remaining_mins} minute{'s' if remaining_mins != 1 else ''}.", "blocked": True, "retry_after": remaining_secs}, status_code=429)
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email_key,)).fetchone()
    conn.close()
    if not user:
        return JSONResponse({"error": "No account found with this email. Please sign up first."}, status_code=404)
    if user["password_hash"] == "google-oauth":
        return JSONResponse({"error": "This account uses Google Sign-In. Please log in with Google instead."}, status_code=400)
    if not verify_password(req.password, user["password_hash"]):
        attempt_data["count"] = attempt_data.get("count", 0) + 1
        if attempt_data["count"] >= MAX_LOGIN_ATTEMPTS:
            attempt_data["blocked_until"] = now + LOGIN_BLOCK_DURATION
            attempt_data["count"] = 0
            _login_attempts[email_key] = attempt_data
            return JSONResponse({"error": f"Too many failed attempts. This account is locked for {LOGIN_BLOCK_DURATION // 60} minutes.", "blocked": True, "retry_after": LOGIN_BLOCK_DURATION}, status_code=429)
        _login_attempts[email_key] = attempt_data
        remaining_attempts = MAX_LOGIN_ATTEMPTS - attempt_data["count"]
        return JSONResponse({"error": f"Incorrect password. {remaining_attempts} attempt{'s' if remaining_attempts != 1 else ''} remaining before account lockout.", "remaining_attempts": remaining_attempts}, status_code=401)
    _login_attempts.pop(email_key, None)
    token = create_token(user["id"], user["email"])
    return {"token": token, "user": {"id": user["id"], "name": user["name"], "email": user["email"]}}


@app.post("/auth/google")
async def google_auth(request: Request):
    body = await request.json()
    access_token = body.get("access_token")
    if not access_token:
        return JSONResponse({"error": "No access token"}, status_code=400)
    google_res = http_requests.get(
        "https://www.googleapis.com/oauth2/v3/userinfo",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    if google_res.status_code != 200:
        return JSONResponse({"error": "Invalid Google token"}, status_code=401)
    google_user = google_res.json()
    email = google_user.get("email")
    name = google_user.get("name", email)
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not user:
        cursor = conn.execute(
            "INSERT INTO users (name, email, password_hash, is_verified) VALUES (?, ?, ?, 1)",
            (name, email, "google-oauth"),
        )
        user_id = cursor.lastrowid
        conn.commit()
    else:
        user_id = user["id"]
        name = user["name"]
    conn.close()
    token = create_token(user_id, email)
    return {"token": token, "user": {"id": user_id, "name": name, "email": email, "is_verified": True}}


@app.get("/auth/me")
def get_me(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    return {"user": user}


@app.post("/auth/verify-email")
async def verify_email(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    body = await request.json()
    code = body.get("code", "").strip()
    if not code:
        return JSONResponse({"error": "Verification code required"}, status_code=400)
    conn = get_db()
    otp = conn.execute("SELECT * FROM otp_codes WHERE user_id = ? ORDER BY created_at DESC LIMIT 1", (user["id"],)).fetchone()
    if not otp or otp["code"] != code:
        conn.close()
        return JSONResponse({"error": "Invalid verification code"}, status_code=400)
    if otp["expires_at"] < time.time():
        conn.close()
        return JSONResponse({"error": "Code expired. Please request a new one."}, status_code=400)
    conn.execute("UPDATE users SET is_verified = 1 WHERE id = ?", (user["id"],))
    conn.execute("DELETE FROM otp_codes WHERE user_id = ?", (user["id"],))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/auth/resend-verification")
async def resend_verification(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    conn = get_db()
    conn.execute("DELETE FROM otp_codes WHERE user_id = ?", (user["id"],))
    conn.commit()
    conn.close()
    try:
        code = generate_otp()
        save_otp(user["id"], code)
        send_otp_email(user["email"], code, user["name"])
    except Exception as e:
        log.error(f"Failed to resend OTP: {e}")
        return JSONResponse({"error": "Failed to send email. Please try again."}, status_code=500)
    return {"ok": True}


@app.post("/auth/forgot-password")
async def forgot_password(request: Request):
    body = await request.json()
    email = body.get("email")
    if not email:
        return JSONResponse({"error": "Email required"}, status_code=400)
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    conn.close()
    if not user:
        return {"ok": True}
    token = secrets.token_urlsafe(32)
    _reset_tokens[token] = email
    reset_link = f"{FRONTEND_URL}/en/reset-password?token={token}"
    try:
        resend.api_key = RESEND_API_KEY
        resend.Emails.send({"from": "Hashtee <no-reply@auth.hashteelab.com>", "to": email, "subject": "Reset your Hashtee password", "html": f'<div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:40px 20px"><h2>Reset your password</h2><p>Click below to reset your password. This link expires in 1 hour.</p><a href="{reset_link}" style="display:inline-block;background:#C0533A;color:white;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:600">Reset Password</a></div>'})
    except Exception as e:
        log.error(f"Failed to send reset email: {e}")
        return JSONResponse({"error": "Failed to send email. Please try again."}, status_code=500)
    return {"ok": True}


@app.post("/auth/reset-password")
async def reset_password(request: Request):
    body = await request.json()
    token = body.get("token")
    new_password = body.get("password")
    if not token or not new_password:
        return JSONResponse({"error": "Token and password required"}, status_code=400)
    email = _reset_tokens.get(token)
    if not email:
        return JSONResponse({"error": "Invalid or expired reset link"}, status_code=400)
    password_hash = hash_password(new_password)
    conn = get_db()
    conn.execute("UPDATE users SET password_hash = ? WHERE email = ?", (password_hash, email))
    conn.commit()
    conn.close()
    del _reset_tokens[token]
    return {"ok": True}


@app.post("/subscriptions")
async def create_subscription(req: SubscriptionRequest, request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    conn = get_db()
    cursor = conn.execute(
        "INSERT INTO subscriptions (user_id, product, plan, amount, razorpay_payment_id) VALUES (?, ?, ?, ?, ?)",
        (user["id"], req.product, req.plan, req.amount, req.razorpay_payment_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "subscription_id": cursor.lastrowid}


@app.get("/subscriptions")
def get_subscriptions(request: Request):
    user = get_current_user(request)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    conn = get_db()
    rows = conn.execute("SELECT * FROM subscriptions WHERE user_id = ? ORDER BY created_at DESC", (user["id"],)).fetchall()
    conn.close()
    return {"subscriptions": [dict(r) for r in rows]}


# ── run ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    print(f"Tastebud starting on http://{HOST}:{PORT}")
    print(f"   DB:    {DB_PATH}")
    print(f"   JSONL: {JSONL_PATH}")
    print(f"   LLM:   {LLM_URL} ({LLM_MODEL})")
    uvicorn.run(app, host=HOST, port=PORT)
