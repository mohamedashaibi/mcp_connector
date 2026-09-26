"""MCP Streamable HTTP endpoint.

    POST /api/method/mcp_connector.api.mcp.endpoint

Authenticate with Frappe's own mechanisms: `Authorization: token <api_key>:<api_secret>`,
an OAuth bearer token, or a desk session (with the CSRF header). The server is
stateless: every POST carries one JSON-RPC message and gets a JSON response.
"""

import json

import frappe
from frappe.utils import get_fullname, get_url
from frappe.utils.response import json_handler
from werkzeug.wrappers import Response

import mcp_connector
from mcp_connector.application.dto import Caller
from mcp_connector.application.mcp_server import (
	DEFAULT_PROTOCOL_VERSION,
	SUPPORTED_PROTOCOL_VERSIONS,
	CallTool,
	HandleMcpMessage,
	McpSession,
	ToolCatalog,
)
from mcp_connector.domain import audit as audit_rules
from mcp_connector.domain.jsonrpc import (
	ACCESS_DENIED,
	INTERNAL_ERROR,
	INVALID_REQUEST,
	PARSE_ERROR,
	RATE_LIMITED,
	failure,
)
from mcp_connector.domain.policy import resolve_policy
from mcp_connector.infrastructure.audit import FrappeAuditSink
from mcp_connector.infrastructure.gateway import FrappeErpGateway
from mcp_connector.infrastructure.policy_source import load_actions, load_profiles
from mcp_connector.infrastructure.rate_limit import within_rate_limit
from mcp_connector.infrastructure.settings import load_settings
from mcp_connector.infrastructure.uow import FrappeUnitOfWork

ENDPOINT_PATH = "/api/method/mcp_connector.api.mcp.endpoint"
MAX_BODY_BYTES = 2 * 1024 * 1024


def _default(obj):
	try:
		return json_handler(obj)
	except TypeError:
		return str(obj)


def _json(status: int, body, headers=None) -> Response:
	response = Response(
		json.dumps(body, default=_default, ensure_ascii=False, separators=(",", ":")),
		status=status,
		mimetype="application/json",
	)
	response.headers["Cache-Control"] = "no-store"
	for key, value in (headers or {}).items():
		response.headers[key] = value
	return response


def _error(status: int, request_id, code: int, message: str, headers=None) -> Response:
	return _json(status, failure(request_id, code, message), headers)


def _site_origin() -> str:
	url = get_url()
	scheme, _, rest = url.partition("://")
	return f"{scheme}://{rest.split('/')[0]}".lower()


def origin_allowed(origin: str, allowed: tuple) -> bool:
	origin = origin.strip().rstrip("/").lower()
	request_origin = f"{frappe.request.scheme}://{frappe.request.host}".lower()
	return origin in allowed or origin in (_site_origin(), request_origin)


def build_handler() -> HandleMcpMessage:
	gateway = FrappeErpGateway()
	catalog = ToolCatalog(gateway)
	call_tool = CallTool(catalog, gateway, FrappeAuditSink(), FrappeUnitOfWork())
	return HandleMcpMessage(catalog, call_tool, mcp_connector.__version__)


def _caller(user: str, roles: list) -> Caller:
	request = getattr(frappe.local, "request", None)
	return Caller(
		user=user,
		full_name=get_fullname(user),
		roles=tuple(roles),
		client_ip=getattr(frappe.local, "request_ip", "") or "",
		user_agent=((request.headers.get("User-Agent") if request else "") or "")[:500],
	)


def build_session(user: str, settings, protocol_version: str = DEFAULT_PROTOCOL_VERSION) -> McpSession:
	"""Resolve everything one request needs about its caller: roles, profiles, tools."""
	roles = frappe.get_roles(user)
	actions = load_actions()
	return McpSession(
		caller=_caller(user, roles),
		policy=resolve_policy(user, roles, load_profiles(), actions),
		settings=settings,
		actions={a.name: a for a in actions},
		protocol_version=protocol_version,
	)


@frappe.whitelist(allow_guest=True, methods=["POST", "GET", "DELETE"])
def endpoint():
	request = frappe.request
	if request.method != "POST":
		# Stateless server: no SSE stream to open (GET) and no session to end (DELETE).
		return _error(405, None, INVALID_REQUEST, "Use POST for MCP messages", {"Allow": "POST"})

	settings = load_settings()
	origin = request.headers.get("Origin")
	if origin and not origin_allowed(origin, settings.allowed_origins):
		return _error(403, None, ACCESS_DENIED, f"Origin {origin} is not allowed")

	protocol_version = request.headers.get("MCP-Protocol-Version") or DEFAULT_PROTOCOL_VERSION
	if protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
		return _error(400, None, INVALID_REQUEST, f"Unsupported MCP-Protocol-Version: {protocol_version}")

	if (request.content_length or 0) > MAX_BODY_BYTES:
		return _error(413, None, INVALID_REQUEST, "Request body too large")
	try:
		message = json.loads(request.get_data(cache=True, as_text=True) or "")
	except ValueError:
		return _error(400, None, PARSE_ERROR, "Parse error: body must be one JSON-RPC message")
	request_id = message.get("id") if isinstance(message, dict) else None
	rpc_method = str(message.get("method") or "")[:140] if isinstance(message, dict) else ""

	user = frappe.session.user
	if not user or user == "Guest":
		return _error(
			401, request_id, ACCESS_DENIED, "Authentication required: send 'Authorization: token <api_key>:<api_secret>'"
		)
	if not settings.enabled:
		return _error(403, request_id, ACCESS_DENIED, "The MCP server is disabled on this site")
	if not within_rate_limit(user, settings.rate_limit_per_minute):
		return _error(429, request_id, RATE_LIMITED, "Rate limit exceeded; retry in a minute", {"Retry-After": "60"})

	session = build_session(user, settings, protocol_version)
	caller = session.caller
	if session.policy.is_empty:
		if audit_rules.should_record(settings.audit_mode, "", audit_rules.DENIED):
			FrappeAuditSink().record(
				audit_rules.AuditEntry(
					user=user,
					rpc_method=rpc_method,
					status=audit_rules.DENIED,
					error="No MCP access profile applies to this user",
					client_ip=caller.client_ip,
					user_agent=caller.user_agent,
				)
			)
		return _error(
			403,
			request_id,
			ACCESS_DENIED,
			f"No MCP access profile applies to {user}. Ask an administrator to add one of your roles to an MCP Profile.",
		)

	try:
		response = build_handler().execute(message, session)
	except Exception:
		frappe.log_error(title="MCP Connector: request failed")
		return _error(500, request_id, INTERNAL_ERROR, "Internal server error")
	if response is None:
		return Response(status=202)
	return _json(200, response)
