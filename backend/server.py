# server.py (UPDATED - integrated properly with daily stats + technique rollups)
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Form
from fastapi.middleware.cors import CORSMiddleware
from tortoise.contrib.fastapi import register_tortoise
from metaphone import doublemetaphone

import whisper
import shutil
import os
from dotenv import load_dotenv
from datetime import date, timedelta
import re

import openai
from pydantic import BaseModel

from auth_clerk import require_clerk_user_id
from models import User, UserTechniqueStats, UserDailyStats
from schemas import UserOut

# ------------------ ENV + KEYS ------------------

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
openai.api_key = os.getenv("OPENAI_API_KEY")

# ------------------ APP + CORS ------------------

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------ WHISPER SETUP ------------------

whisper_model = whisper.load_model("base")

if not os.path.exists("temp_audio"):
    os.makedirs("temp_audio")

def normalize_word(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def singularize_simple(w: str) -> str:
    if w.endswith("ies") and len(w) > 3:
        return w[:-3] + "y"
    if w.endswith("es") and len(w) > 2:
        return w[:-2]
    if w.endswith("s") and len(w) > 1:
        return w[:-1]
    return w

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
    return 3

def phonetic_codes(w: str) -> set[str]:
    a, b = doublemetaphone(w)
    return {c for c in (a, b) if c}

def is_match_phonetic(word: str, target: str) -> tuple[bool, int, float, bool]:
    """
    Returns: (matched, dist, rel, phonetic_match)
    """
    w = singularize_simple(normalize_word(word))
    t = singularize_simple(normalize_word(target))

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

# ------------------ USER ROUTES ------------------

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

# ------------------ SCORE PAYLOAD (CLINICAL) ------------------

class ScoreItem(BaseModel):
    target: str
    awardedPoints: int  # 3 / 2 / 1 / 0
    attemptsUsed: int   # 1..3

    cueShown: bool | None = None
    transcript: str | None = None
    best_guess: str | None = None
    matched: bool | None = None

class ScorePayload(BaseModel):
    technique_id: str
    delta_score: int              # session points total
    session_size: int | None = None
    items: list[ScoreItem] | None = None

def _safe_int(x, default=0) -> int:
    try:
        return int(x)
    except Exception:
        return default

@app.post("/api/score")
async def update_score(payload: ScorePayload, clerk_user_id: str = Depends(require_clerk_user_id)):
    user = await get_or_create_user(clerk_user_id)

    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    # ---- streak logic (unchanged) ----
    if user.last_active_date == today:
        pass
    elif user.last_active_date == yesterday:
        user.streak = (user.streak or 0) + 1
    else:
        user.streak = 1

    user.last_active_date = today

    # ---- points/EXP gained from this session ----
    session_points = max(0, _safe_int(payload.delta_score))
    user.total_exp += session_points
    await user.save()

    # ---- daily aggregation (NEW) ----
    daily = await UserDailyStats.get_or_none(user=user, day=today)
    if not daily:
        daily = await UserDailyStats.create(user=user, day=today, points=0, sessions_played=0)

    daily.points += session_points
    daily.sessions_played += 1
    await daily.save()

    # Make "best_daily_score" a TRUE best daily total (NEW correct meaning)
    user.best_daily_score = max(_safe_int(user.best_daily_score), _safe_int(daily.points))
    await user.save()

    # ---- per-technique aggregation (UPDATED) ----
    stats = await UserTechniqueStats.get_or_none(user=user, technique_id=payload.technique_id)
    if not stats:
        stats = await UserTechniqueStats.create(
            user=user,
            technique_id=payload.technique_id,
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
        )

    # Update "legacy" fields to mirror new naming (so old UI won't break)
    stats.attempts += 1
    stats.total_exp += session_points
    stats.best_score = max(_safe_int(stats.best_score), session_points)

    # Update new fields
    stats.sessions_played += 1
    stats.total_points += session_points
    stats.best_session_points = max(_safe_int(stats.best_session_points), session_points)

    # Derive per-item metrics if items supplied
    items = payload.items or []
    session_size = payload.session_size or (len(items) if items else 0)

    if session_size:
        stats.total_items += _safe_int(session_size)

    if items:
        # attempts and outcome counts
        stats.total_attempts += sum(max(0, _safe_int(it.attemptsUsed)) for it in items)

        for it in items:
            pts = _safe_int(it.awardedPoints)
            if pts == 3:
                stats.correct_first += 1
            elif pts == 2:
                stats.correct_second += 1
            elif pts == 1:
                stats.correct_after_cue += 1
            else:
                stats.incorrect += 1

    await stats.save()

    # Optional analytics block returned to frontend
    avg_attempts = None
    if items:
        total_attempts = sum(max(0, _safe_int(it.attemptsUsed)) for it in items)
        avg_attempts = total_attempts / max(1, len(items))

    return {
        "global": {
            "total_exp": user.total_exp,
            "best_daily_score": user.best_daily_score,   # now true best daily points
            "streak": user.streak,
            "last_active_date": user.last_active_date,
            "today_points": daily.points,
            "today_sessions": daily.sessions_played,
        },
        "technique": {
            "technique_id": stats.technique_id,

            # legacy
            "total_exp": stats.total_exp,
            "best_score": stats.best_score,
            "attempts": stats.attempts,

            # new
            "total_points": stats.total_points,
            "best_session_points": stats.best_session_points,
            "sessions_played": stats.sessions_played,
            "total_items": stats.total_items,
            "total_attempts": stats.total_attempts,
            "correct_first": stats.correct_first,
            "correct_second": stats.correct_second,
            "correct_after_cue": stats.correct_after_cue,
            "incorrect": stats.incorrect,
        },
        "analytics": {
            "session_points": session_points,
            "session_size": session_size,
            "avg_attempts": avg_attempts,
        },
    }

# ------------------ LOCAL WHISPER ENDPOINT ------------------
# Slightly improved: evaluate "matched" vs *target* if provided, while still returning best_guess over vocab.

@app.post("/api/speech-to-text")
async def speech_to_text(
    audio: UploadFile = File(...),
    vocab: str | None = Form(default=None),   # comma-separated session words
    target: str | None = Form(default=None),  # the intended word for this item (frontend already sends this)

    # optional debug/meta (accepted so you won't get 422)
    attempt_num: int | None = Form(default=None),
    cue_shown: int | None = Form(default=None),
):
    temp_path = f"temp_audio/{audio.filename}"
    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(audio.file, buffer)

        kwargs = {"language": "en", "task": "transcribe", "temperature": 0}

        vocab_list: list[str] = []
        if vocab:
            vocab_list = [v.strip() for v in vocab.split(",") if v.strip()][:50]
            if vocab_list:
                kwargs["initial_prompt"] = (
                    "This is a single-word naming exercise. "
                    "The user will say one of these words: " + ", ".join(vocab_list) + "."
                )

        result = whisper_model.transcribe(temp_path, **kwargs)
        text = (result.get("text") or "").strip()

        transcript_norm = normalize_word(text)
        tokens = transcript_norm.split()
        candidates = [transcript_norm] + tokens

        # Compute best_guess over vocab (for UI/feedback)
        best_guess = None
        best_dist = 10**9
        best_rel = 1.0
        best_phon = False
        best_guess_matched = False

        if vocab_list:
            best_score_tuple = None
            for vocab_target in vocab_list:
                target_norm = normalize_word(vocab_target)
                for cand in candidates:
                    matched, dist, rel, phon = is_match_phonetic(cand, target_norm)
                    score_tuple = (0 if matched else 1, rel, dist)

                    if best_score_tuple is None or score_tuple < best_score_tuple:
                        best_score_tuple = score_tuple
                        best_guess = vocab_target
                        best_dist = dist
                        best_rel = rel
                        best_phon = phon
                        best_guess_matched = matched

        # Determine "matched" vs the provided target (clinically correct)
        matched_vs_target = None
        dist_vs_target = None
        rel_vs_target = None
        phon_vs_target = None

        if target:
            tnorm = normalize_word(target)
            # match any candidate token/transcript to the *target*
            best_target_tuple = None
            for cand in candidates:
                m, d, r, p = is_match_phonetic(cand, tnorm)
                tup = (0 if m else 1, r, d)
                if best_target_tuple is None or tup < best_target_tuple:
                    best_target_tuple = tup
                    matched_vs_target = bool(m)
                    dist_vs_target = d
                    rel_vs_target = r
                    phon_vs_target = bool(p)

        # If no target was supplied, fall back to best_guess match info (older behaviour)
        final_matched = matched_vs_target if matched_vs_target is not None else (bool(best_guess_matched) if vocab_list else None)

        return {
            "text": text,
            "matched": final_matched,

            "best_guess": best_guess,
            "distance": dist_vs_target if dist_vs_target is not None else (best_dist if vocab_list else None),
            "rel_distance": rel_vs_target if rel_vs_target is not None else (best_rel if vocab_list else None),
            "phonetic_used": phon_vs_target if phon_vs_target is not None else (bool(best_phon) if vocab_list else None),

            # echoes (debug)
            "target": target,
            "attempt_num": attempt_num,
            "cue_shown": bool(cue_shown) if cue_shown is not None else None,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Transcription failed: {str(e)}")

    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

# ------------------ OPTIONAL GPT FEEDBACK ------------------

@app.post("/api/speech-feedback")
async def speech_feedback(userText: str = File(...), correctAnswer: str = File(...)):
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a speech therapist."},
                {"role": "user", "content": f"I said: {userText}. The correct word was: {correctAnswer}. How did I do?"},
            ],
        )
        return {"feedback": response.choices[0].message.content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"GPT feedback failed: {str(e)}")

