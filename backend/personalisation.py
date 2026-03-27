import base64
import hashlib
import json
import os
import random
import re
import uuid
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from openai import OpenAI

from local_image_generation import LocalImageGenerationError, generate_image_via_comfyui
from models import ImageAsset


# =========================================================
# Paths / catalog loading
# =========================================================

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
PUBLIC_DIR = PROJECT_ROOT / "frontend" / "stroke-recovery" / "public"

CATALOG_PATH = PUBLIC_DIR / "therapy_catalog.json"
NAMING_WITH_CUES_CATALOG_PATH = PUBLIC_DIR / "naming_with_cues.json"
SFA_CATALOG_PATH = PUBLIC_DIR / "semantic_feature_analysis.json"
ACTION_VERB_CATALOG_PATH = PUBLIC_DIR / "action_verb_naming.json"

GENERATED_DIR = PUBLIC_DIR / "generated"
GENERATED_IMAGE_NAMING_DIR = GENERATED_DIR / "image_naming"
GENERATED_NAMING_WITH_CUES_DIR = GENERATED_DIR / "naming_with_cues"
GENERATED_SENTENCE_DIR = GENERATED_DIR / "sentence_repetition"
GENERATED_SFA_DIR = GENERATED_DIR / "semantic_feature_analysis"
GENERATED_ACTION_VERB_DIR = GENERATED_DIR / "action_verb_naming"

GENERATED_IMAGE_NAMING_DIR.mkdir(parents=True, exist_ok=True)
GENERATED_NAMING_WITH_CUES_DIR.mkdir(parents=True, exist_ok=True)
GENERATED_SENTENCE_DIR.mkdir(parents=True, exist_ok=True)
GENERATED_SFA_DIR.mkdir(parents=True, exist_ok=True)
GENERATED_ACTION_VERB_DIR.mkdir(parents=True, exist_ok=True)

TECHNIQUE_OUTPUT_DIRS: dict[str, Path] = {
    "image_naming": GENERATED_IMAGE_NAMING_DIR,
    "naming_with_cues": GENERATED_NAMING_WITH_CUES_DIR,
    "sentence_repetition": GENERATED_SENTENCE_DIR,
    "semantic_feature_analysis": GENERATED_SFA_DIR,
    "action_verb_naming": GENERATED_ACTION_VERB_DIR,
}

TECHNIQUE_PUBLIC_DIRS: dict[str, str] = {
    "image_naming": "generated/image_naming",
    "naming_with_cues": "generated/naming_with_cues",
    "sentence_repetition": "generated/sentence_repetition",
    "semantic_feature_analysis": "generated/semantic_feature_analysis",
    "action_verb_naming": "generated/action_verb_naming",
}

DIFFICULTY_MIN = 1
DIFFICULTY_MAX = 3

# Mixing difficulty for each level appropriately 
DIFFICULTY_MIXES: dict[int, dict[int, float]] = {
    1: {1: 0.80, 2: 0.20},
    2: {1: 0.20, 2: 0.60, 3: 0.20},
    3: {2: 0.30, 3: 0.70},
}

DEFAULT_FUNCTION_WORDS = {
    "a", "an", "the",
    "is", "am", "are", "was", "were", "be", "being", "been",
    "to", "of", "in", "on", "at", "by", "for", "with", "from",
    "and", "or", "but",
    "will", "would", "can", "could", "should", "shall", "may", "might",
    "do", "does", "did",
    "has", "have", "had",
    "he", "she", "it", "they", "we", "you", "i",
    "him", "her", "them", "us",
    "this", "that", "these", "those",
    "my", "your", "his", "their", "our",
    "as", "if", "than", "then", "so", "because", "while",
    "before", "after", "during", "into", "onto", "over", "under",
    "near", "around", "through", "across", "behind", "beside",
}


def _load_catalog_file(
    path: Path,
    list_key: str | None = None,
    merge_keys: tuple[str, ...] | None = None,
) -> list[dict]:
    if not path.exists():
        return []

    raw = json.loads(path.read_text(encoding="utf-8"))

    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]

    if not isinstance(raw, dict):
        return []

    if merge_keys:
        merged: list[dict] = []
        for key in merge_keys:
            value = raw.get(key, [])
            if isinstance(value, list):
                merged.extend(item for item in value if isinstance(item, dict))
        return merged

    if list_key:
        value = raw.get(list_key, [])
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    return []


def load_image_catalog() -> list[dict]:
    return _load_catalog_file(
        CATALOG_PATH,
        merge_keys=("image_naming", "naming_with_cues", "sentence_repetition"),
    )


def load_naming_with_cues_catalog() -> list[dict]:
    dedicated = _load_catalog_file(NAMING_WITH_CUES_CATALOG_PATH)
    therapy = _load_catalog_file(
        CATALOG_PATH,
        list_key="naming_with_cues",
    )

    merged: list[dict] = []
    seen: set[str] = set()

    for item in dedicated + therapy:
        if not isinstance(item, dict):
            continue

        key = _norm(item.get("id") or item.get("word") or "")
        if not key or key in seen:
            continue

        seen.add(key)
        merged.append(item)

    return merged


def load_semantic_feature_catalog() -> list[dict]:
    return _load_catalog_file(
        SFA_CATALOG_PATH,
        list_key="semantic_feature_analysis",
    )


def load_action_verb_catalog() -> list[dict]:
    return _load_catalog_file(
        ACTION_VERB_CATALOG_PATH,
        list_key="action_verb_naming",
    )


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
# Cleaning helpers
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


def _clean_subject(subject: str) -> str:
    subject = re.sub(r"\s+", " ", str(subject or "").strip().lower())
    subject = re.sub(r"[^a-z\s]", "", subject)
    return subject[:60].strip()


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


def _profile_interest_fields(profile: dict) -> tuple[str, str]:
    return profile.get("interests_text", ""), profile.get("avoid_topics_text", "")


def _target_label_for_item(item: dict) -> str:
    return item.get("word", "") or item.get("verb", "") or item.get("sentence", "")


def _clamp_difficulty(value: int) -> int:
    return max(DIFFICULTY_MIN, min(DIFFICULTY_MAX, int(value)))


def _shuffle_copy(items: list[dict]) -> list[dict]:
    shuffled = list(items)
    random.shuffle(shuffled)
    return shuffled


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
# Sentence repetition helpers
# =========================================================

def _sentence_tokens(text: str | None) -> list[str]:
    sentence = _clean_sentence(text or "")
    return re.findall(r"[A-Za-z0-9']+", sentence)


def _sentence_word_count(text: str | None) -> int:
    return len(_sentence_tokens(text))


def _normalise_sentence_surface(text: str) -> str:
    text = _clean_sentence(text)
    if not text:
        return ""

    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"[!?]{2,}", "!", text)
    text = re.sub(r"\.{2,}", ".", text)

    if text:
        text = text[0].upper() + text[1:]

    if text and text[-1] not in ".!?":
        text += "."

    return text


def _sentence_catalog_surface(item: dict) -> str:
    return (
        item.get("sentence")
        or item.get("english_label")
        or item.get("id")
        or ""
    )


def _sentence_effective_level(difficulty: int, sentence_length: int) -> int:
    return _clamp_difficulty(max(_clamp_difficulty(difficulty), _clamp_difficulty(sentence_length)))


