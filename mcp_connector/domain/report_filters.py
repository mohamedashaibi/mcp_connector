"""Best-effort extraction of filter definitions from a Frappe query-report .js file.

Standard ERPNext reports declare their filters only in JavaScript:

    frappe.query_reports["General Ledger"] = {
        filters: [{ fieldname: "company", label: __("Company"), fieldtype: "Link", reqd: 1 }, ...]
    }

We read literal values (strings, __("…") labels, numbers, booleans, arrays of
those) and ignore anything computed (functions, defaults from frappe.defaults…).
Reports that build filters dynamically yield None and are described as such.
"""

import re
from typing import Any, Iterator, Optional

_FILTERS_START = re.compile(r"""["']?filters["']?\s*:\s*\[""")
_TRANSLATED = re.compile(r"""^__\(\s*(["'`])(.*?)\1\s*(?:,[\s\S]*)?\)$""", re.DOTALL)
_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")
_KEEP = ("fieldname", "label", "fieldtype", "options", "reqd", "default", "description")
_OPEN = {"[": "]", "{": "}", "(": ")"}
_CLOSE = {"]", "}", ")"}


def _code_chars(text: str, start: int, end: int) -> Iterator[tuple]:
	"""Yield (index, char) for characters outside string literals and comments."""
	i = start
	while i < end:
		ch = text[i]
		nxt = text[i + 1] if i + 1 < end else ""
		if ch == "/" and nxt == "/":
			newline = text.find("\n", i)
			i = end if newline == -1 else newline
			continue
		if ch == "/" and nxt == "*":
			close = text.find("*/", i + 2)
			i = end if close == -1 else close + 2
			continue
		if ch in "\"'`":
			j = i + 1
			while j < end and text[j] != ch:
				j += 2 if text[j] == "\\" else 1
			yield i, ch  # the opening quote marks where a literal starts
			i = j + 1
			continue
		yield i, ch
		i += 1


def _matching(text: str, open_index: int) -> Optional[int]:
	depth = 0
	for i, ch in _code_chars(text, open_index, len(text)):
		if ch in _OPEN:
			depth += 1
		elif ch in _CLOSE:
			depth -= 1
			if depth == 0:
				return i
	return None


def _split_top_level(text: str, separator: str) -> list:
	parts, depth, last = [], 0, 0
	for i, ch in _code_chars(text, 0, len(text)):
		if ch in _OPEN:
			depth += 1
		elif ch in _CLOSE:
			depth -= 1
		elif ch == separator and depth == 0:
			parts.append(text[last:i])
			last = i + 1
	parts.append(text[last:])
	return [p for p in parts if p.strip()]


def _string_literal(raw: str) -> Optional[str]:
	if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'`":
		body = raw[1:-1]
		if raw[0] == "`" and "${" in body:
			return None
		return (
			body.replace("\\n", "\n").replace('\\"', '"').replace("\\'", "'").replace("\\`", "`").replace("\\\\", "\\")
		)
	return None


_UNKNOWN = object()


def _literal(raw: str) -> Any:
	raw = raw.strip()
	text = _string_literal(raw)
	if text is not None:
		return text
	match = _TRANSLATED.match(raw)
	if match:
		return _string_literal(match.group(1) + match.group(2) + match.group(1))
	if _NUMBER.match(raw):
		return float(raw) if "." in raw else int(raw)
	if raw in ("true", "false"):
		return raw == "true"
	if raw == "null":
		return None
	if raw.startswith("[") and _matching(raw, 0) == len(raw) - 1:
		items = [_literal(p) for p in _split_top_level(raw[1:-1], ",")]
		return _UNKNOWN if any(i is _UNKNOWN for i in items) else items
	return _UNKNOWN


def _parse_object(text: str) -> dict:
	entries = {}
	for part in _split_top_level(text[1:-1], ","):
		pieces = _split_top_level(part, ":")
		if len(pieces) < 2:
			continue
		key = pieces[0].strip().strip("\"'")
		value = _literal(":".join(pieces[1:]))
		if value is not _UNKNOWN:
			entries[key] = value
	return entries


def parse_report_filters(js_source: str) -> Optional[list]:
	"""Return [{fieldname, label, fieldtype, options?, reqd?, default?}, ...] or None."""
	if not js_source:
		return None
	match = _FILTERS_START.search(js_source)
	if not match:
		return None
	open_index = match.end() - 1
	close_index = _matching(js_source, open_index)
	if close_index is None:
		return None

	body = js_source[open_index + 1 : close_index]
	filters = []
	depth = 0
	for i, ch in _code_chars(body, 0, len(body)):
		if ch == "{" and depth == 0:
			end = _matching(body, i)
			if end is None:
				return None
			entry = _parse_object(body[i : end + 1])
			if isinstance(entry.get("fieldname"), str):
				filters.append(_clean(entry))
		if ch in _OPEN:
			depth += 1
		elif ch in _CLOSE:
			depth -= 1
	return filters


def _clean(entry: dict) -> dict:
	result = {k: entry[k] for k in _KEEP if k in entry and entry[k] not in (None, "")}
	if isinstance(result.get("options"), list):
		result["options"] = "\n".join(str(o) for o in result["options"])
	if "reqd" in result:
		result["reqd"] = bool(result["reqd"])
		if not result["reqd"]:
			del result["reqd"]
	return result
