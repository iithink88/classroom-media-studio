#!/usr/bin/env python3
"""Zero-dependency CLI for Agnes image and video generation."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import math
import mimetypes
import os
import socket
import time
import uuid
from pathlib import Path, PureWindowsPath
from typing import Any, Sequence
from urllib import error as urlerror
from urllib import parse, request


DEFAULT_BASE_URL = "https://api.agnes-ai.cn/v1"
IMAGE_25_FLASH_MODEL = "agnes-image-2.5-flash"
LEGACY_IMAGE_MODEL = "agnes-image-2.1-flash"
DEFAULT_IMAGE_MODEL = IMAGE_25_FLASH_MODEL
DEFAULT_IMAGE_MODEL_V2 = IMAGE_25_FLASH_MODEL
FLASH_VIDEO_MODEL = "agnes-video-2.5-flash"
DEFAULT_VIDEO_MODEL = FLASH_VIDEO_MODEL
FLASH_VIDEO_ASPECT_RATIOS = {"21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}
IMAGE_25_FLASH_ASPECT_RATIOS = {"1:1", "3:4", "4:3", "16:9", "9:16", "2:3", "3:2", "21:9"}
VIDEO_URL_FIELDS = ("video_url", "url", "output_url", "result_url", "remixed_from_video_id")
ARGUMENT_ERROR_CODES = {
    "invalid_arguments",
    "invalid_image_options",
    "invalid_poll_interval",
    "invalid_prompt",
    "invalid_timeout",
    "invalid_video_id",
    "invalid_video_options",
    "missing_image",
    "unsupported_parameter",
}
_WINDOWS_RESERVED_STEMS = {
    "CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return default if value is None or value == "" else value


def _base_url() -> str:
    return str(_env("AGNES_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")


def _domain_root() -> str:
    parsed = parse.urlparse(_base_url())
    return f"{parsed.scheme}://{parsed.netloc}"


def _image_model() -> str:
    return str(_env("AGNES_IMAGE_MODEL", DEFAULT_IMAGE_MODEL))


def _image_model_v2() -> str:
    return str(_env("AGNES_IMAGE_MODEL_V2", DEFAULT_IMAGE_MODEL_V2))


def _video_model(model: str | None = None) -> str:
    resolved = (model if model is not None else _env("AGNES_VIDEO_MODEL", DEFAULT_VIDEO_MODEL)).strip()
    if resolved != FLASH_VIDEO_MODEL:
        raise ValueError(f"Unsupported video model {resolved!r}; only {FLASH_VIDEO_MODEL} is supported.")
    return resolved


def _error(code: str, message: str, *, details: Any | None = None, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        payload["details"] = details
    result: dict[str, Any] = {"ok": False, "error": payload}
    result.update(extra)
    return result


def _ensure_output_dir(kind: str) -> Path:
    root = Path(str(_env("AGNES_OUTPUT_DIR", str(Path.cwd() / "outputs")))).expanduser()
    directory = (root / kind).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _sanitize_filename(filename: str | None) -> str | None:
    if not filename:
        return None
    path = PureWindowsPath(filename)
    if path.drive or filename.startswith(("/", "\\")):
        return None
    name = path.name
    if (not name or name in {".", ".."} or name.endswith((" ", "."))
            or name.split(".", 1)[0].upper() in _WINDOWS_RESERVED_STEMS):
        return None
    return name


def _open(req: request.Request, timeout_seconds: float):
    return request.urlopen(req, timeout=timeout_seconds)


def _response_body(raw: bytes) -> Any:
    if not raw:
        return ""
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _request_json(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    timeout_seconds: float = 120.0,
    base_url: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    api_key = _env("AGNES_API_KEY")
    if not api_key:
        return False, _error("missing_api_key", "Set AGNES_API_KEY in the environment before calling Agnes.")

    data = json.dumps(json_body).encode("utf-8") if json_body is not None else None
    req = request.Request(
        f"{base_url or _base_url()}{path}",
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with _open(req, timeout_seconds) as response:
            raw = response.read()
        if not raw:
            return True, {}
        parsed = json.loads(raw.decode("utf-8"))
        if not isinstance(parsed, dict):
            return False, _error("invalid_response", "Agnes returned a JSON value that was not an object.")
        return True, parsed
    except urlerror.HTTPError as exc:
        return False, _error(
            "http_error",
            "Agnes returned a non-success HTTP response.",
            details={"status_code": exc.code, "body": _response_body(exc.read())},
        )
    except (socket.timeout, TimeoutError) as exc:
        return False, _error("timeout", "The request to Agnes timed out.", details={"exception": str(exc)})
    except urlerror.URLError as exc:
        return False, _error("request_error", "The request to Agnes could not be completed.", details={"exception": str(exc.reason)})
    except (OSError, UnicodeDecodeError) as exc:
        return False, _error("request_error", "The request to Agnes could not be completed.", details={"exception": str(exc)})
    except json.JSONDecodeError as exc:
        return False, _error("invalid_response", "Agnes returned a response that was not valid JSON.", details={"exception": str(exc)})


def _is_remote_or_data_url(value: str) -> bool:
    return parse.urlparse(value).scheme in {"http", "https", "data"}


def _path_to_data_url(path_value: str) -> tuple[bool, str | dict[str, Any]]:
    path = Path(path_value).expanduser()
    if not path.exists():
        return False, _error("file_not_found", "Local image path does not exist.", details={"path": str(path)})
    if not path.is_file():
        return False, _error("invalid_file", "Local image path is not a file.", details={"path": str(path)})
    try:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError as exc:
        return False, _error("file_read_error", "Could not read local image path.", details={"path": str(path), "exception": str(exc)})
    mime_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return True, f"data:{mime_type};base64,{encoded}"


def _normalize_image_inputs(values: list[str] | None) -> tuple[bool, list[str] | dict[str, Any]]:
    normalized: list[str] = []
    for value in values or []:
        if _is_remote_or_data_url(value):
            normalized.append(value)
        else:
            ok, converted = _path_to_data_url(value)
            if not ok:
                return False, converted
            normalized.append(str(converted))
    return True, normalized


def _build_image_payload(
    *, prompt: str, model: str, size: str | None = None, ratio: str | None = None,
    return_base64: bool | None = None, response_format: str | None = None,
    image_urls: list[str] | None = None, extra_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"model": model, "prompt": prompt}
    if size:
        payload["size"] = size
    if ratio:
        payload["ratio"] = ratio
    if return_base64 is not None:
        payload["return_base64"] = return_base64
    merged = dict(extra_body or {})
    if model == IMAGE_25_FLASH_MODEL:
        for name in ("model", "prompt", "size", "ratio", "return_base64"):
            merged.pop(name, None)
        if response_format:
            merged.pop("response_format", None)
        if image_urls:
            merged.pop("image", None)
    if response_format:
        merged["response_format"] = response_format
    if image_urls:
        merged["image"] = image_urls
    if merged:
        payload["extra_body"] = merged
    return payload


def _resolve_image_size(model: str, size: str | None, *, v2: bool) -> str:
    if size is not None:
        return size
    return "1K" if model == IMAGE_25_FLASH_MODEL or v2 else "1024x1024"


def _validate_image_options(model: str, *, ratio: str | None) -> dict[str, Any] | None:
    if model != IMAGE_25_FLASH_MODEL or ratio is None:
        return None
    if ratio not in IMAGE_25_FLASH_ASPECT_RATIOS:
        allowed = ", ".join(sorted(IMAGE_25_FLASH_ASPECT_RATIOS))
        return _error(
            "invalid_image_options",
            f"Image 2.5 Flash ratio must be one of {allowed}.",
            details={"ratio": ratio, "ratio_values": sorted(IMAGE_25_FLASH_ASPECT_RATIOS)},
        )
    return None


def _image_entries(response: dict[str, Any]) -> list[dict[str, Any]]:
    data = response.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    return [{key: value} for key in ("url", "b64_json", "image_url")
            if isinstance((value := response.get(key)), str)]


def _safe_name(prefix: str, suffix: str, index: int | None = None) -> str:
    stem = f"{prefix}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    if index is not None:
        stem += f"-{index + 1}"
    return f"{stem}{suffix}"


def _suffix_from_url_or_content_type(url: str, content_type: str | None, default_suffix: str) -> str:
    suffix = Path(parse.urlparse(url).path).suffix
    if suffix:
        return suffix
    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";", 1)[0].strip())
        if guessed:
            return guessed
    return default_suffix


def _indexed_filename(filename: str | None, index: int, total: int) -> str | None:
    if not filename or total == 1 or index == 0:
        return filename
    suffix = Path(filename).suffix
    stem = filename[:-len(suffix)] if suffix else filename
    return f"{stem}-{index + 1}{suffix}"


def _save_data_url(data_url: str, directory: Path, filename: str | None, index: int) -> str:
    header, encoded = data_url.split(",", 1)
    content_type = header[5:].split(";", 1)[0] if header.startswith("data:") else None
    suffix = mimetypes.guess_extension(content_type or "") or ".bin"
    path = directory / (filename or _safe_name("agnes-image", suffix, index))
    path.write_bytes(base64.b64decode(encoded, validate=True))
    return str(path.resolve())


def _save_b64_image(encoded: str, directory: Path, filename: str | None, index: int) -> str:
    if encoded.startswith("data:"):
        return _save_data_url(encoded, directory, filename, index)
    path = directory / (filename or _safe_name("agnes-image", ".png", index))
    path.write_bytes(base64.b64decode(encoded, validate=True))
    return str(path.resolve())


def _download_url(
    url: str, directory: Path, *, filename: str | None = None,
    default_suffix: str, timeout_seconds: float = 180.0,
) -> tuple[bool, str | dict[str, Any]]:
    req = request.Request(url, headers={"User-Agent": "agnes-media-generation/1"})
    try:
        with _open(req, timeout_seconds) as response:
            content = response.read()
            content_type = response.headers.get("Content-Type")
        suffix = _suffix_from_url_or_content_type(url, content_type, default_suffix)
        path = directory / (filename or _safe_name("agnes-media", suffix))
        path.write_bytes(content)
        return True, str(path.resolve())
    except urlerror.HTTPError as exc:
        return False, _error("download_http_error", "The generated asset URL returned a non-success HTTP response.", details={"status_code": exc.code, "url": url})
    except (urlerror.URLError, socket.timeout, TimeoutError) as exc:
        return False, _error("download_error", "The generated asset URL could not be downloaded.", details={"url": url, "exception": str(exc)})
    except OSError as exc:
        return False, _error("file_write_error", "The generated asset could not be written to disk.", details={"url": url, "exception": str(exc)})


def _persist_images(response: dict[str, Any], *, output_filename: str | None = None) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    directory = _ensure_output_dir("images")
    urls: list[str] = []
    local_paths: list[str] = []
    save_errors: list[dict[str, Any]] = []
    entries = _image_entries(response)
    for index, entry in enumerate(entries):
        filename = _indexed_filename(output_filename, index, len(entries))
        url = entry.get("url") or entry.get("image_url")
        encoded = entry.get("b64_json")
        try:
            if isinstance(url, str) and url.startswith("data:"):
                local_paths.append(_save_data_url(url, directory, filename, index))
            elif isinstance(url, str):
                urls.append(url)
                ok, saved = _download_url(url, directory, filename=filename, default_suffix=".png")
                if ok:
                    local_paths.append(str(saved))
                else:
                    save_errors.append(saved["error"])
            elif isinstance(encoded, str):
                local_paths.append(_save_b64_image(encoded, directory, filename, index))
        except (ValueError, OSError, binascii.Error) as exc:
            save_errors.append(_error("save_error", "Could not save generated image data.", details={"exception": str(exc)})["error"])
    return urls, local_paths, save_errors


def _agnes_image_generate_impl(
    prompt: str, *, model: str | None = None, size: str | None = None,
    ratio: str | None = None, return_base64: bool | None = None,
    response_format: str | None = None, image_urls: list[str] | None = None,
    output_filename: str | None = None, extra_body: dict[str, Any] | None = None,
    v2: bool = False,
) -> dict[str, Any]:
    if not prompt.strip():
        return _error("invalid_prompt", "Prompt must not be empty.")
    resolved_model = model or _image_model()
    if not resolved_model.strip():
        return _error("invalid_image_options", "Image model must not be blank.", details={"model": resolved_model})
    option_error = _validate_image_options(resolved_model, ratio=ratio)
    if option_error is not None:
        return option_error
    if resolved_model != IMAGE_25_FLASH_MODEL and ratio is None and v2:
        ratio = "1:1"
    size = _resolve_image_size(resolved_model, size, v2=v2)
    output_filename = _sanitize_filename(output_filename)
    ok, normalized = _normalize_image_inputs(image_urls)
    if not ok:
        return normalized
    payload = _build_image_payload(
        prompt=prompt, model=resolved_model, size=size, ratio=ratio,
        return_base64=return_base64, response_format=response_format,
        image_urls=normalized, extra_body=extra_body,
    )
    ok, response = _request_json("POST", "/images/generations", json_body=payload)
    if not ok:
        return response
    urls, paths, errors = _persist_images(response, output_filename=output_filename)
    if resolved_model == IMAGE_25_FLASH_MODEL and not urls and not paths and not errors:
        return _error("invalid_response", "Agnes returned HTTP 200 without any image fields.", details={"response_keys": sorted(response)})
    return {"ok": True, "model": payload["model"], "image_urls": urls, "local_paths": paths, "save_errors": errors}


def _agnes_image_edit_impl(prompt: str, image_paths: list[str], *, mask_path: str | None = None, **options: Any) -> dict[str, Any]:
    if mask_path:
        return _error("unsupported_parameter", "mask_path is not supported by the confirmed Agnes image API.", details={"parameter": "mask_path", "reason": "Use img2img through extra_body.image."})
    if not image_paths:
        return _error("missing_image", "At least one image URL or local image path is required for image edit.")
    return _agnes_image_generate_impl(prompt, image_urls=image_paths, **options)


def _public_https(value: str) -> bool:
    parsed = parse.urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


def _build_video_payload(
    *, prompt: str, model: str | None = None, duration: float = 5.0,
    resolution: str = "720p", aspect_ratio: str = "16:9", mode: str | None = None,
    seed: int | None = None, first_frame: str | None = None,
    last_frame: str | None = None, reference_images: list[str] | None = None,
    reference_audios: list[str] | None = None,
) -> dict[str, Any]:
    resolved_model = _video_model(model)
    if isinstance(duration, bool) or not float(duration).is_integer() or not 4 <= duration <= 12:
        raise ValueError("Flash duration must be a whole number from 4 to 12 seconds.")
    if resolution.strip().lower() != "720p":
        raise ValueError("Flash resolution must be 720P.")
    if aspect_ratio not in FLASH_VIDEO_ASPECT_RATIOS:
        raise ValueError("Flash aspect_ratio must be one of 21:9, 16:9, 4:3, 1:1, 3:4, or 9:16.")
    if seed is not None and (isinstance(seed, bool) or not isinstance(seed, int)):
        raise ValueError("seed must be an integer.")

    images = list(reference_images or [])
    audios = list(reference_audios or [])
    media = [value for value in (first_frame, last_frame, *images, *audios) if value is not None]
    if not all(isinstance(value, str) and _public_https(value) for value in media):
        raise ValueError("Flash media inputs must be public HTTPS URLs.")
    if len(images) > 5:
        raise ValueError("Flash reference images must not exceed 5.")

    has_keyframes = bool(first_frame or last_frame)
    has_references = bool(images or audios)
    if has_keyframes and has_references:
        raise ValueError("Flash keyframe and reference inputs cannot be mixed.")
    resolved_mode = mode or ("keyframe" if has_keyframes else "reference" if has_references else "text")
    if resolved_mode not in {"text", "keyframe", "reference"}:
        raise ValueError("Flash mode must be text, keyframe, or reference.")
    if resolved_mode == "text" and (has_keyframes or has_references):
        raise ValueError("Flash text mode does not accept media inputs.")
    if resolved_mode == "keyframe":
        if not has_keyframes:
            raise ValueError("Flash keyframe mode requires --first-frame or --last-frame.")
        if has_references:
            raise ValueError("Flash keyframe mode does not accept reference inputs.")
    if resolved_mode == "reference":
        if not has_references:
            raise ValueError("Flash reference mode requires --reference-image or --reference-audio.")
        if has_keyframes:
            raise ValueError("Flash reference mode does not accept keyframes.")

    payload: dict[str, Any] = {
        "model": resolved_model,
        "prompt": prompt,
        "seconds": str(int(duration)),
        "mode": resolved_mode,
        "size": "720P",
        "aspect_ratio": aspect_ratio,
        "n": 1,
    }
    if seed is not None:
        payload["seed"] = seed
    if first_frame:
        payload["first_frame"] = first_frame
    if last_frame:
        payload["last_frame"] = last_frame
    if images:
        payload["images"] = images
    if audios:
        payload["audios"] = audios
    return payload


def _extract_nested(response: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = response.get(key)
        if isinstance(value, str) and value:
            return value
    data = response.get("data")
    return _extract_nested(data, keys) if isinstance(data, dict) else None


def _extract_video_url(response: dict[str, Any]) -> str | None:
    metadata = response.get("metadata")
    if isinstance(metadata, dict):
        value = metadata.get("url")
        if isinstance(value, str) and parse.urlparse(value).scheme in {"http", "https"}:
            return value
    for key in VIDEO_URL_FIELDS:
        value = response.get(key)
        if isinstance(value, str) and parse.urlparse(value).scheme in {"http", "https"}:
            return value
    data = response.get("data")
    if isinstance(data, dict):
        return _extract_video_url(data)
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and (value := _extract_video_url(item)):
                return value
    return None


def _video_success_result(
    response: dict[str, Any], *, fallback_video_id: str | None = None,
    fallback_model: str | None = None, include_raw: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": True,
        "task_id": _extract_nested(response, ("task_id", "id")) or fallback_video_id,
        "video_id": _extract_nested(response, ("video_id",)) or fallback_video_id,
        "status": _extract_nested(response, ("status",)),
        "video_url": _extract_video_url(response),
    }
    for field in ("model", "progress", "seconds", "size", "created_at", "completed_at"):
        if response.get(field) is not None:
            result[field] = response[field]
    if fallback_model and "model" not in result:
        result["model"] = fallback_model
    if response.get("error") is not None:
        result["task_error"] = response["error"]
    if include_raw:
        result["raw"] = response
    return result


def _agnes_video_submit_impl(
    prompt: str, *, model: str | None = None, include_raw: bool = False,
    **options: Any,
) -> dict[str, Any]:
    if not prompt.strip():
        return _error("invalid_prompt", "Prompt must not be empty.")
    try:
        payload = _build_video_payload(prompt=prompt, model=model, **options)
    except (TypeError, ValueError) as exc:
        return _error("invalid_video_options", "Video options are invalid.", details=str(exc))
    ok, response = _request_json("POST", "/videos", json_body=payload)
    return _video_success_result(response, fallback_model=payload["model"], include_raw=include_raw) if ok else response


def _agnes_video_status_impl(
    video_id: str, *, model: str | None = None, include_raw: bool = False,
) -> dict[str, Any]:
    if not video_id.strip():
        return _error("invalid_video_id", "video_id must not be empty.")
    try:
        resolved_model = _video_model(model)
    except ValueError as exc:
        return _error("invalid_video_options", "Video options are invalid.", details=str(exc))
    query = {"video_id": video_id, "model_name": resolved_model}
    ok, response = _request_json("GET", f"/agnesapi?{parse.urlencode(query)}", base_url=_domain_root())
    if not ok:
        response["video_id"] = video_id
        return response
    return _video_success_result(
        response, fallback_video_id=video_id, fallback_model=resolved_model, include_raw=include_raw,
    )


def _download_video(video_url: str, *, output_filename: str | None = None) -> tuple[bool, str | dict[str, Any]]:
    return _download_url(video_url, _ensure_output_dir("videos"), filename=output_filename, default_suffix=".mp4", timeout_seconds=600.0)


def _agnes_video_wait_impl(
    video_id: str, *, timeout_seconds: float = 600.0, poll_interval_seconds: float = 5.0,
    download: bool = True, output_filename: str | None = None, model: str | None = None,
) -> dict[str, Any]:
    if timeout_seconds <= 0:
        return _error("invalid_timeout", "timeout_seconds must be greater than zero.")
    if poll_interval_seconds <= 0:
        return _error("invalid_poll_interval", "poll_interval_seconds must be greater than zero.")
    output_filename = _sanitize_filename(output_filename)
    attempts = max(1, math.ceil(timeout_seconds / poll_interval_seconds) + 1)
    last_response: dict[str, Any] | None = None
    for attempt in range(attempts):
        last_response = _agnes_video_status_impl(video_id, model=model, include_raw=True)
        if not last_response.get("ok"):
            error = last_response.get("error")
            details = error.get("details") if isinstance(error, dict) else None
            status_code = details.get("status_code") if isinstance(details, dict) else None
            if status_code in {429, 503} and attempt < attempts - 1:
                time.sleep(poll_interval_seconds)
                continue
            return last_response
        status = str(last_response.get("status") or "").lower()
        if status == "completed":
            video_url = last_response.get("video_url")
            result = dict(last_response)
            result.pop("raw", None)
            result["local_path"] = None
            if download and isinstance(video_url, str):
                ok, saved = _download_video(video_url, output_filename=output_filename)
                if ok:
                    result["local_path"] = saved
                else:
                    result["download_error"] = saved["error"]
            return result
        if status == "failed":
            return _error("task_failed", "Agnes video task failed.", video_id=video_id, last_response=last_response)
        if attempt < attempts - 1:
            time.sleep(poll_interval_seconds)
    return _error("timeout", "Timed out waiting for Agnes video task to complete.", video_id=video_id, last_response=last_response)


def _agnes_video_generate_impl(
    prompt: str, *, timeout_seconds: float = 600.0,
    poll_interval_seconds: float = 5.0, download: bool = True,
    output_filename: str | None = None, model: str | None = None, **options: Any,
) -> dict[str, Any]:
    try:
        resolved_model = _video_model(model)
    except ValueError as exc:
        return _error("invalid_video_options", "Video options are invalid.", details=str(exc))
    submitted = _agnes_video_submit_impl(prompt, model=resolved_model, include_raw=True, **options)
    if not submitted.get("ok"):
        return submitted
    video_id = submitted.get("video_id")
    if not isinstance(video_id, str) or not video_id:
        return _error("missing_video_id", "Agnes video submission succeeded but no video id was found.", details={"submit_result": submitted})
    result = _agnes_video_wait_impl(
        video_id, timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds, download=download,
        output_filename=output_filename, model=resolved_model,
    )
    if not result.get("ok"):
        result["submit_result"] = submitted
    return result


class CliArgumentError(Exception):
    pass


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliArgumentError(message)


def _json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("value must be a JSON object")
    return parsed


def _add_image_options(parser: argparse.ArgumentParser, *, edit: bool = False) -> None:
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--model")
    parser.add_argument("--size")
    parser.add_argument("--ratio")
    parser.add_argument("--return-base64", action="store_const", const=True, default=None)
    parser.add_argument("--response-format", choices=("url", "b64_json"))
    if edit:
        parser.add_argument("--image", dest="image_paths", action="append", required=True)
        parser.add_argument("--mask-path")
    else:
        parser.add_argument("--image-url", dest="image_urls", action="append")
    parser.add_argument("--output-filename")
    parser.add_argument("--extra-body", type=_json_object)


def _add_video_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--model")
    parser.add_argument("--duration", type=float, default=5.0)
    parser.add_argument("--resolution", default="720p")
    parser.add_argument("--aspect-ratio", default="16:9")
    parser.add_argument("--mode")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--first-frame")
    parser.add_argument("--last-frame")
    parser.add_argument("--reference-image", dest="reference_images", action="append")
    parser.add_argument("--reference-audio", dest="reference_audios", action="append")


def _add_wait_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=5.0)
    parser.add_argument("--no-download", dest="download", action="store_false", default=True)
    parser.add_argument("--output-filename")


def _parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(description="Generate and edit media with Agnes AI.")
    commands = parser.add_subparsers(dest="command", required=True)
    _add_image_options(commands.add_parser("image-generate"))
    _add_image_options(commands.add_parser("image-generate-v2"))
    _add_image_options(commands.add_parser("image-edit"), edit=True)
    _add_video_options(commands.add_parser("video-submit"))
    status = commands.add_parser("video-status")
    status.add_argument("video_id")
    status.add_argument("--model")
    wait = commands.add_parser("video-wait")
    wait.add_argument("video_id")
    wait.add_argument("--model")
    _add_wait_options(wait)
    generate = commands.add_parser("video-generate")
    _add_video_options(generate)
    _add_wait_options(generate)
    return parser


def _video_options(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "model": args.model, "duration": args.duration,
        "resolution": args.resolution, "aspect_ratio": args.aspect_ratio,
        "mode": args.mode, "seed": args.seed,
        "first_frame": args.first_frame, "last_frame": args.last_frame,
        "reference_images": args.reference_images,
        "reference_audios": args.reference_audios,
    }


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "image-generate":
        return _agnes_image_generate_impl(args.prompt, model=args.model, size=args.size, ratio=args.ratio, return_base64=args.return_base64, response_format=args.response_format, image_urls=args.image_urls, output_filename=args.output_filename, extra_body=args.extra_body)
    if args.command == "image-generate-v2":
        return _agnes_image_generate_impl(args.prompt, model=args.model or _image_model_v2(), size=args.size, ratio=args.ratio, return_base64=args.return_base64, response_format=args.response_format, image_urls=args.image_urls, output_filename=args.output_filename, extra_body=args.extra_body, v2=True)
    if args.command == "image-edit":
        return _agnes_image_edit_impl(args.prompt, args.image_paths, mask_path=args.mask_path, model=args.model, size=args.size, ratio=args.ratio, return_base64=args.return_base64, response_format=args.response_format, output_filename=args.output_filename, extra_body=args.extra_body)
    if args.command == "video-submit":
        return _agnes_video_submit_impl(args.prompt, **_video_options(args))
    if args.command == "video-status":
        return _agnes_video_status_impl(args.video_id, model=args.model)
    if args.command == "video-wait":
        return _agnes_video_wait_impl(args.video_id, timeout_seconds=args.timeout_seconds, poll_interval_seconds=args.poll_interval_seconds, download=args.download, output_filename=args.output_filename, model=args.model)
    return _agnes_video_generate_impl(args.prompt, timeout_seconds=args.timeout_seconds, poll_interval_seconds=args.poll_interval_seconds, download=args.download, output_filename=args.output_filename, **_video_options(args))


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
        result = _run(args)
        error = result.get("error")
        error_code = error.get("code") if isinstance(error, dict) else None
        code = 0 if result.get("ok") else 2 if error_code in ARGUMENT_ERROR_CODES else 1
    except CliArgumentError as exc:
        result = _error("invalid_arguments", "Command arguments are invalid.", details=str(exc))
        code = 2
    except Exception as exc:
        result = _error("runtime_error", "The Agnes command could not be completed.", details={"type": type(exc).__name__, "message": str(exc)})
        code = 1
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
