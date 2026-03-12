import os
import re
import shutil
import uuid
from datetime import date, timedelta, datetime

import whisper
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Form
from fastapi.middleware.cors import CORSMiddleware
from metaphone import doublemetaphone
from openai import OpenAI
from pydantic import BaseModel
from tortoise.contrib.fastapi import register_tortoise

from auth_clerk import require_clerk_user_id
from models import (
    User,
    UserTechniqueStats,
    UserDailyStats,
    UserDailyTechniqueStats,
    UserProfile,
    UserContentPack,
    UserItemPerformance,
)
from schemas import UserOut, UserProfileIn, UserProfileOut, ContentPackOut
from personalisation import build_content_pack


# =========================================================
# App / env setup
# =========================================================

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

whisper_model = whisper.load_model("base")

TEMP_AUDIO_DIR = "temp_audio"
os.makedirs(TEMP_AUDIO_DIR, exist_ok=True)


# =========================================================
# Constants
# =========================================================

_PROMPT_LEAK_PHRASES = (
    "the user will say one of these words",
    "single word naming exercise",
    "this is a single word",
)

_MIN_WEBM_BYTES = 12000
_NO_SPEECH_PROB_CUTOFF = 0.80

SUPPORTED_TECHNIQUE_IDS = {
    "image_naming",
    "naming_with_cues",
    "sentence_repetition",
}

DEFAULT_FUNCTION_WORDS = {
    "a", "an", "the",
    "is", "am", "are", "was", "were", "be", "being", "been",
    "to", "of", "in", "on", "at", "by", "for", "with", "from",
    "and", "or", "but",
    "will", "would", "can", "could", "should", "shall", "may", "might",
    "do", "does", "did",
    "has", "have", "had",
    "he", "she", "it", "they", "we", "you", "i",
    "him", "her", "them", "us",
    "this", "that", "these", "those",
    "my", "your", "his", "their", "our",
    "as", "if", "than", "then", "so", "because", "while",
    "before", "after", "during", "into", "onto", "over", "under",
}


# =========================================================
# Normalisation helpers
# =========================================================

def normalize_text(s: str | None) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_word(s: str | None) -> str:
    return normalize_text(s)


def singularize_simple(w: str) -> str:
    w = normalize_word(w)

    if w.endswith("ies") and len(w) > 3:
        return w[:-3] + "y"
    if w.endswith("es") and len(w) > 3:
        return w[:-2]
    if w.endswith("s") and len(w) > 2:
        return w[:-1]
    return w


def normalize_token_for_match(w: str | None) -> str:
    return singularize_simple(normalize_word(w))


def tokenize(s: str | None) -> list[str]:
    norm = normalize_text(s)
    return norm.split() if norm else []


def parse_csv_list(raw: str | None) -> list[str]:
    if not raw:
        return []

    out: list[str] = []
    seen: set[str] = set()

    for part in raw.split(","):
        item = normalize_text(part)
        if item and item not in seen:
            seen.add(item)
            out.append(item)

    return out


def derive_targets_from_sentence(sentence: str) -> tuple[list[str], list[str]]:
    tokens = tokenize(sentence)

    content: list[str] = []
    function: list[str] = []
    seen_content: set[str] = set()
    seen_function: set[str] = set()

    for tok in tokens:
        norm_tok = normalize_word(tok)
        if not norm_tok:
            continue

        if norm_tok in DEFAULT_FUNCTION_WORDS:
            if norm_tok not in seen_function:
                seen_function.add(norm_tok)
                function.append(norm_tok)
        else:
            if norm_tok not in seen_content:
                seen_content.add(norm_tok)
                content.append(norm_tok)

    return content, function


# =========================================================
# Distance / phonetics
# =========================================================

def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            ins = cur[j - 1] + 1
            dele = prev[j] + 1
            sub = prev[j - 1] + (0 if ca == cb else 1)
            cur.append(min(ins, dele, sub))
        prev = cur
    return prev[-1]


def allowed_distance(max_len: int) -> int:
    if max_len <= 3:
        return 1
    if max_len <= 6:
        return 2
    if max_len <= 10:
        return 3
    return 4


def phonetic_codes(w: str) -> set[str]:
    a, b = doublemetaphone(w)
    return {c for c in (a, b) if c}


def is_match_phonetic(word: str, target: str) -> tuple[bool, int, float, bool]:
    w = normalize_token_for_match(word)
    t = normalize_token_for_match(target)

    if not w or not t:
        return (False, 999, 1.0, False)

    if w == t:
        return (True, 0, 0.0, True)

    dist = levenshtein(w, t)
    max_len = max(len(w), len(t))
    rel = dist / max_len if max_len else 1.0

    if dist <= allowed_distance(max_len) or rel <= 0.25:
        return (True, dist, rel, False)

    pw = phonetic_codes(w)
    pt = phonetic_codes(t)
    phonetic_match = bool(pw & pt)

    if phonetic_match:
        return (True, dist, rel, True)

    return (False, dist, rel, False)


def token_matches_target(spoken_token: str, target_token: str, allow_phonetic: bool = True) -> bool:
    spoken_token = normalize_word(spoken_token)
    target_token = normalize_word(target_token)

    if not spoken_token or not target_token:
        return False

    if allow_phonetic:
        matched, _, rel, _ = is_match_phonetic(spoken_token, target_token)
        return matched or rel <= 0.34

    if spoken_token == target_token:
        return True

    dist = levenshtein(spoken_token, target_token)
    max_len = max(len(spoken_token), len(target_token))
    rel = dist / max_len if max_len else 1.0
    return dist <= 1 or rel <= 0.20


# =========================================================
# Phrase candidate helpers
# =========================================================

