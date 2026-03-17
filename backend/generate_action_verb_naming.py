from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# =========================================================
# PATHS
# =========================================================

SCRIPT_PATH = Path(__file__).resolve()
BACKEND_DIR = SCRIPT_PATH.parent
PROJECT_ROOT = BACKEND_DIR.parent
PUBLIC_DIR = PROJECT_ROOT / "frontend" / "stroke-recovery" / "public"
DATA_DIR = BACKEND_DIR / "data"

INPUT_FILE = PUBLIC_DIR / "therapy_catalog.json"
OUTPUT_FILE = PUBLIC_DIR / "action_verb_naming.json"
CHECKPOINT_FILE = DATA_DIR / "action_verb_naming_checkpoint.json"

MODEL = "gpt-4o-mini"
BATCH_SIZE = 25
MAX_RETRIES = 3
SLEEP_BETWEEN_BATCHES = 1.0

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

COMMON_ACTION_VERBS = {
    "run", "running",
    "walk", "walking",
    "jump", "jumping",
    "eat", "eating",
    "drink", "drinking",
    "read", "reading",
    "write", "writing",
    "sleep", "sleeping",
    "sit", "sitting",
    "stand", "standing",
    "swim", "swimming",
    "cook", "cooking",
    "wash", "washing",
    "brush", "brushing",
    "draw", "drawing",
    "paint", "painting",
    "climb", "climbing",
    "dance", "dancing",
    "sing", "singing",
    "laugh", "laughing",
    "cry", "crying",
    "kick", "kicking",
    "throw", "throwing",
    "catch", "catching",
    "open", "opening",
    "close", "closing",
    "carry", "carrying",
    "push", "pushing",
    "pull", "pulling",
    "drive", "driving",
    "ride", "riding",
    "play", "playing",
    "clean", "cleaning",
    "cut", "cutting",
    "build", "building",
    "plant", "planting",
    "dig", "digging",
    "feed", "feeding",
    "hug", "hugging",
    "wave", "waving",
    "smile", "smiling",
    "talk", "talking",
    "listen", "listening",
}

BANNED_ACTION_TARGETS = {
    "rain", "raining",
    "ring", "ringing",
    "revive", "reviving",
    "exist", "existing",
    "be", "being",
    "seem", "seeming",
    "become", "becoming",
    "belong", "belonging",
    "happen", "happening",
    "fall", "falling",   # often ambiguous / unsafe as therapy target
    "die", "dying",
    "kill", "killing",
    "hurt", "hurting",
}


# =========================================================
# HELPERS
# =========================================================

def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def normalize_text(text: str) -> str:
    text = str(text or "").strip().lower()
    text = text.replace("-", " ").replace("_", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_display_text(text: str) -> str:
    text = normalize_text(text)
    text = re.sub(r"[^a-z\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_json_block(text: str) -> Any:
    text = (text or "").strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"```json\s*(.*?)```", text, flags=re.S | re.I)
    if match:
        return json.loads(match.group(1).strip())

    match = re.search(r"(\[.*\])", text, flags=re.S)
    if match:
        return json.loads(match.group(1).strip())

    raise ValueError("Could not parse JSON from model response")


def dedupe_tags(tags: list[str]) -> list[str]:
    out = []
    for tag in tags:
        cleaned = clean_display_text(tag)
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out[:8]


def dedupe_answers(values: list[str]) -> list[str]:
    out = []
    for value in values:
        cleaned = clean_display_text(value)
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out[:6]


def singularize_simple(word: str) -> str:
    word = clean_display_text(word)

    if word.endswith("ies") and len(word) > 3:
        return word[:-3] + "y"
    if word.endswith("es") and len(word) > 3:
        return word[:-2]
    if word.endswith("s") and len(word) > 2:
        return word[:-1]
    return word


def semantic_hint_is_weak(verb: str, hint: str) -> bool:
    verb_clean = clean_display_text(verb)
    hint_norm = normalize_text(hint)

    if not hint_norm:
        return True

    weak_patterns = [
        "this is an action",
        "it is an action",
        "something someone does",
        "an everyday action",
        "daily action",
        "common action",
        "movement action",
        "doing something",
        "this means movement",
        "it has to do with movement",
    ]

    if any(pattern in hint_norm for pattern in weak_patterns):
        return True

    if verb_clean and verb_clean in hint_norm:
        return True

    if len(hint_norm.split()) < 5:
        return True

    return False


def fallback_semantic_hint(verb: str, tags: list[str], subject: str = "") -> str:
    verb_clean = clean_display_text(verb)
    tag_list = [clean_display_text(tag) for tag in tags if clean_display_text(tag)]
    subject = clean_display_text(subject)

    if verb_clean in {"running", "walking", "jumping", "climbing"}:
        return "Think about how the person is moving."
    if verb_clean in {"eating", "drinking", "cooking"}:
        return "Think about what the person is doing with food or drink."
    if verb_clean in {"reading", "writing", "drawing", "painting"}:
        return "Think about what the person is doing with a book, paper, or tools."
    if verb_clean in {"sleeping", "resting"}:
        return "Think about how the person is resting."
    if verb_clean in {"swimming"}:
        return "Think about the action someone does in water."
    if verb_clean in {"laughing", "crying", "smiling"}:
        return "Think about the feeling or expression the person is showing."

    if "movement" in tag_list or "exercise" in tag_list:
        return "Think about how the person is moving."
    if "food" in tag_list or "drink" in tag_list:
        return "Think about what the person is doing with food or drink."
    if "book" in tag_list or "learning" in tag_list or "school" in tag_list:
        return "Think about what task the person is doing."
    if "water" in tag_list:
        return "Think about the action happening in water."

    if subject:
        return f"Think about what the {subject} is doing."

    return "Think about what action is happening in the picture."


def load_catalog_items(path: Path) -> list[dict]:
    raw = load_json(path, [])

    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]

    if isinstance(raw, dict):
        merged = []
        for value in raw.values():
            if isinstance(value, list):
                merged.extend(item for item in value if isinstance(item, dict))
        return merged

    return []


