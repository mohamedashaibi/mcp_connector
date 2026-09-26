import json

import frappe
from frappe import _
from frappe.model.document import Document

from mcp_connector.domain.action_types import DOC_OPERATIONS, OPERATION_FIELD, READ
from mcp_connector.domain.errors import InvalidArguments
from mcp_connector.domain.filters import STANDARD_FIELDS, check_identifier
from mcp_connector.domain.policy import ALL_FIELDS, ONLY_LISTED, parse_field_list
from mcp_connector.domain.protected import is_exposable, is_writable
from mcp_connector.infrastructure.gateway import LAYOUT_TYPES
from mcp_connector.infrastructure.policy_source import clear_cache, parse_fixed_filters, parse_scope


class MCPProfile(Document):
	def validate(self):
		self._dedupe("roles", "role")
		self._dedupe("users", "user")
		self._dedupe("actions", "action")
		self._validate_doctypes()
		self._validate_reports()

	def on_update(self):
		clear_cache()

	def on_trash(self):
		clear_cache()

	def after_rename(self, *args, **kwargs):
		clear_cache()

	def _dedupe(self, table: str, fieldname: str):
		seen, keep = set(), []
		for row in self.get(table):
			value = row.get(fieldname)
			if value and value not in seen:
				seen.add(value)
				keep.append(row)
		self.set(table, keep)

	def _validate_doctypes(self):
		seen = set()
		for row in self.doctypes:
			doctype = row.document_type
			label = frappe.bold(doctype)
			if doctype in seen:
				frappe.throw(_("DocType {0} is listed twice").format(label))
			seen.add(doctype)
			if not is_exposable(doctype):
				frappe.throw(_("{0} can never be exposed over MCP").format(label))
			meta = frappe.get_meta(doctype)
			if meta.istable:
				frappe.throw(_("{0} is a child table; grant its parent DocType instead").format(label))

			operations = [op for op in DOC_OPERATIONS if row.get(OPERATION_FIELD[op])]
			if not operations:
				frappe.throw(_("Tick at least one operation for {0}").format(label))
			if not is_writable(doctype) and operations != [READ]:
				frappe.throw(_("{0} is read-only over MCP: only Read can be granted").format(label))
			if (row.can_submit or row.can_cancel) and not meta.is_submittable:
				frappe.throw(_("{0} is not submittable, so Submit and Cancel cannot be granted").format(label))

			valid_fields = {df.fieldname for df in meta.fields if df.fieldtype not in LAYOUT_TYPES} | STANDARD_FIELDS
			mode = row.field_mode or ALL_FIELDS
			if mode == ALL_FIELDS:
				row.field_list = ""
			else:
				names = parse_field_list(row.field_list)
				unknown = sorted(names - valid_fields)
				if unknown:
					frappe.throw(_("{0} has no field(s): {1}").format(label, ", ".join(unknown)))
				if mode == ONLY_LISTED and not names:
					frappe.throw(_("List at least one field for {0}, or choose All Fields").format(label))
				row.field_list = "\n".join(sorted(names))

			if (row.scope_filters or "").strip():
				if meta.issingle:
					frappe.throw(_("Scope filters are not supported on single DocType {0}").format(label))
				try:
					conditions = parse_scope(row.scope_filters)
				except (ValueError, InvalidArguments) as e:
					frappe.throw(_("Scope filters for {0} are invalid: {1}").format(label, e))
				tables = {df.fieldname for df in meta.fields if df.fieldtype in ("Table", "Table MultiSelect")}
				for fieldname, _op, _value in conditions:
					if fieldname not in valid_fields or fieldname in tables:
						frappe.throw(_("Scope filter field {0} does not exist on {1}").format(frappe.bold(fieldname), label))
				row.scope_filters = json.dumps(json.loads(row.scope_filters), indent=1) if conditions else ""
			else:
				row.scope_filters = ""

	def _validate_reports(self):
		seen = set()
		for row in self.reports:
			label = frappe.bold(row.report)
			if row.report in seen:
				frappe.throw(_("Report {0} is listed twice").format(label))
			seen.add(row.report)
			if (row.fixed_filters or "").strip():
				try:
					fixed = parse_fixed_filters(row.fixed_filters)
					for key in fixed:
						check_identifier(key, "filter")
				except (ValueError, InvalidArguments) as e:
					frappe.throw(_("Fixed filters for {0} must be a JSON object: {1}").format(label, e))
				row.fixed_filters = json.dumps(fixed, indent=1) if fixed else ""
			else:
				row.fixed_filters = ""