def build_phrase_candidates(transcript_norm: str, target_text: str) -> list[str]:
    if not transcript_norm:
        return [""]

    out: list[str] = []
    seen: set[str] = set()

    def add_candidate(x: str) -> None:
        x = normalize_text(x)
        if x and x not in seen:
            seen.add(x)
            out.append(x)

    add_candidate(transcript_norm)

    transcript_tokens = transcript_norm.split()
    target_tokens = normalize_text(target_text).split() if target_text else []

    for tok in transcript_tokens:
        add_candidate(tok)

    if target_tokens:
        n = len(target_tokens)

        for i in range(len(transcript_tokens) - n + 1):
            add_candidate(" ".join(transcript_tokens[i:i + n]))

        for extra_n in {max(1, n - 1), n + 1}:
            if extra_n <= 0:
                continue
            for i in range(len(transcript_tokens) - extra_n + 1):
                add_candidate(" ".join(transcript_tokens[i:i + extra_n]))

    return out


def best_phrase_match(spoken_text: str, target_text: str) -> tuple[bool, int, float, bool]:
    spoken_norm = normalize_text(spoken_text)
    target_norm = normalize_text(target_text)

    if not spoken_norm or not target_norm:
        return (False, 999, 1.0, False)

    candidates = build_phrase_candidates(spoken_norm, target_norm)

    best = (False, 999, 1.0, False)
    best_tuple = (1, 1.0, 999)

    for cand in candidates:
        matched, dist, rel, phon = is_match_phonetic(cand, target_norm)
        tup = (0 if matched else 1, rel, dist)

        if tup < best_tuple:
            best_tuple = tup
            best = (matched, dist, rel, phon)

    return best


def target_item_present(
    transcript_norm: str,
    transcript_tokens: list[str],
    target_item: str,
    allow_phonetic: bool = True,
) -> bool:
    target_item = normalize_text(target_item)
    if not target_item:
        return False

    target_parts = target_item.split()

    if len(target_parts) == 1:
        return any(
            token_matches_target(tok, target_item, allow_phonetic=allow_phonetic)
            for tok in transcript_tokens
        )

    matched, _, rel, _ = best_phrase_match(transcript_norm, target_item)
    return matched or rel <= 0.34


def compute_target_coverage(
    transcript_norm: str,
    target_items: list[str],
    allow_phonetic: bool = True,
) -> tuple[int, float, list[str]]:
    transcript_tokens = tokenize(transcript_norm)
    matched_items: list[str] = []

    for item in target_items:
        if target_item_present(
            transcript_norm,
            transcript_tokens,
            item,
            allow_phonetic=allow_phonetic,
        ):
            matched_items.append(item)

    accuracy = len(matched_items) / len(target_items) if target_items else 1.0
    return len(matched_items), accuracy, matched_items


def compute_similarity_score(a: str, b: str) -> tuple[int, float, float]:
    a = normalize_text(a)
    b = normalize_text(b)

    dist = levenshtein(a, b)
    max_len = max(len(a), len(b))
    rel_distance = dist / max_len if max_len else 1.0
    similarity_score = max(0.0, 1.0 - rel_distance)

    return dist, rel_distance, similarity_score


def extract_max_no_speech_prob(result: dict) -> float:
    segments = result.get("segments") or []
    max_no_speech_prob = 0.0

    for seg in segments:
        try:
            p = float(seg.get("no_speech_prob") or 0.0)
            if p > max_no_speech_prob:
                max_no_speech_prob = p
        except Exception:
            pass

    return max_no_speech_prob


# =========================================================
# User/profile helpers
# =========================================================

@app.get("/")
def index():
    return {"message": "Welcome to ReLingo API – see /docs for Swagger UI"}


async def get_or_create_user(clerk_user_id: str) -> User:
    user = await User.get_or_none(clerk_user_id=clerk_user_id)
    if not user:
        user = await User.create(
            clerk_user_id=clerk_user_id,
            email=None,
            total_exp=0,
            best_daily_score=0,
            streak=0,
            last_active_date=None,
        )
    return user


async def get_or_create_profile(user: User) -> UserProfile:
    prof = await UserProfile.get_or_none(user_id=user.id)
    if not prof:
        prof = await UserProfile.create(user=user)
    return prof


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _pack_wrapper(technique_id: str, difficulty: int, items: list[dict]) -> dict:
    return {
        "pack_id": uuid.uuid4().hex,
        "technique_id": technique_id,
        "difficulty": difficulty,
        "generated_at": _now_iso(),
        "items": items,
    }


def _safe_int(x, default=0) -> int:
    try:
        return int(x)
    except Exception:
        return default


def _validate_technique_id(technique_id: str) -> str:
    technique_id = (technique_id or "").strip()
    if technique_id not in SUPPORTED_TECHNIQUE_IDS:
        raise HTTPException(status_code=400, detail="Unknown technique_id")
    return technique_id


def _item_key_for_analytics(technique_id: str, target: str) -> str:
    target_norm = normalize_text(target)
    return f"{technique_id}::{target_norm}" if target_norm else f"{technique_id}::unknown"


def _item_label_for_analytics(target: str) -> str:
    target = re.sub(r"\s+", " ", (target or "").strip())
    return target[:255] if target else "Unknown item"


async def _get_item_image_from_active_pack(user: User, technique_id: str, target: str) -> str | None:
    row = await UserContentPack.get_or_none(user=user, technique_id=technique_id)
    if not row or not row.pack_json:
        return None

    items = row.pack_json.get("items", []) or []
    target_norm = normalize_text(target)

    for item in items:
        item_word = normalize_text(item.get("word"))
        item_sentence = normalize_text(item.get("sentence"))
        if target_norm and (item_word == target_norm or item_sentence == target_norm):
            image = item.get("image")
            if image:
                return image

    return None


