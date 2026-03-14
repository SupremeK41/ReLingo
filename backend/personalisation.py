import base64
import hashlib
import json
import os
import re
import uuid
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from openai import OpenAI

from local_image_generation import LocalImageGenerationError, generate_image_via_comfyui
from models import ImageAsset


# =========================================================
# Paths and catalog loading
# =========================================================

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
PUBLIC_DIR = PROJECT_ROOT / "frontend" / "stroke-recovery" / "public"

CATALOG_PATH = PUBLIC_DIR / "therapy_catalog.json"
SFA_CATALOG_PATH = PUBLIC_DIR / "semantic_feature_analysis.json"

GENERATED_DIR = PUBLIC_DIR / "generated"
GENERATED_IMAGE_NAMING_DIR = GENERATED_DIR / "image_naming"
GENERATED_NAMING_WITH_CUES_DIR = GENERATED_DIR / "naming_with_cues"
GENERATED_SENTENCE_DIR = GENERATED_DIR / "sentence_repetition"
GENERATED_SFA_DIR = GENERATED_DIR / "semantic_feature_analysis"

GENERATED_IMAGE_NAMING_DIR.mkdir(parents=True, exist_ok=True)
GENERATED_NAMING_WITH_CUES_DIR.mkdir(parents=True, exist_ok=True)
GENERATED_SENTENCE_DIR.mkdir(parents=True, exist_ok=True)
GENERATED_SFA_DIR.mkdir(parents=True, exist_ok=True)


def load_image_catalog() -> list[dict]:
    if not CATALOG_PATH.exists():
        return []

    raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))

    if isinstance(raw, dict):
        merged: list[dict] = []
        for key in ("image_naming", "naming_with_cues", "sentence_repetition"):
            value = raw.get(key, [])
            if isinstance(value, list):
                merged.extend(item for item in value if isinstance(item, dict))
        return merged

    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]

    return []


def load_semantic_feature_catalog() -> list[dict]:
    if not SFA_CATALOG_PATH.exists():
        return []

    raw = json.loads(SFA_CATALOG_PATH.read_text(encoding="utf-8"))

    if isinstance(raw, dict):
        value = raw.get("semantic_feature_analysis", [])
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        return []

    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]

    return []


# =========================================================
# Text generation config
# =========================================================

def _get_text_generation_provider(profile: dict | None = None) -> str:
    provider = os.getenv("TEXT_GENERATION_PROVIDER", "openai").strip().lower()
    if provider not in {"openai", "local"}:
        return "openai"
    return provider


def _get_local_llm_model() -> str:
    return os.getenv("LOCAL_LLM_MODEL", "gemma3:4b").strip() or "gemma3:4b"


def _get_ollama_base_url() -> str:
    return os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")


# =========================================================
# Normalisation and cleaning helpers
# =========================================================

