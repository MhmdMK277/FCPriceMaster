"""Pydantic models mirroring each DB table."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


Platform = Literal["pc", "console"]
CallType = Literal["buy", "sell", "hold", "watch"]
VerdictType = Literal["correct", "incorrect", "neutral", "expired"]


class Card(BaseModel):
    id: Optional[int] = None
    card_key: str
    player_name: str
    version_name: str
    game_edition: str = "fc26"
    created_at_utc: Optional[datetime] = None


class CardAttribute(BaseModel):
    id: Optional[int] = None
    card_id: int
    key: str
    value: str


class PriceSnapshot(BaseModel):
    id: Optional[int] = None
    card_id: int
    platform: Platform
    game_edition: str = "fc26"
    ts_utc: datetime
    bin_price: Optional[int] = None
    volume_proxy: Optional[int] = None
    source: str = "futgg"


class Signal(BaseModel):
    id: Optional[int] = None
    source: str
    source_id: Optional[str] = None
    ts_utc: datetime
    signal_type: str
    raw_text: Optional[str] = None
    source_server: Optional[str] = None
    original_author: Optional[str] = None
    original_ts_utc: Optional[datetime] = None
    has_attachments: bool = False
    signal_category: Optional[str] = None
    priority: str = "medium"
    signal_context: str = "fut_market"


class SignalCardTag(BaseModel):
    signal_id: int
    card_id: int


class Release(BaseModel):
    id: Optional[int] = None
    name: str
    release_type: str
    expected_date: Optional[str] = None
    confirmed: bool = False
    source_signal_id: Optional[int] = None
    notes: Optional[str] = None
    created_at_utc: Optional[datetime] = None


class Recommendation(BaseModel):
    id: Optional[int] = None
    card_id: int
    platform: Platform
    ts_utc: datetime
    call: CallType
    confidence: float = Field(ge=0.0, le=1.0)
    horizon_hours: Optional[int] = None
    target_price: Optional[int] = None
    reasoning: Optional[str] = None
    source: str = "llm"


class Outcome(BaseModel):
    id: Optional[int] = None
    recommendation_id: int
    evaluated_at_utc: datetime
    price_at_call: Optional[int] = None
    price_now: Optional[int] = None
    verdict: Optional[VerdictType] = None
    notes: Optional[str] = None


class ScraperHealth(BaseModel):
    id: Optional[int] = None
    source: str
    run_at_utc: datetime
    success: bool
    records_written: int = 0
    consecutive_failures: int = 0
    last_error: Optional[str] = None
    schema_diff: Optional[str] = None
