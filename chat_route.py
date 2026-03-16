from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from llm import call_llm_with_history
from prompts import CHAT_PROMPT
from rag import retrieve, retrieve_with_score

router = APIRouter()

# In-memory session history: sessionId -> list of {role, content}
_sessions: dict[str, list] = {}

# Distance threshold — above this means the question is out of scope
OUT_OF_SCOPE_THRESHOLD = 1.0

OUT_OF_SCOPE_REPLY = (
    "That's a bit outside what I can help with right now. "
    "For more details, you can book a meeting with our engineering team at hashteelab.com/contact."
)

GENERAL_KEYWORDS = [
    'hello', 'hi', 'hey', 'good morning', 'good evening', 'good afternoon',
    'good night', 'how are you', "what's up", 'wassup', 'sup', 'bye', 'goodbye',
    'thanks', 'thank you', 'haha', 'lol', 'my name', "i'm ", 'i am ', 'call me',
    'nice to meet', 'how old are you', 'who are you', 'what are you',
]

EMOTIONAL_KEYWORDS = [
    'not feeling well', 'feeling sick', 'im sick', "i'm sick", 'feel sick',
    'not well', 'not okay', 'not ok', "i'm not ok", 'im not ok',
    'tired', 'exhausted', 'burnout', 'burned out', 'stressed', 'overwhelmed',
    'sad', 'unhappy', 'depressed', 'lonely', 'anxious', 'anxiety', 'worried',
    'scared', 'angry', 'frustrated', 'upset', 'hurt', 'crying', 'terrible',
    'awful', 'horrible', 'headache', 'fever', 'unwell', 'ill',
]

FOLLOWUP_KEYWORDS = [
    'tell me more', 'more about', 'explain', 'elaborate', 'how does it work',
    'how does that work', 'what about', 'more details', 'can you explain',
    'give me more', 'anything else', 'what else', 'go on', 'continue',
    'that work', 'more info', 'more information', 'how does it',
    'how do you', 'how is it', 'what does it do', 'tell me about this',
    'tell about this', 'what is this', 'how it works',
]

# Direct product answers — bypasses LLM to avoid hallucination on unfamiliar product names
_OBI = "Obi is our AI sound monitoring product for industrial machines. It uses a microphone to detect abnormal sounds like bearing wear or vibrations, giving you 24 to 72 hours early warning before a breakdown — no sensors needed."
_DODO = "Dodo is our machine vision quality inspection product. It uses a camera to detect defects on production lines in under 50ms, replacing human inspectors and working 24/7."
_BINBIN = "Binbin tracks work-in-progress using your existing CCTV cameras — no RFID or barcodes needed. It assigns a unique ID to each item or person and gives a live dashboard of movement across production stages or retail floors."
_PECHI = "Pechi is our AI chatbot product for enterprises. It goes beyond FAQ answering — it can query databases, process transactions, and trigger workflows. It supports English, Hindi, and Tamil."
_LONGLICHI = "Longlichi is our Document AI and OCR product. It extracts structured data from invoices, dispatch notes, garment tags, and number plates at 500 documents per minute, feeding the data directly into your ERP."

PRODUCT_ANSWERS = {
    "obi": _OBI,
    "dodo": _DODO,
    "binbin": _BINBIN,
    "pechi": _PECHI,
    "longlichi": _LONGLICHI,
    "longlitchi": _LONGLICHI,   # common typo
    "longilitchi": _LONGLICHI,
    "longlichi": _LONGLICHI,
    "longlitchi": _LONGLICHI,
    "pechy": _PECHI,            # common typo
    "pachi": _PECHI,
    "binbin": _BINBIN,
    "bin bin": _BINBIN,
    "dodo": _DODO,
}

