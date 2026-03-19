import re
import logging
from fastapi import APIRouter
from fastapi.exceptions import HTTPException
from pydantic import BaseModel

import resend
from llm import call_llm_with_history
from prompts import CHAT_PROMPT
from rag import retrieve_top_k, is_confidential_semantic
from config import RESEND_API_KEY, BOOKING_EMAIL

log = logging.getLogger("tastebud")

router = APIRouter()

resend.api_key = RESEND_API_KEY

# ── Session state ─────────────────────────────────────────────────────────────
_sessions: dict[str, dict] = {}      # session_id → {history, prev_replies, angle_idx, last_topic}
_booking_states: dict[str, dict] = {}  # session_id → booking flow state

# ── Constants ─────────────────────────────────────────────────────────────────
OUT_OF_SCOPE_THRESHOLD = 0.85   # ChromaDB distance — higher = less relevant

CLARIFICATION_TRIGGERS = [
    "explain", "clarify", "elaborate", "understand", "clear", "mean",
    "repeat", "again", "simpler", "simple", "confus", "dont get", "don't get",
    "what do you", "how so", "like what", "in detail",
]

BOOKING_KEYWORDS = [
    "book a demo", "book demo", "book a meeting", "book meeting",
    "schedule a demo", "schedule demo", "schedule a meeting", "schedule meeting",
    "request a demo", "request demo", "want a demo", "want to book",
    "arrange a demo", "set up a meeting", "book a call", "schedule a call",
    "get a demo", "book a slot",
]

CONFIDENTIAL_KEYWORDS = [
    "employee", "employees", "how many people", "team size", "staff",
    "salary", "salaries", "funding", "valuation", "revenue", "investors",
    "profit", "loss", "turnover", "headcount", "how many workers",
    "how big is", "founder", "leadership", "management team",
    "who runs", "who founded", "who owns", "net worth", "stock", "shares",
    "annual report",
]

PROBLEM_KEYWORDS = [
    "quality", "defect", "reject", "inspection", "machine", "factory",
    "production", "breakdown", "failure", "monitor", "track", "document",
    "invoice", "chatbot", "automat", "manufactur", "retail", "detect",
    "vision", "sound", "noise", "vibrat",
]

COMPANY_CONTEXT_KEYWORDS = [
    "what", "tell", "dodo", "binbin", "hashtee", "solution", "price",
    "cost", "service", "tool", "model", "i don", "explain",
]

SPECIFIC_QUALIFIERS = [
    "low light", "dark", "night", "temperature", "humidity", "outdoor", "indoor",
    "underwater", "dust", "waterproof", "explosion", "hazardous", "atex",
    "resolution", "frame rate", "range", "accuracy rate", "speed limit",
    "minimum", "maximum", "how many", "how fast", "how accurate",
]

PREAMBLES = [
    "here is the reply:", "here is my reply:", "here's the reply:",
    "here's a possible reply:", "here's a response:", "here's my response:",
    "here is a possible reply:", "sure, here", "of course,", "reply:", "response:",
]

ANGLES = [
    "Give a clear, direct answer.",
    "Explain HOW it technically works in simple terms.",
    "Focus on the business benefit — time saved, cost reduced, problem eliminated.",
    "Use a real client deployment as your example (Maruti, Aditya Birla, Mubea, Pantaloons).",
    "Compare to the old manual/traditional way of doing it.",
]

CANCEL_WORDS = ["cancel", "stop", "never mind", "nevermind", "forget it", "exit", "quit"]

TOPIC_SIGNALS = ["what ", "who ", "where ", "when ", "why ", "how ", "does ", "can ",
                 "are ", "which ", "tell me about "]

# ── Helpers ───────────────────────────────────────────────────────────────────

def _trim_to_sentences(text: str, n: int) -> str:
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return " ".join(sentences[:n])


def _edit_distance(a: str, b: str) -> int:
    if len(a) > len(b):
        a, b = b, a
    row = list(range(len(a) + 1))
    for j, cb in enumerate(b, 1):
        new_row = [j]
        for i, ca in enumerate(a, 1):
            new_row.append(min(row[i] + 1, new_row[i - 1] + 1, row[i - 1] + (ca != cb)))
        row = new_row
    return row[-1]


