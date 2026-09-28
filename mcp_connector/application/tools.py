"""One use case per MCP action (tool).

Every handler re-checks the policy for the exact doctype/report it touches;
argument enums in the tool schema are a convenience for the model, never the
security boundary.

Several matching profiles are alternatives (see domain.policy): each check
looks for one profile whose grant covers the whole request -- this tool, the
operation, the fields involved and the record's scope.
"""

from dataclasses import dataclass
from typing import Any, Optional

from mcp_connector.domain.action_types import CANCEL, CREATE, DELETE, FRAPPE_PTYPE, READ, SUBMIT, UPDATE
from mcp_connector.domain.catalog import OPERATION_TOOLS
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
from mcp_connector.domain.policy import ActionSpec, EffectivePolicy, union_rules
from mcp_connector.domain.protected import CUSTOM_FIELD, custom_field_problem, is_blocked_method, is_exposable, is_writable

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
# Several scoped grants are merged in memory; each is read from row 0, so bound the window.
MULTI_SCOPE_WINDOW = 5_000


@dataclass
class ToolContext:
	policy: EffectivePolicy
	settings: ServerSettings
	gateway: ErpGateway
	tool: Optional[str] = None  # the tool being called; grants must come from profiles enabling it


def _not_found(doctype: str, name: str) -> NotFound:
	return NotFound(f"{doctype} '{name}' was not found or is outside your access scope")


class DocumentTool:
	operation = READ

	def grants(self, ctx: ToolContext, doctype: str, operation: str = None) -> list:
		operation = operation or self.operation
		if not is_exposable(doctype):
			raise PolicyDenied(f"{doctype} is never available over MCP")
		if operation != READ and not is_writable(doctype):
			raise PolicyDenied(f"{doctype} is read-only over MCP")
		grants = ctx.policy.grants(doctype, operation, ctx.tool)
		if not grants:
			raise PolicyDenied(f"Your MCP access profile does not allow {operation} on {doctype}")
		return grants

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
		grants = self.grants(ctx, doctype)
		info = self.info(ctx, doctype)
		self.require_erp_permission(ctx, doctype)
		return grants, info

	@staticmethod
	def matching(ctx: ToolContext, info: DoctypeInfo, name: str, grants: list) -> list:
		"""The grants whose scope contains the record."""
		if info.issingle:
			return list(grants) if name == info.name else []
		return [g for g in grants if not g.scope or ctx.gateway.document_matches(info.name, name, g.scope)]

	def in_scope(self, ctx: ToolContext, info: DoctypeInfo, name: str, grants: list) -> list:
		"""Grants covering an existing record; a missing and an out-of-scope record look the same."""
		if not info.issingle and not ctx.gateway.document_exists(info.name, name):
			raise _not_found(info.name, name)
		covering = self.matching(ctx, info, name, grants)
		if not covering:
			raise _not_found(info.name, name)
		return covering

	def accepting(self, info: DoctypeInfo, grants: list, data: Any, creating: bool):
		"""Grants whose field rule accepts every field being written, with the cleaned data."""
		accepted, cleaned, first_error = [], None, None
		for grant in grants:
			try:
				result = validate_write_data(data, info, grant.field_rule, creating=creating)
			except (InvalidArguments, PolicyDenied) as e:
				first_error = first_error or e
				continue
			accepted.append(grant)
			cleaned = result
		if not accepted:
			raise first_error or PolicyDenied(f"Your MCP access profile does not allow writing {info.name}")
		return accepted, cleaned

	def check_custom_field(self, ctx: ToolContext, info: DoctypeInfo, accepted: list, data: dict, name: str = None) -> None:
		"""Saving a Custom Field alters a table, which commits at once: nothing can be undone
		afterwards, so every check (the saved result on update included) runs before it."""
		if info.name != CUSTOM_FIELD:
			return
		problem = custom_field_problem({**ctx.gateway.get_document(info.name, name), **data} if name else data)
		if problem:
			raise PolicyDenied(problem)
		if all(g.scope for g in accepted):
			raise PolicyDenied("Custom Field cannot be written under a record scope: the table change commits before the scope is checked")

	def visible(self, ctx: ToolContext, info: DoctypeInfo, doc: dict) -> dict:
		"""Project a written document through the Read grants whose scope contains it."""
		reads = ctx.policy.grants(info.name, READ, "get_document")
		covering = self.matching(ctx, info, doc.get("name"), reads) if reads else []
		if not covering:
			return {k: doc.get(k) for k in ("doctype", "name", "docstatus") if k in doc}
		return project_document(doc, info, union_rules(covering))


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
				for op in ctx.policy.usable_operations(doctype, OPERATION_TOOLS)
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
			if info.has_workflow:
				row["workflow"] = True
			notes = [g.notes for op in operations for g in ctx.policy.grants(doctype, op) if g.notes]
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

		operations = ctx.policy.usable_operations(doctype, OPERATION_TOOLS)
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
			payload["workflow"] = "Submitting and cancelling go through apply_workflow_action; the workflow state is set by it."
		all_grants = [g for op in operations for g in ctx.policy.grants(doctype, op)]
		notes = [g.notes for g in all_grants if g.notes]
		if notes:
			payload["notes"] = " ".join(dict.fromkeys(notes))
		if any(g.scope for g in all_grants):
			payload["access_scope"] = "Only records matching the administrator's scope filters are reachable."
		return ToolOutcome(payload, summary=f"{len(fields)} field(s)", document_type=doctype)


