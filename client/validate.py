"""Lenient schema validation for extraction results (CL-2).

Reads a field-contract YAML from schemas/*.yaml and checks that an
extraction JSON dict has the required keys present with the right basic
type (str/float/int/list). Deliberately does NOT inspect string *values* —
OCR noise (garbled characters, odd whitespace, misread digits inside a
string) must never fail validation. Only missing keys or wrong Python types
are reported as errors.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

# Python types accepted for each schema type name.
_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "str": (str,),
    "float": (float, int),  # ints are acceptable where a float is expected
    "int": (int,),
    "list": (list,),
}


def load_schema(schema_path: str) -> dict[str, Any]:
    with open(schema_path, encoding="utf-8") as fh:
        schema = yaml.safe_load(fh)
    return schema


def _validate_fields(
    data: dict[str, Any],
    fields: dict[str, Any],
    path: str,
    errors: list[str],
) -> None:
    if not isinstance(data, dict):
        errors.append(f"{path or '<root>'}: expected an object, got {type(data).__name__}")
        return

    for field_name, field_spec in fields.items():
        field_path = f"{path}.{field_name}" if path else field_name

        if field_name not in data:
            errors.append(f"{field_path}: missing required field")
            continue

        value = data[field_name]

        if isinstance(field_spec, str):
            # Simple scalar type, e.g. "str", "float", "int".
            allowed_types = _TYPE_MAP.get(field_spec)
            if allowed_types is None:
                errors.append(f"{field_path}: unknown schema type '{field_spec}'")
                continue
            if not isinstance(value, allowed_types) or isinstance(value, bool):
                errors.append(
                    f"{field_path}: expected type {field_spec}, "
                    f"got {type(value).__name__}"
                )
            continue

        if isinstance(field_spec, dict):
            if field_spec.get("type") == "list":
                if not isinstance(value, list):
                    errors.append(
                        f"{field_path}: expected type list, got {type(value).__name__}"
                    )
                    continue
                item_schema = field_spec.get("item", {})
                for idx, item in enumerate(value):
                    _validate_fields(item, item_schema, f"{field_path}[{idx}]", errors)
            else:
                # Nested object: field_spec is itself a field:type mapping.
                _validate_fields(value, field_spec, field_path, errors)
            continue

        errors.append(f"{field_path}: unsupported schema spec {field_spec!r}")


def validate_extraction(data: dict[str, Any], schema_path: str) -> list[str]:
    """Validate `data` against the field contract at `schema_path`.

    Returns a list of human-readable error strings; an empty list means the
    extraction is valid (key fields present with correct basic types).
    """
    schema = load_schema(schema_path)
    fields = schema.get("fields", {})

    errors: list[str] = []
    _validate_fields(data, fields, path="", errors=errors)
    return errors


def schema_path_for(doc_type: str, schemas_dir: str | None = None) -> str:
    """Resolve the schemas/*.yaml path for a document type."""
    base = Path(schemas_dir) if schemas_dir else Path(__file__).resolve().parent.parent / "schemas"
    return str(base / f"{doc_type}.yaml")