def _is_conversational(msg: str) -> bool:
    """True for short social/follow-up messages that don't need RAG — let LLM handle with history."""
    lower = msg.lower().strip()
    words = lower.split()
    if len(words) <= 4:
        return True
    if any(t in lower for t in CLARIFICATION_TRIGGERS):
        return True
    return False


def _send_booking_email(name: str, email: str, interest: str):
    try:
        resend.Emails.send({
            "from": "Hashtee Chat <no-reply@auth.hashteelab.com>",
            "to": [BOOKING_EMAIL],
            "subject": f"Demo Request from {name}",
            "html": (
                "<div style='font-family:sans-serif;max-width:480px;margin:0 auto;padding:40px 20px'>"
                "<h2>New Demo Request via Chat</h2>"
                f"<p><strong>Name:</strong> {name}</p>"
                f"<p><strong>Email:</strong> {email}</p>"
                f"<p><strong>Interest:</strong> {interest}</p>"
                "<p style='margin-top:24px;color:#666;font-size:13px'>Sent via Hashtee website chatbot</p>"
                "</div>"
            ),
        })
    except Exception as e:
        log.warning(f"Booking email failed: {e}")


def _handle_booking(msg: str, session_id: str) -> str | None:
    """Handle multi-step booking flow. Returns response string or None if not in booking flow."""
    lower = msg.lower().strip()

    if any(c in lower for c in CANCEL_WORDS):
        _booking_states.pop(session_id, None)
        return "No problem! Feel free to ask me anything else about Hashtee."

    state = _booking_states.get(session_id)

    if state is None:
        if any(k in lower for k in BOOKING_KEYWORDS):
            _booking_states[session_id] = {"state": "awaiting_name"}
            return "I'd love to help set that up! What's your name?"
        return None

    current_state = state.get("state")

    if current_state == "awaiting_name":
        state["name"] = msg.strip()
        state["state"] = "awaiting_email"
        return f"Nice to meet you, {state['name']}! What's your email address?"

    if current_state == "awaiting_email":
        if not re.match(r"[^@\s]+@[^@\s]+\.[^@\s]+", msg.strip()):
            return "That doesn't look like a valid email — could you double-check it?"
        state["email"] = msg.strip()
        state["state"] = "awaiting_interest"
        return "Got it! What would you like to discuss — which product or problem are you most interested in?"

    if current_state == "awaiting_interest":
        state["interest"] = msg.strip()
        name = state.get("name", "")
        email = state.get("email", "")
        interest = state.get("interest", "")
        _send_booking_email(name, email, interest)
        _booking_states.pop(session_id, None)
        try:
            return (
                f"All done! We've sent your request to the Hashtee team. They'll reach out to "
                f"{email} shortly to schedule the demo."
            )
        except Exception:
            return (
                f"Thanks, {name}! Your request has been noted. The team will reach out to "
                f"{email} soon. You can also visit hashteelab.com/contact directly."
            )

    _booking_states.pop(session_id, None)
    return "Something went wrong. Let's start over — feel free to ask me anything!"


def _is_confidential(msg: str) -> bool:
    """3-layer confidential detection: exact word-boundary + fuzzy + semantic."""
    lower = msg.lower()

    # Layer 1: exact word-boundary match
    for kw in CONFIDENTIAL_KEYWORDS:
        if re.search(r"\b" + re.escape(kw) + r"\b", lower):
            return True

    # Layer 2: fuzzy edit-distance match (catches typos like "salaary")
    msg_words = [re.sub(r"[^a-z]", "", w) for w in lower.split()]
    for word in msg_words:
        if len(word) < 5:
            continue
        for kw in CONFIDENTIAL_KEYWORDS:
            kw_clean = re.sub(r"[^a-z]", "", kw)
            if len(kw_clean) < 5:
                continue
            if _edit_distance(word, kw_clean) <= 1:
                return True

    # Layer 3: semantic similarity
    if is_confidential_semantic(msg):
        return True

    return False