async def _update_item_performance(
    user: User,
    technique_id: str,
    score_item,
) -> None:
    target = (score_item.target or "").strip()
    if not target:
        return

    item_key = _item_key_for_analytics(technique_id, target)
    item_label = _item_label_for_analytics(target)
    image = await _get_item_image_from_active_pack(user, technique_id, target)

    perf = await UserItemPerformance.get_or_none(
        user=user,
        technique_id=technique_id,
        item_key=item_key,
    )

    if not perf:
        perf = await UserItemPerformance.create(
            user=user,
            technique_id=technique_id,
            item_key=item_key,
            item_label=item_label,
            image=image,
            times_seen=0,
            times_correct=0,
            times_incorrect=0,
            correct_first=0,
            correct_second=0,
            correct_after_cue=0,
            total_attempts=0,
            total_points=0,
            times_cue_shown=0,
            total_sentence_accuracy=0.0,
            sentence_attempts=0,
            last_result=None,
        )

    perf.item_label = item_label
    if image:
        perf.image = image

    perf.times_seen += 1
    perf.total_attempts += max(0, _safe_int(score_item.attemptsUsed))
    perf.total_points += max(0, _safe_int(score_item.awardedPoints))

    if bool(score_item.cueShown):
        perf.times_cue_shown += 1

    pts = _safe_int(score_item.awardedPoints)
    if pts == 3:
        perf.times_correct += 1
        perf.correct_first += 1
        perf.last_result = "correct"
    elif pts == 2:
        perf.times_correct += 1
        perf.correct_second += 1
        perf.last_result = "correct"
    elif pts == 1:
        perf.times_correct += 1
        perf.correct_after_cue += 1
        perf.last_result = "correct"
    else:
        perf.times_incorrect += 1
        perf.last_result = "incorrect"

    if score_item.sentence_accuracy is not None:
        perf.total_sentence_accuracy += float(score_item.sentence_accuracy)
        perf.sentence_attempts += 1

    await perf.save()


def _build_item_metric_row(row: UserItemPerformance) -> dict:
    times_seen = _safe_int(row.times_seen)
    total_attempts = _safe_int(row.total_attempts)
    times_correct = _safe_int(row.times_correct)
    times_incorrect = _safe_int(row.times_incorrect)
    times_cue_shown = _safe_int(row.times_cue_shown)

    first_try_rate = row.correct_first / times_seen if times_seen > 0 else 0.0
    avg_attempts = total_attempts / times_seen if times_seen > 0 else 0.0
    cue_rate = times_cue_shown / times_seen if times_seen > 0 else 0.0
    sentence_accuracy = (
        row.total_sentence_accuracy / row.sentence_attempts
        if row.sentence_attempts > 0 else None
    )
    success_rate = times_correct / times_seen if times_seen > 0 else 0.0

    return {
        "item_key": row.item_key,
        "item_label": row.item_label,
        "image": row.image,
        "technique_id": row.technique_id,
        "times_seen": times_seen,
        "times_correct": times_correct,
        "times_incorrect": times_incorrect,
        "correct_first": _safe_int(row.correct_first),
        "correct_second": _safe_int(row.correct_second),
        "correct_after_cue": _safe_int(row.correct_after_cue),
        "total_attempts": total_attempts,
        "avg_attempts": round(avg_attempts, 2),
        "times_cue_shown": times_cue_shown,
        "cue_rate": round(cue_rate, 4),
        "first_try_rate": round(first_try_rate, 4),
        "success_rate": round(success_rate, 4),
        "avg_sentence_accuracy": round(sentence_accuracy, 4) if sentence_accuracy is not None else None,
        "last_result": row.last_result,
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
    }


async def _get_item_analytics_for_user(user: User) -> dict:
    rows = await UserItemPerformance.filter(user=user).all()

    all_rows = [_build_item_metric_row(r) for r in rows]

    hardest_items = sorted(
        all_rows,
        key=lambda x: (-x["times_incorrect"], -x["avg_attempts"], x["first_try_rate"]),
    )[:10]

    most_retried_items = sorted(
        all_rows,
        key=lambda x: (-x["avg_attempts"], -x["times_seen"], -x["times_incorrect"]),
    )[:10]

    most_cue_dependent_items = sorted(
        [r for r in all_rows if r["technique_id"] in {"image_naming", "naming_with_cues"}],
        key=lambda x: (-x["cue_rate"], -x["times_cue_shown"], x["first_try_rate"]),
    )[:10]

    weakest_sentence_items = sorted(
        [r for r in all_rows if r["technique_id"] == "sentence_repetition" and r["avg_sentence_accuracy"] is not None],
        key=lambda x: (x["avg_sentence_accuracy"], -x["times_seen"]),
    )[:10]

    by_technique: dict[str, dict] = {}
    for technique_id in SUPPORTED_TECHNIQUE_IDS:
        technique_rows = [r for r in all_rows if r["technique_id"] == technique_id]

        by_technique[technique_id] = {
            "hardest_items": sorted(
                technique_rows,
                key=lambda x: (-x["times_incorrect"], -x["avg_attempts"], x["first_try_rate"]),
            )[:5],
            "most_retried_items": sorted(
                technique_rows,
                key=lambda x: (-x["avg_attempts"], -x["times_seen"]),
            )[:5],
            "most_cue_dependent_items": sorted(
                technique_rows,
                key=lambda x: (-x["cue_rate"], -x["times_cue_shown"]),
            )[:5],
            "weakest_sentence_items": sorted(
                [r for r in technique_rows if r["avg_sentence_accuracy"] is not None],
                key=lambda x: (x["avg_sentence_accuracy"], -x["times_seen"]),
            )[:5],
        }

    return {
        "hardest_items": hardest_items,
        "most_retried_items": most_retried_items,
        "most_cue_dependent_items": most_cue_dependent_items,
        "weakest_sentence_items": weakest_sentence_items,
        "by_technique": by_technique,
    }


# =========================================================
# User endpoints
# =========================================================

