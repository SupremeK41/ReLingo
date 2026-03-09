# backend/personalisation.py
import json
import os
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
    """
    Loads the unified exported catalog from frontend public.
    """
    if not CATALOG_PATH.exists():
        return []
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


# =========================================================
# Normalisation helpers
# =========================================================

def _norm(s: str) -> str:
    """
    General normalisation for matching/comparison.
    Preserves spaces between words.
    """
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
        "_": " ",
    }
    for old, new in replacements.items():
        s = s.replace(old, new)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _clean_display_word(w: str) -> str:
    """
    Clean word/phrase for display and storage.
    IMPORTANT: preserves spaces, so 'mixed skewer' stays 'mixed skewer'.
    """
    w = _norm(w)
    w = re.sub(r"[^a-z\s]", " ", w)
    w = re.sub(r"\s+", " ", w).strip()
    return w


def _clean_token(w: str) -> str:
    """
    Clean single token for evaluation/comparison lists.
    """
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


def _image_field(it: dict) -> str | None:
    """
    Supports either 'image' or legacy 'file' catalog fields.
    """
    return it.get("image") or it.get("file")


# =========================================================
# Catalog querying
# =========================================================

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


def _filter_catalog_by_interests(
    entries: list[dict],
    interests_text: str,
    avoid_topics_text: str,
) -> tuple[list[dict], list[dict]]:
    """
    Returns:
      preferred_entries, general_entries

    preferred_entries = entries with overlap to user interests
    general_entries   = safe fallback pool excluding avoid-topic matches
    """
    interest_tokens = {_norm(x) for x in re.split(r"[,;/\n]", interests_text or "") if _norm(x)}
    avoid_tokens = {_norm(x) for x in re.split(r"[,;/\n]", avoid_topics_text or "") if _norm(x)}

    preferred = []
    general = []

    for it in entries:
        tags = {_norm(t) for t in (it.get("tags") or [])}
        word = _norm(it.get("word", ""))
        label = _norm(it.get("english_label", ""))

        combined = set(tags)
        if word:
            combined.add(word)
        if label:
            combined.add(label)

        if avoid_tokens and combined & avoid_tokens:
            continue

        general.append(it)

        if interest_tokens and combined & interest_tokens:
            preferred.append(it)

    return preferred, general


def _get_image_naming_candidates(
    catalog: list[dict],
    difficulty: int,
    interests_text: str,
    avoid_topics_text: str,
    limit: int = 200,
) -> list[str]:
    entries = _catalog_entries_for_technique(catalog, "image_naming", difficulty)
    preferred, general = _filter_catalog_by_interests(entries, interests_text, avoid_topics_text)

    seen = set()
    words = []

    for pool in (preferred, general):
        for it in pool:
            word = _clean_display_word(it.get("word", ""))
            if not word or word in seen:
                continue
            seen.add(word)
            words.append(word)
            if len(words) >= limit:
                return words

    return words


def _get_sentence_candidates(
    catalog: list[dict],
    difficulty: int,
    interests_text: str,
    avoid_topics_text: str,
    limit: int = 100,
) -> list[dict]:
    entries = _catalog_entries_for_technique(catalog, "sentence_repetition", difficulty)
    preferred, general = _filter_catalog_by_interests(entries, interests_text, avoid_topics_text)

    out = []
    seen = set()

    for pool in (preferred, general):
        for it in pool:
            key = it.get("id")
            if not key or key in seen:
                continue
            seen.add(key)
            out.append({
                "id": it.get("id"),
                "tags": it.get("tags", []),
                "english_label": it.get("english_label"),
                "file": _image_field(it),
            })
            if len(out) >= limit:
                return out

    return out


def _pick_catalog_image(
    catalog: list[dict],
    want_tags: list[str],
    difficulty: int,
    technique_id: str,
    word: str | None = None,
    english_label: str | None = None,
) -> str | None:
    """
    Strategy:
    1) exact word match for image naming
    2) exact english_label match for sentence repetition if provided
    3) best tag overlap
    """
    entries = _catalog_entries_for_technique(catalog, technique_id, difficulty)

    if technique_id == "image_naming" and word:
        w = _norm(word)
        for it in entries:
            if _norm(it.get("word", "")) == w:
                return _image_field(it)

    if technique_id == "sentence_repetition" and english_label:
        lbl = _norm(english_label)
        for it in entries:
            if _norm(it.get("english_label", "")) == lbl:
                return _image_field(it)

    want = {_norm(t) for t in (want_tags or []) if _norm(t)}
    best_file = None
    best_overlap = 0

    for it in entries:
        tags = {_norm(t) for t in (it.get("tags") or [])}
        overlap = len(want & tags)
        if overlap > best_overlap:
            best_overlap = overlap
            best_file = _image_field(it)

    return best_file if best_overlap > 0 else None


# =========================================================
# Strict JSON extraction
# =========================================================

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
# Prompt building
# =========================================================

