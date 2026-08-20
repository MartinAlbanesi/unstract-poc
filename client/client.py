#!/usr/bin/env python3
"""Extraction client for the Unstract OSS POC.

Posts a document to one of the 3 deployed Prompt Studio APIs (factura,
remito, carta_de_porte), polls the async execution until it finishes, and
prints the extracted, typed JSON to stdout.

Usage:
    python client.py --type factura --file path/to/doc.pdf [--timeout 300]

Exit codes (CL-3):
    0  success — extracted JSON printed to stdout
    1  invalid or missing input file
    2  connection or non-2xx HTTP error talking to the platform
    3  extraction failed (execution reached ERROR status, or no output)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

# Poll cadence for the async execution status endpoint.
POLL_INTERVAL_SECONDS = 3
DEFAULT_TIMEOUT_SECONDS = 300

# Statuses that mean "still running" — see
# unstract/unstract/core/src/unstract/core/data_models.py ExecutionStatus.
PENDING_STATUSES = {"PENDING", "EXECUTING"}
TERMINAL_ERROR_STATUSES = {"ERROR", "STOPPED"}
COMPLETED_STATUS = "COMPLETED"

DOC_TYPES = ("factura", "remito", "carta_de_porte")

# Maps CLI --type value to (API deployment name, env var prefix).
# API name for carta_de_porte uses a hyphen; env var prefix does not.
_TYPE_CONFIG: dict[str, dict[str, str]] = {
    "factura": {"api_name": "factura", "env_prefix": "FACTURA"},
    "remito": {"api_name": "remito", "env_prefix": "REMITO"},
    "carta_de_porte": {"api_name": "carta-porte", "env_prefix": "CARTA_DE_PORTE"},
}


class ClientError(Exception):
    """Base error carrying the process exit code to use."""

    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class InvalidInputError(ClientError):
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=1)


class ApiConnectionError(ClientError):
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=2)


class ExtractionFailedError(ClientError):
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=3)


def load_config(doc_type: str, env: dict[str, str] | None = None) -> dict[str, str]:
    """Resolve base URL, org, API name, and API key for a document type.

    Reads from `env` if provided (for tests), else from `os.environ` after
    loading `deployments/.env` via python-dotenv.
    """
    if env is None:
        # Load once; safe to call repeatedly, does not override already-set vars.
        load_dotenv(Path(__file__).resolve().parent.parent / "deployments" / ".env")
        env = os.environ  # type: ignore[assignment]

    type_config = _TYPE_CONFIG[doc_type]
    prefix = type_config["env_prefix"]

    base_url = env.get("UNSTRACT_BASE_URL")
    org = env.get("UNSTRACT_ORG")
    api_key = env.get(f"{prefix}_API_KEY")
    # Allow overriding the API name via env, else use the documented default.
    api_name = env.get(f"{prefix}_API_NAME") or type_config["api_name"]

    missing = [
        name
        for name, value in (
            ("UNSTRACT_BASE_URL", base_url),
            ("UNSTRACT_ORG", org),
            (f"{prefix}_API_KEY", api_key),
        )
        if not value
    ]
    if missing:
        raise InvalidInputError(
            "Missing required environment variable(s) in deployments/.env: "
            + ", ".join(missing)
        )

    return {
        "base_url": base_url.rstrip("/"),
        "org": org,
        "api_name": api_name,
        "api_key": api_key,
    }


def validate_input_file(file_path: str) -> Path:
    path = Path(file_path)
    if not path.exists():
        raise InvalidInputError(f"File not found: {file_path}")
    if not path.is_file():
        raise InvalidInputError(f"Not a file: {file_path}")
    if path.stat().st_size == 0:
        raise InvalidInputError(f"File is empty: {file_path}")
    return path


def submit_execution(
    session: requests.Session,
    config: dict[str, str],
    file_path: Path,
    timeout: int,
) -> dict[str, Any]:
    """POST the document, return the parsed 'message' payload (execution_id,
    execution_status, status_api, ...).
    """
    url = f"{config['base_url']}/deployment/api/{config['org']}/{config['api_name']}/"
    headers = {"Authorization": f"Bearer {config['api_key']}"}

    try:
        with file_path.open("rb") as fh:
            files = {"files": (file_path.name, fh, "application/pdf")}
            data = {"timeout": str(timeout)}
            response = session.post(
                url, headers=headers, files=files, data=data, timeout=timeout
            )
    except requests.exceptions.ConnectionError as exc:
        raise ApiConnectionError(f"Could not connect to {url}: {exc}") from exc
    except requests.exceptions.Timeout as exc:
        raise ApiConnectionError(f"Request to {url} timed out: {exc}") from exc
    except requests.exceptions.RequestException as exc:
        raise ApiConnectionError(f"Request to {url} failed: {exc}") from exc

    if response.status_code >= 400:
        raise ApiConnectionError(
            f"POST {url} returned HTTP {response.status_code}: {response.text[:500]}"
        )

    try:
        body = response.json()
    except ValueError as exc:
        raise ApiConnectionError(f"POST {url} returned non-JSON body: {exc}") from exc

    message = body.get("message")
    if not isinstance(message, dict) or not message.get("execution_id"):
        raise ExtractionFailedError(
            f"Unexpected submission response shape (no execution_id): {body}"
        )
    return message


def poll_execution(
    session: requests.Session,
    config: dict[str, str],
    execution_id: str,
    timeout: int,
) -> list[dict[str, Any]]:
    """Poll the status endpoint until COMPLETED/ERROR/timeout. Returns the
    per-file result list on success.
    """
    url = f"{config['base_url']}/deployment/api/{config['org']}/{config['api_name']}/"
    headers = {"Authorization": f"Bearer {config['api_key']}"}
    params = {"execution_id": execution_id}

    deadline = time.monotonic() + timeout
    last_status = "PENDING"

    while time.monotonic() < deadline:
        try:
            response = session.get(url, headers=headers, params=params, timeout=30)
        except requests.exceptions.ConnectionError as exc:
            raise ApiConnectionError(f"Could not connect to {url}: {exc}") from exc
        except requests.exceptions.Timeout as exc:
            raise ApiConnectionError(f"Polling {url} timed out: {exc}") from exc
        except requests.exceptions.RequestException as exc:
            raise ApiConnectionError(f"Polling {url} failed: {exc}") from exc

        if response.status_code >= 400 and response.status_code != 422:
            raise ApiConnectionError(
                f"GET {url} returned HTTP {response.status_code}: {response.text[:500]}"
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise ApiConnectionError(f"GET {url} returned non-JSON body: {exc}") from exc

        last_status = body.get("status", last_status)

        if last_status in TERMINAL_ERROR_STATUSES:
            raise ExtractionFailedError(
                f"Execution {execution_id} ended with status {last_status}: "
                f"{body.get('message')}"
            )

        if last_status == COMPLETED_STATUS:
            result = body.get("message")
            if not isinstance(result, list) or not result:
                raise ExtractionFailedError(
                    f"Execution {execution_id} completed but returned no results: {body}"
                )
            return result

        time.sleep(POLL_INTERVAL_SECONDS)

    raise ExtractionFailedError(
        f"Execution {execution_id} did not complete within {timeout}s "
        f"(last status: {last_status})"
    )


def _strip_markdown_json_fence(text: str) -> str:
    """Strip a leading/trailing ```json ... ``` (or plain ```) fence, if present."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


def _unwrap_prompt_studio_output(output: dict[str, Any]) -> dict[str, Any]:
    """Prompt Studio's combined-extraction prompt names its single output
    field '<type>-json' and returns the extracted document as a JSON string
    (sometimes wrapped in a ```json fenced code block) rather than a nested
    object. Detect and unwrap that shape; pass through unchanged otherwise.
    """
    if len(output) == 1:
        (key, value), = output.items()
        if key.endswith("-json") and isinstance(value, str):
            cleaned = _strip_markdown_json_fence(value)
            try:
                parsed = json.loads(cleaned)
            except json.JSONDecodeError as exc:
                raise ExtractionFailedError(
                    f"Could not parse embedded JSON in '{key}': {exc}"
                ) from exc
            if isinstance(parsed, dict):
                return parsed
    return output


def extract_output(result: list[dict[str, Any]]) -> dict[str, Any]:
    """Pull the typed extraction JSON out of the first file's result."""
    first = result[0]
    if first.get("error"):
        raise ExtractionFailedError(f"Extraction failed for file: {first['error']}")

    file_result = first.get("result")
    if not isinstance(file_result, dict):
        raise ExtractionFailedError(f"No result payload in execution output: {first}")

    output = file_result.get("output")
    if output is None:
        raise ExtractionFailedError(f"No 'output' field in extraction result: {file_result}")
    if not isinstance(output, dict):
        raise ExtractionFailedError(f"Unexpected 'output' shape (not an object): {output!r}")

    return _unwrap_prompt_studio_output(output)