def _sentence_constraints(difficulty: int, sentence_length: int) -> dict[str, Any]:
    effective_level = _sentence_effective_level(difficulty, sentence_length)

    if effective_level == 1:
        return {
            "effective_level": 1,
            "word_min": 4,
            "word_max": 6,
            "min_content_words": 2,
            "length_label": "short",
            "difficulty_label": "short/simple",
        }

    if effective_level == 2:
        return {
            "effective_level": 2,
            "word_min": 7,
            "word_max": 10,
            "min_content_words": 3,
            "length_label": "medium",
            "difficulty_label": "medium/moderate",
        }

    return {
        "effective_level": 3,
        "word_min": 11,
        "word_max": 14,
        "min_content_words": 5,
        "length_label": "long",
        "difficulty_label": "longer/richer",
    }


def _sentence_examples_for_level(effective_level: int) -> list[str]:
    if effective_level == 1:
        return [
            "The boy kicks the ball.",
            "A cat sleeps on the sofa.",
        ]
    if effective_level == 2:
        return [
            "The woman carries groceries into the kitchen.",
            "A young boy carefully waters the garden plants.",
        ]
    return [
        "The tired father carries two heavy shopping bags into the house.",
        "A cheerful girl carefully places bright flowers beside the kitchen window.",
    ]


def _derive_targets_from_sentence(sentence: str) -> tuple[list[str], list[str]]:
    tokens = [_clean_token(token) for token in _sentence_tokens(sentence)]
    tokens = [token for token in tokens if token]

    content: list[str] = []
    function: list[str] = []
    seen_content: set[str] = set()
    seen_function: set[str] = set()

    for token in tokens:
        if token in DEFAULT_FUNCTION_WORDS:
            if token not in seen_function:
                seen_function.add(token)
                function.append(token)
        else:
            if token not in seen_content:
                seen_content.add(token)
                content.append(token)

    return content, function


def _sentence_meets_constraints(sentence: str, difficulty: int, sentence_length: int) -> bool:
    constraints = _sentence_constraints(difficulty, sentence_length)
    word_count = _sentence_word_count(sentence)
    content_words, _ = _derive_targets_from_sentence(sentence)

    if word_count < constraints["word_min"] or word_count > constraints["word_max"]:
        return False

    if len(content_words) < constraints["min_content_words"]:
        return False

    return True


# =========================================================
# Difficulty helpers
# =========================================================

def _difficulty_mix_for_level(difficulty: int) -> dict[int, float]:
    return DIFFICULTY_MIXES.get(_clamp_difficulty(difficulty), DIFFICULTY_MIXES[1])


def _difficulty_weighted_order(
    entries: list[dict],
    target_difficulty: int,
    limit: int,
    key_fn: Callable[[dict], str],
) -> list[dict]:
    """
    Build items in a weighted order based on their difficulty:
    then fill any remaining slots from nearest difficulty bands first
    """
    target_difficulty = _clamp_difficulty(target_difficulty)
    mix = _difficulty_mix_for_level(target_difficulty)

    buckets: dict[int, list[dict]] = {1: [], 2: [], 3: []}
    for item in entries:
        level = _clamp_difficulty(_safe_item_difficulty(item))
        buckets[level].append(item)

    out: list[dict] = []
    seen: set[str] = set()

    def try_add(item: dict) -> bool:
        key = key_fn(item)
        if not key or key in seen:
            return False
        seen.add(key)
        out.append(item)
        return True

    # First pass: take proportional quotas
    for level, weight in mix.items():
        target_count = max(0, round(limit * weight))
        taken = 0
        for item in buckets.get(level, []):
            if len(out) >= limit or taken >= target_count:
                break
            if try_add(item):
                taken += 1

    # Second pass: fill remaining from nearest levels first
    fallback_level_orders = {
        1: [1, 2, 3],
        2: [2, 1, 3],
        3: [3, 2, 1],
    }

    for level in fallback_level_orders[target_difficulty]:
        for item in buckets.get(level, []):
            if len(out) >= limit:
                return out[:limit]
            try_add(item)

    return out[:limit]


def _sentence_length_band_from_text(text: str | None) -> int:
    tokens = [token for token in _clean_sentence(text or "").split() if token]
    length = len(tokens)

    if length <= 6:
        return 1
    if length <= 10:
        return 2
    return 3


def _sentence_length_matches(
    item: dict,
    sentence_length: int,
) -> bool:
    sentence_length = _clamp_difficulty(sentence_length)
    label = _sentence_catalog_surface(item)
    band = _sentence_length_band_from_text(str(label).replace("_", " "))
    return band == sentence_length


def _difficulty_description_block(technique_id: str, sentence_length: int | None = None, difficulty: int | None = None) -> str:
    if technique_id in {"image_naming", "naming_with_cues"}:
        return """
Difficulty guide:
- 1 = very common, concrete, visually obvious everyday nouns
- 2 = everyday nouns with a bit more lexical load, length, or specificity
- 3 = more specific, longer, lower-frequency but still concrete and imageable nouns
""".strip()

    if technique_id == "action_verb_naming":
        return """
Difficulty guide:
- 1 = very common, visually obvious everyday actions
- 2 = everyday actions with a bit more specificity
- 3 = more specific or less frequent but still clearly imageable actions
""".strip()

    if technique_id == "semantic_feature_analysis":
        return """
Difficulty guide:
- 1 = highly familiar, easy-to-describe concrete concepts
- 2 = everyday concepts with slightly more specificity
- 3 = more specific but still semantically rich and describable concepts
""".strip()

    effective_level = _sentence_effective_level(difficulty or 1, sentence_length or 1)
    constraints = _sentence_constraints(difficulty or 1, sentence_length or 1)

    return f"""
Difficulty guide:
- 1 = short simple sentences with very common words and simple syntax
- 2 = medium-length sentences with slightly richer vocabulary
- 3 = longer sentences with more content words and harder vocabulary

MANDATORY word-count range for this pack:
- effective sentence level {effective_level}
- {constraints["word_min"]}-{constraints["word_max"]} words per sentence

This is a hard rule:
- do not generate any sentence shorter than {constraints["word_min"]} words
- do not generate any sentence longer than {constraints["word_max"]} words

Richer difficulty means:
- level 1 = one simple idea only
- level 2 = one fuller everyday scene with a little more detail
- level 3 = a clearly richer scene with extra detail such as place, object, description, or added action
""".strip()


# =========================================================
# Catalog helpers
# =========================================================

def _safe_item_difficulty(item: dict) -> int:
    try:
        return _clamp_difficulty(int(item.get("difficulty", 1)))
    except Exception:
        return 1


