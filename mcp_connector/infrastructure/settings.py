import frappe
from frappe.utils import cint

from mcp_connector.application.dto import ServerSettings
from mcp_connector.domain.audit import AUDIT_MODES

SETTINGS_DOCTYPE = "MCP Settings"


def normalize_origins(raw: str) -> tuple:
	origins = []
	for line in (raw or "").replace(",", "\n").splitlines():
		origin = line.strip().rstrip("/").lower()
		if origin and origin not in origins:
			origins.append(origin)
	return tuple(origins)


def load_settings() -> ServerSettings:
	doc = frappe.get_cached_doc(SETTINGS_DOCTYPE)
	max_page = cint(doc.max_page_size) or 100
	return ServerSettings(
		enabled=bool(cint(doc.enabled)),
		server_name=doc.server_name or "ERPNext MCP",
		instructions=doc.instructions or "",
		default_page_size=min(cint(doc.default_page_size) or 20, max_page),
		max_page_size=max_page,
		max_report_rows=cint(doc.max_report_rows) or 200,
		rate_limit_per_minute=max(cint(doc.rate_limit_per_minute), 0),
		audit_mode=doc.audit_mode if doc.audit_mode in AUDIT_MODES else AUDIT_MODES[0],
		log_arguments=bool(cint(doc.log_arguments)),
		log_retention_days=max(cint(doc.log_retention_days), 0),
		allowed_origins=normalize_origins(doc.allowed_origins),
	)
