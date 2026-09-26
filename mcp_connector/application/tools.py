"""One use case per MCP action (tool).

Every handler re-checks the policy for the exact doctype/report it touches;
argument enums in the tool schema are a convenience for the model, never the
security boundary.
"""

from dataclasses import dataclass
from typing import Any

from mcp_connector.domain.action_types import CANCEL, CREATE, DELETE, FRAPPE_PTYPE, READ, SUBMIT, UPDATE
from mcp_connector.domain.errors import InvalidArguments, NotFound, PolicyDenied
from mcp_connector.domain.filters import (
	check_identifier,
	clamp_int,
	normalize_filters,
	order_by_sql,
	parse_fields,
	parse_order_by,
	require_fields,
	sort_rows,
)
from mcp_connector.domain.policy import ActionSpec, DoctypeGrant, EffectivePolicy
from mcp_connector.domain.protected import is_blocked_method, is_exposable, is_writable

from .access import (
	default_list_fields,
	listable_fields,
	project_document,
	readable_fields,
	search_conditions,
	validate_write_data,
)
from .dto import DoctypeInfo, ServerSettings, ToolOutcome
from .ports import ErpGateway

MULTI_SCOPE_COUNT_CAP = 50_000


@dataclass
class ToolContext:
	policy: EffectivePolicy
	settings: ServerSettings
	gateway: ErpGateway


def _not_found(doctype: str, name: str) -> NotFound:
	return NotFound(f"{doctype} '{name}' was not found or is outside your access scope")


class DocumentTool:
	operation = READ

	def grant(self, ctx: ToolContext, doctype: str, operation: str = None) -> DoctypeGrant:
		operation = operation or self.operation
		if not is_exposable(doctype):
			raise PolicyDenied(f"{doctype} is never available over MCP")
		if operation != READ and not is_writable(doctype):
			raise PolicyDenied(f"{doctype} is read-only over MCP")
		grant = ctx.policy.grant(doctype, operation)
		if grant is None:
			raise PolicyDenied(f"Your MCP access profile does not allow {operation} on {doctype}")
		return grant

	def info(self, ctx: ToolContext, doctype: str) -> DoctypeInfo:
		info = ctx.gateway.doctype_info(doctype)
		if info is None or info.istable:
			raise NotFound(f"DocType '{doctype}' does not exist")
		return info

	def require_erp_permission(self, ctx: ToolContext, doctype: str, operation: str = None) -> None:
		operation = operation or self.operation
		if not ctx.gateway.has_permission(doctype, FRAPPE_PTYPE[operation]):
			raise PolicyDenied(f"Your ERPNext roles do not allow {operation} on {doctype}")

	def prepare(self, ctx: ToolContext, doctype: str):
		grant = self.grant(ctx, doctype)
		info = self.info(ctx, doctype)
		self.require_erp_permission(ctx, doctype)
		return grant, info

	def ensure_in_scope(self, ctx: ToolContext, grant: DoctypeGrant, info: DoctypeInfo, name: str) -> None:
		if info.issingle:
			if name != info.name:
				raise _not_found(info.name, name)
			return
		if not ctx.gateway.document_exists(info.name, name):
			raise _not_found(info.name, name)
		if grant.scopes is not None and not any(
			ctx.gateway.document_matches(info.name, name, scope) for scope in grant.scopes
		):
			raise _not_found(info.name, name)

	def in_scope_after_write(self, ctx: ToolContext, grant: DoctypeGrant, info: DoctypeInfo, name: str) -> bool:
		if grant.scopes is None or info.issingle:
			return True
		return any(ctx.gateway.document_matches(info.name, name, scope) for scope in grant.scopes)

	def visible(self, ctx: ToolContext, info: DoctypeInfo, doc: dict) -> dict:
		read = ctx.policy.grant(info.name, READ)
		if read is None:
			return {k: doc.get(k) for k in ("doctype", "name", "docstatus") if k in doc}
		return project_document(doc, info, read.field_rule)


