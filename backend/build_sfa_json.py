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

INPUT_FILE = PUBLIC_DIR / "naming_with_cues.json"
OUTPUT_FILE = DATA_DIR / "semantic_feature_analysis.json"
CHECKPOINT_FILE = DATA_DIR / "semantic_feature_analysis_checkpoint.json"

MODEL = "gpt-4o-mini"
BATCH_SIZE = 25
MAX_RETRIES = 3
SLEEP_BETWEEN_BATCHES = 1.0

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

FEATURE_PROMPTS = [
    "What type of thing is it?",
    "What is it used for or what does it do?",
    "Where might you find it?",
    "What does it look like?",
    "What is it related to?",
    "What actions are linked to it?",
]


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


def clean_display_word(text: str) -> str:
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
        cleaned = clean_display_word(tag)
        if cleaned and cleaned not in out:
            out.append(cleaned)
    return out[:8]


def semantic_hint_is_weak(word: str, hint: str) -> bool:
    word_clean = clean_display_word(word)
    hint_norm = normalize_text(hint)

    if not hint_norm:
        return True

    weak_patterns = [
        "it is related to",
        "something related to",
        "something to do with",
        "it has to do with",
        "related to",
        "this is a thing",
        "it is a thing",
        "everyday object",
        "used in everyday life",
    ]

    if any(pattern in hint_norm for pattern in weak_patterns):
        return True

    if word_clean and word_clean in hint_norm:
        return True

    if len(hint_norm.split()) < 5:
        return True

    return False


def fallback_semantic_hint(word: str, tags: list[str], semantic_cue: str = "") -> str:
    word_clean = clean_display_word(word)
    tag_list = [clean_display_word(tag) for tag in tags if clean_display_word(tag)]
    semantic_cue = str(semantic_cue or "").strip()

    if semantic_cue and word_clean not in normalize_text(semantic_cue):
        return semantic_cue

    if "bird" in tag_list:
        return "It is a kind of bird."
    if "animal" in tag_list:
        return "It is a kind of animal."
    if "food" in tag_list:
        return "It is something people eat."
    if "fruit" in tag_list:
        return "It is a fruit people can eat."
    if "vegetable" in tag_list:
        return "It is a vegetable used in food."
    if "drink" in tag_list:
        return "It is something people drink."
    if "vehicle" in tag_list or "transport" in tag_list:
        return "It is used for transport."
    if "writing" in tag_list:
        return "It is used for writing."
    if "object" in tag_list:
        return "It is an everyday object."
    if "clothing" in tag_list or "clothes" in tag_list:
        return "It is something people wear."
    if "tool" in tag_list:
        return "It is a tool used for a task."
    if "place" in tag_list:
        return "It is a place people go to."

    return "Think about what type of thing it is and what it is used for."


def hard_filter_item(item: dict) -> str | None:
    if not isinstance(item, dict):
        return "not_dict"

    if item.get("technique") != "naming_with_cues":
        return "not_naming_with_cues"

    if not item.get("keep", True):
        return "not_kept"

    word = clean_display_word(item.get("word") or item.get("id") or "")
    image = str(item.get("image") or "").strip()

    if not word:
        return "empty_word"
    if not image:
        return "missing_image"

    banned = [
        "sex", "sexual", "abuse", "weapon", "murder", "blood",
        "violence", "porn", "drug", "cigarette", "contracept",
    ]
    if any(b in word for b in banned):
        return "unsafe"

    return None


# =========================================================
# OPENAI PROMPTS
# =========================================================

SYSTEM_PROMPT = """
You are creating Semantic Feature Analysis therapy data for a stroke-recovery speech therapy app.

For each input item, return exactly one object in this schema:

[
  {
    "source_id": "same_as_input_id",
    "word": "target word",
    "semantic_hint": "short clinically useful semantic support hint",
    "feature_prompts": [
      "What type of thing is it?",
      "What is it used for or what does it do?",
      "Where might you find it?",
      "What does it look like?",
      "What is it related to?",
      "What actions are linked to it?"
    ]
  }
]

Rules:
- semantic_hint must help the user think semantically, but must NOT directly say the target word.
- semantic_hint should sound like something a therapist might say if the patient is stuck.
- Keep semantic_hint short, concrete, and helpful.
- Do not include the target word in semantic_hint.
- feature_prompts should stay broad and reusable.
- Use natural British English.
- Return ONLY a JSON array.
"""