class ListDocumentsTool(DocumentTool):
	operation = READ

	def execute(self, args: dict, ctx: ToolContext) -> ToolOutcome:
		doctype = args["doctype"]
		grants, info = self.prepare(ctx, doctype)
		if info.issingle:
			raise InvalidArguments(f"{doctype} is a single (settings) DocType; use get_document with name '{doctype}'")

		listables = [listable_fields(info, g.field_rule) for g in grants]
		any_listable = set().union(*listables).__contains__
		fields = parse_fields(args.get("fields"), any_listable) or default_list_fields(info, set.intersection(*listables))
		conditions = normalize_filters(args.get("filters"))
		require_fields(conditions, any_listable)
		order = parse_order_by(args.get("order_by"), any_listable)
		limit = clamp_int(args.get("limit"), "limit", ctx.settings.default_page_size, 1, ctx.settings.max_page_size)
		start = clamp_int(args.get("start"), "start", 0, 0, 10_000_000)

		# Only grants under which every field involved is visible may serve the rows.
		needed = {"name", *fields, *(c[0] for c in conditions), *(k for k, _ in order)}
		usable = [(g, listable) for g, listable in zip(grants, listables) if needed <= listable]
		if not usable:
			raise PolicyDenied(
				f"No single access rule of yours shows all of: {', '.join(sorted(needed - {'name'}))}. Ask for fewer fields."
			)
		unscoped = [pair for pair in usable if not pair[0].scope]
		if unscoped:
			usable = unscoped[:1]

		query_fields = list(dict.fromkeys(["name", *fields, *(key for key, _ in order)]))
		search = args.get("search")
		if len(usable) == 1:
			grant, listable = usable[0]
			rows = ctx.gateway.list_documents(
				doctype,
				query_fields,
				list(conditions) + list(grant.scope),
				search_conditions(info, listable, search),
				order_by_sql(order),
				start,
				limit + 1,
			)
		else:
			if start + limit > MULTI_SCOPE_WINDOW:
				raise InvalidArguments(
					f"Paging past row {MULTI_SCOPE_WINDOW} is not available across several access scopes; add filters instead"
				)
			merged = {}
			for grant, listable in usable:
				for row in ctx.gateway.list_documents(
					doctype,
					query_fields,
					list(conditions) + list(grant.scope),
					search_conditions(info, listable, search),
					order_by_sql(order),
					0,
					start + limit + 1,
				):
					merged.setdefault(row["name"], row)
			rows = sort_rows(list(merged.values()), order)[start : start + limit + 1]

		has_more = len(rows) > limit
		data = [{k: row.get(k) for k in fields} for row in rows[:limit]]
		payload = {"doctype": doctype, "start": start, "limit": limit, "returned": len(data), "has_more": has_more, "data": data}
		return ToolOutcome(payload, summary=f"{len(data)} row(s)", document_type=doctype)


