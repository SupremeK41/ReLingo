from tortoise import fields
from tortoise.models import Model


class User(Model):
    id = fields.IntField(pk=True)

    clerk_user_id = fields.CharField(max_length=255, unique=True)
    email = fields.CharField(max_length=255, null=True)

    # Overall EXP across the app
    total_exp = fields.IntField(default=0)

    # Highest total points reached in a single day
    best_daily_score = fields.IntField(default=0)

    streak = fields.IntField(default=0)
    last_active_date = fields.CharField(max_length=20, null=True)

    created_at = fields.DatetimeField(auto_now_add=True)


class UserDailyStats(Model):
    """
    Per-day overall totals so daily progress and best daily score can be tracked properly.
    """
    id = fields.IntField(pk=True)

    user = fields.ForeignKeyField(
        "models.User",
        related_name="daily_stats",
        on_delete=fields.CASCADE,
    )

    # ISO date string: yyyy-mm-dd
    day = fields.CharField(max_length=20)

    points = fields.IntField(default=0)
    sessions_played = fields.IntField(default=0)

    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        unique_together = (("user", "day"),)


class UserTechniqueStats(Model):
    """
    Lifetime stats for each technique.

    Some older fields are still kept so the frontend stays compatible:
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

    # Legacy fields
    total_exp = fields.IntField(default=0)
    best_score = fields.IntField(default=0)
    attempts = fields.IntField(default=0)

    # Lifetime totals
    total_points = fields.IntField(default=0)
    best_session_points = fields.IntField(default=0)
    sessions_played = fields.IntField(default=0)

    total_items = fields.IntField(default=0)
    total_attempts = fields.IntField(default=0)

    correct_first = fields.IntField(default=0)
    correct_second = fields.IntField(default=0)
    correct_after_cue = fields.IntField(default=0)
    incorrect = fields.IntField(default=0)

    # Sentence repetition metrics
    total_sentence_accuracy = fields.FloatField(default=0.0)
    sentence_items_evaluated = fields.IntField(default=0)
    best_sentence_accuracy = fields.FloatField(default=0.0)

    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        unique_together = (("user", "technique_id"),)


class UserDailyTechniqueStats(Model):
    """
    Per-day stats for each technique.
    Used for graphs, streak-style summaries, and progress trends.
    """
    id = fields.IntField(pk=True)

    user = fields.ForeignKeyField(
        "models.User",
        related_name="daily_technique_stats",
        on_delete=fields.CASCADE,
    )

    technique_id = fields.CharField(max_length=50)
    day = fields.CharField(max_length=20)

    points = fields.IntField(default=0)
    sessions_played = fields.IntField(default=0)

    total_items = fields.IntField(default=0)
    total_attempts = fields.IntField(default=0)

    correct_first = fields.IntField(default=0)
    correct_second = fields.IntField(default=0)
    correct_after_cue = fields.IntField(default=0)
    incorrect = fields.IntField(default=0)

    total_sentence_accuracy = fields.FloatField(default=0.0)
    sentence_items_evaluated = fields.IntField(default=0)

    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        unique_together = (("user", "technique_id", "day"),)


class UserItemPerformance(Model):
    """
    Lifetime item-level tracking for each user and technique.

    This is the main table used for analytics like:
    - hardest items
    - most retried items
    - cue dependence
    - weaker sentence targets
    """
    id = fields.IntField(pk=True)

    user = fields.ForeignKeyField(
        "models.User",
        related_name="item_performance",
        on_delete=fields.CASCADE,
    )

    technique_id = fields.CharField(max_length=50)

    # Stable identifier for the therapy item
    item_key = fields.CharField(max_length=255)

    # Human-readable label for dashboards and summaries
    item_label = fields.CharField(max_length=255)

    # Optional image path for image-based tasks
    image = fields.CharField(max_length=255, null=True)

    # Core usage totals
    times_seen = fields.IntField(default=0)
    times_correct = fields.IntField(default=0)
    times_incorrect = fields.IntField(default=0)

    # Performance by attempt quality
    correct_first = fields.IntField(default=0)
    correct_second = fields.IntField(default=0)
    correct_after_cue = fields.IntField(default=0)

    total_attempts = fields.IntField(default=0)
    total_points = fields.IntField(default=0)

    # Cue usage tracking
    times_cue_shown = fields.IntField(default=0)

    # Sentence-specific tracking
    total_sentence_accuracy = fields.FloatField(default=0.0)
    sentence_attempts = fields.IntField(default=0)

    # Most recent result
    last_result = fields.CharField(max_length=20, null=True)  # "correct" or "incorrect"
    last_seen_at = fields.DatetimeField(auto_now=True)

    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        unique_together = (("user", "technique_id", "item_key"),)
        indexes = (("user_id", "technique_id"), ("item_key",))


# =========================================================
# Personalisation
# =========================================================

class UserProfile(Model):
    """
    Stores the user’s personalisation settings.
    One profile row per user.
    """
    id = fields.IntField(pk=True)

    user = fields.OneToOneField(
        "models.User",
        related_name="profile",
        on_delete=fields.CASCADE,
    )

    interests_text = fields.TextField(default="")
    avoid_topics_text = fields.TextField(null=True)

    # 1 = easy, 2 = medium, 3 = hard
    difficulty = fields.IntField(default=1)

    # 1 = short, 2 = medium, 3 = long
    sentence_length = fields.IntField(default=1)

    cue_enabled = fields.BooleanField(default=True)

    # Controls whether anything beyond curated images can be used
    allow_generated_images = fields.BooleanField(default=False)

    # "none" | "openai" | "local"
    image_generation_mode = fields.CharField(max_length=20, default="none")

    updated_at = fields.DatetimeField(auto_now=True)


class UserContentPack(Model):
    """
    Stores generated content packs for each technique.

    Right now the app keeps one active pack per user and technique.
    If needed later, older packs can be kept by marking them inactive.
    """
    id = fields.IntField(pk=True)

    user = fields.ForeignKeyField(
        "models.User",
        related_name="content_packs",
        on_delete=fields.CASCADE,
    )

    technique_id = fields.CharField(max_length=50)
    difficulty = fields.IntField(default=1)

    pack_json = fields.JSONField()
    active = fields.BooleanField(default=True)

    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        indexes = (("user_id", "technique_id"),)


class ImageAsset(Model):
    """
    Stores image metadata so generated images can be reused instead of regenerated.
    """
    id = fields.UUIDField(pk=True)

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

    file_path = fields.CharField(max_length=255)
    prompt_hash = fields.CharField(max_length=128, null=True)

    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        indexes = (("prompt_hash",), ("source",), ("difficulty",))