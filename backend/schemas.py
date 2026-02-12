from pydantic import BaseModel, ConfigDict

from pydantic import BaseModel, ConfigDict

class TechniqueStatsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    technique_id: str
    total_exp: int
    best_score: int
    attempts: int

class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    clerk_user_id: str
    email: str | None = None
    total_exp: int
    best_daily_score: int
    streak: int
    last_active_date: str | None = None