# Questions about internal/confidential company info → redirect to contact
CONFIDENTIAL_KEYWORDS = [
    'employee', 'employees', 'how many people', 'team size', 'staff',
    'salary', 'salaries', 'pay', 'revenue', 'funding', 'valuation',
    'investors', 'investment', 'profit', 'loss', 'turnover', 'headcount',
    'how many workers', 'how big is', 'ceo', 'founder', 'cto', 'coo',
    'leadership', 'management team', 'who runs', 'who founded', 'who owns',
    'net worth', 'stock', 'shares', 'annual report',
]


class ChatRequest(BaseModel):
    message: str
    userId: str = "anon"


class QueryRequest(BaseModel):
    prompt: str
    sessionId: str = "default"


def _direct_product_answer(message: str) -> str | None:
    lower = message.lower()
    for product, answer in PRODUCT_ANSWERS.items():
        if product in lower:
            return answer
    return None


def _is_general_or_emotional(message: str) -> bool:
    lower = message.lower().strip()
    if any(k in lower for k in EMOTIONAL_KEYWORDS):
        return True
    if any(k in lower for k in GENERAL_KEYWORDS) and len(lower) < 60:
        return True
    return False


def _is_followup(message: str) -> bool:
    lower = message.lower().strip()
    return any(k in lower for k in FOLLOWUP_KEYWORDS)


async def _handle(message: str, session_id: str) -> str:
    if not message:
        return "Please type a message!"

    history = _sessions.get(session_id, [])

    # 1. Confidential/internal questions → redirect to contact
    lower_msg = message.lower()
    if any(k in lower_msg for k in CONFIDENTIAL_KEYWORDS):
        reply = OUT_OF_SCOPE_REPLY
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": reply})
        _sessions[session_id] = history[-20:]
        return reply

    # 2. Direct product lookup — always accurate, no LLM hallucination
    direct = _direct_product_answer(message)
    if direct:
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": direct})
        _sessions[session_id] = history[-20:]
        return direct

    # 2. Follow-up question — LLM already has context in history, no RAG needed
    if _is_followup(message) and history:
        reply = call_llm_with_history(CHAT_PROMPT, history + [{"role": "user", "content": message}])
        if not reply:
            raise HTTPException(status_code=500, detail="LLM returned empty response")
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": reply})
        _sessions[session_id] = history[-20:]
        return reply

    # 3. General / emotional — send straight to LLM, no RAG needed
    if _is_general_or_emotional(message):
        reply = call_llm_with_history(CHAT_PROMPT, history + [{"role": "user", "content": message}])
        if not reply:
            raise HTTPException(status_code=500, detail="LLM returned empty response")
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": reply})
        _sessions[session_id] = history[-20:]
        return reply

    # 3. RAG — retrieve best matching chunk and check relevance score
    context, distance = retrieve_with_score(message)

    # Out of scope — similarity too low, question not about Hashtee
    if distance > OUT_OF_SCOPE_THRESHOLD:
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": OUT_OF_SCOPE_REPLY})
        _sessions[session_id] = history[-20:]
        return OUT_OF_SCOPE_REPLY

    # In scope — inject context and let LLM answer
    user_content = (
        f"Rephrase the text below as a friendly 2-sentence answer to: '{message}'\n\n"
        f"Text:\n{context}\n\n"
        f"Only use information from the text above."
    )
    reply = call_llm_with_history(CHAT_PROMPT, history + [{"role": "user", "content": user_content}])
    if not reply:
        raise HTTPException(status_code=500, detail="LLM returned empty response")

    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": reply})
    _sessions[session_id] = history[-20:]
    return reply


@router.post("/chat")
async def chat(req: ChatRequest):
    try:
        reply = await _handle(req.message.strip(), req.userId)
        return {"reply": reply}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/query")
async def query(req: QueryRequest):
    """NanoClaw-compatible endpoint — same format as localhost:3001/query"""
    try:
        reply = await _handle(req.prompt.strip(), req.sessionId)
        return {"reply": reply, "sessionId": req.sessionId}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
