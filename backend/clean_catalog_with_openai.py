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
DATA_DIR = BACKEND_DIR 

INPUT_FILE = PUBLIC_DIR / "image_catalog.json"
OUTPUT_FILE = DATA_DIR / "therapy_catalog.json"
CHECKPOINT_FILE = DATA_DIR / "therapy_catalog_checkpoint.json"

MODEL = "gpt-4o-mini"
BATCH_SIZE = 35
MAX_RETRIES = 3
SLEEP_BETWEEN_BATCHES = 1.0

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

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

def slugify(text: str) -> str:
    text = normalize_text(text)
    text = re.sub(r"[^a-z0-9\s]", "", text)
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_")

def extract_json_block(text: str) -> Any:
    text = (text or "").strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    m = re.search(r"```json\s*(.*?)```", text, flags=re.S | re.I)
    if m:
        return json.loads(m.group(1).strip())

    m = re.search(r"(\[.*\])", text, flags=re.S)
    if m:
        return json.loads(m.group(1).strip())

    raise ValueError("Could not parse JSON from model response")

def dedupe_tags(tags: list[str]) -> list[str]:
    out = []
    for t in tags:
        t = normalize_text(t)
        if t and t not in out:
            out.append(t)
    return out[:8]

def is_numeric_only(text: str) -> bool:
    return bool(re.fullmatch(r"[0-9]+", str(text or "").strip()))

def hard_filter_item(item: dict) -> str | None:
    technique = item.get("technique")
    if technique not in {"image_naming", "sentence_repetition"}:
        return "unknown_technique"

    probe = normalize_text(
        item.get("word") or item.get("english_label") or item.get("id") or ""
    )

    if not probe:
        return "empty_item"
    if is_numeric_only(probe):
        return "numeric_only"

    banned = [
        "sex", "sexual", "abuse", "weapon", "murder", "blood",
        "violence", "porn", "drug", "cigarette", "contracept",
    ]
    if any(b in probe for b in banned):
        return "unsafe"

    return None

# =========================================================
# OPENAI PROMPTS
# =========================================================

SYSTEM_PROMPT = """
You are building a clean therapy catalog for a stroke-recovery speech therapy app.

Your goal is to transform messy imported image entries into a final runtime-ready therapy catalog.

There are two techniques:

1. image_naming
   - keep only strong, concrete, visually clear, everyday naming items
   - examples: apple, dog, chair, bicycle, carrot, spoon
   - reject awkward compounds, abstract concepts, unclear items, symbols, numbers, jargon
   - if a label can be naturally cleaned into a readable everyday term, do so
   - output a single "word" field that is natural and readable
   - "word" can contain a space if it is a natural everyday expression like "pressure cooker", but reject if too awkward for naming
   - tags should be simple and useful

2. sentence_repetition
   - keep only imageable everyday scenes/actions
   - generate a short, clear sentence that matches the visible scene
   - generate required_content and required_function
   - tags should match the scene

Reject anything that is:
- abstract
- unsafe/upsetting
- too visually unclear
- too awkward for therapy
- too niche/technical
- symbols/numbers only
- long institutional/legal/government phrases

Return ONLY valid JSON array.

For each input item, return exactly one object in this schema:

[
  {
    "source_id": "same_as_input_id",
    "keep": true,
    "exclude_reason": null,
    "technique": "image_naming",
    "id": "pressure_cooker",
    "word": "pressure cooker",
    "sentence": null,
    "required_content": [],
    "required_function": [],
    "tags": ["kitchen", "cooking", "pot"],
    "difficulty": 2
  },
  {
    "source_id": "same_as_input_id",
    "keep": true,
    "exclude_reason": null,
    "technique": "sentence_repetition",
    "id": "girl_eating_apple",
    "word": null,
    "sentence": "The girl is eating an apple.",
    "required_content": ["girl", "eat", "apple"],
    "required_function": ["the", "is", "an"],
    "tags": ["girl", "eating", "apple", "food"],
    "difficulty": 1
  },
  {
    "source_id": "same_as_input_id",
    "keep": false,
    "exclude_reason": "too_abstract",
    "technique": "image_naming",
    "id": null,
    "word": null,
    "sentence": null,
    "required_content": [],
    "required_function": [],
    "tags": [],
    "difficulty": 1
  }
]

Rules:
- Preserve source_id exactly from input.
- id must be lowercase snake_case and clean.
- For image_naming:
  - sentence must be null
  - required_content and required_function must be empty
- For sentence_repetition:
  - word must be null
  - sentence must be short and clear
- required_content = key nouns/verbs only
- required_function = grammar/helper words like the, a, is, in, on, and
- tags = lowercase, simple, useful
"""