def likely_subject_from_sentence(sentence: str) -> str:
    sentence_norm = normalize_text(sentence)

    if any(word in sentence_norm.split() for word in {"dog", "cat", "bird", "horse", "cow"}):
        tokens = sentence_norm.split()
        for token in tokens:
            if token in {"dog", "cat", "bird", "horse", "cow"}:
                return token

    if any(word in sentence_norm.split() for word in {"boy", "girl", "child", "man", "woman"}):
        tokens = sentence_norm.split()
        for token in tokens:
            if token in {"boy", "girl", "child", "man", "woman"}:
                return token

    return "person"


def candidate_verbs_from_item(item: dict) -> list[str]:
    candidates: list[str] = []

    item_id = clean_display_text(item.get("id", ""))
    sentence = clean_display_text(item.get("sentence", ""))
    required_content = [
        clean_display_text(x)
        for x in (item.get("required_content") or [])
        if clean_display_text(x)
    ]
    tags = [
        clean_display_text(x)
        for x in (item.get("tags") or [])
        if clean_display_text(x)
    ]

    if item_id.startswith("to "):
        candidates.append(item_id[3:].strip())
    elif item_id.startswith("to_"):
        candidates.append(clean_display_text(item_id.replace("to_", "")))

    candidates.extend(required_content)
    candidates.extend(tags)

    if sentence:
        words = sentence.split()
        candidates.extend(words)

    cleaned = []
    seen = set()
    for cand in candidates:
        cand = singularize_simple(cand)
        cand = clean_display_text(cand)
        if cand and cand not in seen:
            seen.add(cand)
            cleaned.append(cand)

    return cleaned


def choose_action_label(item: dict) -> str | None:
    candidates = candidate_verbs_from_item(item)

    for cand in candidates:
        if cand in BANNED_ACTION_TARGETS:
            continue
        if cand in COMMON_ACTION_VERBS:
            return cand

    return None


def build_seed_items(raw_items: list[dict]) -> list[dict]:
    seed_items = []
    seen_ids = set()
    seen_images = set()

    for item in raw_items:
        if not isinstance(item, dict):
            continue

        if item.get("technique") != "sentence_repetition":
            continue

        if not item.get("keep", True):
            continue

        item_id = str(item.get("id") or "").strip()
        image = str(item.get("image") or "").strip()
        if not item_id or not image:
            continue

        action_label = choose_action_label(item)
        if not action_label:
            continue

        if action_label in BANNED_ACTION_TARGETS:
            continue

        if item_id in seen_ids or image in seen_images:
            continue

        seen_ids.add(item_id)
        seen_images.add(image)

        seed_items.append(
            {
                "id": item_id,
                "keep": True,
                "action_label": action_label,
                "image": image,
                "tags": item.get("tags", []),
                "difficulty": item.get("difficulty", 1),
                "subject_hint": likely_subject_from_sentence(item.get("sentence", "")),
                "source": item.get("source", "catalog"),
                "raw_label": item.get("raw_label"),
                "usage_count": item.get("usage_count", 0),
                "matched_catalog_id": item_id,
            }
        )

    return seed_items