@app.get("/api/me", response_model=UserOut)
async def me(clerk_user_id: str = Depends(require_clerk_user_id)):
    user = await get_or_create_user(clerk_user_id)

    today = date.today().isoformat()
    daily = await UserDailyStats.get_or_none(user=user, day=today)

    return UserOut(
        clerk_user_id=user.clerk_user_id,
        email=user.email,
        total_exp=user.total_exp,
        best_daily_score=user.best_daily_score,
        streak=user.streak,
        last_active_date=user.last_active_date,
        today_points=daily.points if daily else 0,
        today_sessions=daily.sessions_played if daily else 0,
    )


@app.get("/api/personalisation", response_model=UserProfileOut)
async def get_personalisation(clerk_user_id: str = Depends(require_clerk_user_id)):
    user = await get_or_create_user(clerk_user_id)
    prof = await get_or_create_profile(user)

    return UserProfileOut(
        interests_text=prof.interests_text or "",
        avoid_topics_text=prof.avoid_topics_text,
        difficulty=prof.difficulty,
        sentence_length=prof.sentence_length,
        cue_enabled=prof.cue_enabled,
        allow_generated_images=prof.allow_generated_images,
        image_generation_mode=getattr(prof, "image_generation_mode", "none") or "none",
        updated_at=prof.updated_at,
    )


@app.post("/api/personalisation", response_model=UserProfileOut)
async def save_personalisation(
    payload: UserProfileIn,
    clerk_user_id: str = Depends(require_clerk_user_id),
):
    user = await get_or_create_user(clerk_user_id)
    prof = await get_or_create_profile(user)

    requested_mode = (payload.image_generation_mode or "none").strip().lower()
    if requested_mode not in {"none", "openai", "local"}:
        requested_mode = "none"

    allow_generated_images = bool(payload.allow_generated_images) and requested_mode != "none"
    if not allow_generated_images:
        requested_mode = "none"

    prof.interests_text = payload.interests_text or ""
    prof.avoid_topics_text = payload.avoid_topics_text
    prof.difficulty = max(1, min(3, int(payload.difficulty or 1)))
    prof.sentence_length = max(1, min(3, int(payload.sentence_length or 1)))
    prof.cue_enabled = bool(payload.cue_enabled)
    prof.allow_generated_images = allow_generated_images
    prof.image_generation_mode = requested_mode

    await prof.save(
        update_fields=[
            "interests_text",
            "avoid_topics_text",
            "difficulty",
            "sentence_length",
            "cue_enabled",
            "allow_generated_images",
            "image_generation_mode",
        ]
    )
    await prof.refresh_from_db()

    return UserProfileOut(
        interests_text=prof.interests_text or "",
        avoid_topics_text=prof.avoid_topics_text,
        difficulty=prof.difficulty,
        sentence_length=prof.sentence_length,
        cue_enabled=prof.cue_enabled,
        allow_generated_images=prof.allow_generated_images,
        image_generation_mode=prof.image_generation_mode or "none",
        updated_at=prof.updated_at,
    )


# =========================================================
# Content pack generation
# =========================================================

class GeneratePackPayload(BaseModel):
    technique_id: str
    n_items: int | None = 10
    difficulty: int | None = None


@app.post("/api/personalisation/generate", response_model=ContentPackOut)
async def generate_personalised_pack(
    payload: GeneratePackPayload,
    clerk_user_id: str = Depends(require_clerk_user_id),
):
    user = await get_or_create_user(clerk_user_id)
    prof = await get_or_create_profile(user)

    technique_id = _validate_technique_id(payload.technique_id)

    n_items = int(payload.n_items or 10)
    n_items = max(1, min(25, n_items))

    profile_dict = {
        "interests_text": prof.interests_text or "",
        "avoid_topics_text": prof.avoid_topics_text or "",
        "difficulty": int(payload.difficulty or prof.difficulty or 1),
        "sentence_length": int(prof.sentence_length or 1),
        "cue_enabled": bool(prof.cue_enabled),
        "allow_generated_images": bool(prof.allow_generated_images),
        "image_generation_mode": (prof.image_generation_mode or "none"),
    }

    print("PROFILE SETTINGS:")
    print(profile_dict)

    try:
        pack = await build_content_pack(profile_dict, technique_id, n_items=n_items)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate pack: {str(e)}")

    existing = await UserContentPack.get_or_none(user=user, technique_id=technique_id)
    if existing:
        existing.difficulty = int(pack.get("difficulty") or profile_dict["difficulty"])
        existing.pack_json = pack
        existing.active = True
        await existing.save()
    else:
        await UserContentPack.create(
            user=user,
            technique_id=technique_id,
            difficulty=int(pack.get("difficulty") or profile_dict["difficulty"]),
            pack_json=pack,
            active=True,
        )

    return ContentPackOut(
        technique_id=pack["technique_id"],
        difficulty=int(pack["difficulty"]),
        generated_at=pack["generated_at"],
        pack_id=pack["pack_id"],
        items=pack["items"],
    )


@app.get("/api/content-pack/active/{technique_id}", response_model=ContentPackOut)
async def get_active_pack(
    technique_id: str,
    clerk_user_id: str = Depends(require_clerk_user_id),
):
    technique_id = _validate_technique_id(technique_id)
    user = await get_or_create_user(clerk_user_id)

    row = await UserContentPack.get_or_none(user=user, technique_id=technique_id, active=True)
    if not row or not row.pack_json:
        row = await UserContentPack.get_or_none(user=user, technique_id=technique_id)

    if not row or not row.pack_json:
        return ContentPackOut(
            technique_id=technique_id,
            difficulty=1,
            generated_at=datetime.utcnow().isoformat() + "Z",
            pack_id=uuid.uuid4().hex,
            items=[],
        )

    pack = row.pack_json
    return ContentPackOut(
        technique_id=pack.get("technique_id", technique_id),
        difficulty=int(pack.get("difficulty", row.difficulty or 1)),
        generated_at=pack.get("generated_at", datetime.utcnow().isoformat() + "Z"),
        pack_id=pack.get("pack_id", uuid.uuid4().hex),
        items=pack.get("items", []),
    )


