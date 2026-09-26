"""The MCP server use cases: initialize, tools/list, tools/call."""

import json
import time
import traceback
from dataclasses import dataclass, replace
from typing import Any, Callable, Optional

from mcp_connector.domain import audit as audit_rules
from mcp_connector.domain.action_types import FRAPPE_PTYPE, READ
from mcp_connector.domain.catalog import ANY_OPERATION, BUILTIN_BY_NAME, SchemaContext
from mcp_connector.domain.errors import ErpFailure, InvalidArguments, NotFound, PolicyDenied
from mcp_connector.domain.jsonrpc import (
	INVALID_PARAMS,
	METHOD_NOT_FOUND,
	RpcError,
	failure,
	parse_message,
	success,
)
from mcp_connector.domain.policy import ActionSpec, EffectivePolicy
from mcp_connector.domain.protected import is_exposable, is_writable
from mcp_connector.domain.schema import validate_arguments

from .dto import Caller, ServerSettings
from .ports import AuditSink, ErpGateway, UnitOfWork
from .tools import ToolContext, handler_for

SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
LATEST_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]
DEFAULT_PROTOCOL_VERSION = "2025-03-26"  # assumed when the client sends no MCP-Protocol-Version header
SERVER_ID = "erpnext-mcp-connector"

DEFAULT_INSTRUCTIONS = (
	"You are connected to an ERPNext (Frappe) system. Call list_doctypes to see which record types you "
	"may use and describe_doctype before filtering or writing. Reports are listed by list_reports. "
	"Only use values returned by the tools; never guess document names. Every call is audited."
)


@dataclass(frozen=True)
class McpSession:
	caller: Caller
	policy: EffectivePolicy
	settings: ServerSettings
	actions: dict  # tool name -> ActionSpec
	protocol_version: str = DEFAULT_PROTOCOL_VERSION


PermissionCheck = Callable[[str, str], bool]


class ToolCatalog:
	"""Builds the tool descriptors a given policy exposes (tools/list and the inspector)."""

	def __init__(self, gateway: ErpGateway):
		self.gateway = gateway

	def doctypes_for(self, policy: EffectivePolicy, operation: str, allowed: PermissionCheck) -> list:
		def usable(doctype: str, op: str) -> bool:
			return is_exposable(doctype) and (op == READ or is_writable(doctype)) and allowed(doctype, FRAPPE_PTYPE[op])

		if operation == ANY_OPERATION:
			return [dt for dt in policy.doctypes() if any(usable(dt, op) for op in policy.operations_for(dt))]
		return [dt for dt in policy.doctypes_for(operation) if usable(dt, operation)]

	def reports_for(self, policy: EffectivePolicy, report_allowed: Optional[Callable[[str], bool]] = None) -> list:
		result = []
		for report in policy.reports():
			info = self.gateway.report_info(report)
			if info is not None and (report_allowed(report) if report_allowed else info.permitted):
				result.append(report)
		return result

	def describe(
		self,
		policy: EffectivePolicy,
		settings: ServerSettings,
		actions: dict,
		allowed: Optional[PermissionCheck] = None,
		report_allowed: Optional[Callable[[str], bool]] = None,
	) -> list:
		allowed = allowed or self.gateway.has_permission
		reports = None
		doctype_cache: dict = {}
		descriptors = []
		for name in sorted(policy.tools):
			action = actions.get(name)
			if action is None:
				continue
			if action.is_standard:
				tool = BUILTIN_BY_NAME.get(name)
				if tool is None:
					continue
				ctx = SchemaContext(
					default_page_size=settings.default_page_size,
					max_page_size=settings.max_page_size,
					max_report_rows=settings.max_report_rows,
				)
				if tool.operation:
					if tool.operation not in doctype_cache:
						doctype_cache[tool.operation] = self.doctypes_for(policy, tool.operation, allowed)
					if not doctype_cache[tool.operation]:
						continue
					ctx = replace(ctx, doctypes=tuple(doctype_cache[tool.operation]))
				if tool.needs_reports:
					reports = self.reports_for(policy, report_allowed) if reports is None else reports
					if not reports:
						continue
					ctx = replace(ctx, reports=tuple(reports))
				descriptors.append(
					_descriptor(
						name,
						action.title or tool.title,
						action.description or tool.description,
						tool.build_schema(ctx),
						read_only=tool.read_only,
						destructive=tool.destructive,
						idempotent=tool.idempotent,
					)
				)
			else:
				descriptors.append(
					_descriptor(
						name,
						action.title or name.replace("_", " ").capitalize(),
						action.description or action.title or name,
						_custom_schema(action),
						read_only=action.read_only_hint,
						destructive=action.destructive_hint,
						idempotent=False,
					)
				)
		return descriptors


def _custom_schema(action: ActionSpec) -> dict:
	schema = dict(action.input_schema or {"type": "object", "properties": {}})
	schema.setdefault("type", "object")
	schema.setdefault("properties", {})
	schema["additionalProperties"] = False
	return schema


def _descriptor(name, title, description, schema, read_only, destructive, idempotent) -> dict:
	return {
		"name": name,
		"title": title,
		"description": description,
		"inputSchema": schema,
		"annotations": {
			"title": title,
			"readOnlyHint": bool(read_only),
			"destructiveHint": bool(destructive),
			"idempotentHint": bool(idempotent),
			"openWorldHint": False,
		},
	}


def _to_json_text(payload: Any) -> str:
	return json.dumps(payload, default=str, ensure_ascii=False, separators=(",", ":"))