def _is_same_topic(prev_replies: list[str], new_msg: str) -> bool:
    """Check if the new message is on the same topic as recent replies."""
    if not prev_replies:
        return False
    lower = new_msg.lower()
    topic_words = ["pechi", "dodo", "obi", "binbin", "longlichi",
                   "defect", "sound", "track", "document", "chatbot"]
    for word in topic_words:
        found_in_new = word in lower
        found_in_prev = any(word in r.lower() for r in prev_replies[-2:])
        if found_in_new and found_in_prev:
            return True
    # Check topic signal words
    for sig in TOPIC_SIGNALS:
        if lower.startswith(sig):
            return False
    return True


def _llm_with_history(msg: str, history: list[dict], system: str, max_tokens: int = 130) -> str:
    """Send message to LLM with conversation history. Returns trimmed reply."""
    messages = [{"role": "system", "content": system}]
    for h in history[-8:]:
        messages.append(h)
    messages.append({"role": "user", "content": msg})
    raw = call_llm_with_history(messages, max_tokens=max_tokens)
    if not raw:
        return "I'm having a moment — try that again!"
    lower_raw = raw.lower().strip()
    for p in PREAMBLES:
        if lower_raw.startswith(p):
            raw = raw[len(p):].strip()
            break
    return _trim_to_sentences(raw, 2)


