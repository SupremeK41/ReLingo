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
OUTPUT_FILE = DATA_DIR / "naming_with_cues.json"
CHECKPOINT_FILE = DATA_DIR / "naming_with_cues_checkpoint.json"

MODEL = "gpt-4o-mini"
BATCH_SIZE = 25
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
        t = clean_display_word(t)
        if t and t not in out:
            out.append(t)
    return out[:8]


def first_sound_hint(word: str) -> str:
    word = clean_display_word(word)
    if not word:
        return ""
    first = word.split()[0]
    if len(first) >= 4:
        return f"It starts with '{first[:2]}...'."
    return f"It starts with '{first[:1]}'."


def fallback_semantic_cue(word: str, tags: list[str]) -> str:
    word_n = clean_display_word(word)
    tag_list = [clean_display_word(t) for t in tags if clean_display_word(t)]

    if "drink" in tag_list or word_n in {"milk", "juice", "water", "tea", "coffee"}:
        return "You can drink it."
    if "food" in tag_list or "fruit" in tag_list or "vegetable" in tag_list:
        return "It is something you can eat."
    if "animal" in tag_list:
        return "It is an animal."
    if "vehicle" in tag_list or "transport" in tag_list or word_n in {
        "car", "bus", "train", "bike", "bicycle", "aeroplane", "airplane", "aircraft"
    }:
        return "It is used for travel or transport."
    if "utensil" in tag_list or word_n in {"spoon", "fork", "knife"}:
        return "You use it when eating."
    if "device" in tag_list:
        return "It is a device or tool you use."
    if "tool" in tag_list:
        return "It is a tool used for a task."
    if "clothing" in tag_list or "clothes" in tag_list:
        return "You wear it."
    if "furniture" in tag_list or word_n in {"table", "chair", "bed"}:
        return "It is something you use in a room at home."
    if "person" in tag_list or word_n in {"adult", "boy", "girl", "man", "woman"}:
        return "It is a person."
    if "family" in tag_list:
        return "It is connected to people in a family."
    if "place" in tag_list or word_n in {"airport", "school", "shop", "house"}:
        return "It is a place people go to."
    return "It is something used in everyday life."


def cue_is_weak(word: str, cue: str) -> bool:
    w = clean_display_word(word)
    c = normalize_text(cue)

    if not c:
        return True

    weak_patterns = [
        "it is related to",
        "something related to",
        "something to do with",
        "it has to do with",
        "related to",
        "this is a",
        "it is a thing",
        "it is about",
    ]
    if any(p in c for p in weak_patterns):
        return True

    if w and w in c:
        return True

    if len(c.split()) < 4:
        return True

    return False


def phonemic_is_weak(cue: str) -> bool:
    c = normalize_text(cue)
    return not c or (
        "start" not in c
        and "begins" not in c
        and "sound" not in c
        and "letter" not in c
    )


def hard_filter_item(item: dict) -> str | None:
    if not isinstance(item, dict):
        return "not_dict"

    if item.get("technique") != "image_naming":
        return "not_image_naming"

    if not item.get("keep", True):
        return "not_kept"

    probe = clean_display_word(item.get("word") or item.get("id") or "")
    image = str(item.get("image") or "").strip()

    if not probe:
        return "empty_word"
    if not image:
        return "missing_image"

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
You are building cue-based naming therapy data for a stroke-recovery speech therapy app.

Your task is to enrich image naming items with clinically useful cues.

For each input item, return exactly one object in this schema:

[
  {
    "source_id": "same_as_input_id",
    "word": "target word",
    "semantic_cue": "helpful semantic cue that does NOT say the answer",
    "phonemic_cue": "starting sound / letter / syllable cue",
    "full_cue": "exact target word"
  }
]

Rules:
- semantic_cue must not contain the target word.
- semantic_cue should be practical and therapy-like.
- Good semantic cues describe function, category, location, or common use.
- phonemic_cue should mention the starting sound, letter, or first syllable.
- full_cue must exactly equal the target word.
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
            "raw_label": item.get("raw_label"),
            "source": item.get("source", "catalog"),
        })

    return (
        "Generate naming-with-cues data for these therapy items.\n"
        "Return only a JSON array.\n\n"
        f"{json.dumps(simplified, ensure_ascii=False, indent=2)}"
    )


def call_openai_for_batch(batch: list[dict]) -> list[dict]:
    prompt = build_user_prompt(batch)

    resp = client.chat.completions.create(
        model=MODEL,
        temperature=0.2,
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
            "technique": "naming_with_cues",
            "difficulty": original.get("difficulty", 1),
        }

    semantic_cue = str(result.get("semantic_cue") or "").strip()
    phonemic_cue = str(result.get("phonemic_cue") or "").strip()
    full_cue = clean_display_word(result.get("full_cue") or word) or word

    if cue_is_weak(word, semantic_cue):
        semantic_cue = fallback_semantic_cue(word, tags)

    if phonemic_is_weak(phonemic_cue):
        phonemic_cue = first_sound_hint(word)

    return {
        "source_id": source_id,
        "keep": True,
        "exclude_reason": None,
        "technique": "naming_with_cues",
        "id": original_id,
        "word": word,
        "image": image,
        "tags": tags if tags else [word],
        "difficulty": max(1, min(3, int(original.get("difficulty", 1) or 1))),
        "source": original.get("source", "catalog"),
        "raw_label": original.get("raw_label"),
        "pictogram_id": original.get("pictogram_id"),
        "usage_count": original.get("usage_count", 0),
        "semantic_cue": semantic_cue,
        "phonemic_cue": phonemic_cue,
        "full_cue": full_cue,
    }


# =========================================================
# MAIN
# =========================================================

def main() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        raise EnvironmentError("OPENAI_API_KEY is not set")

    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Missing input file: {INPUT_FILE}")

    raw_catalog_data = load_json(INPUT_FILE, [])
    checkpoint: dict[str, dict] = load_json(CHECKPOINT_FILE, {})

    if isinstance(raw_catalog_data, dict):
        raw_catalog: list[dict] = raw_catalog_data.get("image_naming", [])
    elif isinstance(raw_catalog_data, list):
        raw_catalog = raw_catalog_data
    else:
        raw_catalog = []

    print(f"Loaded raw catalog items: {len(raw_catalog)}")
    print(f"Loaded checkpoint items: {len(checkpoint)}")
    print(f"Input file: {INPUT_FILE}")
    print(f"Output file: {OUTPUT_FILE}")
    print(f"Checkpoint file: {CHECKPOINT_FILE}")

    final_kept: list[dict] = []
    to_send: list[dict] = []

    for _, item in checkpoint.items():
        if isinstance(item, dict) and item.get("keep") and item.get("technique") == "naming_with_cues":
            final_kept.append(item)

    for item in raw_catalog:
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
                "technique": "naming_with_cues",
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
                    "technique": "naming_with_cues",
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
                    "technique": "naming_with_cues",
                    "difficulty": original.get("difficulty", 1),
                }
            else:
                validated = validate_result(raw_result, original)

            checkpoint[source_id] = validated

            if validated.get("keep") and validated.get("technique") == "naming_with_cues":
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
    print(f"Naming with cues kept: {len(deduped)}")
    print(f"Wrote final cue catalog: {OUTPUT_FILE}")
    print(f"Checkpoint file: {CHECKPOINT_FILE}")


if __name__ == "__main__":
    main()