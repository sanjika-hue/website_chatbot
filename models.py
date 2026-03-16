from typing import Optional
from pydantic import BaseModel


class Event(BaseModel):
    session_id: str
    event: str
    page: str
    target: Optional[str] = None
    data: Optional[dict] = None
    referrer: Optional[str] = None
    locale: Optional[str] = None
    device: Optional[str] = None
    screen: Optional[str] = None
    timestamp: int


class EventBatch(BaseModel):
    events: list[Event]


class InsightRequest(BaseModel):
    session_id: str
    page: str
    highlighted_text: str
    product_slug: str
    locale: str = "en"
    follow_up_question: Optional[str] = None


class ForYouRequest(BaseModel):
    session_id: str
    answers: Optional[dict] = None  # {industry, challenge, scale, detail}


class SignupRequest(BaseModel):
    name: str
    email: str
    password: str
    phone: Optional[str] = None
    company: Optional[str] = None


class LoginRequest(BaseModel):
    email: str
    password: str


class SubscriptionRequest(BaseModel):
    product: str
    plan: str
    amount: int
    razorpay_payment_id: Optional[str] = None