def build_user_prompt(batch: list[dict]) -> str:
    simplified = []
    for item in batch:
        simplified.append({
            "source_id": item.get("id"),
            "technique": item.get("technique"),
            "word": item.get("word"),
            "english_label": item.get("english_label"),
            "tags": item.get("tags", [])[:8],
            "difficulty": item.get("difficulty", 1),
            "file": item.get("file"),
            "raw_label": item.get("raw_label"),
        })

    return (
        "Transform these imported catalog items into a clean therapy catalog.\n"
        "Return only a JSON array.\n\n"
        f"{json.dumps(simplified, ensure_ascii=False, indent=2)}"
    )

def call_openai_for_batch(batch: list[dict]) -> list[dict]:
    prompt = build_user_prompt(batch)

    resp = client.chat.completions.create(
        model=MODEL,
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    )

    content = resp.choices[0].message.content or ""
    data = extract_json_block(content)

    if not isinstance(data, list):
        raise ValueError("Model did not return a JSON list")

    return data

# =========================================================
# VALIDATION
# =========================================================

def validate_result(result: dict, original: dict) -> dict:
    keep = bool(result.get("keep", False))
    source_id = result.get("source_id")
    technique = result.get("technique") or original.get("technique")
    difficulty = result.get("difficulty", original.get("difficulty", 1))

    try:
        difficulty = max(1, min(3, int(difficulty)))
    except Exception:
        difficulty = 1

    if source_id != original.get("id"):
        source_id = original.get("id")

    if technique not in {"image_naming", "sentence_repetition"}:
        keep = False

    if not keep:
        return {
            "source_id": source_id,
            "keep": False,
            "exclude_reason": result.get("exclude_reason") or "rejected_by_model",
            "technique": technique,
            "difficulty": difficulty,
        }

    clean_id = slugify(result.get("id") or original.get("id") or "")
    tags = dedupe_tags(result.get("tags", []) or original.get("tags", []))

    if technique == "image_naming":
        word = normalize_text(result.get("word") or "")
        if not word:
            return {
                "source_id": source_id,
                "keep": False,
                "exclude_reason": "missing_word",
                "technique": technique,
                "difficulty": difficulty,
            }

        if len(word.split()) > 2:
            return {
                "source_id": source_id,
                "keep": False,
                "exclude_reason": "awkward_long_name",
                "technique": technique,
                "difficulty": difficulty,
            }

        return {
            "source_id": source_id,
            "keep": True,
            "exclude_reason": None,
            "technique": "image_naming",
            "id": clean_id or slugify(word),
            "word": word,
            "image": original.get("file"),
            "tags": tags if tags else [word],
            "difficulty": difficulty,
            "source": original.get("source", "catalog"),
            "raw_label": original.get("raw_label"),
            "pictogram_id": original.get("pictogram_id"),
            "usage_count": original.get("usage_count", 0),
        }

    sentence = str(result.get("sentence") or "").strip()
    sentence = re.sub(r"\s+", " ", sentence)
    sentence = re.sub(r"[^A-Za-z0-9\s\.,'!?-]", "", sentence).strip()

    required_content = []
    for x in result.get("required_content", []):
        token = re.sub(r"[^a-z]", "", normalize_text(x))
        if token and token not in required_content:
            required_content.append(token)

    required_function = []
    for x in result.get("required_function", []):
        token = re.sub(r"[^a-z]", "", normalize_text(x))
        if token and token not in required_function:
            required_function.append(token)

    if not sentence:
        return {
            "source_id": source_id,
            "keep": False,
            "exclude_reason": "missing_sentence",
            "technique": technique,
            "difficulty": difficulty,
        }

    if not required_content:
        required_content = [
            re.sub(r"[^a-z]", "", normalize_text(t))
            for t in tags[:3]
            if re.sub(r"[^a-z]", "", normalize_text(t))
        ]

    if not required_function:
        required_function = ["the"]

    return {
        "source_id": source_id,
        "keep": True,
        "exclude_reason": None,
        "technique": "sentence_repetition",
        "id": clean_id or slugify(sentence),
        "sentence": sentence,
        "image": original.get("file"),
        "required_content": required_content[:6],
        "required_function": required_function[:6],
        "tags": tags,
        "difficulty": difficulty,
        "source": original.get("source", "catalog"),
        "raw_label": original.get("raw_label"),
        "pictogram_id": original.get("pictogram_id"),
        "usage_count": original.get("usage_count", 0),
    }

