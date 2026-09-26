"""Strict parsing of the query pieces an MCP client may send.

Everything the client controls (field names, operators, ordering) is checked
against an allowlist before it ever reaches the ORM. Filtering or sorting on a
field the caller cannot read is rejected, otherwise hidden values could be
probed one comparison at a time.
"""

import re
from typing import Any, Callable, Iterable

from .errors import InvalidArguments

Condition = tuple  # (fieldname, operator, value)

IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,139}$")

OPERATORS = frozenset(
	{"=", "!=", ">", "<", ">=", "<=", "like", "not like", "in", "not in", "is", "between"}
)
_LIST_OPERATORS = frozenset({"in", "not in"})
_SCALAR_TYPES = (str, int, float, bool, type(None))

# Identity and state columns every caller may always read, filter and sort on.
ALWAYS_VISIBLE = frozenset({"name", "docstatus", "creation", "modified"})
# Standard columns that exist on every table but are governed by field rules.
STANDARD_FIELDS = frozenset({"owner", "modified_by", "idx"}) | ALWAYS_VISIBLE

MAX_CONDITIONS = 50
MAX_IN_VALUES = 500


def check_identifier(fieldname: Any, what: str = "field") -> str:
	if not isinstance(fieldname, str) or not IDENTIFIER.match(fieldname):
		raise InvalidArguments(f"Invalid {what} name: {fieldname!r}")
	return fieldname


def _check_scalar(value: Any, fieldname: str) -> None:
	if not isinstance(value, _SCALAR_TYPES):
		raise InvalidArguments(f"Filter value for '{fieldname}' must be a string, number, boolean or null")


def _normalize_one(fieldname: Any, operator: Any, value: Any) -> Condition:
	fieldname = check_identifier(fieldname)
	if not isinstance(operator, str) or operator.strip().lower() not in OPERATORS:
		raise InvalidArguments(
			f"Unsupported operator {operator!r} for '{fieldname}'. Allowed: {', '.join(sorted(OPERATORS))}"
		)
	operator = operator.strip().lower()

	if operator in _LIST_OPERATORS:
		if isinstance(value, str):
			value = [v.strip() for v in value.split(",") if v.strip()]
		if not isinstance(value, (list, tuple)) or not value:
			raise InvalidArguments(f"'{operator}' on '{fieldname}' needs a non-empty list of values")
		if len(value) > MAX_IN_VALUES:
			raise InvalidArguments(f"'{operator}' on '{fieldname}' accepts at most {MAX_IN_VALUES} values")
		for v in value:
			_check_scalar(v, fieldname)
		value = list(value)
	elif operator == "between":
		if not isinstance(value, (list, tuple)) or len(value) != 2:
			raise InvalidArguments(f"'between' on '{fieldname}' needs exactly two values [from, to]")
		for v in value:
			_check_scalar(v, fieldname)
		value = list(value)
	elif operator == "is":
		if value not in ("set", "not set"):
			raise InvalidArguments(f"'is' on '{fieldname}' accepts only \"set\" or \"not set\"")
	elif operator in ("like", "not like"):
		if not isinstance(value, str):
			raise InvalidArguments(f"'{operator}' on '{fieldname}' needs a string pattern")
	else:
		_check_scalar(value, fieldname)
	return (fieldname, operator, value)


def normalize_filters(raw: Any) -> tuple:
	"""Accept `{field: value}`, `{field: [op, value]}` or `[[field, op, value], ...]`.

	A four-item row (`[doctype, field, op, value]`) is accepted for Frappe
	familiarity; the doctype element is ignored because child-table filters are
	not supported.
	"""
	if raw in (None, "", {}, []):
		return ()

	conditions = []
	if isinstance(raw, dict):
		for fieldname, spec in raw.items():
			if isinstance(spec, (list, tuple)) and len(spec) == 2 and isinstance(spec[0], str) and spec[0].strip().lower() in OPERATORS:
				conditions.append(_normalize_one(fieldname, spec[0], spec[1]))
			elif isinstance(spec, (list, tuple)):
				conditions.append(_normalize_one(fieldname, "in", list(spec)))
			else:
				conditions.append(_normalize_one(fieldname, "=", spec))
	elif isinstance(raw, (list, tuple)):
		for row in raw:
			if not isinstance(row, (list, tuple)) or len(row) not in (3, 4):
				raise InvalidArguments("Each filter must be [field, operator, value]")
			if len(row) == 4:
				row = row[1:]
			conditions.append(_normalize_one(*row))
	else:
		raise InvalidArguments("Filters must be an object or a list of [field, operator, value]")

	if len(conditions) > MAX_CONDITIONS:
		raise InvalidArguments(f"At most {MAX_CONDITIONS} filter conditions are allowed")
	return tuple(conditions)


