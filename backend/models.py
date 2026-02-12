from tortoise.models import Model
from tortoise import fields


class User(Model):
    id = fields.IntField(pk=True)

    clerk_user_id = fields.CharField(max_length=255, unique=True)
    email = fields.CharField(max_length=255, null=True)

    total_exp = fields.IntField(default=0)
    best_daily_score = fields.IntField(default=0)
    streak = fields.IntField(default=0)
    last_active_date = fields.CharField(max_length=20, null=True)

    created_at = fields.DatetimeField(auto_now_add=True)


class UserTechniqueStats(Model):
    id = fields.IntField(pk=True)

    # keep it simple: string ID like "image_naming"
    technique_id = fields.CharField(max_length=50)

    # link to user
    user = fields.ForeignKeyField("models.User", related_name="technique_stats", on_delete=fields.CASCADE)

    # per-technique stats
    total_exp = fields.IntField(default=0)
    best_score = fields.IntField(default=0)
    attempts = fields.IntField(default=0)

    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        unique_together = (("user", "technique_id"),)
