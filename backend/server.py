import os
from dotenv import load_dotenv
from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Form
from fastapi.middleware.cors import CORSMiddleware
from tortoise.contrib.fastapi import register_tortoise
from metaphone import doublemetaphone

import whisper
import shutil
from datetime import date, timedelta, datetime
import re
import uuid

from openai import OpenAI
from pydantic import BaseModel

from auth_clerk import require_clerk_user_id
from models import (
    User,
    UserTechniqueStats,
    UserDailyStats,
    UserProfile,
    UserContentPack,
)
from schemas import UserOut, UserProfileIn, UserProfileOut, ContentPackOut

# NEW: use your real pack builder
from personalisation import build_content_pack
# ------------------ ENV + KEYS ------------------

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

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

    # keep your tolerance as-is (you can bump rel to 0.30 later if needed)
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

async def get_or_create_profile(user: User) -> UserProfile:
    prof = await UserProfile.get_or_none(user=user)
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
    
# ------------------ PERSONALISATION ------------------

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
        updated_at=prof.updated_at,
    )


@app.post("/api/personalisation", response_model=UserProfileOut)
async def save_personalisation(
    payload: UserProfileIn,
    clerk_user_id: str = Depends(require_clerk_user_id),
):
    user = await get_or_create_user(clerk_user_id)
    prof = await get_or_create_profile(user)

    prof.interests_text = payload.interests_text or ""
    prof.avoid_topics_text = payload.avoid_topics_text
    prof.difficulty = max(1, min(3, int(payload.difficulty or 1)))
    prof.sentence_length = max(1, min(3, int(payload.sentence_length or 1)))
    prof.cue_enabled = bool(payload.cue_enabled)
    prof.allow_generated_images = bool(payload.allow_generated_images)

    await prof.save()

    return UserProfileOut(
        interests_text=prof.interests_text or "",
        avoid_topics_text=prof.avoid_topics_text,
        difficulty=prof.difficulty,
        sentence_length=prof.sentence_length,
        cue_enabled=prof.cue_enabled,
        allow_generated_images=prof.allow_generated_images,
        updated_at=prof.updated_at,
    )


class GeneratePackPayload(BaseModel):
    technique_id: str
    n_items: int | None = 10
    # optional override; otherwise use profile difficulty/sentence_length
    difficulty: int | None = None


