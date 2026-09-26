import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from mcp_connector.infrastructure.settings import normalize_origins


class MCPSettings(Document):
	def validate(self):
		for fieldname, low, high in (
			("default_page_size", 1, 500),
			("max_page_size", 1, 500),
			("max_report_rows", 1, 10000),
			("rate_limit_per_minute", 0, 100000),
			("log_retention_days", 0, 3650),
		):
			value = cint(self.get(fieldname))
			if not low <= value <= high:
				frappe.throw(
					_("{0} must be between {1} and {2}").format(_(self.meta.get_label(fieldname)), low, high)
				)
		if cint(self.default_page_size) > cint(self.max_page_size):
			frappe.throw(_("Default Page Size cannot exceed Maximum Page Size"))

		origins = normalize_origins(self.allowed_origins)
		for origin in origins:
			if not origin.startswith(("http://", "https://")) or origin.count("/") > 2:
				frappe.throw(_("Invalid origin {0}: use scheme://host[:port]").format(frappe.bold(origin)))
		self.allowed_origins = "\n".join(origins)
