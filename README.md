# ReLingo -- AI-Assisted Speech Therapy for Mild Aphasia

**Project category: university dissertation.** This is my solo academic Master's project, kept separate from Scrimba coursework, independent coding practice and AI-assisted development experiments. See [project context](PROJECT_CONTEXT.md) for its scope and the repository index.

ReLingo is an **AI-assisted speech therapy application designed for
individuals with mild aphasia**. It provides structured, personalised
therapy exercises that users can complete **independently without
requiring a therapist during the session**.

The system uses **Large Language Models (LLMs)** and **AI-generated
content** to dynamically create personalised therapy material, helping
users practise language recovery exercises tailored to their needs.

This project was developed as a **solo Master's project** in Computer
Science.

------------------------------------------------------------------------

# Overview

ReLingo allows users to practise clinically inspired aphasia therapy
techniques through an interactive web application.

The system provides:

-   Personalised therapy exercises generated with AI
-   Speech recognition based evaluation
-   Progress tracking and performance analytics
-   Adaptive content generation
-   AI image generation for therapy stimuli

The goal is to allow individuals with mild aphasia to perform **regular
structured therapy sessions independently**, reinforcing language
recovery outside clinical settings.
The system integrates AI generation, speech evaluation, and adaptive difficulty into a unified therapy pipeline.

------------------------------------------------------------------------

# Key Features

## Personalised Therapy

The system generates personalised therapy content based on the user
profile and history.

LLMs are used to create new therapy items such as:

-   vocabulary targets
-   sentences
-   semantic prompts
-   contextual hints

This allows the therapy material to evolve with the user's progress.

------------------------------------------------------------------------

## Speech Evaluation

Users respond verbally to therapy prompts.

Speech is processed using **OpenAI Whisper**, which provides:

-   speech-to-text transcription
-   robust recognition for imperfect pronunciation

The system then evaluates the response using fuzzy matching and phonetic
comparison to determine correctness.

------------------------------------------------------------------------

# Therapy Techniques

## Image Naming

Users are shown an image and must name the object.

Purpose: - lexical retrieval practice - object naming

## Naming with Cues

Users attempt to name an image but receive phonemic or semantic hints if
they struggle.

Purpose: - cue-assisted word retrieval

## Sentence Repetition

Users read or hear a sentence and repeat it.

Purpose: - speech production - sentence construction - working memory

## Semantic Feature Analysis (SFA)

Users analyse semantic properties of an object before naming it.

Purpose: - strengthen semantic networks - improve word retrieval

## Action Verb Naming

Users identify the **action occurring in an image**.

Purpose: - verb retrieval - action recognition

------------------------------------------------------------------------

# AI Integration

### Large Language Models

Used to generate: - therapy content - vocabulary prompts - semantic
hints - sentences

### Whisper Speech Recognition

Used to transcribe spoken responses and evaluate answers.

### Optional AI Image Generation

Images can be generated dynamically using:

-   OpenAI image generation
-   ComfyUI (local Stable Diffusion)

------------------------------------------------------------------------


# Evaluation Pipeline

ReLingo uses a multi-stage evaluation pipeline:

1. Speech transcription using Whisper
2. Transcript cleaning and normalisation
3. Fuzzy matching using:
   - Levenshtein distance
   - Double Metaphone phonetic encoding
   - Morphological tolerance
4. Scoring based on attempt accuracy and cue usage

This design ensures that **clinically valid near-correct responses are accepted**, rather than relying on strict exact matching.

------------------------------------------------------------------------

# Technology Stack

Frontend - React - Clerk authentication - Web Speech API (TTS) -
MediaRecorder API

Backend - FastAPI - Python - SQLite database - Tortoise ORM

AI / ML - OpenAI API - Whisper speech recognition - ComfyUI (
local image generation)

------------------------------------------------------------------------

# Installation Guide

## 1. Clone the Repository

git clone https://github.com/yourusername/relingo.git cd relingo

------------------------------------------------------------------------

# Backend Setup

cd backend

Create virtual environment:

python -m venv venv

Activate:

Windows: venv\Scripts\activate

Mac/Linux: source venv/bin/activate

Install dependencies:

pip install -r requirements.txt

------------------------------------------------------------------------

# Environment Variables

Create a `.env` file in the backend folder:

OPENAI_API_KEY=your_openai_api_key IMAGE_GEN_MODE=local
COMFYUI_URL=http://127.0.0.1:8188

------------------------------------------------------------------------

# Running the Backend

uvicorn server:app --reload

Backend runs at: http://localhost:8000

------------------------------------------------------------------------

# Frontend Setup

cd frontend/stroke-recovery

npm install

npm start

Frontend runs at: http://localhost:3000

------------------------------------------------------------------------

# Setting up ComfyUI (Optional)

Clone:

git clone https://github.com/comfyanonymous/ComfyUI cd ComfyUI

Install dependencies:

pip install -r requirements.txt

Download a Stable Diffusion checkpoint and place it in:

ComfyUI/models/checkpoints/

Run:

python main.py

ComfyUI runs on: http://127.0.0.1:8188

------------------------------------------------------------------------

# Therapy Content Generation

When generating therapy packs the system:

1.  Checks curated therapy image catalog
2.  Attempts dataset match
3.  Generates image if none exists

Images are stored in:

frontend/stroke-recovery/public/generated/

------------------------------------------------------------------------

# Progress Tracking

ReLingo tracks:

-   session scores
-   attempt accuracy
-   cue usage
-   item difficulty
-   progress trends

------------------------------------------------------------------------

# Accessibility Considerations

Designed for mild aphasia users:

-   simple prompts
-   minimal cognitive load
-   optional voice feedback
-   repeatable exercises
-   supportive evaluation language

------------------------------------------------------------------------

# Limitations

-   Not a replacement for professional therapy
-   Designed primarily for mild aphasia
-   Speech recognition accuracy may vary

------------------------------------------------------------------------

# Author

Karam-e-Abbas Ilyas\
MEng Computer Science / Software Engineering\
University of Birmingham

This project explores **AI-assisted independent speech therapy tools
using LLMs and speech recognition**.