def _tool_error(message: str) -> dict:
	return {"content": [{"type": "text", "text": message}], "isError": True}


class CallTool:
	def __init__(self, catalog: ToolCatalog, gateway: ErpGateway, audit: AuditSink, uow: UnitOfWork):
		self.catalog = catalog
		self.gateway = gateway
		self.audit = audit
		self.uow = uow

	def execute(self, params: dict, session: McpSession) -> dict:
		name = params.get("name")
		arguments = params.get("arguments")
		if not isinstance(name, str) or not name:
			raise RpcError(INVALID_PARAMS, "tools/call needs a tool name")

		action: Optional[ActionSpec] = session.actions.get(name)
		entry = audit_rules.AuditEntry(
			user=session.caller.user,
			rpc_method="tools/call",
			status=audit_rules.DENIED,
			tool=name,
			action_type=action.action_type if action else "",
			arguments=arguments if session.settings.log_arguments else None,
			profiles=session.policy.profiles,
			client_ip=session.caller.client_ip,
			user_agent=session.caller.user_agent,
		)
		if isinstance(arguments, dict):
			entry.document_type = str(arguments.get("doctype") or "")[:140]
			entry.document_name = str(arguments.get("name") or "")[:140]
			entry.report = str(arguments.get("report_name") or "")[:140]

		descriptor = None
		if action is not None and session.policy.allows_tool(name):
			descriptors = self.catalog.describe(session.policy, session.settings, {name: action})
			descriptor = descriptors[0] if descriptors else None
		if descriptor is None:
			entry.error = "Tool not available to this user"
			self._record(entry, session)
			raise RpcError(INVALID_PARAMS, f"Unknown tool: {name}")

		started = time.perf_counter()
		ctx = ToolContext(policy=session.policy, settings=session.settings, gateway=self.gateway)
		structured = session.protocol_version >= "2025-06-18"
		try:
			args = validate_arguments(descriptor["inputSchema"], arguments)
			handler = handler_for(action)
			self.gateway.drain_messages()
			outcome = self.uow.atomic(lambda: handler.execute(args, ctx))
			messages = self.gateway.drain_messages()
			payload = outcome.payload
			if messages:
				payload = {**payload, "_messages": messages} if isinstance(payload, dict) else {"result": payload, "_messages": messages}
			result = {"content": [{"type": "text", "text": _to_json_text(payload)}], "isError": False}
			if structured and isinstance(payload, dict):
				result["structuredContent"] = payload
			entry.status = audit_rules.SUCCESS
			entry.result_summary = outcome.summary
			entry.document_type = outcome.document_type or entry.document_type
			entry.document_name = outcome.document_name or entry.document_name
			entry.report = outcome.report or entry.report
		except PolicyDenied as e:
			result, entry.status, entry.error = _tool_error(f"Permission denied: {e}"), audit_rules.DENIED, str(e)
		except ErpFailure as e:
			denied = e.kind == "permission"
			prefix = "Permission denied" if denied else "Error"
			result = _tool_error(f"{prefix}: {e}")
			entry.status = audit_rules.DENIED if denied else audit_rules.ERROR
			entry.error = str(e)
		except (InvalidArguments, NotFound) as e:
			result, entry.status, entry.error = _tool_error(f"Error: {e}"), audit_rules.ERROR, str(e)
		except Exception:
			self.gateway.drain_messages()
			result = _tool_error(
				f"Unexpected server error while running {name}. An administrator can find the details in the MCP Audit Log."
			)
			entry.status = audit_rules.ERROR
			entry.error = traceback.format_exc()[-4000:]
		entry.duration_ms = int((time.perf_counter() - started) * 1000)
		self._record(entry, session)
		return result

	def _record(self, entry, session: McpSession) -> None:
		if audit_rules.should_record(session.settings.audit_mode, entry.action_type, entry.status):
			self.audit.record(entry)


class HandleMcpMessage:
	"""Route one JSON-RPC message. Returns the response, or None for notifications."""

	def __init__(self, catalog: ToolCatalog, call_tool: CallTool, server_version: str):
		self.catalog = catalog
		self.call_tool = call_tool
		self.server_version = server_version

	def execute(self, message: Any, session: McpSession) -> Optional[dict]:
		try:
			request = parse_message(message)
		except RpcError as e:
			return failure(e.request_id, e.code, e.message, e.data)
		if request.is_notification:
			return None
		try:
			if request.method == "initialize":
				result = self._initialize(request.params, session)
			elif request.method == "ping":
				result = {}
			elif request.method == "tools/list":
				result = {"tools": self.catalog.describe(session.policy, session.settings, session.actions)}
			elif request.method == "tools/call":
				result = self.call_tool.execute(request.params, session)
			else:
				raise RpcError(METHOD_NOT_FOUND, f"Method not found: {request.method}")
		except RpcError as e:
			return failure(request.id, e.code, e.message, e.data)
		return success(request.id, result)

	def _initialize(self, params: dict, session: McpSession) -> dict:
		requested = params.get("protocolVersion")
		version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION
		caller = session.caller
		who = f"{caller.full_name} ({caller.user})" if caller.full_name and caller.full_name != caller.user else caller.user
		instructions = (session.settings.instructions or DEFAULT_INSTRUCTIONS).strip()
		instructions += f"\n\nYou act as the ERPNext user {who}; ERPNext permissions and every action are logged."
		return {
			"protocolVersion": version,
			"capabilities": {"tools": {"listChanged": False}},
			"serverInfo": {"name": SERVER_ID, "title": session.settings.server_name, "version": self.server_version},
			"instructions": instructions,
		}
