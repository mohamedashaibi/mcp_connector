"""A small, strict JSON Schema subset for tool arguments.

Supported keywords: type, properties, required, enum, minimum, maximum,
minLength, maxLength, items (single schema), additionalProperties (bool),
description, default, title. Unknown top-level argument names are rejected.
"""

import re
from typing import Any

from .errors import InvalidArguments

_TYPES = {
	"string": (str,),
	"integer": (int,),
	"number": (int, float),
	"boolean": (bool,),
	"object": (dict,),
	"array": (list,),
	"null": (type(None),),
}
_ALLOWED_KEYWORDS = frozenset(
	{
		"type",
		"properties",
		"required",
		"enum",
		"minimum",
		"maximum",
		"minLength",
		"maxLength",
		"items",
		"additionalProperties",
		"description",
		"default",
		"title",
		"format",
		"examples",
	}
)
_PROPERTY_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


def _type_matches(value: Any, type_name: str) -> bool:
	if type_name in ("integer", "number") and isinstance(value, bool):
		return False
	if type_name == "integer" and isinstance(value, float):
		return value.is_integer()
	return isinstance(value, _TYPES[type_name])


def _check(value: Any, schema: dict, path: str) -> Any:
	types = schema.get("type")
	if types is not None:
		types = [types] if isinstance(types, str) else list(types)
		if not any(_type_matches(value, t) for t in types):
			raise InvalidArguments(f"'{path}' must be of type {' or '.join(types)}")
		if "integer" in types and isinstance(value, float):
			value = int(value)

	if "enum" in schema and value not in schema["enum"]:
		options = ", ".join(repr(o) for o in schema["enum"][:25])
		more = " …" if len(schema["enum"]) > 25 else ""
		raise InvalidArguments(f"'{path}' must be one of: {options}{more}")

	if isinstance(value, (int, float)) and not isinstance(value, bool):
		if "minimum" in schema and value < schema["minimum"]:
			raise InvalidArguments(f"'{path}' must be >= {schema['minimum']}")
		if "maximum" in schema and value > schema["maximum"]:
			raise InvalidArguments(f"'{path}' must be <= {schema['maximum']}")

	if isinstance(value, str):
		if "minLength" in schema and len(value) < schema["minLength"]:
			raise InvalidArguments(f"'{path}' must be at least {schema['minLength']} characters")
		if "maxLength" in schema and len(value) > schema["maxLength"]:
			raise InvalidArguments(f"'{path}' must be at most {schema['maxLength']} characters")

	if isinstance(value, list) and isinstance(schema.get("items"), dict):
		value = [_check(v, schema["items"], f"{path}[{i}]") for i, v in enumerate(value)]

	if isinstance(value, dict) and isinstance(schema.get("properties"), dict):
		value = _check_object(value, schema, path)
	return value


def _check_object(value: dict, schema: dict, path: str) -> dict:
	properties = schema.get("properties") or {}
	for name in schema.get("required") or []:
		if value.get(name) is None:
			raise InvalidArguments(f"Missing required argument '{_join(path, name)}'")
	if schema.get("additionalProperties") is False:
		unknown = sorted(set(value) - set(properties))
		if unknown:
			raise InvalidArguments(
				f"Unknown argument(s) {', '.join(_join(path, u) for u in unknown)}. "
				f"Allowed: {', '.join(sorted(properties)) or 'none'}"
			)
	cleaned = {}
	for name, item in value.items():
		if name in properties and item is not None:
			cleaned[name] = _check(item, properties[name], _join(path, name))
		elif name not in properties:
			cleaned[name] = item
	return cleaned


def _join(path: str, name: str) -> str:
	return f"{path}.{name}" if path else name


def validate_arguments(schema: dict, arguments: Any) -> dict:
	"""Validate tool-call arguments. Top-level unknown keys are always rejected."""
	if arguments is None:
		arguments = {}
	if not isinstance(arguments, dict):
		raise InvalidArguments("Tool arguments must be a JSON object")
	strict = dict(schema)
	strict["additionalProperties"] = False
	return _check_object(arguments, strict, "")


def validate_input_schema(schema: Any) -> dict:
	"""Check an admin-authored input schema for a custom action."""
	if not isinstance(schema, dict):
		raise InvalidArguments("Input schema must be a JSON object")
	if schema.get("type", "object") != "object":
		raise InvalidArguments("Input schema must have \"type\": \"object\"")
	properties = schema.get("properties", {})
	if not isinstance(properties, dict):
		raise InvalidArguments("\"properties\" must be an object")
	for name, prop in properties.items():
		if not _PROPERTY_NAME.match(name):
			raise InvalidArguments(f"Invalid argument name {name!r}: use letters, digits and underscores")
		_validate_property(prop, name)
	required = schema.get("required", [])
	if not isinstance(required, list) or any(r not in properties for r in required):
		raise InvalidArguments("\"required\" must list names defined in \"properties\"")
	unknown = set(schema) - _ALLOWED_KEYWORDS
	if unknown:
		raise InvalidArguments(f"Unsupported schema keyword(s): {', '.join(sorted(unknown))}")
	return {"type": "object", **{k: v for k, v in schema.items() if k != "type"}, "properties": properties}


def _validate_property(prop: Any, name: str) -> None:
	if not isinstance(prop, dict):
		raise InvalidArguments(f"Schema for {name!r} must be an object")
	unknown = set(prop) - _ALLOWED_KEYWORDS
	if unknown:
		raise InvalidArguments(f"Unsupported keyword(s) in {name!r}: {', '.join(sorted(unknown))}")
	types = prop.get("type")
	if types is None:
		raise InvalidArguments(f"{name!r} needs a \"type\"")
	types = [types] if isinstance(types, str) else types
	for t in types:
		if t not in _TYPES:
			raise InvalidArguments(f"{name!r} has unknown type {t!r}")
	if "array" in types and "items" not in prop:
		raise InvalidArguments(f"{name!r} is an array: describe its elements with \"items\" (ChatGPT refuses arrays without it)")
	if "enum" in prop and not isinstance(prop["enum"], list):
		raise InvalidArguments(f"\"enum\" of {name!r} must be a list")
	if "items" in prop:
		_validate_property(prop["items"], f"{name}[]")
	if "properties" in prop:
		for sub, sub_prop in (prop["properties"] or {}).items():
			_validate_property(sub_prop, f"{name}.{sub}")
