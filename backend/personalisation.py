# backend/personalisation.py
import json
import os
import random
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from openai import OpenAI


# =========================================================
# Paths / catalog loading
# =========================================================

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
PUBLIC_DIR = PROJECT_ROOT / "frontend" / "stroke-recovery" / "public"
CATALOG_PATH = PUBLIC_DIR / "image_catalog.json"


def load_image_catalog() -> list[dict]:
    if not CATALOG_PATH.exists():
        return []
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


# =========================================================
# Normalisation helpers
# =========================================================

def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    replacements = {
        "é": "e", "è": "e", "ê": "e", "ë": "e",
        "à": "a", "â": "a", "ä": "a",
        "î": "i", "ï": "i",
        "ô": "o", "ö": "o",
        "ù": "u", "û": "u", "ü": "u",
        "ç": "c",
        "'": " ",
        "-": " ",
    }
    for old, new in replacements.items():
        s = s.replace(old, new)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _clean_word(w: str) -> str:
    w = (w or "").strip().lower()
    w = re.sub(r"[^a-z]", "", w)
    return w


def _clean_token(w: str) -> str:
    w = (w or "").strip().lower()
    w = re.sub(r"[^a-z]", "", w)
    return w


def _clean_sentence(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^A-Za-z0-9\s\.,'!?-]", "", s)
    return s.strip()


def _ensure_list_of_tokens(x: Any, max_len: int = 8) -> list[str]:
    if not isinstance(x, list):
        return []
    out: list[str] = []
    for item in x:
        t = _clean_token(str(item))
        if t:
            out.append(t)
        if len(out) >= max_len:
            break
    return out


def _ensure_list_of_tags(x: Any, max_len: int = 8) -> list[str]:
    if not isinstance(x, list):
        return []
    out: list[str] = []
    for item in x:
        t = _norm(str(item))
        if t:
            out.append(t)
        if len(out) >= max_len:
            break
    return out


def _extract_json(text: str) -> dict:
    if not text:
        raise ValueError("Empty LLM response")

    text = text.strip()

    if text.startswith("{") and text.endswith("}"):
        return json.loads(text)

    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        raise ValueError("No JSON object found in response")

    return json.loads(m.group(0))


# =========================================================
# Catalog filtering / scoring
# =========================================================

ABSTRACT_IMAGE_NAMING_TERMS = {
    "love", "money", "work", "analysis", "administration", "adaptation", "accessibility",
    "acquisition", "achievement", "activity", "help", "support", "argentina", "afghanistan",
    "germany", "america", "africa", "arabia", "august", "afternoon", "after", "before",
    "year", "years", "address", "adults", "friendship", "agriculture", "alphabet",
    "announcement", "archives", "argent", "acnur",
}


def _catalog_entries_for_technique(
    catalog: list[dict],
    technique_id: str,
    difficulty: int,
) -> list[dict]:
    return [
        it for it in catalog
        if it.get("technique") == technique_id
        and int(it.get("difficulty", 1)) <= int(difficulty)
    ]


def _interest_tokens(text: str) -> set[str]:
    parts = re.split(r"[,;/\n]", text or "")
    return {_norm(x) for x in parts if _norm(x)}


def _combined_tokens(entry: dict) -> set[str]:
    tags = {_norm(t) for t in (entry.get("tags") or [])}
    word = _norm(entry.get("word", ""))
    label = _norm(entry.get("english_label", ""))
    entry_id = _norm(entry.get("id", ""))

    out = set(tags)
    if word:
        out.add(word)
    if label:
        out.add(label)
    if entry_id:
        out.add(entry_id)
    return {x for x in out if x}


def _is_good_image_naming_entry(entry: dict) -> bool:
    word = _clean_word(entry.get("word", ""))
    if not word:
        return False

    if len(word) < 2 or len(word) > 14:
        return False

    if word in ABSTRACT_IMAGE_NAMING_TERMS:
        return False

    tags = _combined_tokens(entry)
    if not tags:
        return False

    return True


def _score_entry_for_profile(entry: dict, interests_text: str, avoid_topics_text: str) -> int:
    interests = _interest_tokens(interests_text)
    avoid = _interest_tokens(avoid_topics_text)

    tokens = _combined_tokens(entry)

    if avoid and tokens & avoid:
        return -999

    score = 0

    if interests:
        score += len(tokens & interests) * 6

    usage_count = int(entry.get("usage_count", 0) or 0)
    score += min(usage_count, 5)

    if entry.get("technique") == "image_naming" and _is_good_image_naming_entry(entry):
        score += 2

    if entry.get("english_label"):
        score += 1

    return score


def _select_catalog_entries(
    catalog: list[dict],
    technique_id: str,
    difficulty: int,
    interests_text: str,
    avoid_topics_text: str,
    n_items: int,
    oversample: int = 4,
) -> list[dict]:
    entries = _catalog_entries_for_technique(catalog, technique_id, difficulty)

    scored: list[tuple[int, float, dict]] = []
    for it in entries:
        if technique_id == "image_naming" and not _is_good_image_naming_entry(it):
            continue

        score = _score_entry_for_profile(it, interests_text, avoid_topics_text)
        if score <= -999:
            continue

        scored.append((score, random.random(), it))

    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)

    chosen = []
    used_ids = set()
    used_files = set()

    for _, _, it in scored:
        entry_id = it.get("id")
        file_path = it.get("file")

        if not entry_id or not file_path:
            continue
        if entry_id in used_ids or file_path in used_files:
            continue

        chosen.append(it)
        used_ids.add(entry_id)
        used_files.add(file_path)

        if len(chosen) >= max(n_items * oversample, n_items):
            break

    if len(chosen) <= n_items:
        return chosen[:n_items]

    top = chosen[: max(n_items * oversample, n_items)]
    random.shuffle(top)
    return top[:n_items]


