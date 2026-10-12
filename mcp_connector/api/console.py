"""Whitelisted endpoints for the MCP Console page.

Only the Administrator may use them. Each endpoint checks that itself (never
relying on the page's role list), parses its payload through a strict DTO, and
delegates to one use case.
"""

import frappe
from frappe import _

from mcp_connector.api.mcp import ENDPOINT_PATH
from mcp_connector.application import console as uc
from mcp_connector.application.console_dto import parse_action, parse_log_query, parse_profile, parse_settings
from mcp_connector.application.mcp_server import ToolCatalog
from mcp_connector.domain.errors import InvalidArguments, NotFound
from mcp_connector.infrastructure.console_repos import (
	FrappeActionRepository,
	FrappeAuditQuery,
	FrappeCatalogRepository,
	FrappeDirectory,
	FrappeOAuthClientRepository,
	FrappeProfileRepository,
	FrappeSettingsRepository,
	doctype_flags,
)
from mcp_connector.infrastructure.gateway import LAYOUT_TYPES, FrappeErpGateway
from mcp_connector.infrastructure.permissions import is_administrator

def _require_manager():
	# Explicit on purpose: frappe.only_for() is skipped under tests.
	if not is_administrator():
		raise frappe.PermissionError(_("Only the Administrator can manage the MCP Connector"))


def _run(fn, *args):
	try:
		return fn(*args)
	except NotFound as e:
		frappe.throw(str(e), frappe.DoesNotExistError)
	except InvalidArguments as e:
		frappe.throw(str(e), frappe.ValidationError)


def _payload(raw):
	try:
		return frappe.parse_json(raw) if isinstance(raw, str) else raw
	except ValueError:
		frappe.throw(_("Malformed request payload"))


def _catalog() -> ToolCatalog:
	return ToolCatalog(FrappeErpGateway())


@frappe.whitelist()
def bootstrap():
	_require_manager()
	data = uc.GetBootstrap(FrappeSettingsRepository(), FrappeProfileRepository(), FrappeActionRepository()).execute()
	data["endpoint"] = frappe.utils.get_url(ENDPOINT_PATH)
	data["dashboard"] = uc.GetDashboard(FrappeAuditQuery()).execute(24)
	return data


@frappe.whitelist()
def dashboard(hours=24):
	_require_manager()
	return _run(uc.GetDashboard(FrappeAuditQuery()).execute, frappe.utils.cint(hours))


@frappe.whitelist()
def profiles():
	_require_manager()
	return FrappeProfileRepository().summaries()


@frappe.whitelist()
def get_profile(name):
	_require_manager()
	return _run(uc.GetProfile(FrappeProfileRepository(), FrappeDirectory()).execute, name)


@frappe.whitelist(methods=["POST"])
def save_profile(profile):
	_require_manager()
	payload = _run(parse_profile, _payload(profile))
	name = _run(uc.SaveProfile(FrappeProfileRepository()).execute, payload)
	return _run(uc.GetProfile(FrappeProfileRepository(), FrappeDirectory()).execute, name)


@frappe.whitelist(methods=["POST"])
def delete_profile(name):
	_require_manager()
	_run(uc.DeleteProfile(FrappeProfileRepository()).execute, name)
	return {"deleted": name}


@frappe.whitelist(methods=["POST"])
def duplicate_profile(name, new_name):
	_require_manager()
	return _run(uc.DuplicateProfile(FrappeProfileRepository()).execute, name, new_name)


@frappe.whitelist()
def preview_profile(name):
	_require_manager()
	return _run(uc.PreviewProfile(FrappeProfileRepository(), FrappeSettingsRepository(), _catalog()).execute, name)


@frappe.whitelist()
def inspect_user(user):
	_require_manager()
	use_case = uc.InspectUser(FrappeProfileRepository(), FrappeSettingsRepository(), FrappeDirectory(), _catalog())
	return _run(use_case.execute, user)