class CountDocumentsTool(DocumentTool):
	operation = READ

	def execute(self, args: dict, ctx: ToolContext) -> ToolOutcome:
		doctype = args["doctype"]
		grants, info = self.prepare(ctx, doctype)
		if info.issingle:
			raise InvalidArguments(f"{doctype} is a single (settings) DocType and has no rows to count")
		listables = [listable_fields(info, g.field_rule) for g in grants]
		conditions = normalize_filters(args.get("filters"))
		require_fields(conditions, set().union(*listables).__contains__)
		needed = {c[0] for c in conditions}
		usable = [g for g, listable in zip(grants, listables) if needed <= listable]
		if not usable:
			raise PolicyDenied(f"No single access rule of yours lets you filter on: {', '.join(sorted(needed))}")

		payload = {"doctype": doctype}
		unscoped = [g for g in usable if not g.scope]
		if unscoped or len(usable) == 1:
			scope = [] if unscoped else list(usable[0].scope)
			payload["count"] = ctx.gateway.count_documents(doctype, list(conditions) + scope, [])
		else:
			names, capped = set(), False
			for grant in usable:
				found = ctx.gateway.list_names(doctype, list(conditions) + list(grant.scope), MULTI_SCOPE_COUNT_CAP)
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
		grants, info = self.prepare(ctx, doctype)
		covering = self.in_scope(ctx, info, name, grants)
		doc = project_document(ctx.gateway.get_document(doctype, name), info, union_rules(covering))
		if info.has_workflow:
			doc["_workflow"] = ctx.gateway.workflow_actions(doctype, name)
		return ToolOutcome(doc, summary=f"{doctype} {name}", document_type=doctype, document_name=name)


def _reject_workflow_state(info: DoctypeInfo, data: Any) -> None:
	if info.has_workflow and isinstance(data, dict) and info.workflow_state_field in data:
		raise PolicyDenied(f"'{info.workflow_state_field}' is controlled by the workflow; use apply_workflow_action")


class CreateDocumentTool(DocumentTool):
	operation = CREATE

	def execute(self, args: dict, ctx: ToolContext) -> ToolOutcome:
		doctype = args["doctype"]
		grants, info = self.prepare(ctx, doctype)
		if info.issingle:
			raise InvalidArguments(f"{doctype} is a single (settings) DocType; use update_document instead")
		_reject_workflow_state(info, args["data"])
		accepted, data = self.accepting(info, grants, args["data"], creating=True)
		self.check_custom_field(ctx, info, accepted, data)
		doc = ctx.gateway.create_document(doctype, data)
		if not self.matching(ctx, info, doc["name"], accepted):
			raise PolicyDenied(f"The new {doctype} would fall outside your access scope; nothing was saved")
		return ToolOutcome(
			self.visible(ctx, info, doc), summary=f"Created {doctype} {doc['name']}", document_type=doctype, document_name=doc["name"]
		)


class UpdateDocumentTool(DocumentTool):
	operation = UPDATE

	def execute(self, args: dict, ctx: ToolContext) -> ToolOutcome:
		doctype, name = args["doctype"], args["name"]
		grants, info = self.prepare(ctx, doctype)
		_reject_workflow_state(info, args["data"])
		covering = self.in_scope(ctx, info, name, grants)
		accepted, data = self.accepting(info, covering, args["data"], creating=False)
		self.check_custom_field(ctx, info, accepted, data, name)
		doc = ctx.gateway.update_document(doctype, name, data)
		if not self.matching(ctx, info, name, accepted):
			raise PolicyDenied(f"This change would move {doctype} {name} outside your access scope; nothing was saved")
		return ToolOutcome(self.visible(ctx, info, doc), summary=f"Updated {doctype} {name}", document_type=doctype, document_name=name)