def _catalog_entries_for_technique(
    catalog: list[dict],
    technique_id: str,
) -> list[dict]:
    return [
        item
        for item in catalog
        if item.get("technique") == technique_id
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
        verb = _norm(item.get("verb", ""))
        label = _norm(_sentence_catalog_surface(item))

        combined = set(tags)
        if word:
            combined.add(word)
        if verb:
            combined.add(verb)
        if label:
            combined.add(label)

        if avoid_tokens and combined & avoid_tokens:
            continue

        general.append(item)

        if interest_tokens and combined & interest_tokens:
            preferred.append(item)

    return preferred, general


def _weighted_preferred_then_general(
    preferred: list[dict],
    general: list[dict],
    difficulty: int,
    limit: int,
    key_fn: Callable[[dict], str],
) -> list[dict]:
    preferred_take = _difficulty_weighted_order(preferred, difficulty, limit, key_fn=key_fn)

    if len(preferred_take) >= limit:
        return preferred_take[:limit]

    already = {key_fn(item) for item in preferred_take if key_fn(item)}
    remaining_general = [item for item in general if key_fn(item) not in already]

    general_take = _difficulty_weighted_order(
        remaining_general,
        difficulty,
        limit - len(preferred_take),
        key_fn=key_fn,
    )

    return (preferred_take + general_take)[:limit]


def _get_image_naming_candidates(
    catalog: list[dict],
    difficulty: int,
    interests_text: str,
    avoid_topics_text: str,
    limit: int = 200,
) -> list[str]:
    entries = _catalog_entries_for_technique(catalog, "image_naming")
    preferred, general = _filter_catalog_by_interests(entries, interests_text, avoid_topics_text)

    ordered = _weighted_preferred_then_general(
        preferred=preferred,
        general=general,
        difficulty=difficulty,
        limit=limit,
        key_fn=lambda item: _clean_display_word(item.get("word", "")),
    )

    out: list[str] = []
    seen: set[str] = set()

    for item in ordered:
        word = _clean_display_word(item.get("word", ""))
        if not word or word in seen:
            continue
        seen.add(word)
        out.append(word)

    return out[:limit]


def _get_naming_with_cues_candidates(
    catalog: list[dict],
    difficulty: int,
    interests_text: str,
    avoid_topics_text: str,
    limit: int = 200,
) -> list[str]:
    cue_entries = _catalog_entries_for_technique(catalog, "naming_with_cues")
    image_entries = _catalog_entries_for_technique(catalog, "image_naming")

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

    ordered_cue = _weighted_preferred_then_general(
        preferred=preferred_cue,
        general=general_cue,
        difficulty=difficulty,
        limit=limit,
        key_fn=lambda item: _clean_display_word(item.get("word", "")),
    )
    ordered_img = _weighted_preferred_then_general(
        preferred=preferred_img,
        general=general_img,
        difficulty=difficulty,
        limit=limit,
        key_fn=lambda item: _clean_display_word(item.get("word", "")),
    )

    words: list[str] = []
    seen: set[str] = set()

    for pool in (ordered_cue, ordered_img):
        for item in pool:
            word = _clean_display_word(item.get("word", ""))
            if not word or word in seen:
                continue
            seen.add(word)
            words.append(word)
            if len(words) >= limit:
                return words

    return words[:limit]


def _get_sentence_candidates(
    catalog: list[dict],
    difficulty: int,
    sentence_length: int,
    interests_text: str,
    avoid_topics_text: str,
    limit: int = 100,
) -> list[dict]:
    entries = _catalog_entries_for_technique(catalog, "sentence_repetition")
    preferred, general = _filter_catalog_by_interests(entries, interests_text, avoid_topics_text)

    preferred_matching = [item for item in preferred if _sentence_length_matches(item, sentence_length)]
    general_matching = [item for item in general if _sentence_length_matches(item, sentence_length)]

    ordered = _weighted_preferred_then_general(
        preferred=preferred_matching or preferred,
        general=general_matching or general,
        difficulty=difficulty,
        limit=limit,
        key_fn=lambda item: _norm(item.get("id", "") or _sentence_catalog_surface(item)),
    )

    out: list[dict] = []
    seen: set[str] = set()

    for item in ordered:
        key = item.get("id") or _sentence_catalog_surface(item)
        if not key or key in seen:
            continue
        seen.add(key)

        sentence_text = _normalise_sentence_surface(_sentence_catalog_surface(item).replace("_", " ").strip())
        required_content = item.get("required_content") or []
        required_function = item.get("required_function") or []

        if not required_content or not required_function:
            derived_content, derived_function = _derive_targets_from_sentence(sentence_text)
            required_content = required_content or derived_content
            required_function = required_function or derived_function

        out.append(
            {
                "id": item.get("id"),
                "sentence": sentence_text,
                "required_content": required_content,
                "required_function": required_function,
                "tags": item.get("tags", []),
                "image": _image_field(item),
                "difficulty": _safe_item_difficulty(item),
            }
        )
        if len(out) >= limit:
            return out

    return out[:limit]


def _get_semantic_feature_candidates(
    catalog: list[dict],
    difficulty: int,
    interests_text: str,
    avoid_topics_text: str,
    limit: int = 200,
) -> list[dict]:
    entries = _catalog_entries_for_technique(catalog, "semantic_feature_analysis")
    preferred, general = _filter_catalog_by_interests(entries, interests_text, avoid_topics_text)

    ordered = _weighted_preferred_then_general(
        preferred=preferred,
        general=general,
        difficulty=difficulty,
        limit=limit,
        key_fn=lambda item: _clean_display_word(item.get("word", "")),
    )

    out: list[dict] = []
    seen: set[str] = set()

    for item in ordered:
        word = _clean_display_word(item.get("word", ""))
        if not word or word in seen:
            continue
        seen.add(word)
        out.append(item)
        if len(out) >= limit:
            return out

    return out[:limit]


def _get_action_verb_candidates(
    catalog: list[dict],
    difficulty: int,
    interests_text: str,
    avoid_topics_text: str,
    limit: int = 200,
) -> list[dict]:
    entries = _catalog_entries_for_technique(catalog, "action_verb_naming")
    preferred, general = _filter_catalog_by_interests(entries, interests_text, avoid_topics_text)

    ordered = _weighted_preferred_then_general(
        preferred=preferred,
        general=general,
        difficulty=difficulty,
        limit=limit,
        key_fn=lambda item: _clean_display_word(item.get("verb", "")),
    )

    out: list[dict] = []
    seen: set[str] = set()

    for item in ordered:
        verb = _clean_display_word(item.get("verb", ""))
        if not verb or verb in seen:
            continue
        seen.add(verb)
        out.append(item)
        if len(out) >= limit:
            return out

    return out[:limit]


def _find_catalog_match_by_word(
    entries: list[dict],
    word: str | None = None,
    verb: str | None = None,
) -> tuple[str | None, dict | None]:
    target_word = _norm(word or "")
    target_verb = _norm(verb or "")

    for item in entries:
        item_word = _norm(item.get("word", ""))
        item_verb = _norm(item.get("verb", ""))
        item_id = _norm(item.get("id", ""))

        if target_word and (item_word == target_word or item_id == target_word):
            image = _image_field(item)
            if image:
                return image, item

        if target_verb and (item_verb == target_verb or item_id == target_verb):
            image = _image_field(item)
            if image:
                return image, item

    return None, None


def _pick_catalog_image_strict(
    catalog: list[dict],
    difficulty: int,
    technique_id: str,
    word: str | None = None,
    verb: str | None = None,
    english_label: str | None = None,
) -> tuple[str | None, dict | None]:
    """
    prefer strict catalog matches for the target word/verb and technique, 
    but allow limiteed fallback to related techniques for sentence repetition and semantic feature analysis
    """
    target_difficulty = _clamp_difficulty(difficulty)

    def entries_at_or_below(tech_id: str) -> list[dict]:
        return [
            item
            for item in _catalog_entries_for_technique(catalog, tech_id)
            if _safe_item_difficulty(item) <= target_difficulty
        ]

    if technique_id == "naming_with_cues":
        cue_entries = entries_at_or_below("naming_with_cues")
        image, item = _find_catalog_match_by_word(cue_entries, word=word)
        if image:
            return image, item

        image_entries = entries_at_or_below("image_naming")
        image, item = _find_catalog_match_by_word(image_entries, word=word)
        if image:
            return image, item

        return None, None

    if technique_id == "image_naming":
        entries = entries_at_or_below("image_naming")
        return _find_catalog_match_by_word(entries, word=word)

    if technique_id == "semantic_feature_analysis":
        entries = entries_at_or_below("semantic_feature_analysis")
        image, item = _find_catalog_match_by_word(entries, word=word)
        if image:
            return image, item

        image_entries = entries_at_or_below("image_naming")
        image, item = _find_catalog_match_by_word(image_entries, word=word)
        if image:
            return image, item

        cue_entries = entries_at_or_below("naming_with_cues")
        image, item = _find_catalog_match_by_word(cue_entries, word=word)
        if image:
            return image, item

        return None, None

    if technique_id == "action_verb_naming":
        entries = entries_at_or_below("action_verb_naming")
        return _find_catalog_match_by_word(entries, verb=verb)

    if technique_id == "sentence_repetition" and english_label:
        entries = entries_at_or_below("sentence_repetition")
        target = _norm(english_label)

        for item in entries:
            sentence_value = _norm(_sentence_catalog_surface(item))
            item_id = _norm(item.get("id", ""))
            if sentence_value == target or item_id == target:
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
    difficulty = _clamp_difficulty(int(profile.get("difficulty", 1)))
    sentence_length = _clamp_difficulty(int(profile.get("sentence_length", 1)))
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

        difficulty_block = _difficulty_description_block(technique_id)

        if technique_id == "image_naming":
            return f"""
Return STRICT JSON ONLY (no markdown, no commentary, no extra keys).

You are generating personalised image-naming therapy items for a stroke-recovery app.

User interests/routine: {interests}
Avoid topics: {avoid}
Target difficulty: {difficulty}

{difficulty_block}

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
- Keep the pack consistent with the target difficulty.
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

{difficulty_block}

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
- Keep the pack consistent with the target difficulty.
- Lower difficulty cues should be more direct. Higher difficulty cues may be a little less direct, but still helpful.
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
                f"- {word}: difficulty={_safe_item_difficulty(item)}; tags={tags}; hint={semantic_hint}"
            )

        candidate_preview = "\n".join(candidate_lines)

        mode_rule = (
            "Prefer these catalog targets first, but you may suggest a closely related everyday target if useful."
            if allow_generated_images
            else "Stay close to the curated target pool."
        )

        difficulty_block = _difficulty_description_block(technique_id)

        return f"""
Return STRICT JSON ONLY (no markdown, no commentary, no extra keys).

You are generating personalised semantic feature analysis therapy items for a stroke-recovery app.

User interests/routine: {interests}
Avoid topics: {avoid}
Target difficulty: {difficulty}

{difficulty_block}

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
- semantic_hint should help the user describe or think about the item without giving the answer away too bluntly.
- feature_prompts should be short, natural prompts suitable for semantic feature analysis.
- image_prompt must clearly describe one simple recognisable image for the target item.
- Keep the pack consistent with the target difficulty.
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

    if technique_id == "action_verb_naming":
        candidates = _get_action_verb_candidates(
            catalog=catalog,
            difficulty=difficulty,
            interests_text=interests,
            avoid_topics_text=avoid,
            limit=120,
        )

        candidate_lines = []
        for item in candidates[:50]:
            verb = item.get("verb") or item.get("base_verb") or item.get("id")
            subject = item.get("subject") or "person"
            tags = ", ".join((item.get("tags") or [])[:6])
            semantic_hint = item.get("semantic_hint") or ""
            candidate_lines.append(
                f"- {verb}: difficulty={_safe_item_difficulty(item)}; subject={subject}; tags={tags}; hint={semantic_hint}"
            )

        candidate_preview = "\n".join(candidate_lines)

        mode_rule = (
            "Prefer these catalog actions first, but you may suggest a closely related everyday action if useful."
            if allow_generated_images
            else "Stay close to the curated action pool."
        )

        difficulty_block = _difficulty_description_block(technique_id)

        return f"""
Return STRICT JSON ONLY (no markdown, no commentary, no extra keys).

You are generating personalised action verb naming therapy items for a stroke-recovery app.

User interests/routine: {interests}
Avoid topics: {avoid}
Target difficulty: {difficulty}

{difficulty_block}

Available curated action targets:
{candidate_preview}

Rules:
- Exactly {n_items} items.
- Make the set varied. Do not repeat the same action or near-duplicate actions.
- Each item must follow:
{{
  "verb": "string",
  "base_verb": "string",
  "acceptable_answers": ["string","string"],
  "subject": "string",
  "semantic_hint": "string",
  "tags": ["tag1","tag2","tag3"],
  "image_prompt": "string"
}}

- "verb" should usually be the action form the user is likely to say from the picture, like "running", "eating", "reading".
- "base_verb" should be the plain form, like "run", "eat", "read".
- acceptable_answers should include both the base form and the picture-friendly action form when appropriate.
- subject should usually be "person" unless the scene clearly suggests something like "child", "man", "woman", "dog", or "cat".
- semantic_hint should help the user think about the action without directly saying the verb.
- image_prompt must describe a visible subject clearly doing the action.
- The image must make the action obvious, not just show an object linked to the action.
- Prefer safe, everyday, easy-to-picture actions.
- Avoid abstract, unsafe, upsetting, medical, legal, political, or confusing actions.
- Keep the pack consistent with the target difficulty.
- {mode_rule}
- Output JSON only.

Schema:
{{
  "items": [
    {{
      "verb": "string",
      "base_verb": "string",
      "acceptable_answers": ["string","string"],
      "subject": "string",
      "semantic_hint": "string",
      "tags": ["tag1","tag2","tag3"],
      "image_prompt": "string"
    }}
  ]
}}
""".strip()

    sentence_candidates = _get_sentence_candidates(
        catalog=catalog,
        difficulty=difficulty,
        sentence_length=sentence_length,
        interests_text=interests,
        avoid_topics_text=avoid,
        limit=120,
    )

    scene_hint_lines = []
    for candidate in sentence_candidates[:40]:
        tags = ", ".join(candidate.get("tags", [])[:6])
        label = candidate.get("sentence") or candidate.get("id")
        scene_hint_lines.append(
            f"- {label}: difficulty={candidate.get('difficulty', difficulty)}; {tags}"
        )

    scene_hints = "\n".join(scene_hint_lines)
    sentence_constraints = _sentence_constraints(difficulty, sentence_length)
    difficulty_block = _difficulty_description_block(
        technique_id,
        sentence_length=sentence_length,
        difficulty=difficulty,
    )
    sentence_examples = "\n".join(
        f"- {example}"
        for example in _sentence_examples_for_level(sentence_constraints["effective_level"])
    )

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
Requested sentence length level: {sentence_length}

{difficulty_block}

Curated scene hints:
{scene_hints}

Correct example style for this pack:
{sentence_examples}

Rules:
- Exactly {n_items} items.
- Sentences must be clear, easy to picture, and everyday.
- Prefer simple action scenes involving people, objects, food, pets, sports, home, travel, hobbies, or routines.
- Avoid abstract, unsafe, upsetting, political, medical, legal, or confusing content.
- image_prompt must clearly describe a single scene matching the sentence.
- Keep the pack consistent with both the target difficulty and the sentence length target.
- Lower difficulty = simpler vocabulary and syntax.
- Higher difficulty = longer sentences, more content words, and slightly harder vocabulary.
- {mode_rule}

CRITICAL SENTENCE RULES:
- EVERY sentence must be between {sentence_constraints["word_min"]} and {sentence_constraints["word_max"]} words.
- Do not generate any sentence shorter than {sentence_constraints["word_min"]} words.
- Do not generate any sentence longer than {sentence_constraints["word_max"]} words.
- Difficulty 1 must feel short and simple.
- Difficulty 2 must feel medium and moderately richer.
- Difficulty 3 must feel clearly longer and richer, with extra scene detail.
- Reject your own draft internally if it does not match the required word-count range before you output JSON.

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

    if technique_id == "action_verb_naming":
        return {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "verb": {"type": "string"},
                            "base_verb": {"type": "string"},
                            "acceptable_answers": {"type": "array", "items": {"type": "string"}},
                            "subject": {"type": "string"},
                            "semantic_hint": {"type": "string"},
                            "tags": {"type": "array", "items": {"type": "string"}},
                            "image_prompt": {"type": "string"},
                        },
                        "required": [
                            "verb",
                            "base_verb",
                            "acceptable_answers",
                            "subject",
                            "semantic_hint",
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
# Validation / cleanup
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


def _default_action_acceptable_answers(verb: str, base_verb: str) -> list[str]:
    out: list[str] = []
    for value in [base_verb, verb]:
        cleaned = _clean_display_word(value)
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out[:6]


def _validate_and_clean_items(
    profile: dict,
    technique_id: str,
    items: list[dict],
    n_items: int,
) -> list[dict]:
    out: list[dict] = []
    difficulty = _clamp_difficulty(int(profile.get("difficulty", 1)))
    sentence_length = _clamp_difficulty(int(profile.get("sentence_length", 1)))

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

        elif technique_id == "action_verb_naming":
            verb = _clean_display_word(raw.get("verb", ""))
            base_verb = _clean_display_word(raw.get("base_verb", "") or verb)

            if not verb:
                continue

            if not base_verb:
                base_verb = verb

            tags = _ensure_list_of_tags(raw.get("tags", []), max_len=8)
            if verb not in tags:
                tags = [verb] + tags

            acceptable_answers = _ensure_list_of_strings(
                raw.get("acceptable_answers", []),
                max_len=6,
                max_item_len=60,
            )
            acceptable_answers = [
                _clean_display_word(value)
                for value in acceptable_answers
                if _clean_display_word(value)
            ]

            for value in _default_action_acceptable_answers(verb, base_verb):
                if value not in acceptable_answers:
                    acceptable_answers.append(value)

            acceptable_answers = acceptable_answers[:6]

            subject = _clean_subject(raw.get("subject", "") or "person") or "person"
            semantic_hint = _clean_semantic_cue(raw.get("semantic_hint", ""))

            fallback_prompt = (
                f"A simple clear therapy-style illustration showing a {subject} clearly {verb}. "
                f"The subject must be visible and the action must be obvious. "
                f"Do not focus only on an object linked to the action."
            )
            image_prompt = _clean_image_prompt(raw.get("image_prompt", ""), fallback_prompt)

            out.append(
                {
                    "verb": verb,
                    "base_verb": base_verb,
                    "acceptable_answers": acceptable_answers,
                    "subject": subject,
                    "semantic_hint": semantic_hint,
                    "tags": tags[:8],
                    "image_prompt": image_prompt,
                }
            )

        else:
            sentence = _normalise_sentence_surface(raw.get("sentence", ""))
            if not sentence:
                continue

            if not _sentence_meets_constraints(
                sentence=sentence,
                difficulty=difficulty,
                sentence_length=sentence_length,
            ):
                continue

            derived_content, derived_function = _derive_targets_from_sentence(sentence)
            if len(derived_content) < _sentence_constraints(difficulty, sentence_length)["min_content_words"]:
                continue

            tags = _ensure_list_of_tags(raw.get("tags", []), max_len=8)
            if not tags:
                tags = derived_content[:3]

            fallback_prompt = f"A simple clear therapy-style illustration showing: {sentence}"
            image_prompt = _clean_image_prompt(raw.get("image_prompt", ""), fallback_prompt)

            out.append(
                {
                    "sentence": sentence,
                    "required_content": derived_content[:6],
                    "required_function": derived_function[:6],
                    "tags": tags[:8],
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


def _validate_and_clean_action_verb_items(
    items: list[dict],
    n_items: int,
) -> list[dict]:
    out: list[dict] = []

    for raw in items:
        if not isinstance(raw, dict):
            continue

        verb = _clean_display_word(raw.get("verb", ""))
        base_verb = _clean_display_word(raw.get("base_verb", "") or verb)
        image = str(raw.get("image", "") or "").strip()

        if not verb or not image:
            continue

        if not base_verb:
            base_verb = verb

        tags = _ensure_list_of_tags(raw.get("tags", []), max_len=8)
        if verb not in tags:
            tags = [verb] + tags

        acceptable_answers = _ensure_list_of_strings(
            raw.get("acceptable_answers", []),
            max_len=6,
            max_item_len=60,
        )
        acceptable_answers = [
            _clean_display_word(value)
            for value in acceptable_answers
            if _clean_display_word(value)
        ]

        for value in _default_action_acceptable_answers(verb, base_verb):
            if value not in acceptable_answers:
                acceptable_answers.append(value)

        acceptable_answers = acceptable_answers[:6]
        subject = _clean_subject(raw.get("subject", "") or "person") or "person"
        semantic_hint = _clean_semantic_cue(raw.get("semantic_hint", ""))

        out.append(
            {
                "verb": verb,
                "base_verb": base_verb,
                "acceptable_answers": acceptable_answers,
                "subject": subject,
                "image": image,
                "tags": tags[:8],
                "difficulty": _safe_item_difficulty(raw),
                "semantic_hint": semantic_hint,
                "source": "catalog",
                "matched_catalog_id": raw.get("matched_catalog_id") or raw.get("id"),
                "image_prompt": (
                    f"A simple clear therapy-style illustration showing a {subject} clearly {verb}. "
                    f"The subject must be visible and the action must be obvious. "
                    f"Do not focus only on an object linked to the action."
                ),
            }
        )

        if len(out) >= n_items:
            break

    return out[:n_items]


# =========================================================
# Pack dedupe helpers
# =========================================================

def _item_identity_key(technique_id: str, item: dict) -> str:
    if technique_id in {"image_naming", "naming_with_cues", "semantic_feature_analysis"}:
        return _norm(item.get("word", ""))

    if technique_id == "action_verb_naming":
        verb = _norm(item.get("verb", ""))
        base_verb = _norm(item.get("base_verb", ""))
        return verb or base_verb

    if technique_id == "sentence_repetition":
        return _norm(item.get("sentence", ""))

    return ""


def _dedupe_pack_items(
    items: list[dict],
    technique_id: str,
    limit: int | None = None,
) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()

    for item in items:
        if not isinstance(item, dict):
            continue

        key = _item_identity_key(technique_id, item)
        if not key or key in seen:
            continue

        seen.add(key)
        out.append(item)

        if limit is not None and len(out) >= limit:
            break

    return out


def _extend_with_unique_fillers(
    current_items: list[dict],
    filler_items: list[dict],
    technique_id: str,
    target_count: int,
) -> list[dict]:
    out = list(current_items)
    seen = {
        _item_identity_key(technique_id, item)
        for item in out
        if _item_identity_key(technique_id, item)
    }

    for item in filler_items:
        if len(out) >= target_count:
            break

        key = _item_identity_key(technique_id, item)
        if not key or key in seen:
            continue

        seen.add(key)
        out.append(item)

    return out[:target_count]


# =========================================================
# Catalog fallback items
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

def _catalog_public_asset_exists(image_path: str | None) -> bool:
    rel_path = str(image_path or "").strip().lstrip("/")
    if not rel_path:
        return False
    return (PUBLIC_DIR / rel_path).exists()


def _fallback_sentence_items(
    catalog: list[dict],
    difficulty: int,
    sentence_length: int,
    interests_text: str,
    avoid_topics_text: str,
    n_items: int,
) -> list[dict]:
    entries = _get_sentence_candidates(
        catalog,
        difficulty,
        sentence_length,
        interests_text,
        avoid_topics_text,
        limit=max(40, n_items * 4),
    )

    out: list[dict] = []
    seen = set()

    for item in entries:
        image = str(item.get("image") or "").strip()
        if not image or not _catalog_public_asset_exists(image):
            continue

        key = item.get("id") or item.get("sentence") or image
        if key in seen:
            continue
        seen.add(key)

        sentence = _normalise_sentence_surface((item.get("sentence") or "").strip())
        if not sentence:
            continue

        required_content = item.get("required_content") or []
        required_function = item.get("required_function") or []

        if not required_content or not required_function:
            derived_content, derived_function = _derive_targets_from_sentence(sentence)
            required_content = required_content or derived_content
            required_function = required_function or derived_function

        if not required_content:
            continue

        out.append(
            {
                "sentence": sentence,
                "image": image,
                "required_content": required_content[:6],
                "required_function": required_function[:6],
                "tags": item.get("tags", []) or required_content[:3],
                "source": "catalog",
                "matched_catalog_id": item.get("id"),
                "image_prompt": f"A simple clear therapy-style illustration showing: {sentence}",
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


def _fallback_action_verb_items(
    catalog: list[dict],
    difficulty: int,
    interests_text: str,
    avoid_topics_text: str,
    n_items: int,
) -> list[dict]:
    entries = _get_action_verb_candidates(
        catalog,
        difficulty,
        interests_text,
        avoid_topics_text,
        limit=max(40, n_items * 4),
    )

    return _validate_and_clean_action_verb_items(entries, n_items)


def _catalog_filler_items(
    technique_id: str,
    catalog: list[dict],
    difficulty: int,
    sentence_length: int,
    interests_text: str,
    avoid_topics_text: str,
    n_items: int,
) -> list[dict]:
    if technique_id == "image_naming":
        return _fallback_image_naming_items(
            catalog=catalog,
            difficulty=difficulty,
            interests_text=interests_text,
            avoid_topics_text=avoid_topics_text,
            n_items=n_items,
        )

    if technique_id == "naming_with_cues":
        return _fallback_naming_with_cues_items(
            catalog=catalog,
            difficulty=difficulty,
            interests_text=interests_text,
            avoid_topics_text=avoid_topics_text,
            n_items=n_items,
        )

    if technique_id == "semantic_feature_analysis":
        return _fallback_semantic_feature_items(
            catalog=catalog,
            difficulty=difficulty,
            interests_text=interests_text,
            avoid_topics_text=avoid_topics_text,
            n_items=n_items,
        )

    if technique_id == "action_verb_naming":
        return _fallback_action_verb_items(
            catalog=catalog,
            difficulty=difficulty,
            interests_text=interests_text,
            avoid_topics_text=avoid_topics_text,
            n_items=n_items,
        )

    return _fallback_sentence_items(
        catalog=catalog,
        difficulty=difficulty,
        sentence_length=sentence_length,
        interests_text=interests_text,
        avoid_topics_text=avoid_topics_text,
        n_items=n_items,
    )


def _fill_short_pack_from_catalog(
    cleaned_items: list[dict],
    profile: dict,
    technique_id: str,
    catalog: list[dict],
    difficulty: int,
    n_items: int,
) -> list[dict]:
    if len(cleaned_items) >= n_items:
        return cleaned_items[:n_items]

    missing = n_items - len(cleaned_items)
    interests_text, avoid_topics_text = _profile_interest_fields(profile)
    sentence_length = _clamp_difficulty(int(profile.get("sentence_length", 1)))

    fillers = _catalog_filler_items(
        technique_id=technique_id,
        catalog=catalog,
        difficulty=difficulty,
        sentence_length=sentence_length,
        interests_text=interests_text,
        avoid_topics_text=avoid_topics_text,
        n_items=max(missing * 3, missing),
    )

    return _extend_with_unique_fillers(
        current_items=cleaned_items,
        filler_items=fillers,
        technique_id=technique_id,
        target_count=n_items,
    )


# =========================================================
# Generated image helpers
# =========================================================

def _generated_output_dir(technique_id: str) -> Path:
    return TECHNIQUE_OUTPUT_DIRS.get(technique_id, GENERATED_SENTENCE_DIR)


def _generated_public_path(technique_id: str, filename: str) -> str:
    base = TECHNIQUE_PUBLIC_DIRS.get(technique_id, "generated/sentence_repetition")
    return f"{base}/{filename}"


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

    if technique_id == "action_verb_naming":
        verb = item.get("verb", "")
        subject = item.get("subject", "person")
        base_prompt = item.get("image_prompt", "")
        return (
            f"{base_prompt} "
            f"Create a single clear therapy exercise image showing a {subject} clearly {verb}. "
    
            f"The person must be fully visible with a natural human body. "
            f"The action must be obvious and clearly shown through body movement. "
    
            f"STRICT REQUIREMENTS: "
            f"- exactly one person only "
            f"- normal human anatomy "
            f"- exactly two arms and two legs "
            f"- no extra limbs, no duplicated body parts "
            f"- no distorted hands or faces "
    
            f"Keep the pose simple, realistic, and easy to understand. "
    
            f"Use a simple educational illustration style, centered subject, "
            f"clean composition, plain background, no text, no labels, no watermark."
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


def _generated_item_stem(item: dict) -> str:
    return _slugify(
        item.get("word")
        or item.get("verb")
        or item.get("sentence")
        or "generated_item"
    )


def _local_save_prefix(technique_id: str, stem: str, prompt_hash: str) -> str:
    base = TECHNIQUE_PUBLIC_DIRS.get(technique_id, "generated/sentence_repetition")
    return f"{base}/{stem}_{prompt_hash[:10]}"


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
    stem = _generated_item_stem(item)
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

    stem = _generated_item_stem(item)
    save_prefix = _local_save_prefix(technique_id, stem, prompt_hash)

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
# Row builders
# =========================================================

def _merge_row_tags(base_tags: list[str], matched_catalog_item: dict | None = None) -> list[str]:
    tags = base_tags or []

    if not matched_catalog_item or not matched_catalog_item.get("tags"):
        return tags

    merged_tags: list[str] = []
    seen = set()

    for tag in list(matched_catalog_item.get("tags") or []) + list(tags):
        norm_tag = _norm(str(tag))
        if norm_tag and norm_tag not in seen:
            seen.add(norm_tag)
            merged_tags.append(norm_tag)

    return merged_tags or tags


def _build_naming_with_cues_row(
    base_item: dict,
    image_path: str,
    source: str,
    matched_catalog_item: dict | None = None,
    generation_method: str | None = None,
) -> dict:
    tags = _merge_row_tags(base_item.get("tags") or [], matched_catalog_item)

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
    tags = _merge_row_tags(base_item.get("tags") or [], matched_catalog_item)

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


def _build_action_verb_row(
    base_item: dict,
    image_path: str,
    source: str,
    matched_catalog_item: dict | None = None,
    generation_method: str | None = None,
) -> dict:
    tags = _merge_row_tags(base_item.get("tags") or [], matched_catalog_item)

    verb = _clean_display_word(
        (matched_catalog_item.get("verb") if matched_catalog_item else "")
        or base_item.get("verb", "")
    )
    base_verb = _clean_display_word(
        (matched_catalog_item.get("base_verb") if matched_catalog_item else "")
        or base_item.get("base_verb", "")
        or verb
    )
    subject = _clean_subject(
        (matched_catalog_item.get("subject") if matched_catalog_item else "")
        or base_item.get("subject", "")
        or "person"
    ) or "person"

    acceptable_answers_source = (
        (matched_catalog_item.get("acceptable_answers") if matched_catalog_item else None)
        or base_item.get("acceptable_answers")
        or []
    )
    acceptable_answers = _ensure_list_of_strings(
        acceptable_answers_source,
        max_len=6,
        max_item_len=60,
    )
    acceptable_answers = [
        _clean_display_word(value)
        for value in acceptable_answers
        if _clean_display_word(value)
    ]

    for value in _default_action_acceptable_answers(verb, base_verb):
        if value not in acceptable_answers:
            acceptable_answers.append(value)

    row = {
        "verb": verb,
        "base_verb": base_verb,
        "acceptable_answers": acceptable_answers[:6],
        "subject": subject,
        "image": image_path,
        "semantic_hint": _clean_semantic_cue(
            (matched_catalog_item.get("semantic_hint") if matched_catalog_item else "")
            or base_item.get("semantic_hint", "")
        ),
        "tags": tags,
        "source": source,
        "image_prompt": base_item.get("image_prompt", ""),
        "matched_catalog_id": matched_catalog_item.get("id") if matched_catalog_item else None,
    }

    if generation_method:
        row["generation_method"] = generation_method

    return row


def _build_image_naming_row(
    base_item: dict,
    image_path: str,
    source: str,
    matched_catalog_item: dict | None = None,
    generation_method: str | None = None,
) -> dict:
    row = {
        "word": (base_item.get("word") or "").strip(),
        "image": image_path,
        "tags": base_item.get("tags") or [],
        "source": source,
        "image_prompt": base_item.get("image_prompt", ""),
        "matched_catalog_id": matched_catalog_item.get("id") if matched_catalog_item else None,
    }

    if generation_method:
        row["generation_method"] = generation_method

    return row


def _build_sentence_row(
    base_item: dict,
    image_path: str,
    source: str,
    matched_catalog_item: dict | None = None,
    generation_method: str | None = None,
) -> dict:
    row = {
        "sentence": (base_item.get("sentence") or "").strip(),
        "image": image_path,
        "required_content": base_item.get("required_content") or [],
        "required_function": base_item.get("required_function") or [],
        "tags": base_item.get("tags") or [],
        "source": source,
        "image_prompt": base_item.get("image_prompt", ""),
        "matched_catalog_id": matched_catalog_item.get("id") if matched_catalog_item else None,
    }

    if generation_method:
        row["generation_method"] = generation_method

    return row


# =========================================================
# Image attachment
# =========================================================

async def attach_images_to_items(
    items: list[dict],
    catalog: list[dict],
    technique_id: str,
    difficulty: int,
    profile: dict,
) -> list[dict]:
    out: list[dict] = []
    generation_mode = _get_image_generation_mode(profile)
    interests_text, avoid_topics_text = _profile_interest_fields(profile)
    sentence_length = _clamp_difficulty(int(profile.get("sentence_length", 1)))

    print("[ATTACH] generation_mode:", generation_mode)
    print("[ATTACH] technique_id:", technique_id)
    print("[ATTACH] item_count:", len(items))

    for item in items:
        target_label = _target_label_for_item(item)

        existing_image = str(item.get("image") or "").strip()
        existing_source = str(item.get("source") or "").strip().lower()

        if existing_image and existing_source == "catalog" and _catalog_public_asset_exists(existing_image):
            if technique_id == "image_naming":
                out.append(
                    _build_image_naming_row(
                        base_item=item,
                        image_path=existing_image,
                        source="catalog",
                        matched_catalog_item=None,
                    )
                )
                continue

            if technique_id == "naming_with_cues":
                out.append(
                    _build_naming_with_cues_row(
                        base_item=item,
                        image_path=existing_image,
                        source="catalog",
                        matched_catalog_item=None,
                    )
                )
                continue

            if technique_id == "semantic_feature_analysis":
                out.append(
                    _build_semantic_feature_row(
                        base_item=item,
                        image_path=existing_image,
                        source="catalog",
                        matched_catalog_item=None,
                    )
                )
                continue

            if technique_id == "action_verb_naming":
                out.append(
                    _build_action_verb_row(
                        base_item=item,
                        image_path=existing_image,
                        source="catalog",
                        matched_catalog_item=None,
                    )
                )
                continue

            out.append(
                _build_sentence_row(
                    base_item=item,
                    image_path=existing_image,
                    source="catalog",
                    matched_catalog_item=None,
                )
            )
            continue

        # Action verb naming:
        # 1) try cached/generated first
        # 2) only use curated as fallback if generation is off or fails
        if technique_id == "action_verb_naming":
            if generation_mode != "none":
                print("[ATTACH] action verb generated-cache/generation first:", target_label)
                gen_image, gen_method = await _generate_image_with_selected_method(
                    technique_id=technique_id,
                    item=item,
                    difficulty=difficulty,
                    profile=profile,
                )
                if gen_image:
                    out.append(
                        _build_action_verb_row(
                            base_item=item,
                            image_path=gen_image,
                            source="generated",
                            matched_catalog_item=None,
                            generation_method=gen_method,
                        )
                    )
                    continue

            image, matched_catalog_item = _pick_catalog_image_strict(
                catalog=catalog,
                difficulty=difficulty,
                technique_id=technique_id,
                verb=item.get("verb", ""),
            )

            if image:
                print("[ATTACH] action verb fallback curated image:", target_label, "->", image)
                out.append(
                    _build_action_verb_row(
                        base_item=item,
                        image_path=image,
                        source="catalog",
                        matched_catalog_item=matched_catalog_item,
                    )
                )
                continue

        elif technique_id == "naming_with_cues":
            if generation_mode != "none":
                print("[ATTACH] generation-first for naming_with_cues:", target_label)

                gen_image, gen_method = await _generate_image_with_selected_method(
                    technique_id=technique_id,
                    item=item,
                    difficulty=difficulty,
                    profile=profile,
                )
                if gen_image:
                    out.append(
                        _build_naming_with_cues_row(
                            base_item=item,
                            image_path=gen_image,
                            source="generated",
                            matched_catalog_item=None,
                            generation_method=gen_method,
                        )
                    )
                    continue

                print("[ATTACH] generation-first failed, falling back to curated:", target_label)

            image, matched_catalog_item = _pick_catalog_image_strict(
                catalog=catalog,
                difficulty=difficulty,
                technique_id=technique_id,
                word=item.get("word", ""),
            )

            if image:
                print("[ATTACH] curated image used for naming_with_cues:", target_label, "->", image)
                out.append(
                    _build_naming_with_cues_row(
                        base_item=item,
                        image_path=image,
                        source="catalog",
                        matched_catalog_item=matched_catalog_item,
                    )
                )
                continue

        elif technique_id in {"image_naming", "semantic_feature_analysis"}:
            image, matched_catalog_item = _pick_catalog_image_strict(
                catalog=catalog,
                difficulty=difficulty,
                technique_id=technique_id,
                word=item.get("word", ""),
            )

            if image:
                print("[ATTACH] curated image used for:", target_label, "->", image)

                if technique_id == "image_naming":
                    out.append(
                        _build_image_naming_row(
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
                print("[ATTACH] no curated image found, generating for:", target_label)
                gen_image, gen_method = await _generate_image_with_selected_method(
                    technique_id=technique_id,
                    item=item,
                    difficulty=difficulty,
                    profile=profile,
                )
                if gen_image:
                    if technique_id == "image_naming":
                        out.append(
                            _build_image_naming_row(
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
                    _build_sentence_row(
                        base_item=item,
                        image_path=image,
                        source="catalog",
                        matched_catalog_item=matched_catalog_item,
                    )
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
                        _build_sentence_row(
                            base_item=item,
                            image_path=gen_image,
                            source="generated",
                            matched_catalog_item=None,
                            generation_method=gen_method,
                        )
                    )
                    continue

    if len(out) < len(items):
        missing = len(items) - len(out)
        print("[ATTACH] unresolved item count:", missing)

        fillers = _catalog_filler_items(
            technique_id=technique_id,
            catalog=catalog,
            difficulty=difficulty,
            sentence_length=sentence_length,
            interests_text=interests_text,
            avoid_topics_text=avoid_topics_text,
            n_items=missing,
        )
        out.extend(fillers)

    return out[: len(items)]


# =========================================================
# Main pack builders
# =========================================================

def _load_catalog_for_technique(technique_id: str) -> list[dict]:
    if technique_id == "naming_with_cues":
        return load_naming_with_cues_catalog()
    if technique_id == "semantic_feature_analysis":
        return load_semantic_feature_catalog()
    if technique_id == "action_verb_naming":
        return load_action_verb_catalog()
    return load_image_catalog()


async def build_content_pack(
    profile: dict,
    technique_id: str,
    n_items: int = 10,
) -> dict[str, Any]:
    difficulty = _clamp_difficulty(int(profile.get("difficulty", 1)))
    catalog = _load_catalog_for_technique(technique_id)

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
        n_items=n_items * 3,
    )

    cleaned_items = _dedupe_pack_items(
        items=cleaned_items,
        technique_id=technique_id,
        limit=n_items,
    )

    cleaned_items = _fill_short_pack_from_catalog(
        cleaned_items=cleaned_items,
        profile=profile,
        technique_id=technique_id,
        catalog=catalog,
        difficulty=difficulty,
        n_items=n_items,
    )

    final_items = await attach_images_to_items(
        items=cleaned_items,
        catalog=catalog,
        technique_id=technique_id,
        difficulty=difficulty,
        profile=profile,
    )

    final_items = _dedupe_pack_items(
        items=final_items,
        technique_id=technique_id,
        limit=n_items,
    )

    if len(final_items) < n_items:
        fillers = _fill_short_pack_from_catalog(
            cleaned_items=[],
            profile=profile,
            technique_id=technique_id,
            catalog=catalog,
            difficulty=difficulty,
            n_items=n_items,
        )

        final_items = _extend_with_unique_fillers(
            current_items=final_items,
            filler_items=fillers,
            technique_id=technique_id,
            target_count=n_items,
        )

    return {
        "pack_id": uuid.uuid4().hex,
        "technique_id": technique_id,
        "difficulty": difficulty,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "items": final_items[:n_items],
    }


async def build_curated_pack(
    profile: dict,
    technique_id: str,
    n_items: int = 10,
) -> dict[str, Any]:
    difficulty = _clamp_difficulty(int(profile.get("difficulty", 1)))
    sentence_length = _clamp_difficulty(int(profile.get("sentence_length", 1)))
    interests_text, avoid_topics_text = _profile_interest_fields(profile)
    catalog = _load_catalog_for_technique(technique_id)

    curated_profile = {
        **profile,
        "allow_generated_images": False,
        "image_generation_mode": "none",
    }

    curated_items = _catalog_filler_items(
        technique_id=technique_id,
        catalog=catalog,
        difficulty=difficulty,
        sentence_length=sentence_length,
        interests_text=interests_text,
        avoid_topics_text=avoid_topics_text,
        n_items=max(n_items * 4, n_items),
    )

    curated_items = _shuffle_copy(curated_items)
    curated_items = _dedupe_pack_items(
        items=curated_items,
        technique_id=technique_id,
        limit=n_items,
    )

    final_items = await attach_images_to_items(
        items=curated_items,
        catalog=catalog,
        technique_id=technique_id,
        difficulty=difficulty,
        profile=curated_profile,
    )

    final_items = _dedupe_pack_items(
        items=final_items,
        technique_id=technique_id,
        limit=n_items,
    )

    if len(final_items) < n_items:
        fallback_more = _catalog_filler_items(
            technique_id=technique_id,
            catalog=catalog,
            difficulty=difficulty,
            sentence_length=sentence_length,
            interests_text=interests_text,
            avoid_topics_text=avoid_topics_text,
            n_items=max((n_items - len(final_items)) * 4, n_items),
        )
        fallback_more = _shuffle_copy(fallback_more)

        final_items = _extend_with_unique_fillers(
            current_items=final_items,
            filler_items=fallback_more,
            technique_id=technique_id,
            target_count=n_items,
        )

    return {
        "pack_id": uuid.uuid4().hex,
        "technique_id": technique_id,
        "difficulty": difficulty,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "items": final_items[:n_items],
    }