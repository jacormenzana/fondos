# scripts/ops/open_pr_schema.py
# -*- coding: utf-8 -*-
"""Strict JSON and a small, auditable validator for the two locked schemas of the pull-request pipeline (FND-0353, spec sections 5 and 6).
Standard library only: the runner's virtual environment carries no jsonschema package. The validator implements exactly the keywords the locked schemas use and RAISES on any other keyword,
so a schema edited to rely on something this module does not check cannot pass silently (the schema files are also pinned by hash, spec acceptance 13).

    load_strict_json(raw: bytes) -> object
    validate(instance, schema, path="$") -> list[str]
    sha256_file(path) -> str
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

SUPPORTED = frozenset({"$schema", "$id", "title", "type", "additionalProperties", "required", "properties", "const", "enum", "pattern", "minLength", "maxLength", "minimum", "oneOf"})


class StrictJsonError(ValueError):
    pass


def _no_duplicates(pairs):
    out = {}
    for k, v in pairs:
        if k in out:
            raise StrictJsonError(f"duplicate key {k!r}")
        out[k] = v
    return out


def _no_constant(name):
    raise StrictJsonError(f"{name} is not allowed")


def load_strict_json(raw: bytes):
    """UTF-8 only (no BOM), no duplicate keys, no NaN/Infinity."""
    if raw.startswith(b"\xef\xbb\xbf"):
        raise StrictJsonError("byte order mark")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise StrictJsonError("not valid UTF-8") from None
    try:
        return json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_no_constant)
    except json.JSONDecodeError as exc:
        raise StrictJsonError(f"invalid JSON ({exc.msg})") from None


def _is(value, kind: str) -> bool:
    if kind == "object":
        return isinstance(value, dict)
    if kind == "string":
        return isinstance(value, str)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)       # strict: 1.0 is a float, not an integer
    raise NotImplementedError(f"type {kind!r}")


def validate(instance, schema: dict, path: str = "$") -> list:
    unknown = set(schema) - SUPPORTED
    if unknown:
        raise NotImplementedError(f"schema keyword(s) not supported by open_pr_schema: {sorted(unknown)}")
    errors: list = []
    if "oneOf" in schema:
        passing = [i for i, sub in enumerate(schema["oneOf"]) if not validate(instance, sub, path)]
        if len(passing) != 1:
            errors.append(f"{path}: matches {len(passing)} of {len(schema['oneOf'])} alternatives, exactly one is required")
    if "type" in schema and not _is(instance, schema["type"]):
        return errors + [f"{path}: expected {schema['type']}"]
    if "const" in schema and (instance != schema["const"] or type(instance) is not type(schema["const"])):
        errors.append(f"{path}: must be {schema['const']!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: not one of {schema['enum']}")
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']}")
        if "pattern" in schema and not re.fullmatch(schema["pattern"], instance):
            errors.append(f"{path}: does not match the required pattern")
    if isinstance(instance, int) and not isinstance(instance, bool) and "minimum" in schema and instance < schema["minimum"]:
        errors.append(f"{path}: below {schema['minimum']}")
    if isinstance(instance, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing {key!r}")
        if schema.get("additionalProperties") is False:
            errors += [f"{path}: unexpected key {k!r}" for k in instance if k not in props]
        for key, sub in props.items():
            if key in instance:
                errors += validate(instance[key], sub, f"{path}.{key}")
    return errors


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