def hard_filter_item(item: dict) -> str | None:
    if not isinstance(item, dict):
        return "not_dict"

    if not item.get("keep", True):
        return "not_kept"

    item_id = str(item.get("id") or "").strip()
    image = str(item.get("image") or "").strip()
    action_label = clean_display_text(item.get("action_label") or item.get("verb") or "")

    if not item_id:
        return "missing_id"
    if not image:
        return "missing_image"
    if not action_label:
        return "missing_action_label"

    if action_label in BANNED_ACTION_TARGETS:
        return "banned_action"

    if action_label not in COMMON_ACTION_VERBS:
        return "not_common_action"

    banned = [
        "sex", "sexual", "abuse", "weapon", "murder", "blood",
        "violence", "porn", "drug", "cigarette", "contracept",
    ]
    if any(b in action_label for b in banned):
        return "unsafe"

    return None


# =========================================================
# OPENAI PROMPTS
# =========================================================

SYSTEM_PROMPT = """
You are creating Action Verb Naming therapy data for a stroke-recovery speech therapy app.

For each input item, return exactly one object in this schema:

[
  {
    "source_id": "same_as_input_id",
    "verb": "present participle target like running",
    "base_verb": "dictionary form like run",
    "acceptable_answers": ["run", "running"],
    "subject": "person",
    "semantic_hint": "short clinically useful hint"
  }
]

Rules:
- The target should be a clear everyday action shown in an image.
- verb should usually be the action in picture form, such as running, eating, reading, writing.
- base_verb should be the plain dictionary form, such as run, eat, read, write.
- acceptable_answers must include both the base form and the picture-friendly spoken form where appropriate.
- semantic_hint must help the user think about the action, but must NOT directly say the verb.
- Do not include the target verb in semantic_hint.
- subject should usually be "person" unless the image clearly suggests something else like "child", "dog", or "man".
- Use natural British English.
- Return ONLY a JSON array.
"""


def build_user_prompt(batch: list[dict]) -> str:
    simplified = []

    for item in batch:
        simplified.append({
            "source_id": item.get("id"),
            "action_label": clean_display_text(item.get("action_label") or item.get("verb") or ""),
            "tags": dedupe_tags(item.get("tags", [])),
            "difficulty": item.get("difficulty", 1),
            "subject_hint": str(item.get("subject_hint") or "").strip(),
            "source": item.get("source", "catalog"),
            "raw_label": item.get("raw_label"),
        })

    return (
        "Generate action verb naming therapy data for these image items.\n"
        "Return only a JSON array.\n\n"
        f"{json.dumps(simplified, ensure_ascii=False, indent=2)}"
    )


def call_openai_for_batch(batch: list[dict]) -> list[dict]:
    prompt = build_user_prompt(batch)

    response = client.chat.completions.create(
        model=MODEL,
        temperature=0.2,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )

    content = response.choices[0].message.content or ""
    data = extract_json_block(content)

    if not isinstance(data, list):
        raise ValueError("Model did not return a JSON list")

    return data


# =========================================================
# VALIDATION
# =========================================================

def validate_result(result: dict, original: dict) -> dict:
    source_id = str(result.get("source_id") or original.get("id") or "")
    original_id = str(original.get("id") or "")
    if source_id != original_id:
        source_id = original_id

    original_action = clean_display_text(original.get("action_label") or "")
    verb = clean_display_text(result.get("verb") or original_action)
    base_verb = clean_display_text(result.get("base_verb") or verb)
    image = str(original.get("image") or "").strip()
    tags = dedupe_tags(original.get("tags", []))
    subject = clean_display_text(result.get("subject") or original.get("subject_hint") or "person") or "person"

    if not verb:
        return {
            "source_id": source_id,
            "keep": False,
            "exclude_reason": "missing_verb",
            "technique": "action_verb_naming",
            "difficulty": original.get("difficulty", 1),
        }

    if verb in BANNED_ACTION_TARGETS:
        return {
            "source_id": source_id,
            "keep": False,
            "exclude_reason": "banned_verb",
            "technique": "action_verb_naming",
            "difficulty": original.get("difficulty", 1),
        }

    if not base_verb:
        base_verb = singularize_simple(verb)

    acceptable_answers = dedupe_answers(
        result.get("acceptable_answers") or [base_verb, verb]
    )

    if base_verb not in acceptable_answers:
        acceptable_answers.insert(0, base_verb)
    if verb not in acceptable_answers:
        acceptable_answers.append(verb)

    acceptable_answers = dedupe_answers(acceptable_answers)

    semantic_hint = str(result.get("semantic_hint") or "").strip()
    if semantic_hint_is_weak(verb, semantic_hint):
        semantic_hint = fallback_semantic_hint(
            verb=verb,
            tags=tags,
            subject=subject,
        )

    return {
        "source_id": source_id,
        "keep": True,
        "exclude_reason": None,
        "technique": "action_verb_naming",
        "id": original_id,
        "verb": verb,
        "base_verb": base_verb,
        "acceptable_answers": acceptable_answers,
        "subject": subject,
        "image": image,
        "tags": tags if tags else [verb],
        "difficulty": max(1, min(3, int(original.get("difficulty", 1) or 1))),
        "source": original.get("source", "catalog"),
        "raw_label": original.get("raw_label"),
        "usage_count": original.get("usage_count", 0),
        "semantic_hint": semantic_hint,
        "matched_catalog_id": original.get("matched_catalog_id") or original_id,
        "image_prompt": (
            f"A {subject} {verb} in a clear simple therapy-style image, "
            f"centered, easy to recognise, plain background."
        ),
    }


