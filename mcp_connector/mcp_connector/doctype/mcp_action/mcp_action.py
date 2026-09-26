import json

import frappe
from frappe import _
from frappe.model.document import Document

from mcp_connector.domain.catalog import BUILTIN_BY_NAME
from mcp_connector.domain.errors import InvalidArguments
from mcp_connector.domain.protected import is_blocked_method
from mcp_connector.domain.schema import TOOL_NAME, validate_input_schema
from mcp_connector.infrastructure.policy_source import clear_cache


def resolve_method(method: str):
	"""Return 'server_script' or 'python' for a callable method, or throw why it is not."""
	from frappe.core.doctype.server_script.server_script_utils import get_server_script_map

	method = (method or "").strip()
	if not method:
		frappe.throw(_("Method is required for a custom action"))
	if is_blocked_method(method):
		frappe.throw(_("{0} cannot be exposed: it would bypass MCP DocType rules").format(frappe.bold(method)))
	if method in (get_server_script_map().get("_api") or {}):
		return "server_script"
	try:
		fn = frappe.get_attr(method)
	except Exception:
		frappe.throw(
			_("{0} is neither an importable Python method nor the API method of a Server Script").format(
				frappe.bold(method)
			)
		)
	if fn not in frappe.whitelisted:
		frappe.throw(_("{0} is not whitelisted: decorate it with @frappe.whitelist()").format(frappe.bold(method)))
	return "python"


class MCPAction(Document):
	def validate(self):
		self.tool_name = (self.tool_name or "").strip()
		if not TOOL_NAME.match(self.tool_name):
			frappe.throw(
				_("Tool name must be 2–64 characters of lowercase letters, digits and underscores, starting with a letter")
			)
		builtin = BUILTIN_BY_NAME.get(self.tool_name)
		if self.is_standard:
			self._validate_standard(builtin)
		else:
			self._validate_custom(builtin)

	def _validate_standard(self, builtin):
		if builtin is None:
			if not self.flags.from_sync:
				frappe.throw(_("{0} is not a built-in action").format(frappe.bold(self.tool_name)))
			return
		if self.is_new() and not self.flags.from_sync:
			frappe.throw(_("Standard actions are created by the app"))
		# Everything but enabled and the description override is owned by code.
		self.title = builtin.title
		self.action_type = builtin.action_type
		self.method = None
		self.input_schema = None
		self.read_only_hint = int(builtin.read_only)
		self.destructive_hint = int(builtin.destructive)

	def _validate_custom(self, builtin):
		if builtin is not None:
			frappe.throw(_("{0} is reserved for a standard action").format(frappe.bold(self.tool_name)))
		if not (self.description or "").strip():
			frappe.throw(_("Describe what the action does: the AI decides when to call it from this text"))
		self.method = (self.method or "").strip()
		resolve_method(self.method)
		raw = (self.input_schema or "").strip() or '{"type": "object", "properties": {}}'
		try:
			schema = validate_input_schema(json.loads(raw))
		except ValueError as e:
			frappe.throw(_("Input Schema is not valid JSON: {0}").format(e))
		except InvalidArguments as e:
			frappe.throw(_("Input Schema is invalid: {0}").format(e))
		self.input_schema = json.dumps(schema, indent=1)
		if self.read_only_hint and self.destructive_hint:
			frappe.throw(_("An action cannot be both read-only and destructive"))

	def on_update(self):
		clear_cache()

	def on_trash(self):
		if self.is_standard and not self.flags.from_sync:
			frappe.throw(_("Standard actions cannot be deleted; disable them instead"))
		# Drop the action from every profile so the deletion is not blocked by links.
		frappe.db.delete("MCP Profile Action", {"action": self.name})
		clear_cache()