class ListDoctypesTool:
	def execute(self, args: dict, ctx: ToolContext) -> ToolOutcome:
		rows = []
		for doctype in ctx.policy.doctypes():
			if not is_exposable(doctype):
				continue
			info = ctx.gateway.doctype_info(doctype)
			if info is None or info.istable:
				continue
			operations = [
				op
				for op in ctx.policy.operations_for(doctype)
				if (op == READ or is_writable(doctype))
				and (op not in (SUBMIT, CANCEL) or info.is_submittable)
				and ctx.gateway.has_permission(doctype, FRAPPE_PTYPE[op])
			]
			if not operations:
				continue
			row = {"doctype": doctype, "module": info.module, "operations": operations}
			if info.is_submittable:
				row["submittable"] = True
			if info.issingle:
				row["single"] = True
			notes = [n for op in operations for n in ctx.policy.grant(doctype, op).notes]
			if notes:
				row["notes"] = " ".join(dict.fromkeys(notes))
			rows.append(row)
		return ToolOutcome({"count": len(rows), "doctypes": rows}, summary=f"{len(rows)} doctype(s)")


class DescribeDoctypeTool(DocumentTool):
	def execute(self, args: dict, ctx: ToolContext) -> ToolOutcome:
		doctype = args["doctype"]
		if not is_exposable(doctype):
			raise PolicyDenied(f"{doctype} is never available over MCP")
		rule = ctx.policy.visible_field_rule(doctype)
		if rule is None:
			raise PolicyDenied(f"Your MCP access profile does not include {doctype}")
		info = self.info(ctx, doctype)
		visible = readable_fields(info, rule)

		fields = []
		child_tables = {}
		for f in info.fields:
			if f.fieldname not in visible:
				continue
			entry = {"fieldname": f.fieldname, "label": f.label, "fieldtype": f.fieldtype}
			for key in ("options", "description"):
				value = getattr(f, key)
				if value:
					entry[key] = value[:300]
			for key in ("reqd", "read_only", "allow_on_submit"):
				if getattr(f, key):
					entry[key] = True
			fields.append(entry)
			if f.is_table:
				child_tables[f.fieldname] = {
					"doctype": f.child_doctype,
					"fields": [
						{
							"fieldname": c.fieldname,
							"label": c.label,
							"fieldtype": c.fieldtype,
							**({"options": c.options[:300]} if c.options else {}),
							**({"reqd": True} if c.reqd else {}),
							**({"read_only": True} if c.read_only else {}),
						}
						for c in info.child_fields.get(f.child_doctype, ())
						if c.readable and c.fieldtype != "Password"
					],
				}

		operations = ctx.policy.operations_for(doctype)
		payload = {
			"doctype": doctype,
			"module": info.module,
			"operations": [op for op in operations if op == READ or is_writable(doctype)],
			"naming": info.autoname or "hash",
			"title_field": info.title_field or "name",
			"is_submittable": info.is_submittable,
			"is_single": info.issingle,
			"fields": fields,
		}
		if info.description:
			payload["description"] = info.description[:500]
		if child_tables:
			payload["child_tables"] = child_tables
		if info.has_workflow:
			payload["has_workflow"] = True
		notes = [n for op in operations for n in ctx.policy.grant(doctype, op).notes]
		if notes:
			payload["notes"] = " ".join(dict.fromkeys(notes))
		scopes = [s for s in (ctx.policy.grant(doctype, op).scopes for op in operations) if s]
		if scopes:
			payload["access_scope"] = "Only records matching the administrator's scope filters are visible."
		return ToolOutcome(payload, summary=f"{len(fields)} field(s)", document_type=doctype)


class ListDocumentsTool(DocumentTool):
	operation = READ

	def execute(self, args: dict, ctx: ToolContext) -> ToolOutcome:
		doctype = args["doctype"]
		grant, info = self.prepare(ctx, doctype)
		if info.issingle:
			raise InvalidArguments(f"{doctype} is a single (settings) DocType; use get_document with name '{doctype}'")

		listable = listable_fields(info, grant.field_rule)
		allowed = listable.__contains__
		fields = parse_fields(args.get("fields"), allowed) or default_list_fields(info, listable)
		conditions = normalize_filters(args.get("filters"))
		require_fields(conditions, allowed)
		or_conditions = search_conditions(info, listable, args.get("search"))
		order = parse_order_by(args.get("order_by"), allowed)
		limit = clamp_int(args.get("limit"), "limit", ctx.settings.default_page_size, 1, ctx.settings.max_page_size)
		start = clamp_int(args.get("start"), "start", 0, 0, 10_000_000)

		query_fields = list(dict.fromkeys(["name", *fields, *(key for key, _ in order)]))
		rows = self._query(ctx, grant, query_fields, conditions, or_conditions, order, start, limit + 1)
		has_more = len(rows) > limit
		data = [{k: row.get(k) for k in fields} for row in rows[:limit]]
		payload = {
			"doctype": doctype,
			"start": start,
			"limit": limit,
			"returned": len(data),
			"has_more": has_more,
			"data": data,
		}
		return ToolOutcome(payload, summary=f"{len(data)} row(s)", document_type=doctype)

	def _query(self, ctx, grant, fields, conditions, or_conditions, order, start, limit):
		order_sql = order_by_sql(order)
		scopes = grant.scopes
		if scopes is None or len(scopes) == 1:
			filters = list(conditions) + (list(scopes[0]) if scopes else [])
			return ctx.gateway.list_documents(grant.doctype, fields, filters, or_conditions, order_sql, start, limit)
		# ponytail: union of scopes merged in Python; fine for page-sized reads, a
		# deep offset re-reads start+limit rows per scope.
		merged = {}
		for scope in scopes:
			for row in ctx.gateway.list_documents(
				grant.doctype, fields, list(conditions) + list(scope), or_conditions, order_sql, 0, start + limit
			):
				merged.setdefault(row["name"], row)
		return sort_rows(list(merged.values()), order)[start : start + limit]


