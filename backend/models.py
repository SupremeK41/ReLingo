# models.py (UPDATED)
from tortoise.models import Model
from tortoise import fields


class User(Model):
    id = fields.IntField(pk=True)

    clerk_user_id = fields.CharField(max_length=255, unique=True)
    email = fields.CharField(max_length=255, null=True)

    # Global "points/EXP"
    total_exp = fields.IntField(default=0)

    # Keep existing field for backward compatibility, but we will now make it a *true daily best*
    best_daily_score = fields.IntField(default=0)

    streak = fields.IntField(default=0)
    last_active_date = fields.CharField(max_length=20, null=True)

    created_at = fields.DatetimeField(auto_now_add=True)


class UserDailyStats(Model):
    """
    True per-day rollups so "best_daily_score" can be meaningful.
    """
    id = fields.IntField(pk=True)

    user = fields.ForeignKeyField("models.User", related_name="daily_stats", on_delete=fields.CASCADE)

    day = fields.CharField(max_length=20)  # ISO date yyyy-mm-dd

    points = fields.IntField(default=0)          # total points earned that day (sum of session points)
    sessions_played = fields.IntField(default=0) # number of sessions completed that day

    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        unique_together = (("user", "day"),)


class UserTechniqueStats(Model):
    id = fields.IntField(pk=True)

    technique_id = fields.CharField(max_length=50)
    user = fields.ForeignKeyField("models.User", related_name="technique_stats", on_delete=fields.CASCADE)

    # Keep existing fields for backward compatibility
    total_exp = fields.IntField(default=0)   # we will mirror "total_points" here
    best_score = fields.IntField(default=0)  # we will mirror "best_session_points" here
    attempts = fields.IntField(default=0)    # we will use as "sessions_played"

    # NEW: clinically meaningful rollups for this technique
    total_points = fields.IntField(default=0)
    best_session_points = fields.IntField(default=0)
    sessions_played = fields.IntField(default=0)

    total_items = fields.IntField(default=0)
    total_attempts = fields.IntField(default=0)

    correct_first = fields.IntField(default=0)       # 3 points
    correct_second = fields.IntField(default=0)      # 2 points
    correct_after_cue = fields.IntField(default=0)   # 1 point
    incorrect = fields.IntField(default=0)           # 0 points

    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        unique_together = (("user", "technique_id"),)