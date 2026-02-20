# schemas.py (UPDATED)
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
    email: str | None = None

    total_exp: int
    best_daily_score: int
    streak: int
    last_active_date: str | None = None

    today_points: int | None = None
    today_sessions: int | None = None