class CountDocumentsTool(DocumentTool):
	operation = READ

	def execute(self, args: dict, ctx: ToolContext) -> ToolOutcome:
		doctype = args["doctype"]
		grant, info = self.prepare(ctx, doctype)
		if info.issingle:
			raise InvalidArguments(f"{doctype} is a single (settings) DocType and has no rows to count")
		listable = listable_fields(info, grant.field_rule)
		conditions = normalize_filters(args.get("filters"))
		require_fields(conditions, listable.__contains__)

		payload = {"doctype": doctype}
		scopes = grant.scopes
		if scopes is None or len(scopes) == 1:
			filters = list(conditions) + (list(scopes[0]) if scopes else [])
			payload["count"] = ctx.gateway.count_documents(doctype, filters, [])
		else:
			names = set()
			capped = False
			for scope in scopes:
				found = ctx.gateway.list_names(doctype, list(conditions) + list(scope), MULTI_SCOPE_COUNT_CAP)
				capped = capped or len(found) >= MULTI_SCOPE_COUNT_CAP
				names.update(found)
			payload["count"] = len(names)
			if capped:
				payload["count_is_lower_bound"] = True
		return ToolOutcome(payload, summary=f"count={payload['count']}", document_type=doctype)


class GetDocumentTool(DocumentTool):
	operation = READ

	def execute(self, args: dict, ctx: ToolContext) -> ToolOutcome:
		doctype, name = args["doctype"], args["name"]
		grant, info = self.prepare(ctx, doctype)
		self.ensure_in_scope(ctx, grant, info, name)
		doc = project_document(ctx.gateway.get_document(doctype, name), info, grant.field_rule)
		if info.has_workflow:
			doc["_workflow"] = ctx.gateway.workflow_actions(doctype, name)
		return ToolOutcome(doc, summary=f"{doctype} {name}", document_type=doctype, document_name=name)


class CreateDocumentTool(DocumentTool):
	operation = CREATE

	def execute(self, args: dict, ctx: ToolContext) -> ToolOutcome:
		doctype = args["doctype"]
		grant, info = self.prepare(ctx, doctype)
		if info.issingle:
			raise InvalidArguments(f"{doctype} is a single (settings) DocType; use update_document instead")
		data = validate_write_data(args["data"], info, grant.field_rule, creating=True)
		doc = ctx.gateway.create_document(doctype, data)
		if not self.in_scope_after_write(ctx, grant, info, doc["name"]):
			raise PolicyDenied(f"The new {doctype} would fall outside your access scope; nothing was saved")
		return ToolOutcome(
			self.visible(ctx, info, doc),
			summary=f"Created {doctype} {doc['name']}",
			document_type=doctype,
			document_name=doc["name"],
		)


class UpdateDocumentTool(DocumentTool):
	operation = UPDATE

	def execute(self, args: dict, ctx: ToolContext) -> ToolOutcome:
		doctype, name = args["doctype"], args["name"]
		grant, info = self.prepare(ctx, doctype)
		self.ensure_in_scope(ctx, grant, info, name)
		data = validate_write_data(args["data"], info, grant.field_rule, creating=False)
		doc = ctx.gateway.update_document(doctype, name, data)
		if not self.in_scope_after_write(ctx, grant, info, name):
			raise PolicyDenied(f"This change would move {doctype} {name} outside your access scope; nothing was saved")
		return ToolOutcome(
			self.visible(ctx, info, doc), summary=f"Updated {doctype} {name}", document_type=doctype, document_name=name
		)


