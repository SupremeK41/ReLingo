# models.py (FINAL - daily stats + technique rollups + personalisation packs)
from tortoise.models import Model
from tortoise import fields


class User(Model):
    id = fields.IntField(pk=True)

    clerk_user_id = fields.CharField(max_length=255, unique=True)
    email = fields.CharField(max_length=255, null=True)

    # Global points/EXP
    total_exp = fields.IntField(default=0)

    # TRUE best daily total (max of UserDailyStats.points)
    best_daily_score = fields.IntField(default=0)

    streak = fields.IntField(default=0)
    last_active_date = fields.CharField(max_length=20, null=True)

    created_at = fields.DatetimeField(auto_now_add=True)


class UserDailyStats(Model):
    """
    Per-day rollups so best_daily_score is meaningful.
    """
    id = fields.IntField(pk=True)

    user = fields.ForeignKeyField(
        "models.User",
        related_name="daily_stats",
        on_delete=fields.CASCADE,
    )

    day = fields.CharField(max_length=20)  # ISO date yyyy-mm-dd

    points = fields.IntField(default=0)           # total points earned that day
    sessions_played = fields.IntField(default=0)  # sessions completed that day

    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        unique_together = (("user", "day"),)


class UserTechniqueStats(Model):
    """
    Per-technique aggregates.

    Legacy fields kept so older frontend won't break:
      - total_exp mirrors total_points
      - best_score mirrors best_session_points
      - attempts mirrors sessions_played
    """
    id = fields.IntField(pk=True)

    technique_id = fields.CharField(max_length=50)
    user = fields.ForeignKeyField(
        "models.User",
        related_name="technique_stats",
        on_delete=fields.CASCADE,
    )

    # ---- legacy ----
    total_exp = fields.IntField(default=0)
    best_score = fields.IntField(default=0)
    attempts = fields.IntField(default=0)

    # ---- new rollups ----
    total_points = fields.IntField(default=0)
    best_session_points = fields.IntField(default=0)
    sessions_played = fields.IntField(default=0)

    total_items = fields.IntField(default=0)
    total_attempts = fields.IntField(default=0)

    correct_first = fields.IntField(default=0)        # 3 points
    correct_second = fields.IntField(default=0)       # 2 points
    correct_after_cue = fields.IntField(default=0)    # 1 point
    incorrect = fields.IntField(default=0)            # 0 points

    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        unique_together = (("user", "technique_id"),)


# ---------------------------
# Personalisation models
# ---------------------------

class UserProfile(Model):
    """
    Stores user preferences for LLM personalisation.
    One row per user.
    """
    id = fields.IntField(pk=True)

    user = fields.OneToOneField(
        "models.User",
        related_name="profile",
        on_delete=fields.CASCADE,
    )

    interests_text = fields.TextField(default="")
    avoid_topics_text = fields.TextField(null=True)

    # 1=easy 2=medium 3=hard
    difficulty = fields.IntField(default=1)

    # 1=short 2=medium 3=long
    sentence_length = fields.IntField(default=1)

    cue_enabled = fields.BooleanField(default=True)

    # Later: allow image generation
    allow_generated_images = fields.BooleanField(default=False)

    updated_at = fields.DatetimeField(auto_now=True)


class UserContentPack(Model):
    """
    Stores personalised content packs per technique.

    For MVP you can keep ONE active pack per user+technique.
    Later you can keep history by setting older packs active=False.
    """
    id = fields.IntField(pk=True)

    user = fields.ForeignKeyField(
        "models.User",
        related_name="content_packs",
        on_delete=fields.CASCADE,
    )

    technique_id = fields.CharField(max_length=50)
    difficulty = fields.IntField(default=1)

    # Wrapper JSON (pack_id, generated_at, items, etc.)
    pack_json = fields.JSONField()

    # Optional: allows history later without schema changes
    active = fields.BooleanField(default=True)

    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        # keep one active pack per technique (enforced in server logic)
        indexes = (("user_id", "technique_id"),)


class ImageAsset(Model):
    """
    Optional now; prepares for caching generated images later.
    """
    id = fields.UUIDField(pk=True)

    # null = global shared / curated
    owner_user = fields.ForeignKeyField(
        "models.User",
        null=True,
        related_name="image_assets",
        on_delete=fields.SET_NULL,
    )

    # "curated" or "generated"
    source = fields.CharField(max_length=20, default="curated")

    tags = fields.JSONField(null=True)
    difficulty = fields.IntField(default=1)

    # Store a path the frontend can use
    # e.g. "/SentenceImages/boy_kicking_ball.jpg"
    file_path = fields.CharField(max_length=255)

    # For generated caching later (don’t enforce unique yet for MVP)
    prompt_hash = fields.CharField(max_length=128, null=True)

    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        indexes = (("prompt_hash",), ("source",), ("difficulty",))