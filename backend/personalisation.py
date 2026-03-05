# backend/personalisation.py
import json
import os
import re
import uuid
from datetime import datetime
from typing import Any
from openai import OpenAI


# ------------ Catalog loading ------------

def load_image_catalog() -> list[dict]:
    """
    backend/data/image_catalog.json
    Each entry: { id, file, tags:[...], difficulty:1..3, technique:"sentence_repetition"|"image_naming" }
    """
    path = os.path.join(os.path.dirname(__file__), "data", "image_catalog.json")
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9\s]", " ", (s or "").lower()).strip()


def _pick_catalog_image(
    catalog: list[dict],
    want_tags: list[str],
    difficulty: int,
    technique_id: str,
    word: str | None = None,   # NEW
) -> str | None:
    """
    Strategy:
    1) If image_naming and word provided -> exact match by word first.
    2) Otherwise tag overlap matching, but ONLY accept if overlap > 0.
    """
    # 1) Exact word match (image naming)
    if technique_id == "image_naming" and word:
        w = _norm(word)
        for it in catalog:
            if it.get("technique") and it.get("technique") != technique_id:
                continue
            if int(it.get("difficulty", 1)) > int(difficulty):
                continue
            if _norm(it.get("word", "")) == w:
                return it.get("file")

    # 2) Tag overlap match
    want = set(_norm(t) for t in (want_tags or []) if t)
    best_file = None
    best_overlap = 0

    for it in catalog:
        if it.get("technique") and it.get("technique") != technique_id:
            continue
        if int(it.get("difficulty", 1)) > int(difficulty):
            continue

        tags = set(_norm(t) for t in (it.get("tags") or []))
        overlap = len(want & tags)

        if overlap > best_overlap:
            best_overlap = overlap
            best_file = it.get("file")

    # IMPORTANT: if no overlap, return None so you hit placeholder/fallback
    if best_overlap <= 0:
        return None

    return best_file


# ------------ Strict JSON helpers ------------

def _extract_json(text: str) -> dict:
    """
    Pull the first JSON object from a model response.
    """
    if not text:
        raise ValueError("Empty LLM response")

    text = text.strip()

    # already a JSON object
    if text.startswith("{") and text.endswith("}"):
        return json.loads(text)

    # find first {...} block
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        raise ValueError("No JSON object found in response")

    return json.loads(m.group(0))


def _clean_word(w: str) -> str:
    w = (w or "").strip().lower()
    w = re.sub(r"[^a-z]", "", w)  # single word only, letters only
    return w


def _clean_token(w: str) -> str:
    # for required_content / required_function: lowercase token, no punctuation
    w = (w or "").strip().lower()
    w = re.sub(r"[^a-z]", "", w)
    return w


def _clean_sentence(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    # Keep sentence text readable; remove weird symbols but allow apostrophes
    s = re.sub(r"[^A-Za-z0-9\s\.,'!?-]", "", s)
    return s.strip()


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


def _ensure_list_of_tags(x: Any, max_len: int = 6) -> list[str]:
    if not isinstance(x, list):
        return []
    out: list[str] = []
    for item in x:
        t = _norm(str(item))
        if t:
            out.append(t)
        if len(out) >= max_len:
            break
    return out


# ------------ LLM pack generation ------------

def build_llm_prompt_for_pack(profile: dict, technique_id: str, n_items: int) -> str:
    difficulty = int(profile.get("difficulty", 1))
    sentence_length = int(profile.get("sentence_length", 1))
    interests = (profile.get("interests_text", "") or "").strip()
    avoid = (profile.get("avoid_topics_text") or "").strip()

    if technique_id == "image_naming":
        return f"""
Return STRICT JSON ONLY (no markdown, no commentary, no extra keys).
Generate personalised aphasia therapy content.

Technique: IMAGE NAMING (single word)
User interests/routine: {interests}
Avoid topics: {avoid}

Difficulty scale:
1 = common short words (3-6 letters)
2 = medium words (6-9 letters)
3 = longer / less frequent words (9-12 letters)

Target difficulty: {difficulty}

Schema:
{{
  "items": [
    {{
      "word": "string",
      "tags": ["tag1","tag2","tag3"]
    }}
  ]
}}

Rules:
- Exactly {n_items} items.
- "word" must be ONE WORD (letters only), no spaces, no punctuation, no numbers.
- Keep words realistic and relevant to the user.
- tags describe what an image could depict.
- Output JSON only.
""".strip()

    length_rule = {
        1: "short (4-6 words)",
        2: "medium (7-10 words)",
        3: "long (11-14 words)",
    }[max(1, min(3, sentence_length))]

    return f"""
Return STRICT JSON ONLY (no markdown, no commentary, no extra keys).
Generate personalised aphasia therapy content.

Technique: SENTENCE REPETITION (repeat shown sentence)
User interests/routine: {interests}
Avoid topics: {avoid}

Difficulty scale:
1 = simple present tense SVO sentences
2 = adjectives / prepositional phrases
3 = clauses (while/when/because) but still clear

Target difficulty: {difficulty}
Sentence length target: {length_rule}

Schema:
{{
  "items": [
    {{
      "sentence": "string",
      "required_content": ["noun_or_verb1","noun_or_verb2"],
      "required_function": ["function1","function2"],
      "tags": ["tag1","tag2","tag3"]
    }}
  ]
}}

Rules:
- Exactly {n_items} items.
- Sentences must be unambiguous and easy to picture.
- required_content: key nouns/verbs (lowercase, single tokens).
- required_function: grammar words like the/a/is/to/in/on/and (lowercase tokens).
- tags describe the image scene.
- Output JSON only.
""".strip()



def generate_pack_items_with_llm(profile: dict, technique_id: str, n_items: int) -> list[dict]:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    prompt = build_llm_prompt_for_pack(profile, technique_id, n_items)

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Return only strict JSON. No commentary. No markdown."},
            {"role": "user", "content": prompt},
        ],
        temperature=0.4,
    )

    text = (resp.choices[0].message.content or "").strip()
    data = _extract_json(text)

    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("LLM returned invalid items list")

    return items[:n_items]