def build_user_prompt(batch: list[dict]) -> str:
    simplified = []

    for item in batch:
        simplified.append({
            "source_id": item.get("id"),
            "word": clean_display_word(item.get("word")),
            "tags": dedupe_tags(item.get("tags", [])),
            "difficulty": item.get("difficulty", 1),
            "semantic_cue": str(item.get("semantic_cue") or "").strip(),
            "phonemic_cue": str(item.get("phonemic_cue") or "").strip(),
            "source": item.get("source", "catalog"),
            "raw_label": item.get("raw_label"),
        })

    return (
        "Generate semantic feature analysis support data for these therapy items.\n"
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

    word = clean_display_word(result.get("word") or original.get("word") or "")
    image = str(original.get("image") or "").strip()
    tags = dedupe_tags(original.get("tags", []))

    if not word:
        return {
            "source_id": source_id,
            "keep": False,
            "exclude_reason": "missing_word",
            "technique": "semantic_feature_analysis",
            "difficulty": original.get("difficulty", 1),
        }

    semantic_hint = str(result.get("semantic_hint") or "").strip()
    if semantic_hint_is_weak(word, semantic_hint):
        semantic_hint = fallback_semantic_hint(
            word=word,
            tags=tags,
            semantic_cue=original.get("semantic_cue", ""),
        )

    feature_prompts = result.get("feature_prompts")
    if not isinstance(feature_prompts, list) or len(feature_prompts) < 3:
        feature_prompts = FEATURE_PROMPTS
    else:
        cleaned_prompts = []
        for prompt in feature_prompts:
            prompt = str(prompt or "").strip()
            if prompt:
                cleaned_prompts.append(prompt)
        feature_prompts = cleaned_prompts[:6] if cleaned_prompts else FEATURE_PROMPTS

    return {
        "source_id": source_id,
        "keep": True,
        "exclude_reason": None,
        "technique": "semantic_feature_analysis",
        "id": original_id,
        "word": word,
        "image": image,
        "tags": tags if tags else [word],
        "difficulty": max(1, min(3, int(original.get("difficulty", 1) or 1))),
        "source": original.get("source", "catalog"),
        "raw_label": original.get("raw_label"),
        "pictogram_id": original.get("pictogram_id"),
        "usage_count": original.get("usage_count", 0),
        "semantic_hint": semantic_hint,
        "feature_prompts": feature_prompts,
        "matched_catalog_id": original_id,
    }


# =========================================================
# MAIN
# =========================================================

def main() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        raise EnvironmentError("OPENAI_API_KEY is not set")

    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Missing input file: {INPUT_FILE}")

    raw_items = load_json(INPUT_FILE, [])
    checkpoint: dict[str, dict] = load_json(CHECKPOINT_FILE, {})

    if not isinstance(raw_items, list):
        raise ValueError("Input file must be a list")

    print(f"Loaded source items: {len(raw_items)}")
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
            and item.get("technique") == "semantic_feature_analysis"
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
                "technique": "semantic_feature_analysis",
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
                    "technique": "semantic_feature_analysis",
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
                    "technique": "semantic_feature_analysis",
                    "difficulty": original.get("difficulty", 1),
                }
            else:
                validated = validate_result(raw_result, original)

            checkpoint[source_id] = validated

            if (
                validated.get("keep")
                and validated.get("technique") == "semantic_feature_analysis"
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

    save_json(OUTPUT_FILE, deduped)

    print("Done.")
    print(f"SFA items kept: {len(deduped)}")
    print(f"Wrote final SFA catalog: {OUTPUT_FILE}")
    print(f"Checkpoint file: {CHECKPOINT_FILE}")


if __name__ == "__main__":
    main()