@frappe.whitelist()
def describe_doctype(doctype):
	"""Fields and flags used by the DocType rule editors."""
	_require_manager()
	if not doctype or not frappe.db.exists("DocType", doctype):
		frappe.throw(_("DocType {0} does not exist").format(doctype), frappe.DoesNotExistError)
	meta = frappe.get_meta(doctype)
	fields = [
		{
			"fieldname": df.fieldname,
			"label": _(df.label) if df.label else df.fieldname,
			"fieldtype": df.fieldtype,
			"options": df.options if df.fieldtype in ("Link", "Select", "Dynamic Link") else "",
			"is_table": df.fieldtype in ("Table", "Table MultiSelect"),
		}
		for df in meta.fields
		if df.fieldname and df.fieldtype not in LAYOUT_TYPES
	]
	standard = [
		{"fieldname": f, "label": label, "fieldtype": fieldtype, "options": "", "is_table": False, "standard": True}
		for f, label, fieldtype in (
			("owner", _("Created By"), "Link"),
			("modified_by", _("Last Updated By"), "Link"),
			("idx", _("Index"), "Int"),
		)
	]
	return {"doctype": doctype, "meta": doctype_flags(doctype, meta), "fields": fields + standard}


@frappe.whitelist()
def describe_report(report):
	_require_manager()
	info = FrappeErpGateway().report_info(report)
	if info is None:
		frappe.throw(_("Report {0} does not exist").format(report), frappe.DoesNotExistError)
	return {"report": info.name, "report_type": info.report_type, "ref_doctype": info.ref_doctype, "filters": info.filters}


@frappe.whitelist()
def doctype_catalog():
	"""All DocTypes that may be granted, for the bulk picker."""
	_require_manager()
	return uc.ListDoctypeCatalog(FrappeCatalogRepository()).execute()


@frappe.whitelist()
def report_catalog():
	_require_manager()
	return uc.ListReportCatalog(FrappeCatalogRepository()).execute()


@frappe.whitelist()
def actions():
	_require_manager()
	return FrappeActionRepository().list()


@frappe.whitelist(methods=["POST"])
def save_action(action):
	_require_manager()
	payload = _run(parse_action, _payload(action))
	name = _run(uc.SaveAction(FrappeActionRepository()).execute, payload)
	return {"name": name, "actions": FrappeActionRepository().list()}


@frappe.whitelist(methods=["POST"])
def delete_action(name):
	_require_manager()
	_run(uc.DeleteAction(FrappeActionRepository()).execute, name)
	return {"deleted": name, "actions": FrappeActionRepository().list()}


@frappe.whitelist(methods=["POST"])
def save_settings(settings):
	_require_manager()
	payload = _run(parse_settings, _payload(settings))
	return _run(uc.SaveSettings(FrappeSettingsRepository()).execute, payload)


@frappe.whitelist()
def logs(query=None):
	_require_manager()
	parsed = _run(parse_log_query, _payload(query) or {})
	return uc.SearchLogs(FrappeAuditQuery()).execute(parsed)


@frappe.whitelist()
def log_detail(name):
	_require_manager()
	return _run(uc.GetLog(FrappeAuditQuery()).execute, name)


@frappe.whitelist(methods=["POST"])
def generate_keys(user):
	_require_manager()
	return _run(uc.GenerateKeys(FrappeDirectory()).execute, user)


@frappe.whitelist()
def claude_connector():
	_require_manager()
	data = uc.GetClaudeConnector(FrappeOAuthClientRepository()).execute()
	data["connector_url"] = frappe.utils.get_url(ENDPOINT_PATH)
	return data


@frappe.whitelist(methods=["POST"])
def create_claude_connector():
	_require_manager()
	data = uc.CreateClaudeConnector(FrappeOAuthClientRepository()).execute()
	data["connector_url"] = frappe.utils.get_url(ENDPOINT_PATH)
	return data


@frappe.whitelist()
def chatgpt_connector():
	_require_manager()
	data = uc.GetChatGPTConnector(FrappeOAuthClientRepository()).execute()
	data["connector_url"] = frappe.utils.get_url(ENDPOINT_PATH)
	return data


@frappe.whitelist(methods=["POST"])
def allow_chatgpt_redirect(redirect_uri=None):
	_require_manager()
	data = _run(uc.AllowChatGPTRedirect(FrappeOAuthClientRepository()).execute, redirect_uri)
	data["connector_url"] = frappe.utils.get_url(ENDPOINT_PATH)
	return data
