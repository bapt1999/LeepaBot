import asyncio
import base64
from dataclasses import dataclass
from io import BytesIO
import json
import logging
from pathlib import Path, PurePosixPath
import time
import uuid
import warnings

import httpx

from core import prompts


logger = logging.getLogger(__name__)
PISTON_URL = "http://127.0.0.1:2000/api/v2/execute"
PYTHON_VERSION = "3.12.0"
MAX_CODE_RUNS = 2
MAX_SOURCE_BYTES = 12000
MAX_PNG_BYTES = 262144
MAX_HTTP_BYTES = 2097152
MAX_IMAGE_PIXELS = 1048576
JOB_MEMORY_BYTES = 134217728
_gate = asyncio.Lock()
_jobs = set()
_cooldown_until = 0.0


@dataclass(frozen=True)
class CodeImage:
    data: bytes
    filename: str
    description: str


def python_enabled():
    return bool(getattr(prompts, "USE_PYTHON", False))


def images_enabled():
    return python_enabled() and bool(getattr(prompts, "USE_PYTHON_IMAGES", False))


def tool_definition():
    properties = {
        "code": {"type": "string", "description": "A short Python 3.12 script. Print useful results. Each run has a fresh temporary directory, no network, and 128 MiB RAM."},
    }
    if images_enabled():
        properties.update({
            "image_path": {"type": "string", "description": "Optional relative .png filename saved by your code. Collect this graph for your final Discord reply; at most 256 KiB and 1024 x 1024 pixels."},
            "image_description": {"type": "string", "description": "Short description of what the graph represents, at most 200 characters."},
        })
    return {"type": "function", "function": {
        "name": "run_python",
        "description": "Execute Python privately. Receive text and errors. An optional graph is held for the final reply. Every new run replaces any earlier pending graph, including when the new run fails.",
        "parameters": {"type": "object", "properties": properties, "required": ["code"], "additionalProperties": False},
    }}


def validate_arguments(args):
    allowed = {"code", "image_path", "image_description"} if images_enabled() else {"code"}
    if not isinstance(args, dict) or "code" not in args or set(args) - allowed:
        raise ValueError("Arguments must match the available run_python fields.")
    if not isinstance(args["code"], str) or not args["code"].strip() or len(args["code"].encode()) > MAX_SOURCE_BYTES:
        raise ValueError("Provide nonempty Python source of at most 12000 bytes.")
    path = args.get("image_path", "")
    description = args.get("image_description", "")
    if not isinstance(path, str) or not isinstance(description, str) or len(description) > 200:
        raise ValueError("Image path and description must be short strings.")
    if path:
        parsed = PurePosixPath(path)
        if (
            len(path) > 200 or parsed.is_absolute() or ".." in parsed.parts
            or "\\" in path or any(ord(ch) < 32 for ch in path)
            or parsed.suffix.lower() != ".png"
        ):
            raise ValueError("Use a relative .png filename without parent directories.")
        if not description.strip():
            raise ValueError("Describe the graph in image_description.")


