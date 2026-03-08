from pathlib import Path
import json
import re

# ========= RAW DATA LOCATION =========
RAW_ROOT = Path(r"C:\Users\karam\OneDrive\Documents\Pictograms\pictograms\arasaac")
RAW_PICTOGRAMS_DIR = RAW_ROOT / "pictograms"
NAMES_JSON = RAW_ROOT / "names.json"
COUNTS_JSON = RAW_ROOT / "counts.json"
# =====================================

SCRIPT_PATH = Path(__file__).resolve()
BACKEND_DIR = SCRIPT_PATH.parent
DATA_DIR = BACKEND_DIR / "data"
OUTPUT_FILE = DATA_DIR / "arasaac_raw_processed.json"


def normalize_label(text: str) -> str:
    text = str(text).strip().lower()

    replacements = {
        "é": "e", "è": "e", "ê": "e", "ë": "e",
        "à": "a", "â": "a", "ä": "a",
        "î": "i", "ï": "i",
        "ô": "o", "ö": "o",
        "ù": "u", "û": "u", "ü": "u",
        "ç": "c",
        "'": "",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    text = re.sub(r"\s+", " ", text)
    return text.strip()


def slugify_label(text: str) -> str:
    text = normalize_label(text)
    text = re.sub(r"[^a-z0-9\s\-_]", "", text)
    text = re.sub(r"[\s_]+", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_")


def main():
    print("RAW_ROOT:", RAW_ROOT)
    print("RAW_PICTOGRAMS_DIR:", RAW_PICTOGRAMS_DIR)
    print("NAMES_JSON:", NAMES_JSON)
    print("COUNTS_JSON:", COUNTS_JSON)
    print("OUTPUT_FILE:", OUTPUT_FILE)

    if not RAW_ROOT.exists():
        raise FileNotFoundError(f"RAW_ROOT not found: {RAW_ROOT}")
    if not RAW_PICTOGRAMS_DIR.exists():
        raise FileNotFoundError(f"RAW_PICTOGRAMS_DIR not found: {RAW_PICTOGRAMS_DIR}")
    if not NAMES_JSON.exists():
        raise FileNotFoundError(f"NAMES_JSON not found: {NAMES_JSON}")
    if not COUNTS_JSON.exists():
        raise FileNotFoundError(f"COUNTS_JSON not found: {COUNTS_JSON}")

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    names = json.loads(NAMES_JSON.read_text(encoding="utf-8"))
    counts = json.loads(COUNTS_JSON.read_text(encoding="utf-8"))

    if not isinstance(names, list):
        raise ValueError("names.json must be a list")
    if not isinstance(counts, list):
        raise ValueError("counts.json must be a list")

    raw_files = sorted(
        [p for p in RAW_PICTOGRAMS_DIR.iterdir() if p.is_file()],
        key=lambda p: int(p.name) if p.name.isdigit() else 10**9
    )

    processed = []
    missing_name_count = 0
    non_numeric_count = 0

    for raw_file in raw_files:
        source_filename = raw_file.name
        is_numeric = source_filename.isdigit()

        if not is_numeric:
            non_numeric_count += 1
            continue

        pictogram_id = int(source_filename)

        raw_label = names[pictogram_id] if pictogram_id < len(names) else None
        usage_count = counts[pictogram_id] if pictogram_id < len(counts) else 0

        if raw_label is None:
            missing_name_count += 1
            continue

        raw_label = str(raw_label).strip()

        item = {
            "pictogram_id": pictogram_id,
            "raw_label": raw_label,
            "normalized_raw_label": normalize_label(raw_label),
            "raw_slug": slugify_label(raw_label),
            "usage_count": usage_count,
            "source_file": str(raw_file),
            "source_filename": source_filename,
            "has_numeric_filename": True,
        }

        processed.append(item)

    OUTPUT_FILE.write_text(
        json.dumps(processed, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    print(f"Processed items: {len(processed)}")
    print(f"Missing labels: {missing_name_count}")
    print(f"Non-numeric files skipped: {non_numeric_count}")
    print(f"Wrote file: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()