# =========================================================
# Score tracking
# =========================================================

class ScoreItem(BaseModel):
    target: str
    awardedPoints: int
    attemptsUsed: int
    cueShown: bool | None = None
    transcript: str | None = None
    best_guess: str | None = None
    matched: bool | None = None
    sentence_accuracy: float | None = None


class ScorePayload(BaseModel):
    technique_id: str
    delta_score: int
    session_size: int | None = None
    items: list[ScoreItem] | None = None


@app.post("/api/score")
async def update_score(payload: ScorePayload, clerk_user_id: str = Depends(require_clerk_user_id)):
    user = await get_or_create_user(clerk_user_id)
    technique_id = _validate_technique_id(payload.technique_id)

    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    if user.last_active_date == today:
        pass
    elif user.last_active_date == yesterday:
        user.streak = (user.streak or 0) + 1
    else:
        user.streak = 1

    user.last_active_date = today

    session_points = max(0, _safe_int(payload.delta_score))
    user.total_exp += session_points
    await user.save()

    daily = await UserDailyStats.get_or_none(user=user, day=today)
    if not daily:
        daily = await UserDailyStats.create(user=user, day=today, points=0, sessions_played=0)

    daily.points += session_points
    daily.sessions_played += 1
    await daily.save()

    user.best_daily_score = max(_safe_int(user.best_daily_score), _safe_int(daily.points))
    await user.save()

    stats = await UserTechniqueStats.get_or_none(user=user, technique_id=technique_id)
    if not stats:
        stats = await UserTechniqueStats.create(
            user=user,
            technique_id=technique_id,
            total_exp=0,
            best_score=0,
            attempts=0,
            total_points=0,
            best_session_points=0,
            sessions_played=0,
            total_items=0,
            total_attempts=0,
            correct_first=0,
            correct_second=0,
            correct_after_cue=0,
            incorrect=0,
            total_sentence_accuracy=0.0,
            sentence_items_evaluated=0,
            best_sentence_accuracy=0.0,
        )

    daily_tech = await UserDailyTechniqueStats.get_or_none(
        user=user,
        technique_id=technique_id,
        day=today,
    )
    if not daily_tech:
        daily_tech = await UserDailyTechniqueStats.create(
            user=user,
            technique_id=technique_id,
            day=today,
            points=0,
            sessions_played=0,
            total_items=0,
            total_attempts=0,
            correct_first=0,
            correct_second=0,
            correct_after_cue=0,
            incorrect=0,
            total_sentence_accuracy=0.0,
            sentence_items_evaluated=0,
        )

    stats.attempts += 1
    stats.total_exp += session_points
    stats.best_score = max(_safe_int(stats.best_score), session_points)

    stats.sessions_played += 1
    stats.total_points += session_points
    stats.best_session_points = max(_safe_int(stats.best_session_points), session_points)

    daily_tech.sessions_played += 1
    daily_tech.points += session_points

    items = payload.items or []
    session_size = payload.session_size or (len(items) if items else 0)

    if session_size:
        stats.total_items += _safe_int(session_size)
        daily_tech.total_items += _safe_int(session_size)

    session_avg_sentence_accuracy = None

    if items:
        attempts_sum = sum(max(0, _safe_int(it.attemptsUsed)) for it in items)

        stats.total_attempts += attempts_sum
        daily_tech.total_attempts += attempts_sum

        for it in items:
            pts = _safe_int(it.awardedPoints)

            if pts == 3:
                stats.correct_first += 1
                daily_tech.correct_first += 1
            elif pts == 2:
                stats.correct_second += 1
                daily_tech.correct_second += 1
            elif pts == 1:
                stats.correct_after_cue += 1
                daily_tech.correct_after_cue += 1
            else:
                stats.incorrect += 1
                daily_tech.incorrect += 1

            await _update_item_performance(
                user=user,
                technique_id=technique_id,
                score_item=it,
            )

        if technique_id == "sentence_repetition":
            sentence_accuracies = [
                float(it.sentence_accuracy)
                for it in items
                if it.sentence_accuracy is not None
            ]

            if sentence_accuracies:
                session_avg_sentence_accuracy = sum(sentence_accuracies) / len(sentence_accuracies)

                stats.total_sentence_accuracy += sum(sentence_accuracies)
                stats.sentence_items_evaluated += len(sentence_accuracies)
                stats.best_sentence_accuracy = max(
                    float(stats.best_sentence_accuracy or 0.0),
                    session_avg_sentence_accuracy
                )

                daily_tech.total_sentence_accuracy += sum(sentence_accuracies)
                daily_tech.sentence_items_evaluated += len(sentence_accuracies)

    await stats.save()
    await daily_tech.save()

    avg_attempts = None
    if items:
        total_attempts = sum(max(0, _safe_int(it.attemptsUsed)) for it in items)
        avg_attempts = total_attempts / max(1, len(items))

    lifetime_avg_sentence_accuracy = (
        stats.total_sentence_accuracy / stats.sentence_items_evaluated
        if stats.sentence_items_evaluated > 0 else 0.0
    )

    item_analytics = await _get_item_analytics_for_user(user)

    return {
        "global": {
            "total_exp": user.total_exp,
            "best_daily_score": user.best_daily_score,
            "streak": user.streak,
            "last_active_date": user.last_active_date,
            "today_points": daily.points,
            "today_sessions": daily.sessions_played,
        },
        "technique": {
            "technique_id": stats.technique_id,
            "total_exp": stats.total_exp,
            "best_score": stats.best_score,
            "attempts": stats.attempts,
            "total_points": stats.total_points,
            "best_session_points": stats.best_session_points,
            "sessions_played": stats.sessions_played,
            "total_items": stats.total_items,
            "total_attempts": stats.total_attempts,
            "correct_first": stats.correct_first,
            "correct_second": stats.correct_second,
            "correct_after_cue": stats.correct_after_cue,
            "incorrect": stats.incorrect,
            "total_sentence_accuracy": stats.total_sentence_accuracy,
            "sentence_items_evaluated": stats.sentence_items_evaluated,
            "best_sentence_accuracy": stats.best_sentence_accuracy,
            "lifetime_avg_sentence_accuracy": round(lifetime_avg_sentence_accuracy, 4),
        },
        "daily_technique": {
            "technique_id": daily_tech.technique_id,
            "day": daily_tech.day,
            "points": daily_tech.points,
            "sessions_played": daily_tech.sessions_played,
            "total_items": daily_tech.total_items,
            "total_attempts": daily_tech.total_attempts,
            "correct_first": daily_tech.correct_first,
            "correct_second": daily_tech.correct_second,
            "correct_after_cue": daily_tech.correct_after_cue,
            "incorrect": daily_tech.incorrect,
            "total_sentence_accuracy": daily_tech.total_sentence_accuracy,
            "sentence_items_evaluated": daily_tech.sentence_items_evaluated,
        },
        "analytics": {
            "session_points": session_points,
            "session_size": session_size,
            "avg_attempts": avg_attempts,
            "session_avg_sentence_accuracy": round(session_avg_sentence_accuracy, 4)
            if session_avg_sentence_accuracy is not None else None,
        },
        "item_analytics": item_analytics,
    }


