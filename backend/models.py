from tortoise.models import Model
from tortoise import fields


class User(Model):
    id = fields.IntField(pk=True)

    # Clerk identity (source of truth)
    clerk_user_id = fields.CharField(max_length=255, unique=True)
    email = fields.CharField(max_length=255, null=True)

    # Progress + scoring
    total_exp = fields.IntField(default=0)
    best_daily_score = fields.IntField(default=0)
    streak = fields.IntField(default=0)
    last_active_date = fields.CharField(max_length=20, null=True)

    created_at = fields.DatetimeField(auto_now_add=True)
