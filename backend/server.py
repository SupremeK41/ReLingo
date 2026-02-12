from fastapi import FastAPI, UploadFile, File, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from tortoise.contrib.fastapi import register_tortoise
from models import User
from schemas import UserOut

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
async def speech_to_text(audio: UploadFile = File(...)):
    temp_path = f"temp_audio/{audio.filename}"
    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(audio.file, buffer)

        result = whisper_model.transcribe(temp_path)
        return {"text": result["text"]}

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
