from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict


class TechniqueStatsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    technique_id: str

    # Legacy (kept)
    total_exp: int
    best_score: int
    attempts: int

    # New
    total_points: int
    best_session_points: int
    sessions_played: int

    total_items: int
    total_attempts: int

    correct_first: int
    correct_second: int
    correct_after_cue: int
    incorrect: int


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    clerk_user_id: str
    email: Optional[str] = None

    total_exp: int
    best_daily_score: int
    streak: int
    last_active_date: Optional[str] = None

    today_points: Optional[int] = None
    today_sessions: Optional[int] = None


# ---------------------------
# Personalisation schemas
# ---------------------------

class UserProfileIn(BaseModel):
    interests_text: str = ""
    avoid_topics_text: Optional[str] = None

    # 1=easy 2=medium 3=hard
    difficulty: int = 1

    # 1=short 2=medium 3=long
    sentence_length: int = 1

    cue_enabled: bool = True
    allow_generated_images: bool = False

    # "none" | "openai" | "local"
    image_generation_mode: str = "none"


class UserProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    interests_text: str
    avoid_topics_text: Optional[str] = None
    difficulty: int
    sentence_length: int
    cue_enabled: bool
    allow_generated_images: bool

    # "none" | "openai" | "local"
    image_generation_mode: str = "none"

    updated_at: Optional[datetime] = None


class ContentPackOut(BaseModel):
    """
    Wrapper response for any technique-specific pack.
    items is intentionally Any because shapes differ by technique.
    """
    model_config = ConfigDict(from_attributes=True)

    technique_id: str
    difficulty: int
    generated_at: str
    pack_id: str
    items: list[Any]