@app.post("/api/personalisation/generate", response_model=ContentPackOut)
async def generate_personalised_pack(
    payload: GeneratePackPayload,
    clerk_user_id: str = Depends(require_clerk_user_id),
):
    user = await get_or_create_user(clerk_user_id)
    prof = await get_or_create_profile(user)

    technique_id = (payload.technique_id or "").strip()
    if not technique_id:
        raise HTTPException(status_code=400, detail="technique_id is required")

    n_items = int(payload.n_items or 10)
    n_items = max(1, min(25, n_items))  # clamp so you don’t accidentally generate 500 items

    # Build a profile dict for personalisation.py
    profile_dict = {
        "interests_text": prof.interests_text or "",
        "avoid_topics_text": prof.avoid_topics_text or "",
        "difficulty": int(payload.difficulty or prof.difficulty or 1),
        "sentence_length": int(prof.sentence_length or 1),
        "cue_enabled": bool(prof.cue_enabled),
        "allow_generated_images": bool(prof.allow_generated_images),
    }

    try:
        pack =  build_content_pack(profile_dict, technique_id, n_items=n_items)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate pack: {str(e)}")

    # Upsert active pack row for this user+technique
    existing = await UserContentPack.get_or_none(user=user, technique_id=technique_id)
    if existing:
        existing.difficulty = int(pack.get("difficulty") or profile_dict["difficulty"])
        existing.pack_json = pack
        await existing.save()
    else:
        await UserContentPack.create(
            user=user,
            technique_id=technique_id,
            difficulty=int(pack.get("difficulty") or profile_dict["difficulty"]),
            pack_json=pack,
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
    technique_id = (technique_id or "").strip()
    if technique_id not in ("image_naming", "sentence_repetition"):
        raise HTTPException(status_code=400, detail="Unknown technique_id")

    user = await get_or_create_user(clerk_user_id)

    row = await UserContentPack.get_or_none(user=user, technique_id=technique_id)
    if not row or not row.pack_json:
        # frontend should fall back to curated JSON
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

    # ---- daily aggregation ----
    daily = await UserDailyStats.get_or_none(user=user, day=today)
    if not daily:
        daily = await UserDailyStats.create(user=user, day=today, points=0, sessions_played=0)

    daily.points += session_points
    daily.sessions_played += 1
    await daily.save()

    user.best_daily_score = max(_safe_int(user.best_daily_score), _safe_int(daily.points))
    await user.save()

    # ---- per-technique aggregation ----
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

    # legacy mirrors
    stats.attempts += 1
    stats.total_exp += session_points
    stats.best_score = max(_safe_int(stats.best_score), session_points)

    # new fields
    stats.sessions_played += 1
    stats.total_points += session_points
    stats.best_session_points = max(_safe_int(stats.best_session_points), session_points)

    items = payload.items or []
    session_size = payload.session_size or (len(items) if items else 0)

    if session_size:
        stats.total_items += _safe_int(session_size)

    if items:
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

    avg_attempts = None
    if items:
        total_attempts = sum(max(0, _safe_int(it.attemptsUsed)) for it in items)
        avg_attempts = total_attempts / max(1, len(items))

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
# Fix hallucinations: safe prompt + repetition thresholds + no previous text conditioning
# PLUS: short-audio guard + no-speech gating so tiny clips return empty transcript

_PROMPT_LEAK_PHRASES = (
    "the user will say one of these words",
    "single word naming exercise",
    "this is a single word",
)

# Tune these to your recordings
_MIN_WEBM_BYTES = 12000          # below this: likely too short / near-empty
_NO_SPEECH_PROB_CUTOFF = 0.80    # above this: treat as silence

@app.post("/api/speech-to-text")
async def speech_to_text(
    audio: UploadFile = File(...),
    vocab: str | None = Form(default=None),
    target: str | None = Form(default=None),
    attempt_num: int | None = Form(default=None),
    cue_shown: int | None = Form(default=None),
):
    temp_path = f"temp_audio/{uuid.uuid4().hex}.webm"

    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(audio.file, buffer)

        file_size = os.path.getsize(temp_path)
        too_short = file_size < _MIN_WEBM_BYTES

        kwargs = {
            "language": "en",
            "task": "transcribe",
            "temperature": 0,

            # Key hallucination/repetition controls
            "condition_on_previous_text": False,
            "no_speech_threshold": 0.6,
            "logprob_threshold": -1.0,
            "compression_ratio_threshold": 2.4,
        }

        vocab_list: list[str] = []
        if vocab:
            vocab_list = [v.strip() for v in vocab.split(",") if v.strip()][:50]

        # IMPORTANT: disable prompt if recording is too short
        if vocab_list and not too_short:
            kwargs["initial_prompt"] = "Words: " + ", ".join(vocab_list)

        result = whisper_model.transcribe(temp_path, **kwargs)
        text = (result.get("text") or "").strip()

        # Use Whisper no_speech_prob (segment-level) to detect silence/near-silence
        segments = result.get("segments") or []
        max_no_speech_prob = 0.0
        for seg in segments:
            try:
                p = float(seg.get("no_speech_prob") or 0.0)
                if p > max_no_speech_prob:
                    max_no_speech_prob = p
            except Exception:
                pass

        # If audio is tiny OR Whisper thinks it's silence, return empty transcript
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

        # Safety net: drop obvious prompt leakage (treat as no useful speech)
        tnorm = normalize_word(text)
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

        tokens = tnorm.split()
        candidates = [tnorm] + tokens if tnorm else [""]

        # Compute best_guess over vocab (for UI/feedback)
        best_guess = None
        best_dist = 10**9
        best_rel = 1.0
        best_phon = False
        best_guess_matched = False

        if vocab_list and tnorm:
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

        # Determine "matched" vs provided target (clinically correct)
        matched_vs_target = None
        dist_vs_target = None
        rel_vs_target = None
        phon_vs_target = None

        if target and tnorm:
            t_target = normalize_word(target)
            best_target_tuple = None
            for cand in candidates:
                m, d, r, p = is_match_phonetic(cand, t_target)
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

            # echoes/debug
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
            
@app.post("/api/sentence-eval")
async def sentence_eval(
    audio: UploadFile = File(...),
    target_sentence: str = Form(...),
    required_content: str = Form(...),   # comma-separated
    required_function: str = Form(...),  # comma-separated
):
    temp_path = f"temp_audio/{uuid.uuid4().hex}.webm"

    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(audio.file, buffer)

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
        transcript_norm = normalize_word(text)
        transcript_tokens = transcript_norm.split()

        target_norm = normalize_word(target_sentence)

        content_list = [normalize_word(w) for w in required_content.split(",")]
        function_list = [normalize_word(w) for w in required_function.split(",")]

        # Count content hits
        content_hits = sum(
            1 for w in content_list
            if any(is_match_phonetic(tok, w)[0] for tok in transcript_tokens)
        )

        # Count function hits
        function_hits = sum(
            1 for w in function_list
            if w in transcript_tokens
        )

        # Sentence-level similarity
        dist = levenshtein(transcript_norm, target_norm)
        max_len = max(len(transcript_norm), len(target_norm))
        rel_distance = dist / max_len if max_len else 1.0

        # Scoring tier
        if content_hits == len(content_list) and rel_distance <= 0.25:
            score_level = 3
        elif content_hits == len(content_list):
            score_level = 2
        elif content_hits >= max(1, len(content_list) - 1):
            score_level = 1
        else:
            score_level = 0

        return {
            "text": text,
            "content_hits": content_hits,
            "function_hits": function_hits,
            "rel_distance": rel_distance,
            "score_level": score_level,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

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