def _norm(text: str) -> str:
    text = (text or "").strip().lower()

    replacements = {
        "é": "e",
        "è": "e",
        "ê": "e",
        "ë": "e",
        "à": "a",
        "â": "a",
        "ä": "a",
        "î": "i",
        "ï": "i",
        "ô": "o",
        "ö": "o",
        "ù": "u",
        "û": "u",
        "ü": "u",
        "ç": "c",
        "'": " ",
        "-": " ",
        "_": " ",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _clean_display_word(word: str) -> str:
    word = _norm(word)
    word = re.sub(r"[^a-z\s]", " ", word)
    word = re.sub(r"\s+", " ", word).strip()
    return word


def _clean_token(token: str) -> str:
    token = (token or "").strip().lower()
    token = re.sub(r"[^a-z]", "", token)
    return token


def _clean_sentence(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^A-Za-z0-9\s\.,'!?-]", "", text)
    return text.strip()


def _slugify(text: str) -> str:
    text = _norm(text)
    text = text.replace(" ", "_")
    text = re.sub(r"[^a-z0-9_]", "", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or uuid.uuid4().hex[:8]


def _ensure_list_of_tokens(value: Any, max_len: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []

    out: list[str] = []
    for item in value:
        token = _clean_token(str(item))
        if token:
            out.append(token)
        if len(out) >= max_len:
            break

    return out


def _ensure_list_of_tags(value: Any, max_len: int = 8) -> list[str]:
    if not isinstance(value, list):
        return []

    out: list[str] = []
    for item in value:
        tag = _norm(str(item))
        if tag and tag not in out:
            out.append(tag)
        if len(out) >= max_len:
            break

    return out


def _ensure_list_of_strings(
    value: Any,
    max_len: int = 8,
    max_item_len: int = 180,
) -> list[str]:
    if not isinstance(value, list):
        return []

    out: list[str] = []
    for item in value:
        text = re.sub(r"\s+", " ", str(item or "").strip())
        if text:
            out.append(text[:max_item_len])
        if len(out) >= max_len:
            break

    return out


def _image_field(item: dict) -> str | None:
    return item.get("image") or item.get("file")


def _hash_prompt(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _get_image_generation_mode(profile: dict) -> str:
    allow_generated_images = bool(profile.get("allow_generated_images", False))
    mode = str(profile.get("image_generation_mode", "none") or "none").strip().lower()

    if mode not in {"none", "openai", "local"}:
        mode = "none"

    if not allow_generated_images:
        return "none"

    return mode


# =========================================================
# Cue / hint cleaning
# =========================================================

def _clean_semantic_cue(cue: str) -> str:
    cue = re.sub(r"\s+", " ", str(cue or "").strip())
    return cue[:180]


def _clean_phonemic_cue(cue: str) -> str:
    cue = re.sub(r"\s+", " ", str(cue or "").strip())
    return cue[:120]


# =========================================================
# Catalog querying
# =========================================================

def _safe_item_difficulty(item: dict) -> int:
    try:
        return int(item.get("difficulty", 1))
    except Exception:
        return 1


def _catalog_entries_for_technique(
    catalog: list[dict],
    technique_id: str,
    difficulty: int,
) -> list[dict]:
    return [
        item
        for item in catalog
        if item.get("technique") == technique_id
        and _safe_item_difficulty(item) <= int(difficulty)
        and bool(item.get("keep", True))
    ]


def _filter_catalog_by_interests(
    entries: list[dict],
    interests_text: str,
    avoid_topics_text: str,
) -> tuple[list[dict], list[dict]]:
    interest_tokens = {_norm(x) for x in re.split(r"[,;/\n]", interests_text or "") if _norm(x)}
    avoid_tokens = {_norm(x) for x in re.split(r"[,;/\n]", avoid_topics_text or "") if _norm(x)}

    preferred: list[dict] = []
    general: list[dict] = []

    for item in entries:
        tags = {_norm(tag) for tag in (item.get("tags") or [])}
        word = _norm(item.get("word", ""))
        label = _norm(item.get("english_label", ""))

        combined = set(tags)
        if word:
            combined.add(word)
        if label:
            combined.add(label)

        if avoid_tokens and combined & avoid_tokens:
            continue

        general.append(item)

        if interest_tokens and combined & interest_tokens:
            preferred.append(item)

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
    words: list[str] = []

    for pool in (preferred, general):
        for item in pool:
            word = _clean_display_word(item.get("word", ""))
            if not word or word in seen:
                continue
            seen.add(word)
            words.append(word)
            if len(words) >= limit:
                return words

    return words


def _get_naming_with_cues_candidates(
    catalog: list[dict],
    difficulty: int,
    interests_text: str,
    avoid_topics_text: str,
    limit: int = 200,
) -> list[str]:
    cue_entries = _catalog_entries_for_technique(catalog, "naming_with_cues", difficulty)
    image_entries = _catalog_entries_for_technique(catalog, "image_naming", difficulty)

    preferred_cue, general_cue = _filter_catalog_by_interests(
        cue_entries,
        interests_text,
        avoid_topics_text,
    )
    preferred_img, general_img = _filter_catalog_by_interests(
        image_entries,
        interests_text,
        avoid_topics_text,
    )

    seen = set()
    words: list[str] = []

    for pool in (preferred_cue, general_cue, preferred_img, general_img):
        for item in pool:
            word = _clean_display_word(item.get("word", ""))
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

    out: list[dict] = []
    seen = set()

    for pool in (preferred, general):
        for item in pool:
            key = item.get("id")
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "id": item.get("id"),
                    "tags": item.get("tags", []),
                    "english_label": item.get("english_label"),
                    "image": _image_field(item),
                }
            )
            if len(out) >= limit:
                return out

    return out


def _get_semantic_feature_candidates(
    catalog: list[dict],
    difficulty: int,
    interests_text: str,
    avoid_topics_text: str,
    limit: int = 200,
) -> list[dict]:
    entries = _catalog_entries_for_technique(catalog, "semantic_feature_analysis", difficulty)
    preferred, general = _filter_catalog_by_interests(entries, interests_text, avoid_topics_text)

    out: list[dict] = []
    seen = set()

    for pool in (preferred, general):
        for item in pool:
            word = _clean_display_word(item.get("word", ""))
            if not word or word in seen:
                continue
            seen.add(word)
            out.append(item)
            if len(out) >= limit:
                return out

    return out


def _find_catalog_match_by_word(
    entries: list[dict],
    word: str | None = None,
) -> tuple[str | None, dict | None]:
    target = _norm(word or "")
    if not target:
        return None, None

    for item in entries:
        if _norm(item.get("word", "")) == target:
            image = _image_field(item)
            if image:
                return image, item

    for item in entries:
        if _norm(item.get("id", "")) == target:
            image = _image_field(item)
            if image:
                return image, item

    return None, None


def _pick_catalog_image_strict(
    catalog: list[dict],
    difficulty: int,
    technique_id: str,
    word: str | None = None,
    english_label: str | None = None,
) -> tuple[str | None, dict | None]:
    if technique_id == "naming_with_cues":
        cue_entries = _catalog_entries_for_technique(catalog, "naming_with_cues", difficulty)
        image, item = _find_catalog_match_by_word(cue_entries, word=word)
        if image:
            return image, item

        image_entries = _catalog_entries_for_technique(catalog, "image_naming", difficulty)
        image, item = _find_catalog_match_by_word(image_entries, word=word)
        if image:
            return image, item

        return None, None

    if technique_id == "image_naming":
        entries = _catalog_entries_for_technique(catalog, "image_naming", difficulty)
        return _find_catalog_match_by_word(entries, word=word)

    if technique_id == "semantic_feature_analysis":
        entries = _catalog_entries_for_technique(catalog, "semantic_feature_analysis", difficulty)
        image, item = _find_catalog_match_by_word(entries, word=word)
        if image:
            return image, item

        image_entries = _catalog_entries_for_technique(catalog, "image_naming", difficulty)
        image, item = _find_catalog_match_by_word(image_entries, word=word)
        if image:
            return image, item

        cue_entries = _catalog_entries_for_technique(catalog, "naming_with_cues", difficulty)
        image, item = _find_catalog_match_by_word(cue_entries, word=word)
        if image:
            return image, item

        return None, None

    if technique_id == "sentence_repetition" and english_label:
        entries = _catalog_entries_for_technique(catalog, "sentence_repetition", difficulty)
        target = _norm(english_label)

        for item in entries:
            label = _norm(item.get("english_label", ""))
            item_id = _norm(item.get("id", ""))
            if label == target or item_id == target:
                image = _image_field(item)
                if image:
                    return image, item

    return None, None


# =========================================================
# Strict JSON extraction
# =========================================================

def _extract_json(text: str) -> dict:
    if not text:
        raise ValueError("Empty LLM response")

    text = text.strip()

    if text.startswith("{") and text.endswith("}"):
        return json.loads(text)

    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        raise ValueError("No JSON object found in response")

    return json.loads(match.group(0))


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

    if technique_id in {"image_naming", "naming_with_cues"}:
        candidate_words = (
            _get_naming_with_cues_candidates(
                catalog=catalog,
                difficulty=difficulty,
                interests_text=interests,
                avoid_topics_text=avoid,
                limit=250,
            )
            if technique_id == "naming_with_cues"
            else _get_image_naming_candidates(
                catalog=catalog,
                difficulty=difficulty,
                interests_text=interests,
                avoid_topics_text=avoid,
                limit=250,
            )
        )

        candidate_preview = ", ".join(candidate_words[:120])

        mode_rule = (
            "Prefer these catalog words first, but you may suggest new accurate everyday words or short phrases if needed."
            if allow_generated_images
            else "You MUST choose only from the provided catalog word pool."
        )

        if technique_id == "image_naming":
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
  "tags": ["tag1","tag2","tag3"],
  "image_prompt": "string"
}}

- "word" may be a short everyday word or phrase.
- Preserve natural spaces between words.
- Prefer concrete, easy-to-picture items.
- Avoid anything abstract, unsafe, medical, legal, political, upsetting, or confusing.
- image_prompt must clearly describe one simple recognisable image for the target word.
- image_prompt should be suitable for a clean therapy exercise image.
- {mode_rule}
- Output JSON only.

Schema:
{{
  "items": [
    {{
      "word": "string",
      "tags": ["tag1","tag2","tag3"],
      "image_prompt": "string"
    }}
  ]
}}
""".strip()

        return f"""
Return STRICT JSON ONLY (no markdown, no commentary, no extra keys).

You are generating personalised cue-based naming therapy items for a stroke-recovery app.

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
  "semantic_cue": "string",
  "phonemic_cue": "string",
  "full_cue": "string",
  "tags": ["tag1","tag2","tag3"],
  "image_prompt": "string"
}}

