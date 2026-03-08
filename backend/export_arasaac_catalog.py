from pathlib import Path
import json
import shutil
from collections import defaultdict

SCRIPT_PATH = Path(__file__).resolve()
BACKEND_DIR = SCRIPT_PATH.parent
PROJECT_ROOT = BACKEND_DIR.parent

DATA_DIR = BACKEND_DIR / "data"
INPUT_FILE = DATA_DIR / "arasaac_enriched.json"

PUBLIC_DIR = PROJECT_ROOT / "frontend" / "stroke-recovery" / "public"
ARASAAC_IMAGES_DIR = PUBLIC_DIR / "arasaac_images"
ARASAAC_SENTENCE_DIR = PUBLIC_DIR / "arasaac_sentence_images"
CATALOG_FILE = PUBLIC_DIR / "image_catalog.json"


def unique_destination(folder: Path, stem: str, suffix: str = ".png") -> Path:
    candidate = folder / f"{stem}{suffix}"
    counter = 2
    while candidate.exists():
        candidate = folder / f"{stem}_{counter}{suffix}"
        counter += 1
    return candidate


def dedupe_tags(tags: list[str]) -> list[str]:
    seen = []
    for tag in tags:
        tag = str(tag).strip().lower()
        if tag and tag not in seen:
            seen.append(tag)
    return seen


def main():
    if not INPUT_FILE.exists():
        raise FileNotFoundError(f"Missing enriched file: {INPUT_FILE}")

    ARASAAC_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    ARASAAC_SENTENCE_DIR.mkdir(parents=True, exist_ok=True)

    items = json.loads(INPUT_FILE.read_text(encoding="utf-8"))

    exported_catalog = []
    skipped = 0
    copied = 0

    # optional stats
    counts_by_technique = defaultdict(int)

    for item in items:
        if not item.get("keep", False):
            skipped += 1
            continue

        source_file = item.get("source_file")
        english_filename = item.get("english_filename")
        english_label = item.get("english_label")
        technique = item.get("technique")
        tags = dedupe_tags(item.get("tags", []))
        pictogram_id = item.get("pictogram_id")

        if not source_file or not english_filename or not technique:
            skipped += 1
            continue

        source_path = Path(source_file)
        if not source_path.exists():
            skipped += 1
            continue

        if technique == "image_naming":
            dest = unique_destination(ARASAAC_IMAGES_DIR, english_filename)
            rel_file = f"arasaac_images/{dest.name}"

            shutil.copy2(source_path, dest)

            exported_catalog.append({
                "id": dest.stem,
                "word": english_label,
                "file": rel_file,
                "tags": tags if tags else [english_label],
                "difficulty": 1,
                "technique": "image_naming",
                "source": "arasaac",
                "raw_label": item.get("raw_label"),
                "pictogram_id": pictogram_id,
                "usage_count": item.get("usage_count", 0),
            })

            copied += 1
            counts_by_technique["image_naming"] += 1

        elif technique == "sentence_repetition":
            dest = unique_destination(ARASAAC_SENTENCE_DIR, english_filename)
            rel_file = f"arasaac_sentence_images/{dest.name}"

            shutil.copy2(source_path, dest)

            exported_catalog.append({
                "id": dest.stem,
                "file": rel_file,
                "tags": tags,
                "difficulty": 1,
                "technique": "sentence_repetition",
                "source": "arasaac",
                "raw_label": item.get("raw_label"),
                "english_label": english_label,
                "pictogram_id": pictogram_id,
                "usage_count": item.get("usage_count", 0),
            })

            copied += 1
            counts_by_technique["sentence_repetition"] += 1

        else:
            skipped += 1

    exported_catalog = sorted(
        exported_catalog,
        key=lambda x: (x["technique"], x["id"])
    )

    CATALOG_FILE.write_text(
        json.dumps(exported_catalog, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    print("Export complete.")
    print(f"Copied: {copied}")
    print(f"Skipped: {skipped}")
    print(f"Image naming exported: {counts_by_technique['image_naming']}")
    print(f"Sentence repetition exported: {counts_by_technique['sentence_repetition']}")
    print(f"Catalog written to: {CATALOG_FILE}")
    print(f"Images folder: {ARASAAC_IMAGES_DIR}")
    print(f"Sentence folder: {ARASAAC_SENTENCE_DIR}")


if __name__ == "__main__":
    main()