# ------------------ TECHNIQUE STATS ------------------

@app.get("/api/technique/{technique_id}")
async def get_technique_stats(
    technique_id: str,
    clerk_user_id: str = Depends(require_clerk_user_id),
):
    user = await get_or_create_user(clerk_user_id)
    stats = await UserTechniqueStats.get_or_none(user=user, technique_id=technique_id)

    if not stats:
        return {
            "technique_id": technique_id,
            # legacy
            "total_exp": 0,
            "best_score": 0,
            "attempts": 0,
            # new
            "total_points": 0,
            "best_session_points": 0,
            "sessions_played": 0,
            "total_items": 0,
            "total_attempts": 0,
            "correct_first": 0,
            "correct_second": 0,
            "correct_after_cue": 0,
            "incorrect": 0,
        }

    return {
        "technique_id": stats.technique_id,
        # legacy
        "total_exp": stats.total_exp,
        "best_score": stats.best_score,
        "attempts": stats.attempts,
        # new
        "total_points": stats.total_points,
        "best_session_points": stats.best_session_points,
        "sessions_played": stats.sessions_played,
        "total_items": stats.total_items,
        "total_attempts": stats.total_attempts,
        "correct_first": stats.correct_first,
        "correct_second": stats.correct_second,
        "correct_after_cue": stats.correct_after_cue,
        "incorrect": stats.incorrect,
    }

# ------------------ DATABASE ------------------

register_tortoise(
    app,
    db_url="sqlite://database.sqlite3",
    modules={"models": ["models"]},
    generate_schemas=True,
    add_exception_handlers=True,
)