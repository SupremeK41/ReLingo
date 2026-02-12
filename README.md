# ReLingo — Stroke Speech Recovery Web App (Mild Aphasia)

ReLingo is a web-based speech therapy support app designed to help stroke survivors with **mild aphasia** practice word retrieval and speech production through short, repeatable exercises.  
The current prototype focuses on a core evidence-based technique: **confrontation naming** (“Say what you see”) using image prompts and speech-to-text.

## Features (Current)
- **Naming practice (“Say what you see”)**: image shown → user speaks → system checks correctness
- **Local speech-to-text** using **OpenAI Whisper (run locally)** for transcription
- Optional (in progress): **AI feedback** (GPT) for supportive prompts and error-aware feedback
- Backend API via **FastAPI**
- Database via **SQLite** (Tortoise ORM)

## Tech Stack
**Frontend:** React.js  
**Backend:** Python FastAPI  
**Database:** SQLite + Tortoise ORM  
**Speech-to-Text:** Local Whisper (`openai-whisper`)  
**Optional LLM:** OpenAI API (for feedback only; not required for transcription)

---

## Project Structure
ReLingo/
backend/
server.py
models.py
requirements.txt
database.sqlite3 (generated locally)
frontend/
src/
public/
README.md
.gitignore

---

## Setup Instructions

### 1) Backend (FastAPI)
Open a terminal in the `backend/` folder.

#### Create and activate a virtual environment
**Windows (CMD):**
```bash
python -m venv .venv
.\.venv\Scripts\activate

Install dependencies
pip install -r requirements.txt

Run the backend
python -m uvicorn server:app --reload

2) Frontend (React)

Open a second terminal in the frontend/ folder.

Install dependencies
npm install

Run the frontend
npm start

Environment Variables

Create a .env file locally (do not commit it). Example:

OPENAI_API_KEY=your_key_here


Note: Whisper transcription is local. The OpenAI API key is only needed if using GPT feedback endpoints.

API Endpoints (Key)

POST /api/speech-to-text
Upload audio → returns transcribed text (local Whisper)

POST /user
Create a user (prototype)

GET /user
Get all users

More endpoints (score tracking, streaks, sessions) will be added as development progresses.

Therapy Technique Implemented (Prototype)
Confrontation Naming (Word Retrieval)

The user is shown a picture and asked to name it aloud.
This targets lexical retrieval and spoken word production, which are commonly affected in mild aphasia.

Planned additions:

semantic cueing (category / function prompts)

phonemic cueing (first sound/letters)

session scoring + longitudinal progress tracking