def run_extraction(
    doc_type: str,
    file_path: str,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
    session: requests.Session | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Full flow: validate -> submit -> poll -> extract. Raises ClientError
    subclasses on failure. Returns the extracted JSON dict on success.
    """
    if doc_type not in DOC_TYPES:
        raise InvalidInputError(
            f"Unknown --type '{doc_type}', expected one of {DOC_TYPES}"
        )

    path = validate_input_file(file_path)
    config = load_config(doc_type, env=env)
    sess = session or requests.Session()

    message = submit_execution(sess, config, path, timeout)
    execution_id = message["execution_id"]

    # POST can itself already report COMPLETED for very fast (cached) runs.
    if message.get("execution_status") == COMPLETED_STATUS and message.get("result"):
        result = message["result"]
    elif message.get("execution_status") in TERMINAL_ERROR_STATUSES:
        raise ExtractionFailedError(
            f"Execution {execution_id} failed immediately: {message.get('error')}"
        )
    else:
        result = poll_execution(sess, config, execution_id, timeout)

    return extract_output(result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Unstract OSS extraction client")
    parser.add_argument("--type", required=True, choices=DOC_TYPES, dest="doc_type")
    parser.add_argument("--file", required=True, help="Path to the input PDF")
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Max seconds to wait for extraction (default: {DEFAULT_TIMEOUT_SECONDS})",
    )
    args = parser.parse_args(argv)

    try:
        output = run_extraction(args.doc_type, args.file, timeout=args.timeout)
    except ClientError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code

    # Windows consoles/redirected files often default stdout to a legacy
    # codepage (e.g. cp1252), which corrupts the accented characters (ñ, °,
    # etc.) that are routine in Argentine documents. Force UTF-8 so redirected
    # output is always valid, decodable JSON regardless of host console.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