- "word" should be a concrete, everyday, easy-to-picture target.
- Prefer common household items, food, transport, animals, hobbies, places, or routine objects.
- Avoid abstract, unsafe, medical, legal, political, upsetting, or confusing targets.
- semantic_cue must help identify the word without directly saying the word itself.
- semantic_cue should sound clinically useful, like a real therapy hint.
- phonemic_cue should give the starting sound, first letter, or first syllable.
- full_cue must be the exact target word.
- image_prompt must clearly describe one simple recognisable image for the target word.
- {mode_rule}
- Output JSON only.

Schema:
{{
  "items": [
    {{
      "word": "string",
      "semantic_cue": "string",
      "phonemic_cue": "string",
      "full_cue": "string",
      "tags": ["tag1","tag2","tag3"],
      "image_prompt": "string"
    }}
  ]
}}
""".strip()

    if technique_id == "semantic_feature_analysis":
        candidates = _get_semantic_feature_candidates(
            catalog=catalog,
            difficulty=difficulty,
            interests_text=interests,
            avoid_topics_text=avoid,
            limit=120,
        )

        candidate_lines = []
        for item in candidates[:50]:
            word = item.get("word") or item.get("id")
            tags = ", ".join((item.get("tags") or [])[:6])
            semantic_hint = item.get("semantic_hint") or ""
            candidate_lines.append(
                f"- {word}: tags={tags}; hint={semantic_hint}"
            )

        candidate_preview = "\n".join(candidate_lines)

        mode_rule = (
            "Prefer these catalog targets first, but you may suggest a closely related everyday target if useful."
            if allow_generated_images
            else "Stay close to the curated target pool."
        )

        return f"""
Return STRICT JSON ONLY (no markdown, no commentary, no extra keys).

You are generating personalised semantic feature analysis therapy items for a stroke-recovery app.

User interests/routine: {interests}
Avoid topics: {avoid}
Target difficulty: {difficulty}

Available curated SFA targets:
{candidate_preview}

Rules:
- Exactly {n_items} items.
- Each item must follow:
{{
  "word": "string",
  "semantic_hint": "string",
  "feature_prompts": [
    "What type of thing is it?",
    "What is it used for or what does it do?",
    "Where might you find it?",
    "What does it look like?",
    "What is it related to?",
    "What actions are linked to it?"
  ],
  "tags": ["tag1","tag2","tag3"],
  "image_prompt": "string"
}}

- The image will show the target item, but the user is encouraged to describe it instead of directly naming it.
- "word" should be a concrete everyday object, food, animal, place, or item that is easy to describe semantically.
- semantic_hint should help the user describe or think about the item without just giving the answer away too bluntly.
- feature_prompts should be short, natural prompts suitable for semantic feature analysis.
- image_prompt must clearly describe one simple recognisable image for the target item.
- {mode_rule}
- Output JSON only.