class DeleteDocumentTool(DocumentTool):
	operation = DELETE

	def execute(self, args: dict, ctx: ToolContext) -> ToolOutcome:
		doctype, name = args["doctype"], args["name"]
		grant, info = self.prepare(ctx, doctype)
		if info.issingle:
			raise InvalidArguments(f"{doctype} is a single (settings) DocType and cannot be deleted")
		self.ensure_in_scope(ctx, grant, info, name)
		ctx.gateway.delete_document(doctype, name)
		return ToolOutcome(
			{"deleted": True, "doctype": doctype, "name": name},
			summary=f"Deleted {doctype} {name}",
			document_type=doctype,
			document_name=name,
		)


class _DocstatusTool(DocumentTool):
	verb = ""

	def execute(self, args: dict, ctx: ToolContext) -> ToolOutcome:
		doctype, name = args["doctype"], args["name"]
		grant, info = self.prepare(ctx, doctype)
		if not info.is_submittable:
			raise InvalidArguments(f"{doctype} is not submittable")
		self.ensure_in_scope(ctx, grant, info, name)
		doc = self.change(ctx, doctype, name)
		return ToolOutcome(
			self.visible(ctx, info, doc),
			summary=f"{self.verb} {doctype} {name}",
			document_type=doctype,
			document_name=name,
		)


class SubmitDocumentTool(_DocstatusTool):
	operation = SUBMIT
	verb = "Submitted"

	def change(self, ctx, doctype, name):
		return ctx.gateway.submit_document(doctype, name)


class CancelDocumentTool(_DocstatusTool):
	operation = CANCEL
	verb = "Cancelled"

	def change(self, ctx, doctype, name):
		return ctx.gateway.cancel_document(doctype, name)


class ApplyWorkflowActionTool(DocumentTool):
	operation = UPDATE

	def execute(self, args: dict, ctx: ToolContext) -> ToolOutcome:
		doctype, name, action = args["doctype"], args["name"], args["action"]
		grant, info = self.prepare(ctx, doctype)
		if not info.has_workflow:
			raise InvalidArguments(f"{doctype} has no active workflow")
		self.ensure_in_scope(ctx, grant, info, name)

		workflow = ctx.gateway.workflow_actions(doctype, name)
		transition = next((t for t in workflow.get("actions", []) if t.get("action") == action), None)
		if transition is None:
			available = ", ".join(t["action"] for t in workflow.get("actions", [])) or "none"
			raise InvalidArguments(f"Workflow action '{action}' is not available to you now. Available: {available}")
		current, target = workflow.get("docstatus", 0), transition.get("next_docstatus", 0)
		if target == 1 and current == 0:
			self.grant(ctx, doctype, SUBMIT)
		if target == 2 and current == 1:
			self.grant(ctx, doctype, CANCEL)

		doc = ctx.gateway.apply_workflow_action(doctype, name, action)
		return ToolOutcome(
			self.visible(ctx, info, doc),
			summary=f"{action}: {doctype} {name} → {transition.get('next_state')}",
			document_type=doctype,
			document_name=name,
		)


def _report_payload_base(info) -> dict:
	payload = {"report_name": info.name, "report_type": info.report_type}
	if info.ref_doctype:
		payload["ref_doctype"] = info.ref_doctype
	return payload


class ListReportsTool:
	def execute(self, args: dict, ctx: ToolContext) -> ToolOutcome:
		rows = []
		for report in ctx.policy.reports():
			info = ctx.gateway.report_info(report)
			if info is None or not info.permitted:
				continue
			row = _report_payload_base(info)
			grant = ctx.policy.report_grant(report)
			if grant.notes:
				row["notes"] = " ".join(grant.notes)
			if any(grant.fixed_filter_options) and all(grant.fixed_filter_options):
				row["has_fixed_filters"] = True
			rows.append(row)
		return ToolOutcome({"count": len(rows), "reports": rows}, summary=f"{len(rows)} report(s)")


class _ReportTool:
	def prepare(self, ctx: ToolContext, report: str):
		grant = ctx.policy.report_grant(report)
		if grant is None:
			raise PolicyDenied(f"Your MCP access profile does not allow the report '{report}'")
		info = ctx.gateway.report_info(report)
		if info is None:
			raise NotFound(f"Report '{report}' does not exist")
		if not info.permitted:
			raise PolicyDenied(f"Your ERPNext roles do not allow the report '{report}'")
		return grant, info


