"""Field-level projection and write validation shared by the document tools."""

from typing import Any

from mcp_connector.domain.errors import InvalidArguments, PolicyDenied
from mcp_connector.domain.filters import STANDARD_FIELDS, check_identifier
from mcp_connector.domain.policy import FieldRule

from .dto import SECRET_TYPES, SETTABLE_READ_ONLY, DoctypeInfo

_TEXT_SEARCH_TYPES = frozenset(
	{"Data", "Link", "Dynamic Link", "Select", "Small Text", "Text", "Read Only", "Phone", "Autocomplete"}
)
_IGNORED_ROW_KEYS = frozenset({"doctype", "idx", "parent", "parentfield", "parenttype"})
MAX_CHILD_ROWS = 500


def readable_fields(info: DoctypeInfo, rule: FieldRule) -> set:
	names = {
		f.fieldname
		for f in info.fields
		if f.readable and f.fieldtype not in SECRET_TYPES and rule.allows(f.fieldname)
	}
	names.update(s for s in STANDARD_FIELDS if rule.allows(s))
	return names


def listable_fields(info: DoctypeInfo, rule: FieldRule) -> set:
	tables = {f.fieldname for f in info.fields if f.is_table}
	return readable_fields(info, rule) - tables


def default_list_fields(info: DoctypeInfo, listable: set) -> list:
	fields = ["name"]
	candidates = [info.title_field] + [f.fieldname for f in info.fields if f.in_list_view][:6]
	if info.is_submittable:
		candidates.append("docstatus")
	candidates.append("modified")
	for fieldname in candidates:
		if fieldname and fieldname in listable and fieldname not in fields:
			fields.append(fieldname)
	return fields


def search_conditions(info: DoctypeInfo, listable: set, text: Any) -> list:
	if text in (None, ""):
		return []
	if not isinstance(text, str):
		raise InvalidArguments("search must be a string")
	text = text.strip()
	if not text:
		return []
	candidates = ["name", info.title_field, *info.search_fields]
	fields = []
	for fieldname in candidates:
		f = info.field(fieldname) if fieldname != "name" else None
		if fieldname and fieldname in listable and fieldname not in fields and (fieldname == "name" or (f and f.fieldtype in _TEXT_SEARCH_TYPES)):
			fields.append(fieldname)
	return [(fieldname, "like", f"%{text}%") for fieldname in fields]


def _clean(value: Any) -> bool:
	return value is not None and value != ""


def project_document(doc: dict, info: DoctypeInfo, rule: FieldRule) -> dict:
	"""Keep only what the caller may see; child rows keep their readable fields."""
	visible = readable_fields(info, rule)
	result = {"doctype": info.name}
	for key, value in doc.items():
		if key not in visible:
			continue
		f = info.field(key)
		if f is not None and f.is_table:
			child = {
				c.fieldname
				for c in info.child_fields.get(f.child_doctype, ())
				if c.readable and c.fieldtype not in SECRET_TYPES
			} | {"name", "idx"}
			result[key] = [{k: v for k, v in (row or {}).items() if k in child and _clean(v)} for row in value or []]
		elif _clean(value):
			result[key] = value
	return result


def validate_write_data(data: Any, info: DoctypeInfo, rule: FieldRule, creating: bool) -> dict:
	if not isinstance(data, dict) or not data:
		raise InvalidArguments("data must be a non-empty object of field values")

	cleaned = {}
	for key, value in data.items():
		check_identifier(key)
		if key == "name":
			if creating and info.autoname.lower() == "prompt":
				if not isinstance(value, str) or not value.strip():
					raise InvalidArguments("name must be a non-empty string")
				cleaned["name"] = value.strip()
				continue
			raise InvalidArguments(f"'name' cannot be set on {info.name}; it is assigned automatically")
		if key in STANDARD_FIELDS or key == "doctype":
			raise InvalidArguments(f"'{key}' is managed by the system and cannot be set")
		f = info.field(key)
		if f is None:
			raise InvalidArguments(f"{info.name} has no field '{key}'. Use describe_doctype to list its fields")
		if not rule.allows(key) or not f.writable:
			raise PolicyDenied(f"You may not set field '{key}' on {info.name}")
		if f.fieldtype in SECRET_TYPES:
			raise PolicyDenied(f"Password field '{key}' cannot be set through MCP")
		if f.read_only and key not in SETTABLE_READ_ONLY:
			raise InvalidArguments(f"Field '{key}' on {info.name} is read-only (computed or fetched automatically)")
		if f.is_table:
			value = _validate_rows(key, value, info)
		cleaned[key] = value
	return cleaned


def _validate_rows(fieldname: str, rows: Any, info: DoctypeInfo) -> list:
	if not isinstance(rows, list):
		raise InvalidArguments(f"'{fieldname}' is a child table and must be a list of row objects")
	if len(rows) > MAX_CHILD_ROWS:
		raise InvalidArguments(f"'{fieldname}' accepts at most {MAX_CHILD_ROWS} rows")
	child_doctype = info.field(fieldname).child_doctype
	fields = {c.fieldname: c for c in info.child_fields.get(child_doctype, ())}
	cleaned_rows = []
	for index, row in enumerate(rows):
		if not isinstance(row, dict):
			raise InvalidArguments(f"Row {index + 1} of '{fieldname}' must be an object")
		cleaned = {}
		for key, value in row.items():
			if key in _IGNORED_ROW_KEYS:
				continue
			check_identifier(key)
			if key == "name":
				cleaned["name"] = value
				continue
			f = fields.get(key)
			if f is None:
				raise InvalidArguments(f"Row {index + 1} of '{fieldname}': {child_doctype} has no field '{key}'")
			if f.fieldtype in SECRET_TYPES or not f.writable:
				raise PolicyDenied(f"You may not set '{key}' in '{fieldname}'")
			if f.read_only:
				# Rows copied from get_document carry computed values (amount, rate…); ERPNext
				# recalculates them, so dropping them is what the caller meant.
				continue
			cleaned[key] = value
		cleaned_rows.append(cleaned)
	return cleaned_rows
