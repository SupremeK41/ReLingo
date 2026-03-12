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
from models import ImageAsset
from local_image_generation import generate_image_via_comfyui, LocalImageGenerationError


# =========================================================
# Paths / catalog loading
# =========================================================

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
PUBLIC_DIR = PROJECT_ROOT / "frontend" / "stroke-recovery" / "public"

# Unified therapy catalog
CATALOG_PATH = PUBLIC_DIR / "therapy_catalog.json"

GENERATED_DIR = PUBLIC_DIR / "generated"
GENERATED_IMAGE_NAMING_DIR = GENERATED_DIR / "image_naming"
GENERATED_NAMING_WITH_CUES_DIR = GENERATED_DIR / "naming_with_cues"
GENERATED_SENTENCE_DIR = GENERATED_DIR / "sentence_repetition"

GENERATED_IMAGE_NAMING_DIR.mkdir(parents=True, exist_ok=True)
GENERATED_NAMING_WITH_CUES_DIR.mkdir(parents=True, exist_ok=True)
GENERATED_SENTENCE_DIR.mkdir(parents=True, exist_ok=True)


def load_image_catalog() -> list[dict]:
    if not CATALOG_PATH.exists():
        return []

    raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))

    if isinstance(raw, dict):
        merged: list[dict] = []
        for key in ("image_naming", "naming_with_cues", "sentence_repetition"):
            value = raw.get(key, [])
            if isinstance(value, list):
                merged.extend([x for x in value if isinstance(x, dict)])
        return merged

    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]

    return []


# =========================================================
# Text generation provider config
# =========================================================

def _get_text_generation_provider(profile: dict | None = None) -> str:
    provider = os.getenv("TEXT_GENERATION_PROVIDER", "openai").strip().lower()
    if provider not in {"openai", "local"}:
        provider = "openai"
    return provider


def _get_local_llm_model() -> str:
    return os.getenv("LOCAL_LLM_MODEL", "gemma3:4b").strip() or "gemma3:4b"


def _get_ollama_base_url() -> str:
    return os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")


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
        "_": " ",
    }
    for old, new in replacements.items():
        s = s.replace(old, new)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _clean_display_word(w: str) -> str:
    w = _norm(w)
    w = re.sub(r"[^a-z\s]", " ", w)
    w = re.sub(r"\s+", " ", w).strip()
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