class DescribeReportTool(_ReportTool):
	def execute(self, args: dict, ctx: ToolContext) -> ToolOutcome:
		report = args["report_name"]
		grant, info = self.prepare(ctx, report)
		payload = _report_payload_base(info)
		if info.description:
			payload["description"] = info.description[:500]
		if info.filters is not None:
			payload["filters"] = info.filters
		elif info.report_type == "Report Builder" and info.ref_doctype:
			payload["filters_hint"] = (
				f"Pass filters on {info.ref_doctype} fields: {{fieldname: value}} or {{fieldname: [operator, value]}}."
			)
		else:
			payload["filters_hint"] = "This report defines its filters dynamically; common ones are company, from_date and to_date."
		options = [o for o in grant.fixed_filter_options if o]
		if options and len(options) == len(grant.fixed_filter_options):
			payload["fixed_filters"] = options[0] if len(options) == 1 else options
			payload["fixed_filters_note"] = "These values are enforced by the administrator and override yours."
		if grant.notes:
			payload["notes"] = " ".join(grant.notes)
		return ToolOutcome(payload, summary=report, report=report)


def _validate_report_filters(filters: Any) -> dict:
	if filters in (None, ""):
		return {}
	if not isinstance(filters, dict):
		raise InvalidArguments("filters must be an object keyed by filter fieldname")
	if len(filters) > 50:
		raise InvalidArguments("At most 50 report filters are allowed")
	scalar = (str, int, float, bool, type(None))
	for key, value in filters.items():
		check_identifier(key, "filter")
		if isinstance(value, list):
			for item in value:
				if isinstance(item, list):
					if not all(isinstance(v, scalar) for v in item):
						raise InvalidArguments(f"Filter '{key}' has an unsupported nested value")
				elif not isinstance(item, scalar):
					raise InvalidArguments(f"Filter '{key}' must hold plain values")
		elif not isinstance(value, scalar):
			raise InvalidArguments(f"Filter '{key}' must be a string, number, boolean or list")
	return dict(filters)


class RunReportTool(_ReportTool):
	def execute(self, args: dict, ctx: ToolContext) -> ToolOutcome:
		report = args["report_name"]
		grant, info = self.prepare(ctx, report)
		requested = _validate_report_filters(args.get("filters"))
		filters = grant.resolve_filters(requested)
		limit = clamp_int(args.get("limit"), "limit", ctx.settings.max_report_rows, 1, ctx.settings.max_report_rows)

		result = ctx.gateway.run_report(report, filters, limit)
		rows = result.get("rows", [])[:limit]
		total = result.get("total_rows", len(rows))
		payload = {
			"report_name": report,
			"filters_applied": filters,
			"columns": result.get("columns", []),
			"rows": rows,
			"returned": len(rows),
			"total_rows": total,
			"truncated": total > len(rows),
		}
		for key in ("message", "report_summary"):
			if result.get(key):
				payload[key] = result[key]
		return ToolOutcome(payload, summary=f"{len(rows)}/{total} row(s)", report=report)


class CustomActionTool:
	def __init__(self, action: ActionSpec):
		self.action = action

	def execute(self, args: dict, ctx: ToolContext) -> ToolOutcome:
		if not self.action.method or is_blocked_method(self.action.method):
			raise PolicyDenied(f"Custom action '{self.action.name}' points at a method that is not allowed")
		result = ctx.gateway.call_method(self.action.method, args)
		return ToolOutcome({"result": result}, summary=f"Called {self.action.method}")


BUILTIN_HANDLERS = {
	"list_doctypes": ListDoctypesTool(),
	"describe_doctype": DescribeDoctypeTool(),
	"list_documents": ListDocumentsTool(),
	"get_document": GetDocumentTool(),
	"count_documents": CountDocumentsTool(),
	"create_document": CreateDocumentTool(),
	"update_document": UpdateDocumentTool(),
	"apply_workflow_action": ApplyWorkflowActionTool(),
	"delete_document": DeleteDocumentTool(),
	"submit_document": SubmitDocumentTool(),
	"cancel_document": CancelDocumentTool(),
	"list_reports": ListReportsTool(),
	"describe_report": DescribeReportTool(),
	"run_report": RunReportTool(),
}


def handler_for(action: ActionSpec):
	if action.is_standard:
		return BUILTIN_HANDLERS.get(action.name)
	return CustomActionTool(action)
