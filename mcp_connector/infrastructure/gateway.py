"""ErpGateway on top of the Frappe ORM. Every call runs as frappe.session.user
with Frappe's role, user and field-level permissions enforced."""

import html
import json
import os
import re
from contextlib import contextmanager
from typing import Any, Optional

import frappe
from frappe.model.workflow import apply_workflow, get_transitions, get_workflow_name
from frappe.modules import get_module_path, scrub
from frappe.utils import strip_html

from mcp_connector.application.dto import DoctypeInfo, FieldInfo, ReportInfo
from mcp_connector.domain.errors import ErpFailure
from mcp_connector.domain.report_filters import parse_report_filters

LAYOUT_TYPES = frozenset({"Section Break", "Column Break", "Tab Break", "HTML", "Button", "Image", "Fold", "Heading"})
_WHITESPACE = re.compile(r"\s+")


def clean_message(value: Any) -> str:
	text = html.unescape(strip_html(str(value or "")))
	return _WHITESPACE.sub(" ", text).strip()[:1000]


def _exception_message(exc: Exception) -> str:
	message = "" if isinstance(exc, frappe.MandatoryError) else clean_message(exc)
	if not message:
		log = getattr(frappe.local, "message_log", None) or []
		if log:
			message = clean_message(_parse_log_entry(log[-1]))
	return message or exc.__class__.__name__


def _parse_log_entry(entry: Any) -> str:
	if isinstance(entry, str):
		try:
			entry = json.loads(entry)
		except ValueError:
			return entry
	if isinstance(entry, dict):
		return entry.get("message") or ""
	return str(entry)


@contextmanager
def erp_errors():
	"""Translate Frappe's expected exceptions into ErpFailure; let anything else bubble."""
	try:
		yield
	except frappe.PermissionError as e:
		raise ErpFailure("permission", _exception_message(e)) from e
	except frappe.DoesNotExistError as e:
		raise ErpFailure("not_found", _exception_message(e)) from e
	except (frappe.TimestampMismatchError, frappe.DuplicateEntryError, frappe.UniqueValidationError) as e:
		raise ErpFailure("conflict", _exception_message(e)) from e
	except frappe.ValidationError as e:
		raise ErpFailure("validation", _exception_message(e)) from e


def _plain(row) -> dict:
	return dict(row)


def _frappe_filters(doctype: str, conditions) -> list:
	return [[doctype, f, op, list(v) if isinstance(v, tuple) else v] for f, op, v in conditions]