def _slugify(s: str) -> str:
    s = _norm(s)
    s = s.replace(" ", "_")
    s = re.sub(r"[^a-z0-9_]", "", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or uuid.uuid4().hex[:8]


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
        if t and t not in out:
            out.append(t)
        if len(out) >= max_len:
            break
    return out


def _image_field(it: dict) -> str | None:
    return it.get("image") or it.get("file")


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
# Cue cleaning helpers
# IMPORTANT:
# We no longer replace weak cues with generic fallback cues.
# We only preserve catalog cues or AI-generated cues.
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

def _safe_item_difficulty(it: dict) -> int:
    try:
        return int(it.get("difficulty", 1))
    except Exception:
        return 1


def _catalog_entries_for_technique(
    catalog: list[dict],
    technique_id: str,
    difficulty: int,
) -> list[dict]:
    return [
        it for it in catalog
        if it.get("technique") == technique_id
        and _safe_item_difficulty(it) <= int(difficulty)
        and bool(it.get("keep", True))
    ]


def _filter_catalog_by_interests(
    entries: list[dict],
    interests_text: str,
    avoid_topics_text: str,
) -> tuple[list[dict], list[dict]]:
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
        cue_entries, interests_text, avoid_topics_text
    )
    preferred_img, general_img = _filter_catalog_by_interests(
        image_entries, interests_text, avoid_topics_text
    )

    seen = set()
    words = []

    for pool in (preferred_cue, general_cue, preferred_img, general_img):
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
                "image": _image_field(it),
            })
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

    for it in entries:
        if _norm(it.get("word", "")) == target:
            img = _image_field(it)
            if img:
                return img, it

    for it in entries:
        if _norm(it.get("id", "")) == target:
            img = _image_field(it)
            if img:
                return img, it

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
        img, it = _find_catalog_match_by_word(cue_entries, word=word)
        if img:
            return img, it

        image_entries = _catalog_entries_for_technique(catalog, "image_naming", difficulty)
        img, it = _find_catalog_match_by_word(image_entries, word=word)
        if img:
            return img, it

        return None, None

    if technique_id == "image_naming":
        entries = _catalog_entries_for_technique(catalog, "image_naming", difficulty)
        return _find_catalog_match_by_word(entries, word=word)

    if technique_id == "sentence_repetition" and english_label:
        entries = _catalog_entries_for_technique(catalog, "sentence_repetition", difficulty)
        target = _norm(english_label)
        for it in entries:
            label = _norm(it.get("english_label", ""))
            item_id = _norm(it.get("id", ""))
            if label == target or item_id == target:
                img = _image_field(it)
                if img:
                    return img, it

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
# LLM generation helpers
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

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Ollama HTTP error {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Could not connect to Ollama at {_get_ollama_base_url()}. "
            f"Make sure Ollama is running and the model is pulled. Original error: {e}"
        ) from e

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

    resp = client.chat.completions.create(
        model=os.getenv("OPENAI_TEXT_MODEL", "gpt-4o-mini"),
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
# Cleaning / validation
# =========================================================

def _clean_image_prompt(prompt: str, fallback: str) -> str:
    prompt = (prompt or "").strip()
    if prompt:
        prompt = re.sub(r"\s+", " ", prompt)
        return prompt[:500]
    return fallback


def _validate_and_clean_items(profile: dict, technique_id: str, items: list[dict], n_items: int) -> list[dict]:
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
                out.append({
                    "word": word,
                    "tags": tags[:8],
                    "image_prompt": image_prompt,
                })
            else:
                semantic_cue = _clean_semantic_cue(raw.get("semantic_cue", ""))
                phonemic_cue = _clean_phonemic_cue(raw.get("phonemic_cue", ""))
                full_cue = _clean_display_word(raw.get("full_cue", "") or word) or word

                out.append({
                    "word": word,
                    "semantic_cue": semantic_cue,
                    "phonemic_cue": phonemic_cue,
                    "full_cue": full_cue,
                    "tags": tags[:8],
                    "image_prompt": image_prompt,
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

            fallback_prompt = (
                f"A simple clear therapy-style illustration showing: {sentence}"
            )
            image_prompt = _clean_image_prompt(raw.get("image_prompt", ""), fallback_prompt)

            out.append({
                "sentence": sentence,
                "required_content": required_content,
                "required_function": required_function,
                "tags": tags,
                "image_prompt": image_prompt,
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
    seen = set()

    for w in words:
        if w in seen:
            continue
        seen.add(w)

        img, matched = _pick_catalog_image_strict(
            catalog=catalog,
            difficulty=difficulty,
            technique_id="image_naming",
            word=w,
        )
        if img:
            out.append({
                "word": w,
                "image": img,
                "tags": (matched.get("tags") if matched else []) or [w],
                "source": "catalog",
                "matched_catalog_id": matched.get("id") if matched else None,
                "image_prompt": f"A simple clear therapy-style illustration of {w}, centered, easy to recognise, plain background.",
            })
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
        catalog, difficulty, interests_text, avoid_topics_text, limit=max(50, n_items * 5)
    )
    out = []
    seen = set()

    for w in words:
        if w in seen:
            continue
        seen.add(w)

        img, matched = _pick_catalog_image_strict(
            catalog=catalog,
            difficulty=difficulty,
            technique_id="naming_with_cues",
            word=w,
        )
        if img:
            tags = (matched.get("tags") if matched else []) or [w]

            semantic_cue = (matched.get("semantic_cue") if matched else "") or ""
            phonemic_cue = (matched.get("phonemic_cue") if matched else "") or ""
            full_cue = (matched.get("full_cue") if matched else "") or w

            out.append({
                "word": w,
                "image": img,
                "semantic_cue": _clean_semantic_cue(semantic_cue),
                "phonemic_cue": _clean_phonemic_cue(phonemic_cue),
                "full_cue": _clean_display_word(full_cue) or w,
                "tags": tags,
                "source": "catalog",
                "matched_catalog_id": matched.get("id") if matched else None,
                "image_prompt": f"A simple clear therapy-style illustration of {w}, centered, easy to recognise, plain background.",
            })
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
        catalog, difficulty, interests_text, avoid_topics_text, limit=max(40, n_items * 4)
    )

    out = []
    seen = set()

    for it in entries:
        image = it.get("image")
        if not image:
            continue

        key = it.get("id") or it.get("english_label") or image
        if key in seen:
            continue
        seen.add(key)

        label = (it.get("english_label") or "").strip()
        tags = it.get("tags", [])

        sentence = label.replace("_", " ").strip().capitalize()
        if sentence and not sentence.endswith("."):
            sentence += "."

        out.append({
            "sentence": sentence or "The person is doing an activity.",
            "image": image,
            "required_content": [t for t in tags[:3] if _clean_token(t)],
            "required_function": ["the"],
            "tags": tags,
            "source": "catalog",
            "matched_catalog_id": it.get("id"),
            "image_prompt": f"A simple clear therapy-style illustration showing: {sentence or 'The person is doing an activity.'}",
        })

        if len(out) >= n_items:
            break

    return out[:n_items]


# =========================================================
# Generated image helpers
# =========================================================

def _generated_output_dir(technique_id: str) -> Path:
    if technique_id == "image_naming":
        return GENERATED_IMAGE_NAMING_DIR
    if technique_id == "naming_with_cues":
        return GENERATED_NAMING_WITH_CUES_DIR
    return GENERATED_SENTENCE_DIR


def _generated_public_path(technique_id: str, filename: str) -> str:
    if technique_id == "image_naming":
        return f"generated/image_naming/{filename}"
    if technique_id == "naming_with_cues":
        return f"generated/naming_with_cues/{filename}"
    return f"generated/sentence_repetition/{filename}"


def _build_image_generation_prompt(technique_id: str, item: dict) -> str:
    if technique_id in {"image_naming", "naming_with_cues"}:
        word = item.get("word", "")
        base_prompt = item.get("image_prompt", "")
        return (
            f"{base_prompt} "
            f"Create a single clear therapy exercise image for the target word '{word}'. "
            f"Use a simple educational illustration style, centered subject, uncluttered composition, high recognisability, plain or very clean background, no text, no labels, no watermark."
        ).strip()

    sentence = item.get("sentence", "")
    base_prompt = item.get("image_prompt", "")
    return (
        f"{base_prompt} "
        f"Create a single clear therapy exercise image matching the sentence '{sentence}'. "
        f"Use a simple educational illustration style, easy to understand action scene, uncluttered composition, no text, no labels, no watermark."
    ).strip()


async def _get_cached_generated_image_path(prompt_hash: str) -> str | None:
    asset = await ImageAsset.get_or_none(prompt_hash=prompt_hash, source="generated")
    if asset and asset.file_path:
        rel = asset.file_path.lstrip("/")
        abs_path = PUBLIC_DIR / rel
        if abs_path.exists():
            return rel
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
        image_b64 = getattr(first, "b64_json", None) or (first.get("b64_json") if isinstance(first, dict) else None)

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
    except LocalImageGenerationError as e:
        print("[LOCAL GEN] failed:", str(e))
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
        img = await _generate_image_with_openai(
            technique_id=technique_id,
            item=item,
            difficulty=difficulty,
        )
        return img, "openai" if img else None

    if mode == "local":
        img = await _generate_image_locally(
            technique_id=technique_id,
            item=item,
            difficulty=difficulty,
        )
        return img, "local" if img else None

    return None, None


# =========================================================
# Image attachment / resolution
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
        merged_tags = []
        seen = set()
        for t in list(matched_catalog_item.get("tags") or []) + list(tags):
            norm_t = _norm(str(t))
            if norm_t and norm_t not in seen:
                seen.add(norm_t)
                merged_tags.append(norm_t)
        tags = merged_tags or tags

    # Prefer curated catalog cues over AI-generated cues when a catalog item exists.
    # Do not fall back to generic/default cues anymore.
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

    for it in items:
        # Keep your temporary demo behaviour for local generation on naming tasks.
        if technique_id in {"image_naming", "naming_with_cues"} and generation_mode == "local":
            print("[ATTACH] FORCE LOCAL GENERATION for word:", it.get("word", ""))

            gen_img, gen_method = await _generate_image_with_selected_method(
                technique_id=technique_id,
                item=it,
                difficulty=difficulty,
                profile=profile,
            )
            if gen_img:
                if technique_id == "image_naming":
                    out.append({
                        "word": (it.get("word") or "").strip(),
                        "image": gen_img,
                        "tags": it.get("tags") or [],
                        "source": "generated",
                        "generation_method": gen_method,
                        "image_prompt": it.get("image_prompt", ""),
                        "matched_catalog_id": None,
                    })
                else:
                    out.append(
                        _build_naming_with_cues_row(
                            base_item=it,
                            image_path=gen_img,
                            source="generated",
                            matched_catalog_item=None,
                            generation_method=gen_method,
                        )
                    )
                continue

            print("[ATTACH] Local generation failed, falling back to curated match for:", it.get("word", ""))

        if technique_id in {"image_naming", "naming_with_cues"}:
            img, matched_catalog_item = _pick_catalog_image_strict(
                catalog=catalog,
                difficulty=difficulty,
                technique_id=technique_id,
                word=it.get("word", ""),
            )

            if img:
                print("[ATTACH] curated image used for:", it.get("word", ""), "->", img)

                if technique_id == "image_naming":
                    out.append({
                        "word": (it.get("word") or "").strip(),
                        "image": img,
                        "tags": it.get("tags") or [],
                        "source": "catalog",
                        "image_prompt": it.get("image_prompt", ""),
                        "matched_catalog_id": matched_catalog_item.get("id") if matched_catalog_item else None,
                    })
                else:
                    out.append(
                        _build_naming_with_cues_row(
                            base_item=it,
                            image_path=img,
                            source="catalog",
                            matched_catalog_item=matched_catalog_item,
                        )
                    )
                continue

            if generation_mode != "none":
                print("[ATTACH] trying generation after no curated match for:", it.get("word", ""))
                gen_img, gen_method = await _generate_image_with_selected_method(
                    technique_id=technique_id,
                    item=it,
                    difficulty=difficulty,
                    profile=profile,
                )
                if gen_img:
                    if technique_id == "image_naming":
                        out.append({
                            "word": (it.get("word") or "").strip(),
                            "image": gen_img,
                            "tags": it.get("tags") or [],
                            "source": "generated",
                            "generation_method": gen_method,
                            "image_prompt": it.get("image_prompt", ""),
                            "matched_catalog_id": None,
                        })
                    else:
                        out.append(
                            _build_naming_with_cues_row(
                                base_item=it,
                                image_path=gen_img,
                                source="generated",
                                matched_catalog_item=None,
                                generation_method=gen_method,
                            )
                        )
                    continue

        else:
            english_label = _clean_sentence(it.get("sentence", ""))
            img, matched_catalog_item = _pick_catalog_image_strict(
                catalog=catalog,
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
                    "tags": it.get("tags") or [],
                    "source": "catalog",
                    "image_prompt": it.get("image_prompt", ""),
                    "matched_catalog_id": matched_catalog_item.get("id") if matched_catalog_item else None,
                })
                continue

            if generation_mode != "none":
                gen_img, gen_method = await _generate_image_with_selected_method(
                    technique_id=technique_id,
                    item=it,
                    difficulty=difficulty,
                    profile=profile,
                )
                if gen_img:
                    out.append({
                        "sentence": (it.get("sentence") or "").strip(),
                        "image": gen_img,
                        "required_content": it.get("required_content") or [],
                        "required_function": it.get("required_function") or [],
                        "tags": it.get("tags") or [],
                        "source": "generated",
                        "generation_method": gen_method,
                        "image_prompt": it.get("image_prompt", ""),
                        "matched_catalog_id": None,
                    })
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

async def build_content_pack(profile: dict, technique_id: str, n_items: int = 10) -> dict[str, Any]:
    difficulty = int(profile.get("difficulty", 1))
    catalog = load_image_catalog()

    raw_items = generate_pack_items(
        profile=profile,
        technique_id=technique_id,
        n_items=n_items,
        catalog=catalog,
    )

    cleaned = _validate_and_clean_items(profile, technique_id, raw_items, n_items)

    final_items = await attach_images_to_items(
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