def _handle(msg: str, session_id: str) -> str:
    """Main chat logic."""
    if not msg.strip():
        return "Please type a message!"

    lower_msg = msg.lower().strip()

    # Booking flow (state machine — keep)
    booking_resp = _handle_booking(msg, session_id)
    if booking_resp is not None:
        return booking_resp

    # Session state
    sess = _sessions.setdefault(session_id, {
        "history": [],
        "prev_replies": [],
        "angle_idx": 0,
        "last_topic": "",
        "more_count": 0,
    })

    history: list[dict] = sess["history"]
    prev_replies: list[str] = sess["prev_replies"]

    def _save_and_return(reply: str) -> str:
        history.append({"role": "user", "content": msg})
        history.append({"role": "assistant", "content": reply})
        if len(history) > 20:
            sess["history"] = history[-20:]
        return reply

    # Confidential detection (security guard — keep)
    if _is_confidential(lower_msg):
        return _save_and_return(
            "That's a bit outside what I can help with right now. "
            "For more details, you can book a meeting with our engineering team at hashteelab.com/contact."
        )

    # Conversational / clarification — skip RAG, let LLM respond using history
    if _is_conversational(lower_msg):
        reply = _llm_with_history(msg, history, CHAT_PROMPT)
        return _save_and_return(reply)

    # "More" / stop condition
    is_more = lower_msg.strip() in ["more", "tell me more", "continue", "go on", "and?"]
    if is_more:
        sess["more_count"] = sess.get("more_count", 0) + 1
        if sess["more_count"] >= 3:
            sess["more_count"] = 0
            return _save_and_return(
                "I think I've covered the key points on this — is there a specific part of your setup or industry I can help you apply this to?"
            )
    else:
        sess["more_count"] = 0

    # RAG retrieval
    force_answer = any(k in lower_msg for k in PROBLEM_KEYWORDS)
    chunks = retrieve_top_k(lower_msg, top_k=3)
    relevant = [c for c in chunks if c["distance"] < OUT_OF_SCOPE_THRESHOLD or force_answer]

    # No relevant RAG chunks — check if it's a known Hashtee-adjacent topic (jobs, greetings)
    # otherwise hard redirect to keep bot on-scope
    if not relevant:
        lower_no_rag = lower_msg
        is_job = any(k in lower_no_rag for k in [
            "join", "career", "hiring", "vacancy", "vacancies", "job", "work for", "work at", "work with"
        ])
        is_greeting = len(lower_no_rag.split()) <= 3
        if is_job or is_greeting:
            reply = _llm_with_history(msg, history, CHAT_PROMPT)
        else:
            reply = (
                "I can only help with questions about Hashtee and our products. "
                "Is there something specific about what we do that I can help with?"
            )
        prev_replies.append(reply)
        if len(prev_replies) > 5:
            sess["prev_replies"] = prev_replies[-5:]
        return _save_and_return(reply)

    context = "\n\n---\n\n".join(c["text"] for c in relevant)

    # Hallucination guard
    hard_rule = ""
    for qualifier in SPECIFIC_QUALIFIERS:
        if qualifier in lower_msg:
            hard_rule = (
                f"\n\n[HARD RULE] The question asks about: {qualifier}. "
                "This specific detail is NOT in the knowledge above. "
                "You MUST respond: \"I'm not sure about that specific detail — "
                "you can check hashteelab.com or reach out at hashteelab.com/contact.\""
            )
            break

    # Angle rotation — reset on topic change
    if not _is_same_topic(prev_replies, msg):
        sess["angle_idx"] = 0
        sess["last_topic"] = lower_msg[:50]

    angle_idx = sess["angle_idx"] % len(ANGLES)
    angle_instruction = ANGLES[angle_idx]
    sess["angle_idx"] = angle_idx + 1

    # Follow-up question (every other reply)
    ask_followup = (angle_idx % 2 == 0) and not is_more
    followup_instruction = (
        "\nAfter your 2-sentence answer, add ONE short follow-up question (under 10 words) relevant to the user's industry or use case."
        if ask_followup else ""
    )

    # Anti-repetition
    anti_rep = ""
    if prev_replies:
        anti_rep = (
            "\n\n[ANTI-REPETITION] You already gave these answers:\n"
            + "\n".join(f'- "{r}"' for r in prev_replies[-3:])
            + "\nDo NOT reuse any of those sentences. This time: " + angle_instruction
        )

    system = (
        CHAT_PROMPT
        + "\n\n[Examples of good replies]\n"
        "Q: What does Hashtee do?\n"
        "A: We build AI that solves real factory problems — catching defects, predicting breakdowns, or tracking items through production. Tell me your industry and I'll point you to the right fit.\n\n"
        "Q: What is Dodo?\n"
        "A: Dodo puts a camera on your line and flags every defect automatically — no human inspector needed. At Mubea, it now guarantees zero defective springs leave the factory.\n\n"
        "Q: What models or technology do you use?\n"
        "A: For visual inspection we use computer vision AI, for machine health we use audio anomaly detection, and for documents we use OCR models. Each one learns your specific environment.\n\n"
        f"\n\nReply in max 2 sentences. Conversational, no lists, no bullet points. Only use facts from the knowledge above."
    )

    user_turn = (
        f"[Knowledge about Hashtee]\n{context}{hard_rule}{anti_rep}\n\n[Question]\n{msg}"
        + f"\n\nReply in EXACTLY 2 short sentences. No lists. Pick the single most relevant point and say it conversationally."
        + f" If the question has multiple parts, answer each part briefly in your 2 sentences.{followup_instruction}"
    )

    messages = [{"role": "system", "content": system}]
    for h in history[-6:]:
        messages.append(h)
    messages.append({"role": "user", "content": user_turn})

    max_s = 3 if ask_followup else 2
    raw = call_llm_with_history(messages, max_tokens=180)

    if not raw:
        return "LLM returned empty response"

    lower_raw = raw.lower().strip()
    for p in PREAMBLES:
        if lower_raw.startswith(p):
            raw = raw[len(p):].strip()
            break

    reply = _trim_to_sentences(raw, max_s)

    prev_replies.append(reply)
    if len(prev_replies) > 5:
        sess["prev_replies"] = prev_replies[-5:]

    return _save_and_return(reply)


# ── Request models ─────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: str = "anon"
    userId: str = "anon"


class QueryRequest(BaseModel):
    prompt: str = ""
    sessionId: str = "default"
    text: str = ""
    max_sentences: int = 2


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.post("/chat")
def chat(req: ChatRequest):
    try:
        reply = _handle(req.message, req.session_id)
        return {"reply": reply}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Chat error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/query")
def query(req: QueryRequest):
    """NanoClaw-compatible endpoint — same format as localhost:3001/query"""
    session_id = req.sessionId or "default"
    message = req.prompt or req.text or ""
    try:
        reply = _handle(message, session_id)
        return {"reply": reply}
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Query error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
