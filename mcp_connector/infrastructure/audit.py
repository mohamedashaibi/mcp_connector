import frappe

from mcp_connector.domain.audit import AuditEntry, serialize_arguments

AUDIT_DOCTYPE = "MCP Audit Log"


class FrappeAuditSink:
	def record(self, entry: AuditEntry) -> None:
		try:
			doc = frappe.get_doc(
				{
					"doctype": AUDIT_DOCTYPE,
					"user": entry.user,
					"tool": entry.tool[:140],
					"status": entry.status,
					"action_type": entry.action_type,
					"rpc_method": entry.rpc_method[:140],
					"document_type": entry.document_type or None,
					"document_name": entry.document_name or None,
					"report": entry.report or None,
					"duration_ms": entry.duration_ms,
					"arguments": serialize_arguments(entry.arguments) if entry.arguments is not None else None,
					"result_summary": (entry.result_summary or "")[:1000],
					"error": (entry.error or "")[:8000],
					"profiles": ", ".join(entry.profiles),
					"client_ip": (entry.client_ip or "")[:140],
					"user_agent": (entry.user_agent or "")[:500],
				}
			)
			doc.flags.ignore_links = True
			doc.insert(ignore_permissions=True)
		except Exception:
			# Auditing must never break the response the client is waiting for.
			frappe.log_error(title="MCP Connector: could not write audit log")


def purge_old_logs() -> None:
	from mcp_connector.infrastructure.settings import load_settings
	from mcp_connector.mcp_connector.doctype.mcp_audit_log.mcp_audit_log import MCPAuditLog

	days = load_settings().log_retention_days
	if days > 0:
		MCPAuditLog.clear_old_logs(days)
