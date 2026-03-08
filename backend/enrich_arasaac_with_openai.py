from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List
from dotenv import load_dotenv
load_dotenv()
from openai import OpenAI

# =========================================================
# CONFIG
# =========================================================

SCRIPT_PATH = Path(__file__).resolve()
BACKEND_DIR = SCRIPT_PATH.parent
DATA_DIR = BACKEND_DIR / "data"

INPUT_FILE = DATA_DIR / "arasaac_raw_processed.json"
OUTPUT_FILE = DATA_DIR / "arasaac_enriched.json"
CHECKPOINT_FILE = DATA_DIR / "arasaac_enriched_checkpoint.json"

MODEL = "gpt-4o-mini"
BATCH_SIZE = 50
SLEEP_BETWEEN_BATCHES = 1.0
MAX_RETRIES = 3

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# =========================================================
# HARD FILTERS BEFORE OPENAI
# =========================================================

BANNED_SUBSTRINGS = {
    "abus sexuel",
    "sexe",
    "sexuel",
    "viol",
    "violence",
    "meurtre",
    "arme",
    "armes",
    "sang",
    "drogue",
    "porn",
    "préservatif",
    "preservatif",
    "contraceptif",
}

SKIP_EXACT = {
    "a", "b", "c", "d", "e", "f", "g",
    "h", "i", "j", "k", "l", "m", "n",
    "o", "p", "q", "r", "s", "t", "u",
    "v", "w", "x", "y", "z",
    "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
}

def normalize_text(text: str) -> str:
    text = str(text).strip().lower()
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
        text = text.replace(old, new)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def slugify(text: str) -> str:
    text = normalize_text(text)
    text = re.sub(r"[^a-z0-9\s_]", "", text)
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_")

def is_numberish(text: str) -> bool:
    return bool(re.fullmatch(r"[0-9]+", text.strip()))

def looks_like_duplicate_variant(label: str) -> bool:
    return bool(re.search(r"_[0-9]+$", label.strip().lower()))

def hard_skip_reason(raw_label: str) -> str | None:
    label = normalize_text(raw_label)

    if not label:
        return "empty_label"

    if label in SKIP_EXACT:
        return "single_letter_or_digit"

    if is_numberish(label):
        return "numeric_only"

    if looks_like_duplicate_variant(raw_label):
        return "duplicate_variant_suffix"

    if len(label) > 60:
        return "too_long"

    if any(bad in label for bad in BANNED_SUBSTRINGS):
        return "unsafe_or_irrelevant"

    if "euro" in label or "centime" in label:
        return "money_denomination"

    return None

# =========================================================
# JSON HELPERS
# =========================================================

def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))

def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def extract_json_block(text: str) -> Any:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"```json\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if match:
        return json.loads(match.group(1).strip())

    match = re.search(r"(\[.*\])", text, flags=re.DOTALL)
    if match:
        return json.loads(match.group(1).strip())

    raise ValueError("Could not parse JSON from model response.")

# =========================================================
# OPENAI ENRICHMENT
# =========================================================

SYSTEM_PROMPT = """
You are helping prepare a curated image dataset for a stroke-recovery speech therapy app called ReLingo.

Your job:
1. Translate French label text into simple English.
2. Decide whether the item is useful and appropriate for speech therapy.
3. Classify it into one of:
   - "image_naming" for single concrete objects/entities suitable for naming
   - "sentence_repetition" for actions/scenes/phrases suitable for sentence prompts
   - or reject it
4. Produce short, clean English tags.

Clinical preferences:
- Prefer concrete, everyday therapy-friendly content:
  foods, animals, household objects, body parts, clothes, vehicles, common actions, daily routines, simple social scenes.
- Reject content that is:
  explicit sexual content, graphic violence, disturbing, highly abstract, legal/institutional jargon, money denominations, obscure technical terms, confusing symbols, single letters, random numbers, or not useful for aphasia/stroke therapy.

Output rules:
- Return ONLY valid JSON.
- Return a JSON array with one result object per input item.
- Preserve the "pictogram_id" exactly.
- english_label should be short and natural.
- english_filename must be lowercase snake_case and filename-safe.
- tags should be short lowercase English tags.
- For image_naming, prefer a single-word english_label where possible.
- If rejecting, set:
  keep=false, skip_reason=<reason>, english_label=null, english_filename=null, technique=null, tags=[]

Allowed skip reasons:
- unsafe_or_not_therapy_appropriate
- too_abstract
- money_denomination
- symbol_or_letter
- not_useful_for_therapy
- unclear_translation
"""

