from fastapi import FastAPI, UploadFile, File, HTTPException, Depends, Form
from fastapi.middleware.cors import CORSMiddleware
from tortoise.contrib.fastapi import register_tortoise
from models import User
from schemas import UserOut
from metaphone import doublemetaphone


import whisper
import shutil
import os
from dotenv import load_dotenv
from datetime import date, timedelta
from models import User, UserTechniqueStats


import openai
from pydantic import BaseModel

# ✅ NEW: import JWT verifier dependency
from auth_clerk import require_clerk_user_id

import re

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

    # edit tolerance
    if dist <= allowed_distance(max_len) or rel <= 0.25:
        return (True, dist, rel, False)

    # phonetic tolerance
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


# ------------------ USER/STATS ROUTES (SECURE) ------------------

@app.get("/api/me", response_model=UserOut)
async def me(clerk_user_id: str = Depends(require_clerk_user_id)):
    user = await get_or_create_user(clerk_user_id)
    return UserOut(
        clerk_user_id=user.clerk_user_id,
        email=user.email,
        total_exp=user.total_exp,
        best_daily_score=user.best_daily_score,
        streak=user.streak,
        last_active_date=user.last_active_date,
    )


class ScorePayload(BaseModel):
    technique_id: str  # e.g. "image_naming"
    delta_score: int
    delta_exp: int | None = None



@app.post("/api/score")
async def update_score(payload: ScorePayload, clerk_user_id: str = Depends(require_clerk_user_id)):
    user = await get_or_create_user(clerk_user_id)

    # ---- global streak logic (unchanged) ----
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    if user.last_active_date == today:
        pass
    elif user.last_active_date == yesterday:
        user.streak = (user.streak or 0) + 1
    else:
        user.streak = 1

    user.last_active_date = today

    # ---- global exp ----
    gained_exp = max(0, int(payload.delta_score))
    user.total_exp += gained_exp
    user.best_daily_score = max(user.best_daily_score, int(payload.delta_score))
    await user.save()

    # ---- per-technique stats ----
    stats = await UserTechniqueStats.get_or_none(user=user, technique_id=payload.technique_id)
    if not stats:
        stats = await UserTechniqueStats.create(
            user=user,
            technique_id=payload.technique_id,
            total_exp=0,
            best_score=0,
            attempts=0,
        )

    stats.attempts += 1
    stats.total_exp += gained_exp
    stats.best_score = max(stats.best_score, int(payload.delta_score))
    await stats.save()

    return {
        "global": {
            "total_exp": user.total_exp,
            "best_daily_score": user.best_daily_score,
            "streak": user.streak,
            "last_active_date": user.last_active_date,
        },
        "technique": {
            "technique_id": stats.technique_id,
            "total_exp": stats.total_exp,
            "best_score": stats.best_score,
            "attempts": stats.attempts,
        },
    }


# ------------------ LOCAL WHISPER ENDPOINT ------------------

@app.post("/api/speech-to-text")
async def speech_to_text(
    audio: UploadFile = File(...),
    vocab: str | None = Form(default=None),  # comma-separated session words
):
    temp_path = f"temp_audio/{audio.filename}"
    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(audio.file, buffer)

        kwargs = {
            "language": "en",
            "task": "transcribe",
            "temperature": 0,
        }

        vocab_list: list[str] = []
        if vocab:
            vocab_list = [v.strip() for v in vocab.split(",") if v.strip()]
            vocab_list = vocab_list[:50]
            if vocab_list:
                kwargs["initial_prompt"] = (
                    "This is a single-word naming exercise. "
                    "The user will say one of these words: " + ", ".join(vocab_list) + "."
                )

        result = whisper_model.transcribe(temp_path, **kwargs)
        text = (result.get("text") or "").strip()

        # If no vocab provided, just return transcript
        if not vocab_list:
            return {"text": text, "matched": None, "best_guess": None}

        # Find best target in vocab_list using our tolerant matcher
        best = None
        best_dist = 10**9
        best_rel = 1.0
        best_phon = False
        best_matched = False

        # compare against the transcript as a whole + tokens
        transcript_norm = normalize_word(text)
        tokens = transcript_norm.split()

        candidates = [transcript_norm] + tokens

        for target in vocab_list:
            target_norm = normalize_word(target)
            # try matching any candidate (whole transcript or token)
            for cand in candidates:
                matched, dist, rel, phon = is_match_phonetic(cand, target_norm)

                # choose best by: matched first, then lower rel, then lower dist
                score_tuple = (
                    0 if matched else 1,
                    rel,
                    dist,
                )

                if best is None:
                    best = target
                    best_dist = dist
                    best_rel = rel
                    best_phon = phon
                    best_matched = matched
                    best_score = score_tuple
                else:
                    if score_tuple < best_score:
                        best = target
                        best_dist = dist
                        best_rel = rel
                        best_phon = phon
                        best_matched = matched
                        best_score = score_tuple

        return {
            "text": text,
            "best_guess": best,          # which vocab word it thinks is closest
            "matched": bool(best_matched),# whether it counts as correct match
            "distance": best_dist,
            "rel_distance": best_rel,
            "phonetic_used": bool(best_phon),
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
    

@app.get("/api/technique/{technique_id}")
async def get_technique_stats(
    technique_id: str,
    clerk_user_id: str = Depends(require_clerk_user_id),
):
    user = await get_or_create_user(clerk_user_id)

    stats = await UserTechniqueStats.get_or_none(user=user, technique_id=technique_id)
    if not stats:
        # return zeros if never played
        return {
            "technique_id": technique_id,
            "total_exp": 0,
            "best_score": 0,
            "attempts": 0,
        }

    return {
        "technique_id": stats.technique_id,
        "total_exp": stats.total_exp,
        "best_score": stats.best_score,
        "attempts": stats.attempts,
    }



# ------------------ DATABASE ------------------

register_tortoise(
    app,
    db_url="sqlite://database.sqlite3",
    modules={"models": ["models"]},
    generate_schemas=True,
    add_exception_handlers=True,
)