class FrappeErpGateway:
	def __init__(self):
		self._infos: dict = {}

	# -- metadata ---------------------------------------------------------
	def _doctype_exists(self, doctype: str) -> bool:
		return bool(doctype) and bool(frappe.db.exists("DocType", doctype))

	def doctype_info(self, doctype: str) -> Optional[DoctypeInfo]:
		if doctype in self._infos:
			return self._infos[doctype]
		info = None
		if self._doctype_exists(doctype):
			meta = frappe.get_meta(doctype)
			read_levels = set(meta.get_permlevel_access("read")) | {0}
			write_levels = set(meta.get_permlevel_access("write")) | {0}
			fields = self._fields(meta, read_levels, write_levels)
			child_fields = {
				f.options: self._fields(frappe.get_meta(f.options), read_levels, write_levels)
				for f in meta.fields
				if f.fieldtype in ("Table", "Table MultiSelect") and f.options
			}
			info = DoctypeInfo(
				name=doctype,
				module=meta.module or "",
				is_submittable=bool(meta.is_submittable),
				issingle=bool(meta.issingle),
				istable=bool(meta.istable),
				title_field=meta.title_field or "",
				search_fields=tuple(s.strip() for s in (meta.search_fields or "").split(",") if s.strip()),
				autoname=(meta.autoname or "").strip(),
				description=meta.description or "",
				has_workflow=bool(get_workflow_name(doctype)),
				fields=fields,
				child_fields=child_fields,
			)
		self._infos[doctype] = info
		return info

	@staticmethod
	def _fields(meta, read_levels: set, write_levels: set) -> tuple:
		return tuple(
			FieldInfo(
				fieldname=df.fieldname,
				label=df.label or df.fieldname,
				fieldtype=df.fieldtype,
				options=df.options or "",
				reqd=bool(df.reqd),
				read_only=bool(df.read_only),
				readable=(df.permlevel or 0) in read_levels,
				writable=(df.permlevel or 0) in write_levels,
				in_list_view=bool(df.in_list_view),
				allow_on_submit=bool(df.allow_on_submit),
				description=clean_message(df.description) if df.description else "",
			)
			for df in meta.fields
			if df.fieldtype not in LAYOUT_TYPES and df.fieldname
		)

	def has_permission(self, doctype: str, ptype: str, user: Optional[str] = None) -> bool:
		if not self._doctype_exists(doctype):
			return False
		return bool(frappe.has_permission(doctype, ptype, user=user))

	# -- reads ------------------------------------------------------------
	def list_documents(self, doctype, fields, filters, or_filters, order_by, start, limit) -> list:
		with erp_errors():
			rows = frappe.get_list(
				doctype,
				fields=list(fields),
				filters=_frappe_filters(doctype, filters),
				or_filters=_frappe_filters(doctype, or_filters) or None,
				order_by=order_by,
				limit_start=start,
				limit_page_length=limit,
			)
		return [_plain(r) for r in rows]

	def count_documents(self, doctype, filters, or_filters) -> int:
		with erp_errors():
			rows = frappe.get_list(
				doctype,
				fields=["count(name) as total_count"],
				filters=_frappe_filters(doctype, filters),
				or_filters=_frappe_filters(doctype, or_filters) or None,
				order_by=None,
			)
		return int(rows[0].get("total_count") or 0) if rows else 0

	def list_names(self, doctype, filters, limit) -> list:
		with erp_errors():
			return frappe.get_list(
				doctype, filters=_frappe_filters(doctype, filters), pluck="name", limit_page_length=limit, order_by=None
			)

	def document_exists(self, doctype, name) -> bool:
		return bool(name) and bool(frappe.db.exists(doctype, name))

	def document_matches(self, doctype, name, conditions) -> bool:
		filters = [[doctype, "name", "=", name], *_frappe_filters(doctype, conditions)]
		return bool(frappe.get_all(doctype, filters=filters, pluck="name", limit_page_length=1, order_by=None))

	def _load(self, doctype, name, ptype):
		doc = frappe.get_doc(doctype, name)
		if not doc.has_permission(ptype):
			raise frappe.PermissionError(f"You do not have {ptype} permission on {doctype} {name}")
		return doc

	@staticmethod
	def _as_dict(doc) -> dict:
		return doc.as_dict(convert_dates_to_str=True)

	def get_document(self, doctype, name) -> dict:
		with erp_errors():
			doc = self._load(doctype, name, "read")
			doc.apply_fieldlevel_read_permissions()
			return self._as_dict(doc)

	# -- writes -----------------------------------------------------------
	def create_document(self, doctype, data) -> dict:
		with erp_errors():
			doc = frappe.get_doc({**data, "doctype": doctype})
			doc.insert()
			return self._as_dict(doc)

	def update_document(self, doctype, name, data) -> dict:
		with erp_errors():
			doc = self._load(doctype, name, "write")
			for fieldname, value in data.items():
				df = doc.meta.get_field(fieldname)
				if df and df.fieldtype in ("Table", "Table MultiSelect"):
					doc.set(fieldname, self._merge_rows(doc.get(fieldname) or [], value))
				else:
					doc.set(fieldname, value)
			doc.save()
			return self._as_dict(doc)

	@staticmethod
	def _merge_rows(existing, rows) -> list:
		"""Rows naming an existing row update it in place; the rest are appended as new."""
		by_name = {row.name: row for row in existing}
		merged = []
		for row in rows:
			row = dict(row)
			current = by_name.pop(row.pop("name", None), None)
			if current is not None:
				current.update(row)
				merged.append(current)
			else:
				merged.append(row)
		return merged

	def delete_document(self, doctype, name) -> None:
		with erp_errors():
			frappe.delete_doc(doctype, name)

	def submit_document(self, doctype, name) -> dict:
		with erp_errors():
			doc = self._load(doctype, name, "submit")
			doc.submit()
			return self._as_dict(doc)

	def cancel_document(self, doctype, name) -> dict:
		with erp_errors():
			doc = self._load(doctype, name, "cancel")
			doc.cancel()
			return self._as_dict(doc)

	# -- workflow ---------------------------------------------------------
	def workflow_actions(self, doctype, name) -> dict:
		workflow_name = get_workflow_name(doctype)
		with erp_errors():
			doc = frappe.get_doc(doctype, name)
			if not workflow_name:
				return {"state": None, "docstatus": doc.docstatus, "actions": []}
			workflow = frappe.get_cached_doc("Workflow", workflow_name)
			docstatus_of = {s.state: int(s.doc_status or 0) for s in workflow.states}
			actions, seen = [], set()
			for t in get_transitions(doc, workflow):
				if t.action in seen:
					continue
				seen.add(t.action)
				actions.append({"action": t.action, "next_state": t.next_state, "next_docstatus": docstatus_of.get(t.next_state, 0)})
			return {"state": doc.get(workflow.workflow_state_field), "docstatus": doc.docstatus, "actions": actions}

	def apply_workflow_action(self, doctype, name, action) -> dict:
		with erp_errors():
			doc = self._load(doctype, name, "read")
			return self._as_dict(apply_workflow(doc.as_dict(), action))

	# -- reports ----------------------------------------------------------
	def report_info(self, report) -> Optional[ReportInfo]:
		if not report or not frappe.db.exists("Report", report):
			return None
		doc = frappe.get_cached_doc("Report", report)
		permitted = bool(doc.is_permitted()) and not doc.disabled
		if permitted and doc.ref_doctype:
			permitted = bool(frappe.has_permission(doc.ref_doctype, "report"))
		return ReportInfo(
			name=doc.name,
			report_type=doc.report_type,
			ref_doctype=doc.ref_doctype or "",
			module=doc.module or "",
			is_standard=doc.is_standard == "Yes",
			permitted=permitted,
			filters=self._report_filters(doc),
		)

	def _report_filters(self, doc, depth: int = 0) -> Optional[list]:
		if doc.report_type == "Custom Report" and doc.reference_report and depth == 0:
			if frappe.db.exists("Report", doc.reference_report):
				return self._report_filters(frappe.get_cached_doc("Report", doc.reference_report), depth + 1)
			return None
		if doc.get("filters"):
			return [
				{
					k: v
					for k, v in {
						"fieldname": f.fieldname,
						"label": f.label,
						"fieldtype": f.fieldtype,
						"options": f.options,
						"reqd": bool(f.mandatory) or None,
						"default": f.default,
					}.items()
					if v not in (None, "")
				}
				for f in doc.filters
			]
		if doc.is_standard == "Yes" and doc.report_type in ("Script Report", "Query Report") and doc.module:
			folder = scrub(doc.name)
			path = os.path.join(get_module_path(doc.module), "report", folder, f"{folder}.js")
			if os.path.exists(path):
				with open(path, encoding="utf-8") as handle:
					return parse_report_filters(handle.read())
		return None

	def run_report(self, report, filters, limit) -> dict:
		with erp_errors():
			doc = frappe.get_doc("Report", report)
			message = summary = None
			if doc.report_type == "Report Builder":
				columns, rows = doc.get_data(filters=filters, limit=limit + 1, user=frappe.session.user, as_dict=False)
			else:
				from frappe.desk.query_report import run

				data = run(report, filters=filters, ignore_prepared_report=True)
				columns, rows = data.get("columns") or [], data.get("result") or []
				message, summary = data.get("message"), data.get("report_summary")

		columns = [self._column(c) for c in columns]
		names = [c["fieldname"] for c in columns]
		normalized = []
		for row in rows:
			if isinstance(row, dict):
				normalized.append({k: self._cell(v) for k, v in row.items()})
			elif isinstance(row, (list, tuple)):
				normalized.append({name: self._cell(v) for name, v in zip(names, row)})
		result = {"columns": columns, "rows": normalized[:limit], "total_rows": len(normalized)}
		if message:
			result["message"] = clean_message(message)
		if summary:
			result["report_summary"] = [
				{"label": clean_message(s.get("label")), "value": s.get("value")} for s in summary if isinstance(s, dict)
			]
		return result

	@staticmethod
	def _column(column) -> dict:
		if isinstance(column, dict):
			fieldname = column.get("fieldname") or column.get("label") or ""
			return {
				"fieldname": fieldname,
				"label": clean_message(column.get("label") or fieldname),
				"fieldtype": column.get("fieldtype") or "Data",
			}
		parts = str(column).split(":")
		fieldtype = parts[1].split("/")[0] if len(parts) > 1 and parts[1] else "Data"
		return {"fieldname": parts[0], "label": parts[0], "fieldtype": fieldtype}

	@staticmethod
	def _cell(value):
		if isinstance(value, str) and "<" in value:
			return clean_message(value)
		return value

	# -- custom actions ---------------------------------------------------
	def call_method(self, method, arguments) -> Any:
		from frappe.core.doctype.server_script.server_script_utils import get_server_script_map

		with erp_errors():
			script = get_server_script_map().get("_api", {}).get(method)
			if script:
				frappe.local.form_dict = frappe._dict(arguments)
				frappe.response["message"] = None
				flags = frappe.get_doc("Server Script", script).execute_method()
				return flags if flags else frappe.response.get("message")
			fn = frappe.get_attr(method)
			frappe.is_whitelisted(fn)
			return frappe.call(fn, **arguments)

	def drain_messages(self) -> list:
		log = getattr(frappe.local, "message_log", None) or []
		messages = [clean_message(_parse_log_entry(entry)) for entry in log]
		frappe.local.message_log = []
		return [m for m in dict.fromkeys(messages) if m]