def build_user_prompt(batch: List[Dict[str, Any]]) -> str:
    simplified = [
        {
            "pictogram_id": item["pictogram_id"],
            "raw_label": item["raw_label"],
            "normalized_raw_label": item.get("normalized_raw_label"),
            "usage_count": item.get("usage_count", 0),
        }
        for item in batch
    ]

    schema = [
        {
            "pictogram_id": 123,
            "keep": True,
            "skip_reason": None,
            "english_label": "apple",
            "english_filename": "apple",
            "technique": "image_naming",
            "tags": ["apple", "fruit", "food"],
        },
        {
            "pictogram_id": 456,
            "keep": True,
            "skip_reason": None,
            "english_label": "washing hands",
            "english_filename": "washing_hands",
            "technique": "sentence_repetition",
            "tags": ["washing", "hands", "water", "soap"],
        },
        {
            "pictogram_id": 789,
            "keep": False,
            "skip_reason": "unsafe_or_not_therapy_appropriate",
            "english_label": None,
            "english_filename": None,
            "technique": None,
            "tags": [],
        },
    ]

    return (
        "Classify and enrich these ARASAAC items.\n\n"
        "Return ONLY a JSON array matching this schema style:\n"
        f"{json.dumps(schema, ensure_ascii=False, indent=2)}\n\n"
        "Input items:\n"
        f"{json.dumps(simplified, ensure_ascii=False, indent=2)}"
    )