# =========================================================
# MAIN
# =========================================================

def main() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        raise EnvironmentError("OPENAI_API_KEY is not set")

    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Missing input file: {INPUT_FILE}")

    raw_catalog_items = load_catalog_items(INPUT_FILE)
    raw_items = build_seed_items(raw_catalog_items)
    checkpoint: dict[str, dict] = load_json(CHECKPOINT_FILE, {})

    print(f"Loaded raw catalog items: {len(raw_catalog_items)}")
    print(f"Built usable action seed items: {len(raw_items)}")
    print(f"Loaded checkpoint items: {len(checkpoint)}")
    print(f"Input file: {INPUT_FILE}")
    print(f"Output file: {OUTPUT_FILE}")
    print(f"Checkpoint file: {CHECKPOINT_FILE}")

    final_kept: list[dict] = []
    to_send: list[dict] = []

    for _, item in checkpoint.items():
        if (
            isinstance(item, dict)
            and item.get("keep")
            and item.get("technique") == "action_verb_naming"
        ):
            final_kept.append(item)

    for item in raw_items:
        if not isinstance(item, dict):
            continue

        source_id = str(item.get("id") or "")
        if not source_id:
            continue

        if source_id in checkpoint:
            continue

        reason = hard_filter_item(item)
        if reason:
            checkpoint[source_id] = {
                "source_id": source_id,
                "keep": False,
                "exclude_reason": reason,
                "technique": "action_verb_naming",
                "difficulty": item.get("difficulty", 1),
            }
            continue

        to_send.append(item)

    print(f"Remaining for OpenAI transformation: {len(to_send)}")

    for start in range(0, len(to_send), BATCH_SIZE):
        batch = to_send[start:start + BATCH_SIZE]
        print(f"Processing batch {start // BATCH_SIZE + 1} ({len(batch)} items)")

        model_results = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                model_results = call_openai_for_batch(batch)
                break
            except Exception as e:
                print(f"  Attempt {attempt} failed: {e}")
                time.sleep(2 * attempt)

        if model_results is None:
            for item in batch:
                source_id = str(item.get("id"))
                checkpoint[source_id] = {
                    "source_id": source_id,
                    "keep": False,
                    "exclude_reason": "batch_failed_retry_later",
                    "technique": "action_verb_naming",
                    "difficulty": item.get("difficulty", 1),
                }
            save_json(CHECKPOINT_FILE, checkpoint)
            continue

        result_map = {}
        for result in model_results:
            if isinstance(result, dict):
                sid = str(result.get("source_id"))
                result_map[sid] = result

        for original in batch:
            source_id = str(original.get("id"))
            raw_result = result_map.get(source_id)

            if raw_result is None:
                validated = {
                    "source_id": source_id,
                    "keep": False,
                    "exclude_reason": "missing_from_model_response",
                    "technique": "action_verb_naming",
                    "difficulty": original.get("difficulty", 1),
                }
            else:
                validated = validate_result(raw_result, original)

            checkpoint[source_id] = validated

            if (
                validated.get("keep")
                and validated.get("technique") == "action_verb_naming"
            ):
                final_kept.append(validated)

        save_json(CHECKPOINT_FILE, checkpoint)
        print(f"  Saved checkpoint: {len(checkpoint)} items")
        time.sleep(SLEEP_BETWEEN_BATCHES)

    deduped = []
    seen_ids = set()
    seen_images = set()

    for item in final_kept:
        item_id = item.get("id")
        image = item.get("image")

        if not item_id or item_id in seen_ids:
            continue
        if image and image in seen_images:
            continue

        seen_ids.add(item_id)
        if image:
            seen_images.add(image)
        deduped.append(item)

    deduped = sorted(deduped, key=lambda x: x["id"])

    save_json(OUTPUT_FILE, {"action_verb_naming": deduped})

    print("Done.")
    print(f"Action verb items kept: {len(deduped)}")
    print(f"Wrote final action verb catalog: {OUTPUT_FILE}")
    print(f"Checkpoint file: {CHECKPOINT_FILE}")


if __name__ == "__main__":
    main()