class DeleteDocumentTool(DocumentTool):
	operation = DELETE

	def execute(self, args: dict, ctx: ToolContext) -> ToolOutcome:
		doctype, name = args["doctype"], args["name"]
		grants, info = self.prepare(ctx, doctype)
		if info.issingle:
			raise InvalidArguments(f"{doctype} is a single (settings) DocType and cannot be deleted")
		self.in_scope(ctx, info, name, grants)
		ctx.gateway.delete_document(doctype, name)
		return ToolOutcome(
			{"deleted": True, "doctype": doctype, "name": name}, summary=f"Deleted {doctype} {name}", document_type=doctype, document_name=name
		)


class _DocstatusTool(DocumentTool):
	verb = ""

	def execute(self, args: dict, ctx: ToolContext) -> ToolOutcome:
		doctype, name = args["doctype"], args["name"]
		grants, info = self.prepare(ctx, doctype)
		if not info.is_submittable:
			raise InvalidArguments(f"{doctype} is not submittable")
		if info.has_workflow:
			raise InvalidArguments(f"{doctype} follows an approval workflow; use apply_workflow_action instead")
		self.in_scope(ctx, info, name, grants)
		doc = self.change(ctx, doctype, name)
		return ToolOutcome(self.visible(ctx, info, doc), summary=f"{self.verb} {doctype} {name}", document_type=doctype, document_name=name)


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
		grants, info = self.prepare(ctx, doctype)
		if not info.has_workflow:
			raise InvalidArguments(f"{doctype} has no active workflow")
		covering_profiles = {g.profile for g in self.in_scope(ctx, info, name, grants)}

		workflow = ctx.gateway.workflow_actions(doctype, name)
		transition = next((t for t in workflow.get("actions", []) if t.get("action") == action), None)
		if transition is None:
			available = ", ".join(t["action"] for t in workflow.get("actions", [])) or "none"
			raise InvalidArguments(f"Workflow action '{action}' is not available to you now. Available: {available}")
		current, target = workflow.get("docstatus", 0), transition.get("next_docstatus", 0)
		for needed, op in ((target == 1 and current == 0, SUBMIT), (target == 2 and current == 1, CANCEL)):
			# Submitting or cancelling through a workflow needs that grant too, in scope, from the
			# same profile that allows the update -- never pieced together from two profiles.
			if not needed:
				continue
			same_profile = [g for g in self.grants(ctx, doctype, op) if g.profile in covering_profiles]
			if not self.matching(ctx, info, name, same_profile):
				raise PolicyDenied(f"'{action}' would {op.lower()} {doctype} {name}, which your access profile does not allow")

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
		for report in ctx.policy.reports(ctx.tool):
			info = ctx.gateway.report_info(report)
			if info is None or not info.permitted:
				continue
			row = _report_payload_base(info)
			grant = ctx.policy.report_grant(report, ctx.tool)
			if grant.notes:
				row["notes"] = " ".join(grant.notes)
			if all(grant.fixed_filter_options):
				row["has_fixed_filters"] = True
			rows.append(row)
		return ToolOutcome({"count": len(rows), "reports": rows}, summary=f"{len(rows)} report(s)")


class _ReportTool:
	def prepare(self, ctx: ToolContext, report: str):
		grant = ctx.policy.report_grant(report, ctx.tool)
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
		elif info.filterable is not None:
			payload["filters_hint"] = (
				"Pass {fieldname: value} or {fieldname: [operator, value]} for these columns: "
				+ ", ".join(sorted(info.filterable))
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


def _validate_report_filters(filters: Any, filterable: Optional[set]) -> dict:
	if filters in (None, ""):
		return {}
	if not isinstance(filters, dict):
		raise InvalidArguments("filters must be an object keyed by filter fieldname")
	if len(filters) > 50:
		raise InvalidArguments("At most 50 report filters are allowed")
	scalar = (str, int, float, bool, type(None))
	for key, value in filters.items():
		check_identifier(key, "filter")
		if filterable is not None and key not in filterable:
			# Report Builder filters hit the base DocType directly: only the report's own columns
			# may be filtered, so hidden fields cannot be probed through them.
			raise InvalidArguments(f"This report can only be filtered on its columns: {', '.join(sorted(filterable))}")
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
		requested = _validate_report_filters(args.get("filters"), info.filterable)
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