def call_openai_for_batch(batch: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    user_prompt = build_user_prompt(batch)

    response = client.chat.completions.create(
        model=MODEL,
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )

    content = response.choices[0].message.content or ""
    data = extract_json_block(content)

    if not isinstance(data, list):
        raise ValueError("Model response was not a JSON list.")

    return data

# =========================================================
# MERGING / VALIDATION
# =========================================================

def validate_result(item: Dict[str, Any]) -> Dict[str, Any]:
    keep = bool(item.get("keep", False))
    pictogram_id = item.get("pictogram_id")

    if keep:
        english_label = item.get("english_label")
        english_filename = item.get("english_filename")
        technique = item.get("technique")
        tags = item.get("tags", [])

        if not english_label or not technique:
            keep = False

        if technique not in {"image_naming", "sentence_repetition"}:
            keep = False

        if not english_filename and english_label:
            english_filename = slugify(english_label)

        if technique == "image_naming" and english_label:
            english_label = normalize_text(english_label)
            if len(english_label.split()) > 2:
                # keep it clinically simpler
                keep = False

        tags = [slugify(str(t)).replace("_", " ") for t in tags if str(t).strip()]
        tags = [t for t in tags if t]
        deduped_tags = []
        for t in tags:
            if t not in deduped_tags:
                deduped_tags.append(t)

        return {
            "pictogram_id": pictogram_id,
            "keep": keep,
            "skip_reason": None if keep else "invalid_model_output",
            "english_label": normalize_text(english_label) if keep and english_label else None,
            "english_filename": slugify(english_filename) if keep and english_filename else None,
            "technique": technique if keep else None,
            "tags": deduped_tags if keep else [],
        }

    return {
        "pictogram_id": pictogram_id,
        "keep": False,
        "skip_reason": item.get("skip_reason") or "rejected_by_model",
        "english_label": None,
        "english_filename": None,
        "technique": None,
        "tags": [],
    }

def merge_result(original: Dict[str, Any], enriched: Dict[str, Any]) -> Dict[str, Any]:
    return {
        **original,
        "keep": enriched["keep"],
        "skip_reason": enriched["skip_reason"],
        "english_label": enriched["english_label"],
        "english_filename": enriched["english_filename"],
        "technique": enriched["technique"],
        "tags": enriched["tags"],
    }

# =========================================================
# MAIN
# =========================================================

def main() -> None:
    if not os.getenv("OPENAI_API_KEY"):
        raise EnvironmentError("OPENAI_API_KEY is not set.")

    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Missing input file: {INPUT_FILE}")

    raw_items: List[Dict[str, Any]] = load_json(INPUT_FILE, default=[])
    checkpoint: Dict[str, Dict[str, Any]] = load_json(CHECKPOINT_FILE, default={})

    print(f"Loaded raw items: {len(raw_items)}")
    print(f"Loaded checkpoint items: {len(checkpoint)}")

    final_results: List[Dict[str, Any]] = []
    to_send: List[Dict[str, Any]] = []

    for item in raw_items:
        pictogram_id = item["pictogram_id"]
        key = str(pictogram_id)

        if key in checkpoint:
            final_results.append(checkpoint[key])
            continue

        reason = hard_skip_reason(item.get("raw_label", ""))
        if reason:
            skipped_item = {
                **item,
                "keep": False,
                "skip_reason": reason,
                "english_label": None,
                "english_filename": None,
                "technique": None,
                "tags": [],
            }
            checkpoint[key] = skipped_item
            final_results.append(skipped_item)
            continue

        to_send.append(item)

    print(f"Hard-skipped or resumed: {len(final_results)}")
    print(f"Remaining for OpenAI enrichment: {len(to_send)}")

    for start in range(0, len(to_send), BATCH_SIZE):
        batch = to_send[start:start + BATCH_SIZE]
        batch_ids = [item["pictogram_id"] for item in batch]
        print(f"Processing batch {start // BATCH_SIZE + 1}: {batch_ids[0]} -> {batch_ids[-1]} ({len(batch)} items)")

        last_error = None
        raw_results = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                raw_results = call_openai_for_batch(batch)
                break
            except Exception as e:
                last_error = e
                print(f"  Attempt {attempt} failed: {e}")
                time.sleep(2 * attempt)

        if raw_results is None:
            print("  Batch failed permanently, marking items for retry later.")
            for item in batch:
                retry_item = {
                    **item,
                    "keep": False,
                    "skip_reason": "batch_failed_retry_later",
                    "english_label": None,
                    "english_filename": None,
                    "technique": None,
                    "tags": [],
                }
                checkpoint[str(item["pictogram_id"])] = retry_item
                final_results.append(retry_item)
            save_json(CHECKPOINT_FILE, checkpoint)
            continue

        result_map: Dict[int, Dict[str, Any]] = {}
        for r in raw_results:
            validated = validate_result(r)
            if validated["pictogram_id"] is not None:
                result_map[int(validated["pictogram_id"])] = validated

        for item in batch:
            pid = int(item["pictogram_id"])
            validated = result_map.get(pid)
            if validated is None:
                validated = {
                    "pictogram_id": pid,
                    "keep": False,
                    "skip_reason": "missing_from_model_response",
                    "english_label": None,
                    "english_filename": None,
                    "technique": None,
                    "tags": [],
                }

            merged = merge_result(item, validated)
            checkpoint[str(pid)] = merged
            final_results.append(merged)

        save_json(CHECKPOINT_FILE, checkpoint)
        print(f"  Saved checkpoint: {len(checkpoint)} items")
        time.sleep(SLEEP_BETWEEN_BATCHES)

    final_results_sorted = sorted(final_results, key=lambda x: x["pictogram_id"])
    save_json(OUTPUT_FILE, final_results_sorted)

    kept = sum(1 for x in final_results_sorted if x.get("keep"))
    skipped = len(final_results_sorted) - kept

    print(f"Done.")
    print(f"Kept: {kept}")
    print(f"Skipped: {skipped}")
    print(f"Wrote final file: {OUTPUT_FILE}")
    print(f"Checkpoint file: {CHECKPOINT_FILE}")

if __name__ == "__main__":
    main()