# =========================================================
# Speech-to-text (image naming / single response)
# =========================================================

@app.post("/api/speech-to-text")
async def speech_to_text(
    audio: UploadFile = File(...),
    vocab: str | None = Form(default=None),
    target: str | None = Form(default=None),
    attempt_num: int | None = Form(default=None),
    cue_shown: int | None = Form(default=None),
):
    temp_path = f"{TEMP_AUDIO_DIR}/{uuid.uuid4().hex}.webm"

    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(audio.file, buffer)

        file_size = os.path.getsize(temp_path)
        too_short = file_size < _MIN_WEBM_BYTES

        kwargs = {
            "language": "en",
            "task": "transcribe",
            "temperature": 0,
            "condition_on_previous_text": False,
            "no_speech_threshold": 0.6,
            "logprob_threshold": -1.0,
            "compression_ratio_threshold": 2.4,
        }

        vocab_list: list[str] = []
        if vocab:
            vocab_list = [v.strip() for v in vocab.split(",") if v.strip()][:50]

        if vocab_list and not too_short:
            kwargs["initial_prompt"] = "Words: " + ", ".join(vocab_list)

        result = whisper_model.transcribe(temp_path, **kwargs)
        text = (result.get("text") or "").strip()
        tnorm = normalize_text(text)

        max_no_speech_prob = extract_max_no_speech_prob(result)

        if too_short or max_no_speech_prob >= _NO_SPEECH_PROB_CUTOFF:
            return {
                "text": "",
                "matched": False if target else None,
                "best_guess": None,
                "distance": None,
                "rel_distance": None,
                "phonetic_used": None,
                "target": target,
                "attempt_num": attempt_num,
                "cue_shown": bool(cue_shown) if cue_shown is not None else None,
                "too_short": too_short,
                "no_speech_prob": max_no_speech_prob,
            }

        if any(p in tnorm for p in _PROMPT_LEAK_PHRASES):
            return {
                "text": "",
                "matched": False if target else None,
                "best_guess": None,
                "distance": None,
                "rel_distance": None,
                "phonetic_used": None,
                "target": target,
                "attempt_num": attempt_num,
                "cue_shown": bool(cue_shown) if cue_shown is not None else None,
                "too_short": False,
                "no_speech_prob": max_no_speech_prob,
                "prompt_leak": True,
            }

        candidates = build_phrase_candidates(tnorm, target or "")

        best_guess = None
        best_dist = 10**9
        best_rel = 1.0
        best_phon = False
        best_guess_matched = False

        if vocab_list and tnorm:
            best_score_tuple = None

            for vocab_target in vocab_list:
                vocab_candidates = build_phrase_candidates(tnorm, vocab_target)

                for cand in vocab_candidates:
                    matched, dist, rel, phon = is_match_phonetic(cand, vocab_target)
                    score_tuple = (0 if matched else 1, rel, dist)

                    if best_score_tuple is None or score_tuple < best_score_tuple:
                        best_score_tuple = score_tuple
                        best_guess = vocab_target
                        best_dist = dist
                        best_rel = rel
                        best_phon = phon
                        best_guess_matched = matched

        matched_vs_target = None
        dist_vs_target = None
        rel_vs_target = None
        phon_vs_target = None

        if target and tnorm:
            best_target_tuple = None

            for cand in candidates:
                m, d, r, p = is_match_phonetic(cand, target)
                tup = (0 if m else 1, r, d)

                if best_target_tuple is None or tup < best_target_tuple:
                    best_target_tuple = tup
                    matched_vs_target = bool(m)
                    dist_vs_target = d
                    rel_vs_target = r
                    phon_vs_target = bool(p)

        final_matched = (
            matched_vs_target
            if matched_vs_target is not None
            else (bool(best_guess_matched) if vocab_list and tnorm else None)
        )

        return {
            "text": text,
            "matched": final_matched,
            "best_guess": best_guess,
            "distance": dist_vs_target if dist_vs_target is not None else (best_dist if vocab_list and tnorm else None),
            "rel_distance": rel_vs_target if rel_vs_target is not None else (best_rel if vocab_list and tnorm else None),
            "phonetic_used": phon_vs_target if phon_vs_target is not None else (bool(best_phon) if vocab_list and tnorm else None),
            "target": target,
            "attempt_num": attempt_num,
            "cue_shown": bool(cue_shown) if cue_shown is not None else None,
            "too_short": False,
            "no_speech_prob": max_no_speech_prob,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")

    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


# =========================================================
# Sentence repetition evaluation
# =========================================================

@app.post("/api/sentence-eval")
async def sentence_eval(
    audio: UploadFile = File(...),
    target_sentence: str = Form(...),
    required_content: str = Form(...),
    required_function: str = Form(...),
):
    temp_path = f"{TEMP_AUDIO_DIR}/{uuid.uuid4().hex}.webm"

    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(audio.file, buffer)

        file_size = os.path.getsize(temp_path)
        too_short = file_size < _MIN_WEBM_BYTES

        result = whisper_model.transcribe(
            temp_path,
            language="en",
            task="transcribe",
            temperature=0,
            condition_on_previous_text=False,
            no_speech_threshold=0.6,
            logprob_threshold=-1.0,
            compression_ratio_threshold=2.4,
        )

        text = (result.get("text") or "").strip()
        transcript_norm = normalize_text(text)
        target_norm = normalize_text(target_sentence)

        max_no_speech_prob = extract_max_no_speech_prob(result)

        if too_short or max_no_speech_prob >= _NO_SPEECH_PROB_CUTOFF:
            return {
                "text": "",
                "content_hits": 0,
                "function_hits": 0,
                "content_accuracy": 0.0,
                "function_accuracy": 0.0,
                "similarity_score": 0.0,
                "weighted_accuracy": 0.0,
                "rel_distance": 1.0,
                "score_level": 0,
                "too_short": too_short,
                "no_speech_prob": max_no_speech_prob,
                "exact_match": False,
            }

        derived_content, derived_function = derive_targets_from_sentence(target_sentence)
        provided_content = parse_csv_list(required_content)
        provided_function = parse_csv_list(required_function)

        if transcript_norm == target_norm and transcript_norm:
            return {
                "text": text,
                "content_hits": len(derived_content),
                "function_hits": len(derived_function),
                "content_accuracy": 1.0,
                "function_accuracy": 1.0,
                "similarity_score": 1.0,
                "weighted_accuracy": 1.0,
                "rel_distance": 0.0,
                "score_level": 3,
                "too_short": False,
                "no_speech_prob": max_no_speech_prob,
                "exact_match": True,
                "derived_content_items": derived_content,
                "derived_function_items": derived_function,
                "matched_content_items": derived_content,
                "matched_function_items": derived_function,
                "provided_content_items": provided_content,
                "provided_function_items": provided_function,
            }

        content_hits, content_accuracy, matched_content_items = compute_target_coverage(
            transcript_norm,
            derived_content,
            allow_phonetic=True,
        )

        function_hits, function_accuracy, matched_function_items = compute_target_coverage(
            transcript_norm,
            derived_function,
            allow_phonetic=False,
        )

        _, rel_distance, similarity_score = compute_similarity_score(transcript_norm, target_norm)

        weighted_accuracy = (
            0.70 * content_accuracy +
            0.10 * function_accuracy +
            0.20 * similarity_score
        )

        if content_accuracy >= 0.85 and weighted_accuracy >= 0.80:
            score_level = 3
        elif content_accuracy >= 0.65 and weighted_accuracy >= 0.62:
            score_level = 2
        elif content_accuracy >= 0.40 or weighted_accuracy >= 0.45:
            score_level = 1
        else:
            score_level = 0

        return {
            "text": text,
            "content_hits": content_hits,
            "function_hits": function_hits,
            "content_accuracy": round(content_accuracy, 4),
            "function_accuracy": round(function_accuracy, 4),
            "similarity_score": round(similarity_score, 4),
            "weighted_accuracy": round(weighted_accuracy, 4),
            "rel_distance": round(rel_distance, 4),
            "score_level": score_level,
            "too_short": False,
            "no_speech_prob": max_no_speech_prob,
            "exact_match": False,
            "derived_content_items": derived_content,
            "derived_function_items": derived_function,
            "matched_content_items": matched_content_items,
            "matched_function_items": matched_function_items,
            "provided_content_items": provided_content,
            "provided_function_items": provided_function,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


# =========================================================
# Optional speech feedback
# =========================================================

@app.post("/api/speech-feedback")
async def speech_feedback(userText: str = Form(...), correctAnswer: str = Form(...)):
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a supportive speech therapy assistant. "
                        "Give brief, encouraging, clinically appropriate feedback."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"I said: {userText}. "
                        f"The correct answer was: {correctAnswer}. "
                        f"Give short supportive feedback."
                    ),
                },
            ],
            temperature=0.4,
        )

        feedback = response.choices[0].message.content or "Good effort."
        return {"feedback": feedback.strip()}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"GPT feedback failed: {str(e)}")