# =========================================================
# MAIN
# =========================================================

def main() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        raise EnvironmentError("OPENAI_API_KEY is not set")

    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Missing input file: {INPUT_FILE}")

    raw_catalog: list[dict] = load_json(INPUT_FILE, [])
    checkpoint: dict[str, dict] = load_json(CHECKPOINT_FILE, {})

    print(f"Loaded raw catalog items: {len(raw_catalog)}")
    print(f"Loaded checkpoint items: {len(checkpoint)}")

    final_kept = {
        "image_naming": [],
        "sentence_repetition": [],
    }

    to_send: list[dict] = []

    # restore checkpointed kept items
    for key, item in checkpoint.items():
        if item.get("keep") and item.get("technique") in final_kept:
            final_kept[item["technique"]].append(item)

    for item in raw_catalog:
        source_id = str(item.get("id"))

        if source_id in checkpoint:
            continue

        reason = hard_filter_item(item)
        if reason:
            checkpoint[source_id] = {
                "source_id": source_id,
                "keep": False,
                "exclude_reason": reason,
                "technique": item.get("technique"),
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
                    "technique": item.get("technique"),
                    "difficulty": item.get("difficulty", 1),
                }
            save_json(CHECKPOINT_FILE, checkpoint)
            continue

        result_map = {}
        for result in model_results:
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
                    "technique": original.get("technique"),
                    "difficulty": original.get("difficulty", 1),
                }
            else:
                validated = validate_result(raw_result, original)

            checkpoint[source_id] = validated

            if validated.get("keep") and validated.get("technique") in final_kept:
                final_kept[validated["technique"]].append(validated)

        save_json(CHECKPOINT_FILE, checkpoint)
        print(f"  Saved checkpoint: {len(checkpoint)} items")
        time.sleep(SLEEP_BETWEEN_BATCHES)

    # Deduplicate by final id inside each technique
    for technique in ("image_naming", "sentence_repetition"):
        deduped = []
        seen_ids = set()
        seen_images = set()

        for item in final_kept[technique]:
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

        final_kept[technique] = sorted(deduped, key=lambda x: x["id"])

    save_json(OUTPUT_FILE, final_kept)

    print("Done.")
    print(f"Image naming kept: {len(final_kept['image_naming'])}")
    print(f"Sentence repetition kept: {len(final_kept['sentence_repetition'])}")
    print(f"Wrote final therapy catalog: {OUTPUT_FILE}")
    print(f"Checkpoint file: {CHECKPOINT_FILE}")

if __name__ == "__main__":
    main()