Schema:
{{
  "items": [
    {{
      "word": "string",
      "semantic_hint": "string",
      "feature_prompts": ["string","string","string"],
      "tags": ["tag1","tag2","tag3"],
      "image_prompt": "string"
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
    for candidate in sentence_candidates[:40]:
        tags = ", ".join(candidate.get("tags", [])[:6])
        label = candidate.get("english_label") or candidate.get("id")
        scene_hint_lines.append(f"- {label}: {tags}")

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
- image_prompt must clearly describe a single scene matching the sentence.
- {mode_rule}

Each item must follow:
{{
  "sentence": "string",
  "required_content": ["noun_or_verb1","noun_or_verb2"],
  "required_function": ["function1","function2"],
  "tags": ["tag1","tag2","tag3"],
  "image_prompt": "string"
}}

- required_content = key content words only
- required_function = grammar/helper words like the/a/is/to/in/on/and
- tags = imageable scene tags
- image_prompt = one clear simple scene description matching the sentence
- Output JSON only.

Schema:
{{
  "items": [
    {{
      "sentence": "string",
      "required_content": ["noun_or_verb1","noun_or_verb2"],
      "required_function": ["function1","function2"],
      "tags": ["tag1","tag2","tag3"],
      "image_prompt": "string"
    }}
  ]
}}
""".strip()


# =========================================================
# Structured output schema
# =========================================================

def _pack_items_schema(technique_id: str) -> dict:
    if technique_id == "image_naming":
        return {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "word": {"type": "string"},
                            "tags": {"type": "array", "items": {"type": "string"}},
                            "image_prompt": {"type": "string"},
                        },
                        "required": ["word", "tags", "image_prompt"],
                    },
                }
            },
            "required": ["items"],
        }

    if technique_id == "naming_with_cues":
        return {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "word": {"type": "string"},
                            "semantic_cue": {"type": "string"},
                            "phonemic_cue": {"type": "string"},
                            "full_cue": {"type": "string"},
                            "tags": {"type": "array", "items": {"type": "string"}},
                            "image_prompt": {"type": "string"},
                        },
                        "required": [
                            "word",
                            "semantic_cue",
                            "phonemic_cue",
                            "full_cue",
                            "tags",
                            "image_prompt",
                        ],
                    },
                }
            },
            "required": ["items"],
        }

    if technique_id == "semantic_feature_analysis":
        return {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "word": {"type": "string"},
                            "semantic_hint": {"type": "string"},
                            "feature_prompts": {"type": "array", "items": {"type": "string"}},
                            "tags": {"type": "array", "items": {"type": "string"}},
                            "image_prompt": {"type": "string"},
                        },
                        "required": [
                            "word",
                            "semantic_hint",
                            "feature_prompts",
                            "tags",
                            "image_prompt",
                        ],
                    },
                }
            },
            "required": ["items"],
        }

    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "minItems": 1,
                "items": {
                    "type": "object",
                    "properties": {
                        "sentence": {"type": "string"},
                        "required_content": {"type": "array", "items": {"type": "string"}},
                        "required_function": {"type": "array", "items": {"type": "string"}},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "image_prompt": {"type": "string"},
                    },
                    "required": [
                        "sentence",
                        "required_content",
                        "required_function",
                        "tags",
                        "image_prompt",
                    ],
                },
            }
        },
        "required": ["items"],
    }


# =========================================================
# Ollama helper
# =========================================================

def _ollama_chat_json(
    model: str,
    system_prompt: str,
    user_prompt: str,
    schema: dict,
    temperature: float = 0.2,
) -> dict:
    url = f"{_get_ollama_base_url()}/api/chat"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "format": schema,
        "options": {
            "temperature": temperature,
        },
    }

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Ollama HTTP error {error.code}: {body}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(
            f"Could not connect to Ollama at {_get_ollama_base_url()}. "
            f"Make sure Ollama is running and the model is pulled. Original error: {error}"
        ) from error

    data = json.loads(raw)
    message = data.get("message") or {}
    content = (message.get("content") or "").strip()

    if not content:
        raise ValueError("Ollama returned empty content")

    return _extract_json(content)


# =========================================================
# LLM generation
# =========================================================

def _generate_pack_items_with_openai(
    profile: dict,
    technique_id: str,
    n_items: int,
    catalog: list[dict],
) -> list[dict]:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    prompt = build_llm_prompt_for_pack(profile, technique_id, n_items, catalog)

    response = client.chat.completions.create(
        model=os.getenv("OPENAI_TEXT_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": "Return only strict JSON. No commentary. No markdown."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.5,
    )

    text = (response.choices[0].message.content or "").strip()
    data = _extract_json(text)

    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("OpenAI returned invalid items list")

    return items[:n_items]


def _generate_pack_items_with_local_llm(
    profile: dict,
    technique_id: str,
    n_items: int,
    catalog: list[dict],
) -> list[dict]:
    prompt = build_llm_prompt_for_pack(profile, technique_id, n_items, catalog)
    schema = _pack_items_schema(technique_id)

    data = _ollama_chat_json(
        model=_get_local_llm_model(),
        system_prompt="Return only strict JSON matching the provided schema. No commentary. No markdown.",
        user_prompt=prompt,
        schema=schema,
        temperature=0.2,
    )

    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("Local LLM returned invalid items list")

    return items[:n_items]


def generate_pack_items(
    profile: dict,
    technique_id: str,
    n_items: int,
    catalog: list[dict],
) -> list[dict]:
    provider = _get_text_generation_provider(profile)

    if provider == "local":
        return _generate_pack_items_with_local_llm(
            profile=profile,
            technique_id=technique_id,
            n_items=n_items,
            catalog=catalog,
        )

    return _generate_pack_items_with_openai(
        profile=profile,
        technique_id=technique_id,
        n_items=n_items,
        catalog=catalog,
    )


# =========================================================
# Item validation and cleanup
# =========================================================

def _clean_image_prompt(prompt: str, fallback: str) -> str:
    prompt = (prompt or "").strip()
    if prompt:
        prompt = re.sub(r"\s+", " ", prompt)
        return prompt[:500]
    return fallback


def _default_sfa_feature_prompts() -> list[str]:
    return [
        "What type of thing is it?",
        "What is it used for or what does it do?",
        "Where might you find it?",
        "What does it look like?",
        "What is it related to?",
        "What actions are linked to it?",
    ]


def _validate_and_clean_items(
    profile: dict,
    technique_id: str,
    items: list[dict],
    n_items: int,
) -> list[dict]:
    out: list[dict] = []

    for raw in items:
        if not isinstance(raw, dict):
            continue

        if technique_id in {"image_naming", "naming_with_cues"}:
            word = _clean_display_word(raw.get("word", ""))
            if not word:
                continue

            tags = _ensure_list_of_tags(raw.get("tags", []))
            if word not in tags:
                tags = [word] + tags

            fallback_prompt = (
                f"A simple clear therapy-style illustration of {word}, centered, easy to recognise, plain background."
            )
            image_prompt = _clean_image_prompt(raw.get("image_prompt", ""), fallback_prompt)

            if technique_id == "image_naming":
                out.append(
                    {
                        "word": word,
                        "tags": tags[:8],
                        "image_prompt": image_prompt,
                    }
                )
            else:
                semantic_cue = _clean_semantic_cue(raw.get("semantic_cue", ""))
                phonemic_cue = _clean_phonemic_cue(raw.get("phonemic_cue", ""))
                full_cue = _clean_display_word(raw.get("full_cue", "") or word) or word

                out.append(
                    {
                        "word": word,
                        "semantic_cue": semantic_cue,
                        "phonemic_cue": phonemic_cue,
                        "full_cue": full_cue,
                        "tags": tags[:8],
                        "image_prompt": image_prompt,
                    }
                )

        elif technique_id == "semantic_feature_analysis":
            word = _clean_display_word(raw.get("word", ""))
            if not word:
                continue

            tags = _ensure_list_of_tags(raw.get("tags", []), max_len=8)
            if word not in tags:
                tags = [word] + tags

            semantic_hint = _clean_semantic_cue(raw.get("semantic_hint", ""))
            feature_prompts = _ensure_list_of_strings(
                raw.get("feature_prompts", []),
                max_len=6,
                max_item_len=140,
            )
            if not feature_prompts:
                feature_prompts = _default_sfa_feature_prompts()

            fallback_prompt = (
                f"A simple clear therapy-style illustration of {word}, centered, easy to recognise, plain background."
            )
            image_prompt = _clean_image_prompt(raw.get("image_prompt", ""), fallback_prompt)

            out.append(
                {
                    "word": word,
                    "semantic_hint": semantic_hint,
                    "feature_prompts": feature_prompts,
                    "tags": tags[:8],
                    "image_prompt": image_prompt,
                }
            )

        else:
            sentence = _clean_sentence(raw.get("sentence", ""))
            if not sentence:
                continue

            required_content = _ensure_list_of_tokens(raw.get("required_content", []), max_len=6)
            required_function = _ensure_list_of_tokens(raw.get("required_function", []), max_len=6)
            tags = _ensure_list_of_tags(raw.get("tags", []), max_len=8)

            if len(required_content) < 1:
                continue

            fallback_prompt = f"A simple clear therapy-style illustration showing: {sentence}"
            image_prompt = _clean_image_prompt(raw.get("image_prompt", ""), fallback_prompt)

            out.append(
                {
                    "sentence": sentence,
                    "required_content": required_content,
                    "required_function": required_function,
                    "tags": tags,
                    "image_prompt": image_prompt,
                }
            )

        if len(out) >= n_items:
            break

    return out[:n_items]


def _validate_and_clean_semantic_feature_items(
    items: list[dict],
    n_items: int,
) -> list[dict]:
    out: list[dict] = []

    for raw in items:
        if not isinstance(raw, dict):
            continue

        word = _clean_display_word(raw.get("word", ""))
        image = str(raw.get("image", "") or "").strip()

        if not word or not image:
            continue

        tags = _ensure_list_of_tags(raw.get("tags", []), max_len=8)
        if word not in tags:
            tags = [word] + tags

        semantic_hint = _clean_semantic_cue(raw.get("semantic_hint", ""))
        feature_prompts = _ensure_list_of_strings(
            raw.get("feature_prompts", []),
            max_len=6,
            max_item_len=140,
        )
        if not feature_prompts:
            feature_prompts = _default_sfa_feature_prompts()

        out.append(
            {
                "word": word,
                "image": image,
                "tags": tags[:8],
                "difficulty": _safe_item_difficulty(raw),
                "semantic_hint": semantic_hint,
                "feature_prompts": feature_prompts,
                "source": "catalog",
                "matched_catalog_id": raw.get("matched_catalog_id") or raw.get("id"),
                "image_prompt": f"A simple clear therapy-style illustration of {word}, centered, easy to recognise, plain background.",
            }
        )

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
        catalog,
        difficulty,
        interests_text,
        avoid_topics_text,
        limit=max(50, n_items * 5),
    )

    out: list[dict] = []
    seen = set()

    for word in words:
        if word in seen:
            continue
        seen.add(word)

        image, matched = _pick_catalog_image_strict(
            catalog=catalog,
            difficulty=difficulty,
            technique_id="image_naming",
            word=word,
        )
        if not image:
            continue

        out.append(
            {
                "word": word,
                "image": image,
                "tags": (matched.get("tags") if matched else []) or [word],
                "source": "catalog",
                "matched_catalog_id": matched.get("id") if matched else None,
                "image_prompt": f"A simple clear therapy-style illustration of {word}, centered, easy to recognise, plain background.",
            }
        )

        if len(out) >= n_items:
            break

    return out[:n_items]


def _fallback_naming_with_cues_items(
    catalog: list[dict],
    difficulty: int,
    interests_text: str,
    avoid_topics_text: str,
    n_items: int,
) -> list[dict]:
    words = _get_naming_with_cues_candidates(
        catalog,
        difficulty,
        interests_text,
        avoid_topics_text,
        limit=max(50, n_items * 5),
    )

    out: list[dict] = []
    seen = set()

    for word in words:
        if word in seen:
            continue
        seen.add(word)

        image, matched = _pick_catalog_image_strict(
            catalog=catalog,
            difficulty=difficulty,
            technique_id="naming_with_cues",
            word=word,
        )
        if not image:
            continue

        tags = (matched.get("tags") if matched else []) or [word]

        semantic_cue = (matched.get("semantic_cue") if matched else "") or ""
        phonemic_cue = (matched.get("phonemic_cue") if matched else "") or ""
        full_cue = (matched.get("full_cue") if matched else "") or word

        out.append(
            {
                "word": word,
                "image": image,
                "semantic_cue": _clean_semantic_cue(semantic_cue),
                "phonemic_cue": _clean_phonemic_cue(phonemic_cue),
                "full_cue": _clean_display_word(full_cue) or word,
                "tags": tags,
                "source": "catalog",
                "matched_catalog_id": matched.get("id") if matched else None,
                "image_prompt": f"A simple clear therapy-style illustration of {word}, centered, easy to recognise, plain background.",
            }
        )

        if len(out) >= n_items:
            break

    return out[:n_items]


def _fallback_sentence_items(
    catalog: list[dict],
    difficulty: int,
    interests_text: str,
    avoid_topics_text: str,
    n_items: int,
) -> list[dict]:
    entries = _get_sentence_candidates(
        catalog,
        difficulty,
        interests_text,
        avoid_topics_text,
        limit=max(40, n_items * 4),
    )

    out: list[dict] = []
    seen = set()

    for item in entries:
        image = item.get("image")
        if not image:
            continue

        key = item.get("id") or item.get("english_label") or image
        if key in seen:
            continue
        seen.add(key)

        label = (item.get("english_label") or "").strip()
        tags = item.get("tags", [])

        sentence = label.replace("_", " ").strip().capitalize()
        if sentence and not sentence.endswith("."):
            sentence += "."

        out.append(
            {
                "sentence": sentence or "The person is doing an activity.",
                "image": image,
                "required_content": [tag for tag in tags[:3] if _clean_token(tag)],
                "required_function": ["the"],
                "tags": tags,
                "source": "catalog",
                "matched_catalog_id": item.get("id"),
                "image_prompt": f"A simple clear therapy-style illustration showing: {sentence or 'The person is doing an activity.'}",
            }
        )

        if len(out) >= n_items:
            break

    return out[:n_items]


def _fallback_semantic_feature_items(
    catalog: list[dict],
    difficulty: int,
    interests_text: str,
    avoid_topics_text: str,
    n_items: int,
) -> list[dict]:
    entries = _get_semantic_feature_candidates(
        catalog,
        difficulty,
        interests_text,
        avoid_topics_text,
        limit=max(40, n_items * 4),
    )

    return _validate_and_clean_semantic_feature_items(entries, n_items)


# =========================================================
# Generated image helpers
# =========================================================

def _generated_output_dir(technique_id: str) -> Path:
    if technique_id == "image_naming":
        return GENERATED_IMAGE_NAMING_DIR
    if technique_id == "naming_with_cues":
        return GENERATED_NAMING_WITH_CUES_DIR
    if technique_id == "semantic_feature_analysis":
        return GENERATED_SFA_DIR
    return GENERATED_SENTENCE_DIR


def _generated_public_path(technique_id: str, filename: str) -> str:
    if technique_id == "image_naming":
        return f"generated/image_naming/{filename}"
    if technique_id == "naming_with_cues":
        return f"generated/naming_with_cues/{filename}"
    if technique_id == "semantic_feature_analysis":
        return f"generated/semantic_feature_analysis/{filename}"
    return f"generated/sentence_repetition/{filename}"


def _build_image_generation_prompt(technique_id: str, item: dict) -> str:
    if technique_id in {"image_naming", "naming_with_cues", "semantic_feature_analysis"}:
        word = item.get("word", "")
        base_prompt = item.get("image_prompt", "")
        return (
            f"{base_prompt} "
            f"Create a single clear therapy exercise image for the target word '{word}'. "
            f"Use a simple educational illustration style, centered subject, uncluttered composition, "
            f"high recognisability, plain or very clean background, no text, no labels, no watermark."
        ).strip()

    sentence = item.get("sentence", "")
    base_prompt = item.get("image_prompt", "")
    return (
        f"{base_prompt} "
        f"Create a single clear therapy exercise image matching the sentence '{sentence}'. "
        f"Use a simple educational illustration style, easy to understand action scene, "
        f"uncluttered composition, no text, no labels, no watermark."
    ).strip()


async def _get_cached_generated_image_path(prompt_hash: str) -> str | None:
    asset = await ImageAsset.get_or_none(prompt_hash=prompt_hash, source="generated")
    if asset and asset.file_path:
        rel_path = asset.file_path.lstrip("/")
        abs_path = PUBLIC_DIR / rel_path
        if abs_path.exists():
            return rel_path
    return None


async def _store_generated_asset(
    prompt_hash: str,
    file_path: str,
    difficulty: int,
    tags: list[str],
) -> None:
    existing = await ImageAsset.get_or_none(prompt_hash=prompt_hash, source="generated")
    if existing:
        existing.file_path = file_path
        existing.tags = tags
        existing.difficulty = difficulty
        await existing.save()
        return

    await ImageAsset.create(
        owner_user=None,
        source="generated",
        tags=tags,
        difficulty=difficulty,
        file_path=file_path,
        prompt_hash=prompt_hash,
    )


def _decode_image_b64_to_file(b64_data: str, output_path: Path) -> None:
    raw = base64.b64decode(b64_data)
    output_path.write_bytes(raw)


async def _generate_image_with_openai(
    technique_id: str,
    item: dict,
    difficulty: int,
) -> str | None:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    final_prompt = _build_image_generation_prompt(technique_id, item)
    prompt_hash = _hash_prompt(final_prompt)

    cached = await _get_cached_generated_image_path(prompt_hash)
    if cached:
        return cached

    out_dir = _generated_output_dir(technique_id)
    stem = _slugify(item.get("word") or item.get("sentence") or "generated_item")
    filename = f"{stem}_{prompt_hash[:10]}.png"
    output_path = out_dir / filename
    public_path = _generated_public_path(technique_id, filename)

    response = client.images.generate(
        model="gpt-image-1",
        prompt=final_prompt,
        size="1024x1024",
    )

    image_b64 = None
    data = getattr(response, "data", None) or []
    if data:
        first = data[0]
        image_b64 = getattr(first, "b64_json", None) or (
            first.get("b64_json") if isinstance(first, dict) else None
        )

    if not image_b64:
        return None

    _decode_image_b64_to_file(image_b64, output_path)

    await _store_generated_asset(
        prompt_hash=prompt_hash,
        file_path=public_path,
        difficulty=difficulty,
        tags=item.get("tags") or [],
    )

    return public_path


async def _generate_image_locally(
    technique_id: str,
    item: dict,
    difficulty: int,
) -> str | None:
    final_prompt = _build_image_generation_prompt(technique_id, item)
    prompt_hash = _hash_prompt(final_prompt)

    cached = await _get_cached_generated_image_path(prompt_hash)
    if cached:
        print("[LOCAL GEN] using cached image:", cached)
        return cached

    stem = _slugify(item.get("word") or item.get("sentence") or "generated_item")

    if technique_id == "image_naming":
        save_prefix = f"generated/image_naming/{stem}_{prompt_hash[:10]}"
    elif technique_id == "naming_with_cues":
        save_prefix = f"generated/naming_with_cues/{stem}_{prompt_hash[:10]}"
    elif technique_id == "semantic_feature_analysis":
        save_prefix = f"generated/semantic_feature_analysis/{stem}_{prompt_hash[:10]}"
    else:
        save_prefix = f"generated/sentence_repetition/{stem}_{prompt_hash[:10]}"

    print("[LOCAL GEN] technique:", technique_id)
    print("[LOCAL GEN] stem:", stem)
    print("[LOCAL GEN] prompt:", final_prompt)
    print("[LOCAL GEN] save_prefix:", save_prefix)

    try:
        public_path = generate_image_via_comfyui(
            prompt_text=final_prompt,
            save_prefix=save_prefix,
            technique_id=technique_id,
        )
        print("[LOCAL GEN] generated path:", public_path)
    except LocalImageGenerationError as error:
        print("[LOCAL GEN] failed:", str(error))
        return None

    await _store_generated_asset(
        prompt_hash=prompt_hash,
        file_path=public_path,
        difficulty=difficulty,
        tags=item.get("tags") or [],
    )
    print("[LOCAL GEN] stored generated asset:", public_path)

    return public_path


async def _generate_image_with_selected_method(
    technique_id: str,
    item: dict,
    difficulty: int,
    profile: dict,
) -> tuple[str | None, str | None]:
    mode = _get_image_generation_mode(profile)

    if mode == "none":
        return None, None

    if mode == "openai":
        image = await _generate_image_with_openai(
            technique_id=technique_id,
            item=item,
            difficulty=difficulty,
        )
        return image, "openai" if image else None

    if mode == "local":
        image = await _generate_image_locally(
            technique_id=technique_id,
            item=item,
            difficulty=difficulty,
        )
        return image, "local" if image else None

    return None, None


# =========================================================
# Image attachment and resolution
# =========================================================

def _build_naming_with_cues_row(
    base_item: dict,
    image_path: str,
    source: str,
    matched_catalog_item: dict | None = None,
    generation_method: str | None = None,
) -> dict:
    tags = base_item.get("tags") or []

    if matched_catalog_item and matched_catalog_item.get("tags"):
        merged_tags: list[str] = []
        seen = set()

        for tag in list(matched_catalog_item.get("tags") or []) + list(tags):
            norm_tag = _norm(str(tag))
            if norm_tag and norm_tag not in seen:
                seen.add(norm_tag)
                merged_tags.append(norm_tag)

        tags = merged_tags or tags

    semantic_cue_source = (
        (matched_catalog_item.get("semantic_cue") if matched_catalog_item else "")
        or base_item.get("semantic_cue")
        or ""
    )
    phonemic_cue_source = (
        (matched_catalog_item.get("phonemic_cue") if matched_catalog_item else "")
        or base_item.get("phonemic_cue")
        or ""
    )
    full_cue_source = (
        (matched_catalog_item.get("full_cue") if matched_catalog_item else "")
        or base_item.get("full_cue")
        or base_item.get("word", "")
    )

    row = {
        "word": (base_item.get("word") or "").strip(),
        "image": image_path,
        "semantic_cue": _clean_semantic_cue(semantic_cue_source),
        "phonemic_cue": _clean_phonemic_cue(phonemic_cue_source),
        "full_cue": _clean_display_word(full_cue_source) or _clean_display_word(base_item.get("word", "")),
        "tags": tags,
        "source": source,
        "image_prompt": base_item.get("image_prompt", ""),
        "matched_catalog_id": matched_catalog_item.get("id") if matched_catalog_item else None,
    }

    if generation_method:
        row["generation_method"] = generation_method

    return row


def _build_semantic_feature_row(
    base_item: dict,
    image_path: str,
    source: str,
    matched_catalog_item: dict | None = None,
    generation_method: str | None = None,
) -> dict:
    tags = base_item.get("tags") or []

    if matched_catalog_item and matched_catalog_item.get("tags"):
        merged_tags: list[str] = []
        seen = set()

        for tag in list(matched_catalog_item.get("tags") or []) + list(tags):
            norm_tag = _norm(str(tag))
            if norm_tag and norm_tag not in seen:
                seen.add(norm_tag)
                merged_tags.append(norm_tag)

        tags = merged_tags or tags

    semantic_hint_source = (
        (matched_catalog_item.get("semantic_hint") if matched_catalog_item else "")
        or base_item.get("semantic_hint")
        or ""
    )
    feature_prompt_source = (
        (matched_catalog_item.get("feature_prompts") if matched_catalog_item else None)
        or base_item.get("feature_prompts")
        or _default_sfa_feature_prompts()
    )

    row = {
        "word": (base_item.get("word") or "").strip(),
        "image": image_path,
        "semantic_hint": _clean_semantic_cue(semantic_hint_source),
        "feature_prompts": _ensure_list_of_strings(feature_prompt_source, max_len=6, max_item_len=140)
        or _default_sfa_feature_prompts(),
        "tags": tags,
        "source": source,
        "image_prompt": base_item.get("image_prompt", ""),
        "matched_catalog_id": matched_catalog_item.get("id") if matched_catalog_item else None,
    }

    if generation_method:
        row["generation_method"] = generation_method

    return row


async def attach_images_to_items(
    items: list[dict],
    catalog: list[dict],
    technique_id: str,
    difficulty: int,
    profile: dict,
) -> list[dict]:
    out: list[dict] = []
    generation_mode = _get_image_generation_mode(profile)

    print("[ATTACH] generation_mode:", generation_mode)
    print("[ATTACH] technique_id:", technique_id)
    print("[ATTACH] item_count:", len(items))

    for item in items:
        if technique_id in {"image_naming", "naming_with_cues", "semantic_feature_analysis"} and generation_mode == "local":
            print("[ATTACH] FORCE LOCAL GENERATION for word:", item.get("word", ""))

            gen_image, gen_method = await _generate_image_with_selected_method(
                technique_id=technique_id,
                item=item,
                difficulty=difficulty,
                profile=profile,
            )
            if gen_image:
                if technique_id == "image_naming":
                    out.append(
                        {
                            "word": (item.get("word") or "").strip(),
                            "image": gen_image,
                            "tags": item.get("tags") or [],
                            "source": "generated",
                            "generation_method": gen_method,
                            "image_prompt": item.get("image_prompt", ""),
                            "matched_catalog_id": None,
                        }
                    )
                elif technique_id == "naming_with_cues":
                    out.append(
                        _build_naming_with_cues_row(
                            base_item=item,
                            image_path=gen_image,
                            source="generated",
                            matched_catalog_item=None,
                            generation_method=gen_method,
                        )
                    )
                else:
                    out.append(
                        _build_semantic_feature_row(
                            base_item=item,
                            image_path=gen_image,
                            source="generated",
                            matched_catalog_item=None,
                            generation_method=gen_method,
                        )
                    )
                continue

            print("[ATTACH] Local generation failed, falling back to curated match for:", item.get("word", ""))

        if technique_id in {"image_naming", "naming_with_cues", "semantic_feature_analysis"}:
            image, matched_catalog_item = _pick_catalog_image_strict(
                catalog=catalog,
                difficulty=difficulty,
                technique_id=technique_id,
                word=item.get("word", ""),
            )

            if image:
                print("[ATTACH] curated image used for:", item.get("word", ""), "->", image)

                if technique_id == "image_naming":
                    out.append(
                        {
                            "word": (item.get("word") or "").strip(),
                            "image": image,
                            "tags": item.get("tags") or [],
                            "source": "catalog",
                            "image_prompt": item.get("image_prompt", ""),
                            "matched_catalog_id": matched_catalog_item.get("id") if matched_catalog_item else None,
                        }
                    )
                elif technique_id == "naming_with_cues":
                    out.append(
                        _build_naming_with_cues_row(
                            base_item=item,
                            image_path=image,
                            source="catalog",
                            matched_catalog_item=matched_catalog_item,
                        )
                    )
                else:
                    out.append(
                        _build_semantic_feature_row(
                            base_item=item,
                            image_path=image,
                            source="catalog",
                            matched_catalog_item=matched_catalog_item,
                        )
                    )
                continue

            if generation_mode != "none":
                print("[ATTACH] trying generation after no curated match for:", item.get("word", ""))
                gen_image, gen_method = await _generate_image_with_selected_method(
                    technique_id=technique_id,
                    item=item,
                    difficulty=difficulty,
                    profile=profile,
                )
                if gen_image:
                    if technique_id == "image_naming":
                        out.append(
                            {
                                "word": (item.get("word") or "").strip(),
                                "image": gen_image,
                                "tags": item.get("tags") or [],
                                "source": "generated",
                                "generation_method": gen_method,
                                "image_prompt": item.get("image_prompt", ""),
                                "matched_catalog_id": None,
                            }
                        )
                    elif technique_id == "naming_with_cues":
                        out.append(
                            _build_naming_with_cues_row(
                                base_item=item,
                                image_path=gen_image,
                                source="generated",
                                matched_catalog_item=None,
                                generation_method=gen_method,
                            )
                        )
                    else:
                        out.append(
                            _build_semantic_feature_row(
                                base_item=item,
                                image_path=gen_image,
                                source="generated",
                                matched_catalog_item=None,
                                generation_method=gen_method,
                            )
                        )
                    continue

        else:
            english_label = _clean_sentence(item.get("sentence", ""))
            image, matched_catalog_item = _pick_catalog_image_strict(
                catalog=catalog,
                difficulty=difficulty,
                technique_id=technique_id,
                english_label=english_label,
            )

            if image:
                out.append(
                    {
                        "sentence": (item.get("sentence") or "").strip(),
                        "image": image,
                        "required_content": item.get("required_content") or [],
                        "required_function": item.get("required_function") or [],
                        "tags": item.get("tags") or [],
                        "source": "catalog",
                        "image_prompt": item.get("image_prompt", ""),
                        "matched_catalog_id": matched_catalog_item.get("id") if matched_catalog_item else None,
                    }
                )
                continue

            if generation_mode != "none":
                gen_image, gen_method = await _generate_image_with_selected_method(
                    technique_id=technique_id,
                    item=item,
                    difficulty=difficulty,
                    profile=profile,
                )
                if gen_image:
                    out.append(
                        {
                            "sentence": (item.get("sentence") or "").strip(),
                            "image": gen_image,
                            "required_content": item.get("required_content") or [],
                            "required_function": item.get("required_function") or [],
                            "tags": item.get("tags") or [],
                            "source": "generated",
                            "generation_method": gen_method,
                            "image_prompt": item.get("image_prompt", ""),
                            "matched_catalog_id": None,
                        }
                    )
                    continue

    if len(out) < len(items):
        missing = len(items) - len(out)
        print("[ATTACH] unresolved item count:", missing)

        if technique_id == "image_naming":
            fillers = _fallback_image_naming_items(
                catalog=catalog,
                difficulty=difficulty,
                interests_text=profile.get("interests_text", ""),
                avoid_topics_text=profile.get("avoid_topics_text", ""),
                n_items=missing,
            )
        elif technique_id == "naming_with_cues":
            fillers = _fallback_naming_with_cues_items(
                catalog=catalog,
                difficulty=difficulty,
                interests_text=profile.get("interests_text", ""),
                avoid_topics_text=profile.get("avoid_topics_text", ""),
                n_items=missing,
            )
        elif technique_id == "semantic_feature_analysis":
            fillers = _fallback_semantic_feature_items(
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

    return out[: len(items)]


# =========================================================
# Main pack builder
# =========================================================

async def build_content_pack(
    profile: dict,
    technique_id: str,
    n_items: int = 10,
) -> dict[str, Any]:
    difficulty = int(profile.get("difficulty", 1))

    if technique_id == "semantic_feature_analysis":
        sfa_catalog = load_semantic_feature_catalog()

        raw_items = generate_pack_items(
            profile=profile,
            technique_id=technique_id,
            n_items=n_items,
            catalog=sfa_catalog,
        )

        cleaned_items = _validate_and_clean_items(
            profile,
            technique_id,
            raw_items,
            n_items,
        )

        final_items = await attach_images_to_items(
            items=cleaned_items,
            catalog=sfa_catalog,
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

    catalog = load_image_catalog()

    raw_items = generate_pack_items(
        profile=profile,
        technique_id=technique_id,
        n_items=n_items,
        catalog=catalog,
    )

    cleaned_items = _validate_and_clean_items(
        profile,
        technique_id,
        raw_items,
        n_items,
    )

    final_items = await attach_images_to_items(
        items=cleaned_items,
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