# =========================================================
# Image naming: choose image-backed items first
# =========================================================

def build_image_naming_pack_from_catalog(profile: dict, catalog: list[dict], n_items: int) -> list[dict]:
    difficulty = int(profile.get("difficulty", 1))

    selected = _select_catalog_entries(
        catalog=catalog,
        technique_id="image_naming",
        difficulty=difficulty,
        interests_text=profile.get("interests_text", ""),
        avoid_topics_text=profile.get("avoid_topics_text", ""),
        n_items=n_items,
        oversample=5,
    )

    out = []
    used_words = set()

    for it in selected:
        word = _clean_word(it.get("word", ""))
        image = it.get("file")
        if not word or not image:
            continue
        if word in used_words:
            continue

        used_words.add(word)
        out.append({
            "word": word,
            "image": image,
            "tags": it.get("tags", []),
            "source": "catalog",
            "catalog_id": it.get("id"),
        })

    return out[:n_items]


# =========================================================
# Sentence repetition: choose scene first, then write sentence
# =========================================================

def build_sentence_prompt_from_selected_scenes(profile: dict, selected_scenes: list[dict]) -> str:
    difficulty = int(profile.get("difficulty", 1))
    sentence_length = int(profile.get("sentence_length", 1))
    interests = (profile.get("interests_text", "") or "").strip()
    avoid = (profile.get("avoid_topics_text") or "").strip()

    scene_lines = []
    for idx, scene in enumerate(selected_scenes, start=1):
        label = scene.get("english_label") or scene.get("id") or "scene"
        tags = ", ".join(scene.get("tags", [])[:8])
        scene_lines.append(f'{idx}. label="{label}" tags=[{tags}]')

    length_rule = {
        1: "short (4-6 words)",
        2: "medium (7-10 words)",
        3: "long (11-14 words)",
    }[max(1, min(3, sentence_length))]

    joined = "\n".join(scene_lines)

    return f"""
Return STRICT JSON ONLY (no markdown, no commentary, no extra keys).

You are generating sentence-repetition therapy items for a stroke-recovery app.

User interests/routine: {interests}
Avoid topics: {avoid}
Target difficulty: {difficulty}
Sentence length target: {length_rule}

Write exactly ONE sentence for each provided scene.
The sentence MUST clearly match that specific scene.
Do not invent unrelated contexts.

Scenes:
{joined}

Schema:
{{
  "items": [
    {{
      "scene_index": 1,
      "sentence": "string",
      "required_content": ["noun_or_verb1","noun_or_verb2"],
      "required_function": ["function1","function2"],
      "tags": ["tag1","tag2","tag3"]
    }}
  ]
}}

Rules:
- One item per scene_index.
- Sentences must be easy to picture and clinically simple.
- Prefer everyday actions and concrete scenes.
- required_content = key nouns/verbs only.
- required_function = helper/grammar words like the/a/is/to/in/on/and.
- tags should stay close to the given scene.
- Output JSON only.
""".strip()