def require_fields(conditions: Iterable[Condition], is_allowed: Callable[[str], bool], context: str = "filter") -> None:
	for fieldname, _, _ in conditions:
		if not is_allowed(fieldname):
			raise InvalidArguments(f"Cannot {context} on field '{fieldname}': it does not exist or is not visible to you")


def parse_fields(raw: Any, is_allowed: Callable[[str], bool], max_fields: int = 60) -> list:
	"""Validate the requested column list. Returns [] when nothing was requested."""
	if raw in (None, "", []):
		return []
	if isinstance(raw, str):
		raw = [f.strip() for f in raw.split(",") if f.strip()]
	if not isinstance(raw, (list, tuple)):
		raise InvalidArguments("fields must be a list of field names")
	if len(raw) > max_fields:
		raise InvalidArguments(f"At most {max_fields} fields can be requested")
	result = []
	for fieldname in raw:
		check_identifier(fieldname)
		if not is_allowed(fieldname):
			raise InvalidArguments(f"Field '{fieldname}' does not exist or is not visible to you")
		if fieldname not in result:
			result.append(fieldname)
	return result


_ORDER_PART = re.compile(r"^([A-Za-z_][A-Za-z0-9_]{0,139})(?:\s+(asc|desc))?$", re.IGNORECASE)


def parse_order_by(raw: Any, is_allowed: Callable[[str], bool], default: str = "modified desc") -> tuple:
	"""Return ((field, direction), ...) for `"field [asc|desc], ..."` (max three keys)."""
	if raw in (None, ""):
		raw = default
	if not isinstance(raw, str):
		raise InvalidArguments("order_by must be a string like 'modified desc'")
	parts = [p.strip() for p in raw.split(",") if p.strip()]
	if not parts or len(parts) > 3:
		raise InvalidArguments("order_by accepts one to three 'field [asc|desc]' parts")
	keys = []
	for part in parts:
		match = _ORDER_PART.match(part)
		if not match:
			raise InvalidArguments(f"Invalid order_by part: {part!r}")
		fieldname, direction = match.group(1), (match.group(2) or "asc").lower()
		if not is_allowed(fieldname):
			raise InvalidArguments(f"Cannot sort on field '{fieldname}': it does not exist or is not visible to you")
		keys.append((fieldname, direction))
	return tuple(keys)


def order_by_sql(keys: tuple) -> str:
	# Safe: every part was matched against IDENTIFIER and asc/desc above.
	return ", ".join(f"`{fieldname}` {direction}" for fieldname, direction in keys)


def sort_rows(rows: list, keys: tuple) -> list:
	"""Sort merged result rows in Python the way the database would (NULLs first on asc)."""

	def sort_key(fieldname):
		def key(row):
			value = row.get(fieldname)
			if value is None:
				return (0, "")
			if isinstance(value, (int, float)) and not isinstance(value, bool):
				return (1, value)
			return (2, str(value))

		return key

	result = list(rows)
	for fieldname, direction in reversed(keys):
		result.sort(key=sort_key(fieldname), reverse=direction == "desc")
	return result


def clamp_int(raw: Any, name: str, default: int, minimum: int, maximum: int) -> int:
	if raw in (None, ""):
		return default
	if isinstance(raw, bool):
		raise InvalidArguments(f"{name} must be an integer")
	try:
		value = int(raw)
	except (TypeError, ValueError):
		raise InvalidArguments(f"{name} must be an integer") from None
	if value != raw and not (isinstance(raw, str) and raw.strip().lstrip("-").isdigit()):
		raise InvalidArguments(f"{name} must be an integer")
	return max(minimum, min(maximum, value))