# =========================================================
# Progress summary
# =========================================================

@app.get("/api/progress/summary")
async def get_progress_summary(clerk_user_id: str = Depends(require_clerk_user_id)):
    user = await get_or_create_user(clerk_user_id)

    today = date.today()
    days = [(today - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]

    daily_rows = await UserDailyStats.filter(user=user, day__in=days).all()
    daily_map = {row.day: row for row in daily_rows}

    daily_points = []
    for d in days:
        row = daily_map.get(d)
        daily_points.append({
            "day": d,
            "points": row.points if row else 0,
            "sessions": row.sessions_played if row else 0,
        })

    technique_rows = await UserTechniqueStats.filter(user=user).all()
    techniques = {}

    for row in technique_rows:
        lifetime_avg_sentence_accuracy = (
            row.total_sentence_accuracy / row.sentence_items_evaluated
            if row.sentence_items_evaluated > 0 else 0.0
        )

        avg_attempts_per_item = (
            row.total_attempts / row.total_items
            if row.total_items > 0 else 0.0
        )

        first_try_accuracy = (
            row.correct_first / row.total_items
            if row.total_items > 0 else 0.0
        )

        cue_usage_rate = (
            row.correct_after_cue / row.total_items
            if row.total_items > 0 else 0.0
        )

        techniques[row.technique_id] = {
            "technique_id": row.technique_id,
            "total_exp": row.total_exp,
            "best_score": row.best_score,
            "attempts": row.attempts,
            "total_points": row.total_points,
            "best_session_points": row.best_session_points,
            "sessions_played": row.sessions_played,
            "total_items": row.total_items,
            "total_attempts": row.total_attempts,
            "correct_first": row.correct_first,
            "correct_second": row.correct_second,
            "correct_after_cue": row.correct_after_cue,
            "incorrect": row.incorrect,
            "total_sentence_accuracy": row.total_sentence_accuracy,
            "sentence_items_evaluated": row.sentence_items_evaluated,
            "best_sentence_accuracy": row.best_sentence_accuracy,
            "lifetime_avg_sentence_accuracy": round(lifetime_avg_sentence_accuracy, 4),
            "avg_attempts_per_item": round(avg_attempts_per_item, 2),
            "first_try_accuracy": round(first_try_accuracy, 4),
            "cue_usage_rate": round(cue_usage_rate, 4),
        }

    daily_tech_rows = await UserDailyTechniqueStats.filter(user=user, day__in=days).all()

    by_technique_day = {}
    for row in daily_tech_rows:
        by_technique_day[(row.technique_id, row.day)] = row

    technique_history = {}

    for technique_id in sorted(SUPPORTED_TECHNIQUE_IDS):
        series = []

        for d in days:
            row = by_technique_day.get((technique_id, d))

            points = row.points if row else 0
            sessions_played = row.sessions_played if row else 0
            total_items = row.total_items if row else 0
            total_attempts = row.total_attempts if row else 0
            correct_first = row.correct_first if row else 0
            correct_after_cue = row.correct_after_cue if row else 0
            total_sentence_accuracy = row.total_sentence_accuracy if row else 0.0
            sentence_items_evaluated = row.sentence_items_evaluated if row else 0

            first_try_accuracy = (
                correct_first / total_items if total_items > 0 else 0.0
            )
            avg_attempts_per_item = (
                total_attempts / total_items if total_items > 0 else 0.0
            )
            cue_usage_rate = (
                correct_after_cue / total_items if total_items > 0 else 0.0
            )
            sentence_accuracy = (
                total_sentence_accuracy / sentence_items_evaluated
                if sentence_items_evaluated > 0 else 0.0
            )

            series.append({
                "day": d,
                "points": points,
                "sessions": sessions_played,
                "first_try_accuracy": round(first_try_accuracy, 4),
                "avg_attempts_per_item": round(avg_attempts_per_item, 2),
                "cue_usage_rate": round(cue_usage_rate, 4),
                "sentence_accuracy": round(sentence_accuracy, 4),
                "total_items": total_items,
            })

        technique_history[technique_id] = series

    item_analytics = await _get_item_analytics_for_user(user)

    return {
        "daily_points": daily_points,
        "techniques": techniques,
        "technique_history": technique_history,
        "item_analytics": item_analytics,
    }


@app.get("/api/technique/{technique_id}")
async def get_technique_stats(
    technique_id: str,
    clerk_user_id: str = Depends(require_clerk_user_id),
):
    technique_id = _validate_technique_id(technique_id)
    user = await get_or_create_user(clerk_user_id)
    stats = await UserTechniqueStats.get_or_none(user=user, technique_id=technique_id)

    if not stats:
        return {
            "technique_id": technique_id,
            "total_exp": 0,
            "best_score": 0,
            "attempts": 0,
            "total_points": 0,
            "best_session_points": 0,
            "sessions_played": 0,
            "total_items": 0,
            "total_attempts": 0,
            "correct_first": 0,
            "correct_second": 0,
            "correct_after_cue": 0,
            "incorrect": 0,
            "total_sentence_accuracy": 0.0,
            "sentence_items_evaluated": 0,
            "best_sentence_accuracy": 0.0,
            "lifetime_avg_sentence_accuracy": 0.0,
            "item_analytics": {
                "hardest_items": [],
                "most_retried_items": [],
                "most_cue_dependent_items": [],
                "weakest_sentence_items": [],
            },
        }

    lifetime_avg_sentence_accuracy = (
        stats.total_sentence_accuracy / stats.sentence_items_evaluated
        if stats.sentence_items_evaluated > 0 else 0.0
    )

    user_item_analytics = await _get_item_analytics_for_user(user)
    technique_item_analytics = user_item_analytics["by_technique"].get(
        technique_id,
        {
            "hardest_items": [],
            "most_retried_items": [],
            "most_cue_dependent_items": [],
            "weakest_sentence_items": [],
        },
    )

    return {
        "technique_id": stats.technique_id,
        "total_exp": stats.total_exp,
        "best_score": stats.best_score,
        "attempts": stats.attempts,
        "total_points": stats.total_points,
        "best_session_points": stats.best_session_points,
        "sessions_played": stats.sessions_played,
        "total_items": stats.total_items,
        "total_attempts": stats.total_attempts,
        "correct_first": stats.correct_first,
        "correct_second": stats.correct_second,
        "correct_after_cue": stats.correct_after_cue,
        "incorrect": stats.incorrect,
        "total_sentence_accuracy": stats.total_sentence_accuracy,
        "sentence_items_evaluated": stats.sentence_items_evaluated,
        "best_sentence_accuracy": stats.best_sentence_accuracy,
        "lifetime_avg_sentence_accuracy": round(lifetime_avg_sentence_accuracy, 4),
        "item_analytics": technique_item_analytics,
    }


# =========================================================
# DB registration
# =========================================================

register_tortoise(
    app,
    db_url="sqlite://database.sqlite3",
    modules={"models": ["models"]},
    generate_schemas=True,
    add_exception_handlers=True,
)