def _generate_sentence_items_for_selected_scenes(profile: dict, selected_scenes: list[dict]) -> list[dict]:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    prompt = build_sentence_prompt_from_selected_scenes(profile, selected_scenes)

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Return only strict JSON. No commentary. No markdown."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.4,
    )

    text = (resp.choices[0].message.content or "").strip()
    data = _extract_json(text)

    items = data.get("items")
    if not isinstance(items, list):
        raise ValueError("LLM returned invalid sentence scene items")

    return items


def build_sentence_repetition_pack_from_catalog(profile: dict, catalog: list[dict], n_items: int) -> list[dict]:
    difficulty = int(profile.get("difficulty", 1))

    selected = _select_catalog_entries(
        catalog=catalog,
        technique_id="sentence_repetition",
        difficulty=difficulty,
        interests_text=profile.get("interests_text", ""),
        avoid_topics_text=profile.get("avoid_topics_text", ""),
        n_items=n_items,
        oversample=4,
    )

    if not selected:
        return []

    raw_items = _generate_sentence_items_for_selected_scenes(profile, selected)

    by_index: dict[int, dict] = {}
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        idx = raw.get("scene_index")
        if isinstance(idx, int):
            by_index[idx] = raw

    out = []
    used_files = set()

    for idx, scene in enumerate(selected, start=1):
        image = scene.get("file")
        if not image or image in used_files:
            continue

        raw = by_index.get(idx, {})
        sentence = _clean_sentence(raw.get("sentence", ""))
        required_content = _ensure_list_of_tokens(raw.get("required_content", []), max_len=6)
        required_function = _ensure_list_of_tokens(raw.get("required_function", []), max_len=6)
        model_tags = _ensure_list_of_tags(raw.get("tags", []), max_len=8)

        scene_label = (scene.get("english_label") or scene.get("id") or "The person is doing an activity").replace("_", " ")
        scene_tags = scene.get("tags", []) or []

        if not sentence:
            sentence = scene_label.strip().capitalize()
            if sentence and not sentence.endswith("."):
                sentence += "."

        if not required_content:
            required_content = [_clean_token(t) for t in scene_tags[:3] if _clean_token(t)]

        if not required_function:
            required_function = ["the"]

        final_tags = model_tags or scene_tags

        used_files.add(image)
        out.append({
            "sentence": sentence,
            "image": image,
            "required_content": required_content,
            "required_function": required_function,
            "tags": final_tags,
            "source": "catalog",
            "catalog_id": scene.get("id"),
        })

    return out[:n_items]


# =========================================================
# Main pack builder
# =========================================================

def build_content_pack(profile: dict, technique_id: str, n_items: int = 10) -> dict[str, Any]:
    difficulty = int(profile.get("difficulty", 1))
    catalog = load_image_catalog()

    if technique_id == "image_naming":
        final_items = build_image_naming_pack_from_catalog(profile, catalog, n_items)
    elif technique_id == "sentence_repetition":
        final_items = build_sentence_repetition_pack_from_catalog(profile, catalog, n_items)
    else:
        raise ValueError(f"Unknown technique_id: {technique_id}")

    return {
        "pack_id": uuid.uuid4().hex,
        "technique_id": technique_id,
        "difficulty": difficulty,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "items": final_items,
    }