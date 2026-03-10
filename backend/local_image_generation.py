# backend/local_image_generation.py
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent
PUBLIC_DIR = PROJECT_ROOT / "frontend" / "stroke-recovery" / "public"

DEFAULT_COMFYUI_BASE_URL = os.getenv("COMFYUI_BASE_URL", "http://127.0.0.1:8188").rstrip("/")
DEFAULT_WORKFLOW_PATH = os.getenv(
    "COMFYUI_WORKFLOW_PATH",
    str(BACKEND_DIR / "comfyui_workflow_api.json"),
)

# These must match your exported API workflow JSON
DEFAULT_PROMPT_NODE_ID = os.getenv("COMFYUI_PROMPT_NODE_ID", "87")
DEFAULT_SAVE_NODE_ID = os.getenv("COMFYUI_SAVE_NODE_ID", "92")

DEFAULT_TIMEOUT_SECONDS = int(os.getenv("COMFYUI_TIMEOUT_SECONDS", "180"))
DEFAULT_POLL_INTERVAL_SECONDS = float(os.getenv("COMFYUI_POLL_INTERVAL_SECONDS", "1.0"))


class LocalImageGenerationError(Exception):
    pass


def _log(*parts: object) -> None:
    print("[LOCAL GEN]", *parts)


def _http_json(
    url: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = None
    headers = {}

    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url=url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        raise LocalImageGenerationError(
            f"ComfyUI HTTP {e.code} on {url}: {body}"
        ) from e
    except urllib.error.URLError as e:
        raise LocalImageGenerationError(
            f"Could not reach ComfyUI at {url}: {e}"
        ) from e


def _http_bytes(url: str) -> bytes:
    req = urllib.request.Request(url=url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="ignore")
        raise LocalImageGenerationError(
            f"ComfyUI HTTP {e.code} when downloading image from {url}: {body}"
        ) from e
    except urllib.error.URLError as e:
        raise LocalImageGenerationError(
            f"Could not download image from ComfyUI at {url}: {e}"
        ) from e


def _load_workflow_template(workflow_path: str | Path | None = None) -> dict[str, Any]:
    wf_path = Path(workflow_path or DEFAULT_WORKFLOW_PATH)
    _log("Loading workflow from:", wf_path)

    if not wf_path.exists():
        raise LocalImageGenerationError(
            f"ComfyUI workflow file not found: {wf_path}"
        )

    try:
        workflow = json.loads(wf_path.read_text(encoding="utf-8"))
        _log("Workflow loaded successfully.")
        return workflow
    except Exception as e:
        raise LocalImageGenerationError(
            f"Failed to parse ComfyUI workflow JSON: {wf_path}"
        ) from e


def _set_node_text_input(
    workflow: dict[str, Any],
    node_id: str,
    value: str,
    field_name: str = "text",
) -> None:
    node = workflow.get(node_id)
    if not isinstance(node, dict):
        raise LocalImageGenerationError(f"Workflow node '{node_id}' not found")

    inputs = node.get("inputs")
    if not isinstance(inputs, dict):
        raise LocalImageGenerationError(f"Workflow node '{node_id}' has no inputs")

    inputs[field_name] = value
    _log(f"Set node {node_id}.{field_name} =", value[:160] + ("..." if len(value) > 160 else ""))


def _set_save_prefix(
    workflow: dict[str, Any],
    node_id: str,
    prefix: str,
) -> None:
    node = workflow.get(node_id)
    if not isinstance(node, dict):
        raise LocalImageGenerationError(f"Save node '{node_id}' not found")

    inputs = node.get("inputs")
    if not isinstance(inputs, dict):
        raise LocalImageGenerationError(f"Save node '{node_id}' has no inputs")

    inputs["filename_prefix"] = prefix
    _log(f"Set save prefix on node {node_id} =", prefix)


def _queue_prompt(
    workflow: dict[str, Any],
    base_url: str,
) -> str:
    payload = {"prompt": workflow}
    _log("Queueing prompt to ComfyUI at:", f"{base_url}/prompt")
    data = _http_json(f"{base_url}/prompt", method="POST", payload=payload)

    prompt_id = data.get("prompt_id")
    if not prompt_id:
        raise LocalImageGenerationError(f"ComfyUI did not return prompt_id: {data}")

    _log("Prompt queued successfully. prompt_id =", prompt_id)
    return str(prompt_id)


def _get_history_item(prompt_id: str, base_url: str) -> dict[str, Any] | None:
    data = _http_json(f"{base_url}/history/{prompt_id}", method="GET")
    if not isinstance(data, dict):
        return None
    return data.get(prompt_id)


def _extract_output_image_record(history_item: dict[str, Any]) -> dict[str, Any] | None:
    """
    Returns the first image record from history, e.g.:
    {
      "filename": "...png",
      "subfolder": "...",
      "type": "output"
    }
    """
    outputs = history_item.get("outputs")
    if not isinstance(outputs, dict):
        return None

    for node_id, node_output in outputs.items():
        if not isinstance(node_output, dict):
            continue

        images = node_output.get("images")
        if not isinstance(images, list):
            continue

        for img in images:
            if isinstance(img, dict) and img.get("filename"):
                _log(f"Found output image on node {node_id}:", img)
                return img

    return None


def _build_view_url(base_url: str, image_record: dict[str, Any]) -> str:
    params = {
        "filename": image_record.get("filename", ""),
        "subfolder": image_record.get("subfolder", ""),
        "type": image_record.get("type", "output"),
    }
    url = f"{base_url}/view?{urllib.parse.urlencode(params)}"
    _log("Built ComfyUI view URL:", url)
    return url


def _normalise_relative_public_path(path_str: str) -> str:
    rel = str(path_str).replace("\\", "/").lstrip("/")
    rel = rel.replace("../", "").replace("..\\", "")
    return rel


def _derive_output_relpath(
    save_prefix: str,
    image_record: dict[str, Any],
) -> str:
    """
    We do NOT rely on ComfyUI saving directly into frontend/public.
    Instead, we download the generated image from ComfyUI and write it ourselves
    into frontend/public/<save_prefix>.<ext>
    """
    rel_prefix = _normalise_relative_public_path(save_prefix)
    filename = str(image_record.get("filename", "") or "").strip()

    suffix = Path(filename).suffix.lower() if filename else ""
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
        suffix = ".png"

    rel_path = f"{rel_prefix}{suffix}"
    _log("Derived frontend relative path:", rel_path)
    return rel_path


def _write_public_file(rel_path: str, content: bytes) -> None:
    abs_path = PUBLIC_DIR / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(content)
    _log("Wrote generated file to:", abs_path)


def generate_image_via_comfyui(
    *,
    prompt_text: str,
    save_prefix: str,
    technique_id: str,
    base_url: str | None = None,
    workflow_path: str | Path | None = None,
    prompt_node_id: str | None = None,
    save_node_id: str | None = None,
    timeout_seconds: int | None = None,
    poll_interval_seconds: float | None = None,
) -> str:
    """
    Returns a frontend relative path like:
      generated/image_naming/foo_abcd1234.png
    """
    _log("Starting local generation...")
    _log("Technique:", technique_id)
    _log("Prompt text:", prompt_text[:220] + ("..." if len(prompt_text) > 220 else ""))
    _log("Save prefix:", save_prefix)

    base_url = (base_url or DEFAULT_COMFYUI_BASE_URL).rstrip("/")
    timeout_seconds = timeout_seconds or DEFAULT_TIMEOUT_SECONDS
    poll_interval_seconds = poll_interval_seconds or DEFAULT_POLL_INTERVAL_SECONDS
    prompt_node_id = str(prompt_node_id or DEFAULT_PROMPT_NODE_ID)
    save_node_id = str(save_node_id or DEFAULT_SAVE_NODE_ID)

    _log("ComfyUI base URL:", base_url)
    _log("Prompt node ID:", prompt_node_id)
    _log("Save node ID:", save_node_id)
    _log("Timeout seconds:", timeout_seconds)
    _log("Poll interval seconds:", poll_interval_seconds)

    workflow = _load_workflow_template(workflow_path)

    _set_node_text_input(
        workflow=workflow,
        node_id=prompt_node_id,
        value=prompt_text,
        field_name="text",
    )
    _set_save_prefix(
        workflow=workflow,
        node_id=save_node_id,
        prefix=save_prefix,
    )

    prompt_id = _queue_prompt(workflow=workflow, base_url=base_url)

    start = time.time()
    poll_count = 0

    while time.time() - start < timeout_seconds:
        poll_count += 1
        elapsed = round(time.time() - start, 1)
        _log(f"Polling history... attempt={poll_count}, elapsed={elapsed}s, prompt_id={prompt_id}")

        history_item = _get_history_item(prompt_id=prompt_id, base_url=base_url)

        if history_item:
            _log("History item found for prompt.")
            image_record = _extract_output_image_record(history_item)
            if image_record:
                view_url = _build_view_url(base_url, image_record)
                _log("Downloading image bytes...")
                image_bytes = _http_bytes(view_url)
                _log("Downloaded bytes:", len(image_bytes))

                rel_path = _derive_output_relpath(
                    save_prefix=save_prefix,
                    image_record=image_record,
                )
                _write_public_file(rel_path, image_bytes)
                _log("Local generation complete. Returning:", rel_path)
                return rel_path
            else:
                _log("History exists but no output image record yet.")
        else:
            _log("No history item yet.")

        time.sleep(poll_interval_seconds)

    raise LocalImageGenerationError(
        f"Timed out waiting for ComfyUI output for prompt_id={prompt_id}"
    )