def build_llm_prompt_for_pack(
    profile: dict,
    technique_id: str,
    n_items: int,
    catalog: list[dict],
) -> str:
    difficulty = int(profile.get("difficulty", 1))
    sentence_length = int(profile.get("sentence_length", 1))
    interests = (profile.get("interests_text", "") or "").strip()
    avoid = (profile.get("avoid_topics_text") or "").strip()
    allow_generated_images = bool(profile.get("allow_generated_images", False))

    if technique_id == "image_naming":
        candidate_words = _get_image_naming_candidates(
            catalog=catalog,
            difficulty=difficulty,
            interests_text=interests,
            avoid_topics_text=avoid,
            limit=250,
        )

        candidate_preview = ", ".join(candidate_words[:120])

        mode_rule = (
            "Prefer these catalog words first, but you may suggest closely related new words if truly useful."
            if allow_generated_images
            else "You MUST choose only from the provided catalog word pool."
        )

        return f"""
Return STRICT JSON ONLY (no markdown, no commentary, no extra keys).

You are generating personalised image-naming therapy items for a stroke-recovery app.

User interests/routine: {interests}
Avoid topics: {avoid}
Target difficulty: {difficulty}

Available curated catalog word pool:
{candidate_preview}

Rules:
- Exactly {n_items} items.
- Each item must follow:
{{
  "word": "string",
  "tags": ["tag1","tag2","tag3"]
}}

- "word" may be a short everyday word or phrase from the catalog.
- Preserve natural spaces between words.
- Prefer concrete, everyday, pictureable words.
- Avoid anything abstract, unsafe, medical, legal, or confusing.
- {mode_rule}
- tags should describe what the image depicts.
- Output JSON only.

Schema:
{{
  "items": [
    {{
      "word": "string",
      "tags": ["tag1","tag2","tag3"]
    }}
  ]
}}
""".strip()

    sentence_candidates = _get_sentence_candidates(
        catalog=catalog,
        difficulty=difficulty,
        interests_text=interests,
        avoid_topics_text=avoid,
        limit=120,
    )

    scene_hint_lines = []
    for c in sentence_candidates[:40]:
        tags = ", ".join(c.get("tags", [])[:6])
        lbl = c.get("english_label") or c.get("id")
        scene_hint_lines.append(f"- {lbl}: {tags}")

    scene_hints = "\n".join(scene_hint_lines)

    length_rule = {
        1: "short (4-6 words)",
        2: "medium (7-10 words)",
        3: "long (11-14 words)",
    }[max(1, min(3, sentence_length))]

    mode_rule = (
        "Prefer these curated scene ideas first, but you may invent a closely related new simple scene if useful."
        if allow_generated_images
        else "Stay close to the curated scene ideas and generate only clearly pictureable everyday scenes."
    )

    return f"""
Return STRICT JSON ONLY (no markdown, no commentary, no extra keys).

You are generating personalised sentence-repetition therapy items for a stroke-recovery app.

User interests/routine: {interests}
Avoid topics: {avoid}
Target difficulty: {difficulty}
Sentence length target: {length_rule}

Curated scene hints:
{scene_hints}

Rules:
- Exactly {n_items} items.
- Sentences must be clear, easy to picture, and everyday.
- Prefer simple action scenes involving people, objects, food, pets, sports, home, travel, hobbies, or routines.
- Avoid abstract, unsafe, upsetting, political, medical, legal, or confusing content.
- {mode_rule}

Each item must follow:
{{
  "sentence": "string",
  "required_content": ["noun_or_verb1","noun_or_verb2"],
  "required_function": ["function1","function2"],
  "tags": ["tag1","tag2","tag3"]
}}

- required_content = key content words only
- required_function = grammar/helper words like the/a/is/to/in/on/and
- tags = imageable scene tags
- Output JSON only.

Schema:
{{
  "items": [
    {{
      "sentence": "string",
      "required_content": ["noun_or_verb1","noun_or_verb2"],
      "required_function": ["function1","function2"],
      "tags": ["tag1","tag2","tag3"]
    }}
  ]
}}
""".strip()


# =========================================================
# LLM generation
# =========================================================

def generate_pack_items_with_llm(
    profile: dict,
    technique_id: str,
    n_items: int,
    catalog: list[dict],
) -> list[dict]:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    prompt = build_llm_prompt_for_pack(profile, technique_id, n_items, catalog)

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Return only strict JSON. No commentary. No markdown."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.5,
    )

    text = (resp.choices[0].message.content or "").strip()
    data = _extract_json(text)

    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("LLM returned invalid items list")

    return items[:n_items]


# =========================================================
# Cleaning / validation
# =========================================================