def _validate_and_clean_items(profile: dict, technique_id: str, items: list[dict], n_items: int) -> list[dict]:
    """
    Enforce schema + clean outputs. If LLM output is messy, salvage what we can.
    """
    out: list[dict] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue

        if technique_id == "image_naming":
            word = _clean_word(raw.get("word", ""))
            if not word:
                continue
            tags = _ensure_list_of_tags(raw.get("tags", []))
            out.append({"word": word, "tags": tags})

        else:
            sentence = _clean_sentence(raw.get("sentence", ""))
            if not sentence:
                continue

            required_content = _ensure_list_of_tokens(raw.get("required_content", []), max_len=6)
            required_function = _ensure_list_of_tokens(raw.get("required_function", []), max_len=6)
            tags = _ensure_list_of_tags(raw.get("tags", []))

            # minimal guard: need at least 1 content token for your evaluator
            if len(required_content) < 1:
                continue

            out.append({
                "sentence": sentence,
                "required_content": required_content,
                "required_function": required_function,
                "tags": tags,
            })

        if len(out) >= n_items:
            break

    # Hard fallback: if LLM gave too few valid items, pad with safe defaults
    if len(out) < n_items:
        need = n_items - len(out)
        if technique_id == "image_naming":
            defaults = [
                {"word": "water", "tags": ["drink", "cup", "kitchen"]},
                {"word": "phone", "tags": ["call", "device", "hand"]},
                {"word": "shirt", "tags": ["clothes", "wear", "wardrobe"]},
                {"word": "bread", "tags": ["food", "kitchen", "toast"]},
                {"word": "chair", "tags": ["seat", "room", "furniture"]},
            ]
        else:
            defaults = [
                {"sentence": "The boy kicks the ball.", "required_content": ["boy", "kicks", "ball"], "required_function": ["the"], "tags": ["boy", "kick", "ball"]},
                {"sentence": "The woman drinks water.", "required_content": ["woman", "drinks", "water"], "required_function": ["the"], "tags": ["woman", "drink", "water"]},
                {"sentence": "The dog runs in the park.", "required_content": ["dog", "runs", "park"], "required_function": ["the", "in"], "tags": ["dog", "run", "park"]},
                {"sentence": "The man opens the door.", "required_content": ["man", "opens", "door"], "required_function": ["the"], "tags": ["man", "open", "door"]},
                {"sentence": "The girl reads a book.", "required_content": ["girl", "reads", "book"], "required_function": ["the", "a"], "tags": ["girl", "read", "book"]},
            ]

        out.extend(defaults[:need])

    return out[:n_items]


def attach_images_from_catalog(items: list[dict], catalog: list[dict], technique_id: str, difficulty: int) -> list[dict]:
    out: list[dict] = []
    for it in items:
        tags = it.get("tags") or []
        if technique_id == "image_naming":
            img = _pick_catalog_image(
            catalog,
            tags,
            difficulty=difficulty,
            technique_id=technique_id,
            word=it.get("word", ""),
        )
        else:
            img = _pick_catalog_image(
            catalog,
            tags,
            difficulty=difficulty,
            technique_id=technique_id,
        )
        if technique_id == "image_naming":
            out.append({
                "word": (it.get("word") or "").strip(),
                "image": img or "placeholder.jpg",
            })
        else:
            out.append({
                "sentence": (it.get("sentence") or "").strip(),
                "image": img or "placeholder.jpg",
                "required_content": it.get("required_content") or [],
                "required_function": it.get("required_function") or [],
            })
    return out


def build_content_pack(profile: dict, technique_id: str, n_items: int = 10) -> dict[str, Any]:
    """
    Returns: {pack_id, technique_id, difficulty, generated_at, items:[...]}
    """
    difficulty = int(profile.get("difficulty", 1))
    catalog = load_image_catalog()

    raw_items = generate_pack_items_with_llm(profile, technique_id, n_items)
    cleaned = _validate_and_clean_items(profile, technique_id, raw_items, n_items)
    final_items = attach_images_from_catalog(cleaned, catalog, technique_id, difficulty)

    return {
        "pack_id": uuid.uuid4().hex,
        "technique_id": technique_id,
        "difficulty": difficulty,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "items": final_items,
    }