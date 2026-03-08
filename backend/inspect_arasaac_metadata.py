from pathlib import Path
import json

RAW_ROOT = Path(r"C:\Users\karam\OneDrive\Documents\Pictograms\pictograms\arasaac")

FILES_TO_CHECK = [
    "manifest.json",
    "names.json",
    "counts.json",
    "synsets.json",
]

def preview(obj, depth=0, max_items=5):
    indent = "  " * depth

    if isinstance(obj, dict):
        print(f"{indent}dict with {len(obj)} keys")
        for i, (k, v) in enumerate(obj.items()):
            if i >= max_items:
                break
            print(f"{indent}- key: {repr(k)}")
            preview(v, depth + 1, max_items)
    elif isinstance(obj, list):
        print(f"{indent}list with {len(obj)} items")
        for i, item in enumerate(obj[:max_items]):
            print(f"{indent}- item {i}:")
            preview(item, depth + 1, max_items)
    else:
        print(f"{indent}{type(obj).__name__}: {repr(obj)}")

def main():
    print("RAW ROOT:", RAW_ROOT)
    print("EXISTS:", RAW_ROOT.exists())

    pictograms = RAW_ROOT / "pictograms"
    print("\nPICTOGRAM FOLDER EXISTS:", pictograms.exists())
    if pictograms.exists():
        first_files = list(pictograms.iterdir())[:10]
        print("First 10 pictogram files:")
        for f in first_files:
            print(" ", f.name, "| suffix:", repr(f.suffix))

    for filename in FILES_TO_CHECK:
        path = RAW_ROOT / filename
        print("\n" + "=" * 60)
        print("FILE:", filename)
        print("EXISTS:", path.exists())

        if not path.exists():
            continue

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print("FAILED TO READ JSON:", e)
            continue

        preview(data, max_items=3)

if __name__ == "__main__":
    main()