def clean_png(encoded, description):
    from PIL import Image, UnidentifiedImageError

    if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_PNG_BYTES + 2) // 3):
        raise ValueError("PNG exceeds the 256 KiB limit.")
    data = base64.b64decode(encoded, validate=True)
    if not 0 < len(data) <= MAX_PNG_BYTES:
        raise ValueError("Invalid PNG size.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data), formats=["PNG"]) as picture:
                width, height = picture.size
                if not (0 < width <= 1024 and 0 < height <= 1024) or width * height > MAX_IMAGE_PIXELS:
                    raise ValueError("Graphs must be at most 1024 x 1024 pixels.")
                if getattr(picture, "is_animated", False):
                    raise ValueError("Animated images are unavailable.")
                rgba = picture.convert("RGBA")
                clean = Image.frombytes("RGBA", rgba.size, rgba.tobytes())
                output = BytesIO()
                clean.save(output, format="PNG", compress_level=6)
    except (OSError, UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("The output could not be decoded as a supported PNG.") from exc
    data = output.getvalue()
    if len(data) > MAX_PNG_BYTES:
        raise ValueError("The validated PNG exceeds 256 KiB.")
    return CodeImage(data, f"leepa-plot-{uuid.uuid4().hex[:8]}.png", description.strip())


async def _execute(args, turn_id, cancelled):
    global _cooldown_until
    async with _gate:
        if cancelled():
            return {"ok": False, "error": "Turn cancelled."}, None
        wrapper = Path(__file__).with_name("piston_job.py").read_text(encoding="utf-8")
        payload = {
            "language": "python", "version": PYTHON_VERSION,
            "files": [
                {"name": "piston_job.py", "content": wrapper},
                {"name": "user_code.py", "content": args["code"]},
                {"name": "request.json", "content": json.dumps({"image_path": args.get("image_path", "")})},
            ],
            "run_timeout": 10000, "run_cpu_time": 5000,
            "run_memory_limit": JOB_MEMORY_BYTES,
            "compile_timeout": 10000, "compile_cpu_time": 5000,
            "compile_memory_limit": JOB_MEMORY_BYTES,
        }
        started = time.monotonic()
        logger.info(
            "[CODE] turn=%s python=%s image_path=%r image_description=%r source=%r",
            turn_id, PYTHON_VERSION, args.get("image_path"),
            args.get("image_description"), args["code"],
        )
        try:
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=15.0) as client:
                async with asyncio.timeout(15.0):
                    async with client.stream("POST", PISTON_URL, json=payload) as response:
                        response.raise_for_status()
                        body = bytearray()
                        async for part in response.aiter_bytes():
                            if len(body) + len(part) > MAX_HTTP_BYTES:
                                raise ValueError("Piston's response exceeded the size limit.")
                            body.extend(part)
        except (httpx.HTTPError, TimeoutError):
            _cooldown_until = time.monotonic() + 10.0
            logger.exception("[CODE] turn=%s Piston request failed; pausing new jobs briefly.", turn_id)
            return {"ok": False, "retryable": False, "error": "Piston is unavailable or did not finish in time. Do not claim execution succeeded."}, None
        if cancelled():
            return {"ok": False, "error": "Turn cancelled; results discarded."}, None
        response = json.loads(body)
        run = response.get("run", {}) if isinstance(response, dict) else {}
        if not isinstance(run, dict) or run.get("code") != 0 or run.get("signal") or run.get("status"):
            logger.warning("[CODE] turn=%s sandbox failure: code=%r status=%r signal=%r", turn_id, run.get("code") if isinstance(run, dict) else None, run.get("status") if isinstance(run, dict) else None, run.get("signal") if isinstance(run, dict) else None)
            return {"ok": False, "error": "The sandbox stopped the job or its wrapper. The time, memory, or output limit may have been reached."}, None
        raw = run.get("stdout")
        if not isinstance(raw, str) or len(raw) > 524288:
            raise ValueError("Invalid execution envelope.")
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get("protocol") != "leepa-python-1":
            raise ValueError("Invalid execution envelope.")
        for field in ("stdout", "stderr"):
            if not isinstance(result.get(field), str) or len(result[field]) > 4096:
                raise ValueError("Invalid execution text.")
        if type(result.get("exit_code")) is not int or type(result.get("timed_out")) is not bool:
            raise ValueError("Invalid execution status.")
        output = {
            "ok": result["exit_code"] == 0 and not result["timed_out"],
            "exit_code": result["exit_code"], "timed_out": result["timed_out"],
            "stdout": result["stdout"], "stderr": result["stderr"],
            "text_truncated": result.get("text_truncated") is True,
            "image_queued": False,
        }
        image = None
        if args.get("image_path") and output["ok"] and images_enabled():
            try:
                if "png" not in result:
                    raise ValueError(str(result.get("image_error") or "The requested PNG was not created.")[:200])
                image = await asyncio.to_thread(clean_png, result["png"], args["image_description"])
                output.update(image_queued=True, image_description=image.description, image_bytes=len(image.data))
            except (ValueError, ImportError) as exc:
                output["ok"] = False
                output["image_error"] = str(exc)[:200]
                output["error"] = (
                    "Python finished, but the requested graph could not be attached. "
                    "If another Python run is available, correct the image error and "
                    "create the graph again. No image is queued."
                )
        logger.info("[CODE] turn=%s %.2fs result=%s", turn_id, time.monotonic() - started, json.dumps(output, ensure_ascii=True))
        return output, image


async def run_python(args, turn_id, cancelled):
    if not python_enabled() or cancelled():
        return {"ok": False, "retryable": False, "error": "Python is disabled or the turn was cancelled."}, None
    validate_arguments(args)
    if _gate.locked() or _jobs or time.monotonic() < _cooldown_until:
        return {"ok": False, "retryable": False, "error": "The Python worker is busy. Finish this reply without another execution attempt."}, None
    task = asyncio.create_task(_execute(args, turn_id, cancelled))
    _jobs.add(task)

    def finished(job):
        _jobs.discard(job)
        if not job.cancelled():
            job.exception()

    task.add_done_callback(finished)
    # A cancelled chat leaves the bounded worker request to finish and clean up.
    result, image = await asyncio.shield(task)
    if cancelled():
        return {"ok": False, "error": "Turn cancelled; results discarded."}, None
    return result, image
