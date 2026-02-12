from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from tortoise.contrib.fastapi import register_tortoise
from models import user_pydanticIn, user_pydantic, User
import whisper
import shutil
import os
from dotenv import load_dotenv
import openai

# Load environment variables (for GPT if needed)
load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")

# Load Whisper model once
whisper_model = whisper.load_model("base")

# Ensure temp folder exists
if not os.path.exists("temp_audio"):
    os.makedirs("temp_audio")


app = FastAPI()

# Allow frontend access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

@app.get("/")
def index():
    return {"message": "Welcome to ReLingo API – see /docs for Swagger UI"}

# ------------------ USER ROUTES ------------------

@app.post("/user")
async def add_user(user_info: user_pydanticIn):
    user_obj = await User.create(**user_info.dict(exclude_unset=True))
    response = await user_pydantic.from_tortoise_orm(user_obj)
    return {"status": "ok", "data": response}

@app.get("/user")
async def get_all_users():
    response = await user_pydantic.from_queryset(User.all())
    return {"status": "ok", "data": response}

@app.get("/user/{id}")
async def get_specific_user(id: int):
    user_obj = await User.get(id=id)
    response = await user_pydantic.from_tortoise_orm(user_obj)
    return {"status": "ok", "data": response}

@app.put("/user/{id}")
async def update_user(id: int, update_info: user_pydanticIn):
    user_obj = await User.get(id=id)
    update_info = update_info.dict(exclude_unset=True)
    user_obj.name = update_info["name"]
    user_obj.best_score = max(update_info["best_score"], user_obj.best_score)
    user_obj.total_exp += update_info["score"]
    await user_obj.save()
    response = await user_pydantic.from_tortoise_orm(user_obj)
    return {"status": "ok", "data": response}

@app.delete("/user/{id}")
async def delete_user(id: int):
    await User.filter(id=id).delete()
    return {"status": "ok"}

# ------------------ LOCAL WHISPER ENDPOINT ------------------

@app.post("/api/speech-to-text")
async def speech_to_text(audio: UploadFile = File(...)):
    temp_path = f"temp_audio/{audio.filename}"
    try:
        with open(temp_path, "wb") as buffer:
            shutil.copyfileobj(audio.file, buffer)

        result = whisper_model.transcribe(temp_path)
        print(f"[INFO] Received audio file: {audio.filename}")
        print(f"[INFO] Saved to temp path: {temp_path}")
        print(f"[INFO] Whisper result: {result['text']}")

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
                {"role": "user", "content": f"I said: {userText}. The correct word was: {correctAnswer}. How did I do?"}
            ]
        )
        return {"feedback": response.choices[0].message.content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"GPT feedback failed: {str(e)}")

# ------------------ DATABASE ------------------

register_tortoise(
    app,
    db_url="sqlite://database.sqlite3",
    modules={"models": ["models"]},
    generate_schemas=True,
    add_exception_handlers=True
)
