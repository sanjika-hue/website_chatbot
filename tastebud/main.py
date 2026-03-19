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

import auth
import config
import db
import llm
import models
import prompts
from chat_route import router as chat_router

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("tastebud")

# ── Login rate-limiting ────────────────────────────────────────────────────────
MAX_LOGIN_ATTEMPTS = 5
LOGIN_BLOCK_DURATION = 15  # minutes
_login_attempts: dict[str, dict] = {}
_reset_tokens: dict[str, str] = {}

# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(_app):
    db.init_db()
    yield


app = FastAPI(title="Tastebud", lifespan=lifespan)
app.include_router(chat_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

resend.api_key = config.RESEND_API_KEY


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"ok": True}


# ── Session analysis ──────────────────────────────────────────────────────────

async def maybe_analyze_session(session_id: str):
    """Check if a session qualifies for LLM analysis, then call it."""
    with db.get_db() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM events WHERE session_id = ?", (session_id,)
        ).fetchone()[0]
        if count < config.MIN_EVENTS_FOR_REC:
            return

        last_rec = conn.execute(
            "SELECT created_at FROM recommendations WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if last_rec and (time.time() - last_rec["created_at"]) < config.REC_COOLDOWN:
            return

        rows = conn.execute(
            "SELECT * FROM events WHERE session_id = ? ORDER BY timestamp", (session_id,)
        ).fetchall()
        events = [dict(r) for r in rows]

    log.info(f"Analyzing session {session_id}… ({len(events)} events)")
    summary = llm.format_session_for_llm(events)
    log.info(f"User session ({len(events)} events):\n{summary}")

    result = llm.call_llm(prompts.RECOMMENDATION_PROMPT, summary, max_tokens=200)
    if not result:
        return
    try:
        data = json.loads(result)
        message = data.get("message", "")
        page = data.get("page", "")
        cta = data.get("cta", "")
        with db.get_db() as conn:
            conn.execute(
                "INSERT INTO recommendations (session_id, message, page, cta) VALUES (?, ?, ?, ?)",
                (session_id, message, page, cta),
            )
            conn.commit()
        log.info(f"Recommendation saved for {session_id}: {message}")
    except Exception as e:
        log.warning(f"Recommendation parse error: {e}")


# ── Events ────────────────────────────────────────────────────────────────────

@app.post("/events")
async def receive_events(batch: models.EventBatch, background_tasks: BackgroundTasks):
    with db.get_db() as conn:
        for e in batch.events:
            conn.execute(
                """
                INSERT INTO events
                    (session_id, event, page, target, data,
                     referrer, locale, device, screen, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    e.session_id, e.event, e.page, e.target,
                    json.dumps(e.data) if e.data else None,
                    e.referrer, e.locale, e.device, e.screen, e.timestamp,
                ),
            )
        conn.commit()

    if batch.events:
        background_tasks.add_task(maybe_analyze_session, batch.events[-1].session_id)

    return {"ok": True, "count": len(batch.events)}


@app.get("/events")
def query_events(
    session_id: Optional[str] = None,
    event: Optional[str] = None,
    page: Optional[str] = None,
    limit: int = Query(100, le=1000),
    offset: int = 0,
):
    conditions = []
    params: list = []
    if session_id:
        conditions.append("session_id = ?")
        params.append(session_id)
    if event:
        conditions.append("event = ?")
        params.append(event)
    if page:
        conditions.append("page LIKE ?")
        params.append(f"%{page}%")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    sql = f"SELECT * FROM events {where} ORDER BY timestamp DESC LIMIT ? OFFSET ?"
    params += [limit, offset]

    with db.get_db() as conn:
        rows = conn.execute(sql, params).fetchall()
    return {"events": [dict(r) for r in rows]}


@app.get("/events/sessions")
def list_sessions(limit: int = Query(50, le=500)):
    with db.get_db() as conn:
        rows = conn.execute("""
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
        """, (limit,)).fetchall()
    return {"sessions": [dict(r) for r in rows]}


# ── Recommendation ────────────────────────────────────────────────────────────

@app.get("/recommendation")
def get_recommendation(session_id: str):
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT message, page, cta, created_at FROM recommendations WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
    if not row:
        return {"recommendation": None}
    return {"recommendation": dict(row)}


# ── Insight ───────────────────────────────────────────────────────────────────

@app.post("/insight")
async def get_insight(req: models.InsightRequest, request: Request, background_tasks: BackgroundTasks):
    session_id = request.headers.get("x-session-id", "unknown")

    with db.get_db() as conn:
        last = conn.execute(
            "SELECT created_at FROM insights WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if last and (time.time() - last["created_at"]) < config.INSIGHT_COOLDOWN:
            return {"cooldown": True}

    product_ctx = f"Product: {req.product_slug}\nPage: {req.highlighted_text}"
    user_prompt = f'\n\nThe user clicked this follow-up question: "{req.follow_up_question}"\nAnswer it specifically and suggest 2-3 new follow-up questions.' if req.follow_up_question else ""

    result = llm.call_llm(
        prompts.INSIGHT_PROMPT,
        product_ctx + user_prompt,
        max_tokens=250,
    )
    if not result:
        return {"error": "llm_failed"}

    try:
        data = json.loads(result)
        message = data.get("message", "")
        questions = json.dumps(data.get("questions", []))
        with db.get_db() as conn:
            conn.execute(
                "INSERT INTO insights (session_id, page, highlighted_text, product_slug, locale, message, questions) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (session_id, req.product_slug, req.highlighted_text, req.product_slug, req.locale, message, questions),
            )
            conn.commit()
        log.info(f"Insight for {session_id} on {req.product_slug}")
        return data
    except Exception as e:
        log.warning(f"Insight parse error: {e}")
        return {"error": "llm_failed"}


# ── For You ───────────────────────────────────────────────────────────────────

@app.post("/for-you")
async def get_for_you(req: models.ForYouRequest, request: Request):
    session_id = request.headers.get("x-session-id", "unknown")

    with db.get_db() as conn:
        insight_rows = conn.execute(
            "SELECT highlighted_text, product_slug, message, questions FROM insights WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        ).fetchall()
        event_rows = conn.execute(
            "SELECT * FROM events WHERE session_id = ? ORDER BY timestamp", (session_id,)
        ).fetchall()

    answers = req.answers or {}
    parts = ["USER PROFILE:"]
    if answers.get("industry"):
        parts.append(f"- Industry: {answers['industry']}")
    if answers.get("challenge"):
        parts.append(f"- Challenge: {answers['challenge']}")
    if answers.get("scale"):
        parts.append(f"- Scale: {answers['scale']}")
    if answers.get("detail"):
        parts.append(f'- Problem: "{answers["detail"]}"')

    events = [dict(r) for r in event_rows]
    page_times: dict[str, int] = {}
    page_scrolls: dict[str, int] = {}
    interest_signals: list[str] = []
    questions_clicked: list[str] = []

    for e in events:
        page = e.get("page", "")
        data = e.get("data") or {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                data = {}
        t = data.get("timeOnPage", 0)
        s = data.get("scrollDepth", 0)
        if t:
            page_times[page] = page_times.get(page, 0) + int(t)
        if s:
            page_scrolls[page] = max(page_scrolls.get(page, 0), int(s))
        if e.get("event") == "text" and data.get("text"):
            interest_signals.append(f'{e.get("product_slug", page)}: "{data["text"]}"')
        if e.get("event") == "insight_answer" and data.get("question_text"):
            questions_clicked.append(f'- "{data["question_text"]}"')

    sorted_pages = sorted(page_times.items(), key=lambda x: -x[1])
    browsing_parts = [f"\nBROWSING ({len(events)} events, {len(page_times)} pages):"]
    for pg, t in sorted_pages[:5]:
        s = page_scrolls.get(pg, 0)
        browsing_parts.append(f"- {pg} time:{t} scroll:{s}")

    if interest_signals:
        browsing_parts.append("\nINTEREST SIGNALS:")
        for sig in interest_signals[-5:]:
            browsing_parts.append(f"- {sig}")

    if questions_clicked:
        browsing_parts.append("\nQUESTIONS CLICKED:")
        browsing_parts.extend(questions_clicked[-5:])

    full_prompt = (
        "Generate personalized content for this user.\n\n"
        + "\n".join(parts)
        + "\n".join(browsing_parts)
        + "\n\nRemember: creatively match Hashtee products to their needs even if the use case isn't explicitly listed."
    )

    result = llm.call_llm(prompts.FOR_YOU_PROMPT, full_prompt, max_tokens=800)
    if not result:
        return {"sections": [], "summary": "For You"}

    try:
        data = json.loads(result)
        log.info(f"For-you page for {session_id} — {len(data.get('sections', []))} sections")
        return data
    except Exception as e:
        log.warning(f"For-you parse error: {e}")
        return {"sections": [], "summary": "For You"}


# ── OCR ───────────────────────────────────────────────────────────────────────

ALLOWED_EXTENSIONS = {".pdf", "image"}
MAX_FILE_SIZE = 20971520  # 20 MB
OCR_RATE_LIMIT = 5
_ocr_usage: dict[str, int] = {}


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.post("/ocr")
async def ocr_convert(request: Request, file: UploadFile, page: Optional[int] = None):
    ip = _get_client_ip(request)
    if ip not in config.OCR_WHITELIST_IPS:
        usage = _ocr_usage.get(ip, 0)
        if usage >= OCR_RATE_LIMIT:
            raise Exception("Demo limit reached (5 conversions). Contact us for full access.")
        _ocr_usage[ip] = usage + 1

    content = await file.read()
    filename = file.filename or "upload"
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext not in {".pdf"} and not (file.content_type or "").startswith("image/"):
        return JSONResponse(status_code=400, content={"error": f"Unsupported file type: {ext}. Demo supports PDF and images only."})
    if len(content) > MAX_FILE_SIZE:
        return JSONResponse(status_code=400, content={"error": "File too large (max 20 MB)"})

    return JSONResponse(status_code=200, content={"ok": True, "message": "OCR processing not available in this deployment."})


# ── Admin ──────────────────────────────────────────────────────────────────────

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
</style>
</head>
<body>
<div class="card">
  <h1>Tastebud Admin</h1>
  <p class="sub">LLM endpoint configuration</p>
  <div id="current">Enter password to load config</div>
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
    $('url').value=d.llm_url;$('model').value=d.llm_model;$('apikey').value=d.llm_api_key==='***'?'':d.llm_api_key||'';
    $('current').innerHTML=`Current: ${d.llm_url} — ${d.llm_model}`;
    $('fields').style.display='block';
  }catch(e){st('Failed to load config','err')}
}
async function save(){
  const pw=$('pw').value;if(!pw){st('Password required','err');return}
  try{
    const r=await fetch(base+'/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:pw,llm_url:$('url').value,llm_model:$('model').value,llm_api_key:$('apikey').value})});
    const d=await r.json();
    if(d.ok){st('Saved successfully','ok');load()}else{st(d.error||'Failed','err')}
  }catch(e){st('Request failed: '+e.message,'err')}
}
async function test(){
  st('Testing connection...','info');
  try{
    const r=await fetch(base+'/test');const d=await r.json();
    if(d.ok){st('Connected — model responded','ok')}else{st('Failed: '+d.error,'err')}
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
    if body.get("password") != config.ADMIN_PASSWORD:
        return JSONResponse(status_code=403, content={"error": "Invalid password"})
    llm_url = body.get("llm_url")
    llm_model = body.get("llm_model")
    llm_api_key = body.get("llm_api_key", "")
    if not llm_url and not llm_model:
        # Read-only request
        url, model, api_key = llm.get_llm_config()
        return {"llm_url": url, "llm_model": model, "llm_api_key": "***" if api_key else ""}
    if not llm_url or not llm_model:
        return JSONResponse(status_code=400, content={"error": "URL and model are required"})
    llm.save_llm_config(llm_url, llm_model, llm_api_key)
    log.info(f"LLM config updated: {llm_url} ({llm_model})")
    return {"ok": True}


@app.get("/admin/test")
def admin_test():
    return llm.test_llm_connection()


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.post("/auth/signup")
async def signup(req: models.SignupRequest):
    result = auth.signup(req.name, req.email, req.password, req.phone or "", req.company or "")
    if "error" in result:
        return JSONResponse(status_code=400, content=result)
    return result


@app.post("/auth/login")
async def login(req: models.LoginRequest, request: Request):
    ip = _get_client_ip(request)
    attempt_info = _login_attempts.get(ip, {"count": 0, "blocked_until": 0})

    if attempt_info.get("blocked_until", 0) > time.time():
        remaining = int((attempt_info["blocked_until"] - time.time()) / 60) + 1
        return JSONResponse(status_code=429, content={
            "error": f"Too many failed attempts. Try again in {remaining} minute{'s' if remaining != 1 else ''}."
        })

    result = auth.login(req.email, req.password)
    if "error" in result:
        attempt_info["count"] = attempt_info.get("count", 0) + 1
        if attempt_info["count"] >= MAX_LOGIN_ATTEMPTS:
            attempt_info["blocked_until"] = time.time() + LOGIN_BLOCK_DURATION * 60
            attempt_info["count"] = 0
        _login_attempts[ip] = attempt_info
        status = result.pop("status_code", 401)
        return JSONResponse(status_code=status, content=result)

    _login_attempts.pop(ip, None)
    return result


@app.get("/auth/me")
async def get_me(request: Request):
    authorization = request.headers.get("Authorization", "")
    user = auth.get_current_user(authorization)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    return {"user": user}


@app.post("/auth/verify-email")
async def verify_email(request: Request):
    body = await request.json()
    authorization = request.headers.get("Authorization", "")
    user = auth.get_current_user(authorization)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    code = body.get("code")
    if not code:
        return JSONResponse(status_code=400, content={"error": "Verification code required"})
    result = auth.verify_email(user["id"], code)
    if "error" in result:
        return JSONResponse(status_code=400, content=result)
    return result


@app.post("/auth/resend-verification")
async def resend_verification(request: Request):
    authorization = request.headers.get("Authorization", "")
    user = auth.get_current_user(authorization)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    result = auth.resend_verification(user["id"])
    if "error" in result:
        return JSONResponse(status_code=500, content={"error": "Failed to send email. Please try again."})
    return result


@app.post("/auth/forgot-password")
async def forgot_password(request: Request):
    body = await request.json()
    email = body.get("email", "").strip()
    if not email:
        return JSONResponse(status_code=400, content={"error": "Email required"})
    with db.get_db() as conn:
        row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if not row:
        return {"ok": True}  # Don't reveal if email exists
    token = secrets.token_hex(32)
    _reset_tokens[token] = email
    reset_url = f"{config.FRONTEND_URL}/en/reset-password?token={token}"
    try:
        resend.Emails.send({
            "from": "Hashtee <no-reply@auth.hashteelab.com>",
            "to": [email],
            "subject": "Reset your Hashtee password",
            "html": (
                "<div style='font-family:sans-serif;max-width:480px;margin:0 auto;padding:40px 20px'>"
                "<h2>Reset your password</h2>"
                "<p>Click below to reset your password. This link expires in 1 hour.</p>"
                f"<a href='{reset_url}' style='display:inline-block;background:#C0533A;color:white;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:600'>Reset Password</a>"
                "</div>"
            ),
        })
    except Exception as e:
        log.warning(f"Failed to send reset email: {e}")
    return {"ok": True}


@app.post("/auth/reset-password")
async def reset_password(request: Request):
    body = await request.json()
    token = body.get("token", "")
    password = body.get("password", "")
    if not token or not password:
        return JSONResponse(status_code=400, content={"error": "Token and password required"})
    email = _reset_tokens.pop(token, None)
    if not email:
        return JSONResponse(status_code=400, content={"error": "Invalid or expired reset link"})
    pw_hash = auth.hash_password(password)
    with db.get_db() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE email = ?", (pw_hash, email))
        conn.commit()
    return {"ok": True}


# ── Subscriptions ──────────────────────────────────────────────────────────────

@app.post("/subscriptions")
async def create_subscription(req: models.SubscriptionRequest, request: Request):
    authorization = request.headers.get("Authorization", "")
    user = auth.get_current_user(authorization)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO subscriptions (user_id, product, plan, amount, razorpay_payment_id) VALUES (?, ?, ?, ?, ?)",
            (user["id"], req.product, req.plan, req.amount, req.razorpay_payment_id),
        )
        conn.commit()
    return {"ok": True}


@app.get("/subscriptions")
async def get_subscriptions(request: Request):
    authorization = request.headers.get("Authorization", "")
    user = auth.get_current_user(authorization)
    if not user:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM subscriptions WHERE user_id = ? ORDER BY created_at DESC",
            (user["id"],),
        ).fetchall()
    return {"subscriptions": [dict(r) for r in rows]}


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    print(f"🫐 Tastebud starting on http://{config.HOST}:{config.PORT}")
    print(f"   DB:    {config.DB_PATH}")
    print(f"   JSONL: {config.JSONL_PATH}")
    print(f"   LLM:   {config.LLM_URL}")
    uvicorn.run("main:app", host=config.HOST, port=config.PORT, reload=False)