def _validate_and_clean_items(profile: dict, technique_id: str, items: list[dict], n_items: int) -> list[dict]:
    out: list[dict] = []

    for raw in items:
        if not isinstance(raw, dict):
            continue

        if technique_id == "image_naming":
            word = _clean_display_word(raw.get("word", ""))
            if not word:
                continue

            tags = _ensure_list_of_tags(raw.get("tags", []))
            if word not in tags:
                tags = [word] + tags

            out.append({
                "word": word,
                "tags": tags[:8],
            })

        else:
            sentence = _clean_sentence(raw.get("sentence", ""))
            if not sentence:
                continue

            required_content = _ensure_list_of_tokens(raw.get("required_content", []), max_len=6)
            required_function = _ensure_list_of_tokens(raw.get("required_function", []), max_len=6)
            tags = _ensure_list_of_tags(raw.get("tags", []), max_len=8)

            if len(required_content) < 1:
                continue

            out.append({
                "sentence": sentence,
                "required_content": required_content,
                "required_function": required_function,
                "tags": tags,
            })

        if len(out) >= n_items:
            break

    return out[:n_items]


# =========================================================
# Catalog-backed fallback items
# =========================================================

def _fallback_image_naming_items(
    catalog: list[dict],
    difficulty: int,
    interests_text: str,
    avoid_topics_text: str,
    n_items: int,
) -> list[dict]:
    words = _get_image_naming_candidates(
        catalog, difficulty, interests_text, avoid_topics_text, limit=max(50, n_items * 5)
    )
    out = []
    for w in words[:n_items]:
        img = _pick_catalog_image(catalog, [w], difficulty, "image_naming", word=w)
        if img:
            out.append({"word": w, "image": img, "source": "catalog"})
    return out[:n_items]


def _fallback_sentence_items(
    catalog: list[dict],
    difficulty: int,
    interests_text: str,
    avoid_topics_text: str,
    n_items: int,
) -> list[dict]:
    entries = _get_sentence_candidates(
        catalog, difficulty, interests_text, avoid_topics_text, limit=max(40, n_items * 4)
    )

    out = []
    for it in entries[:n_items]:
        label = (it.get("english_label") or "").strip()
        tags = it.get("tags", [])
        image = it.get("file")
        if not image:
            continue

        sentence = label.replace("_", " ").strip().capitalize()
        if sentence and not sentence.endswith("."):
            sentence += "."

        out.append({
            "sentence": sentence or "The person is doing an activity.",
            "image": image,
            "required_content": [t for t in tags[:3] if _clean_token(t)],
            "required_function": ["the"],
            "source": "catalog",
        })

    return out[:n_items]


# =========================================================
# Image attachment / resolution
# =========================================================

def attach_images_from_catalog(
    items: list[dict],
    catalog: list[dict],
    technique_id: str,
    difficulty: int,
    profile: dict,
) -> list[dict]:
    out: list[dict] = []

    for it in items:
        tags = it.get("tags") or []

        if technique_id == "image_naming":
            img = _pick_catalog_image(
                catalog=catalog,
                want_tags=tags,
                difficulty=difficulty,
                technique_id=technique_id,
                word=it.get("word", ""),
            )
            if img:
                out.append({
                    "word": (it.get("word") or "").strip(),
                    "image": img,
                    "tags": tags,
                    "source": "catalog",
                })

        else:
            english_label = _clean_sentence(it.get("sentence", ""))
            img = _pick_catalog_image(
                catalog=catalog,
                want_tags=tags,
                difficulty=difficulty,
                technique_id=technique_id,
                english_label=english_label,
            )
            if img:
                out.append({
                    "sentence": (it.get("sentence") or "").strip(),
                    "image": img,
                    "required_content": it.get("required_content") or [],
                    "required_function": it.get("required_function") or [],
                    "tags": tags,
                    "source": "catalog",
                })

    # If some items did not resolve, fill with curated fallback items
    if len(out) < len(items):
        missing = len(items) - len(out)
        if technique_id == "image_naming":
            fillers = _fallback_image_naming_items(
                catalog=catalog,
                difficulty=difficulty,
                interests_text=profile.get("interests_text", ""),
                avoid_topics_text=profile.get("avoid_topics_text", ""),
                n_items=missing,
            )
        else:
            fillers = _fallback_sentence_items(
                catalog=catalog,
                difficulty=difficulty,
                interests_text=profile.get("interests_text", ""),
                avoid_topics_text=profile.get("avoid_topics_text", ""),
                n_items=missing,
            )
        out.extend(fillers)

    return out[:len(items)]


# =========================================================
# Main pack builder
# =========================================================

def build_content_pack(profile: dict, technique_id: str, n_items: int = 10) -> dict[str, Any]:
    difficulty = int(profile.get("difficulty", 1))
    catalog = load_image_catalog()

    raw_items = generate_pack_items_with_llm(
        profile=profile,
        technique_id=technique_id,
        n_items=n_items,
        catalog=catalog,
    )

    cleaned = _validate_and_clean_items(profile, technique_id, raw_items, n_items)

    final_items = attach_images_from_catalog(
        items=cleaned,
        catalog=catalog,
        technique_id=technique_id,
        difficulty=difficulty,
        profile=profile,
    )

    return {
        "pack_id": uuid.uuid4().hex,
        "technique_id": technique_id,
        "difficulty": difficulty,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "items": final_items,
    }