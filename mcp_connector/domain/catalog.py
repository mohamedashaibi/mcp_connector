"""The built-in MCP tools ("standard actions") and their argument schemas.

Schemas are built per caller: the `doctype` and `report_name` arguments carry
an `enum` of exactly what that caller's profiles allow, so the model sees its
boundaries up front instead of discovering them through errors.
"""

from dataclasses import dataclass
from typing import Callable, Optional

from .action_types import CANCEL, CREATE, DELETE, READ, REPORT, SUBMIT, UPDATE

ANY_OPERATION = "*"  # enum lists every doctype with at least one granted operation


@dataclass(frozen=True)
class SchemaContext:
	doctypes: tuple = ()  # doctypes allowed for this tool's operation
	reports: tuple = ()
	default_page_size: int = 20
	max_page_size: int = 100
	max_report_rows: int = 200


@dataclass(frozen=True)
class ToolDefinition:
	name: str
	action_type: str
	title: str
	description: str
	build_schema: Callable[[SchemaContext], dict]
	operation: Optional[str] = None  # doctype operation whose doctypes feed the enum
	needs_reports: bool = False
	read_only: bool = False
	destructive: bool = False
	idempotent: bool = False


def _doctype_arg(ctx: SchemaContext, description: str = "DocType name") -> dict:
	return {"type": "string", "description": description, "enum": list(ctx.doctypes)}


def _name_arg() -> dict:
	return {"type": "string", "description": "Document name (ID), e.g. SINV-00012", "minLength": 1, "maxLength": 140}


def _obj(properties: dict, required: tuple = ()) -> dict:
	schema = {"type": "object", "properties": properties, "additionalProperties": False}
	if required:
		schema["required"] = list(required)
	return schema


FILTERS_DESCRIPTION = (
	"Filters combined with AND. Either an object {\"status\": \"Open\", \"grand_total\": [\">\", 100]} "
	"or a list [[\"posting_date\", \"between\", [\"2024-01-01\", \"2024-01-31\"]]]. "
	"Operators: =, !=, >, <, >=, <=, like, not like, in, not in, is (\"set\"/\"not set\"), between."
)


def _list_documents_schema(ctx: SchemaContext) -> dict:
	return _obj(
		{
			"doctype": _doctype_arg(ctx),
			"fields": {
				"type": "array",
				"items": {"type": "string"},
				"description": "Columns to return. Defaults to name, title and list-view fields. Child-table fields are not supported here; use get_document.",
			},
			"filters": {"type": ["object", "array"], "description": FILTERS_DESCRIPTION},
			"search": {
				"type": "string",
				"maxLength": 140,
				"description": "Free-text search across the name, title and search fields (substring match).",
			},
			"order_by": {
				"type": "string",
				"description": "Sort, e.g. \"modified desc\" or \"posting_date desc, name asc\" (max 3 keys).",
			},
			"limit": {
				"type": "integer",
				"minimum": 1,
				"maximum": ctx.max_page_size,
				"description": f"Rows per page (default {ctx.default_page_size}, max {ctx.max_page_size}).",
			},
			"start": {"type": "integer", "minimum": 0, "description": "Row offset for paging (default 0)."},
		},
		("doctype",),
	)


def _doctype_only_schema(ctx: SchemaContext) -> dict:
	return _obj({"doctype": _doctype_arg(ctx)}, ("doctype",))


def _doc_ref_schema(ctx: SchemaContext) -> dict:
	return _obj({"doctype": _doctype_arg(ctx), "name": _name_arg()}, ("doctype", "name"))


def _count_schema(ctx: SchemaContext) -> dict:
	return _obj(
		{"doctype": _doctype_arg(ctx), "filters": {"type": ["object", "array"], "description": FILTERS_DESCRIPTION}},
		("doctype",),
	)


def _create_schema(ctx: SchemaContext) -> dict:
	return _obj(
		{
			"doctype": _doctype_arg(ctx),
			"data": {
				"type": "object",
				"description": "Field values keyed by fieldname. Child tables are lists of row objects, e.g. {\"items\": [{\"item_code\": \"X\", \"qty\": 2}]}. Call describe_doctype first to learn the fields.",
			},
		},
		("doctype", "data"),
	)


def _update_schema(ctx: SchemaContext) -> dict:
	return _obj(
		{
			"doctype": _doctype_arg(ctx),
			"name": _name_arg(),
			"data": {
				"type": "object",
				"description": "Fields to change. A child table given here becomes the complete table: rows with an existing row \"name\" update that row (other fields kept), rows without \"name\" are added, and existing rows you leave out are removed. Read-only child fields are ignored.",
			},
		},
		("doctype", "name", "data"),
	)


def _workflow_schema(ctx: SchemaContext) -> dict:
	return _obj(
		{
			"doctype": _doctype_arg(ctx),
			"name": _name_arg(),
			"action": {
				"type": "string",
				"minLength": 1,
				"maxLength": 140,
				"description": "Workflow action label exactly as listed in the document's _workflow.actions (see get_document).",
			},
		},
		("doctype", "name", "action"),
	)


def _no_args_schema(ctx: SchemaContext) -> dict:
	return _obj({})


def _report_arg(ctx: SchemaContext) -> dict:
	return {"type": "string", "description": "Report name", "enum": list(ctx.reports)}


def _describe_report_schema(ctx: SchemaContext) -> dict:
	return _obj({"report_name": _report_arg(ctx)}, ("report_name",))


def _run_report_schema(ctx: SchemaContext) -> dict:
	return _obj(
		{
			"report_name": _report_arg(ctx),
			"filters": {
				"type": "object",
				"description": "Report filters keyed by fieldname, e.g. {\"company\": \"ACME\", \"from_date\": \"2024-01-01\"}. Call describe_report to see them. Some values may be fixed by the administrator and will be overridden.",
			},
			"limit": {
				"type": "integer",
				"minimum": 1,
				"maximum": ctx.max_report_rows,
				"description": f"Maximum rows to return (default and max {ctx.max_report_rows}).",
			},
		},
		("report_name",),
	)


BUILTIN_TOOLS = (
	ToolDefinition(
		name="list_doctypes",
		action_type=READ,
		title="List available DocTypes",
		description="List the ERPNext DocTypes (record types) you can work with and which operations you may perform on each. Start here when unsure which DocType holds some data.",
		build_schema=_no_args_schema,
		read_only=True,
		idempotent=True,
	),
	ToolDefinition(
		name="describe_doctype",
		action_type=READ,
		title="Describe a DocType",
		description="Show the fields of a DocType (fieldname, label, type, options, mandatory), its child tables, naming and whether it is submittable. Use it before filtering, creating or updating records.",
		build_schema=_doctype_only_schema,
		operation=ANY_OPERATION,
		read_only=True,
		idempotent=True,
	),
	ToolDefinition(
		name="list_documents",
		action_type=READ,
		title="List documents",
		description="Query records of a DocType with filters, free-text search, sorting and paging. Returns at most the configured page size; use start to page and has_more to know when to stop.",
		build_schema=_list_documents_schema,
		operation=READ,
		read_only=True,
		idempotent=True,
	),
	ToolDefinition(
		name="get_document",
		action_type=READ,
		title="Get a document",
		description="Fetch one full record including its child tables. When the DocType has a workflow, the response lists the actions you may apply in _workflow.",
		build_schema=_doc_ref_schema,
		operation=READ,
		read_only=True,
		idempotent=True,
	),
	ToolDefinition(
		name="count_documents",
		action_type=READ,
		title="Count documents",
		description="Count records of a DocType matching optional filters.",
		build_schema=_count_schema,
		operation=READ,
		read_only=True,
		idempotent=True,
	),
	ToolDefinition(
		name="create_document",
		action_type=CREATE,
		title="Create a document",
		description="Create a new record (saved as Draft for submittable DocTypes). ERPNext validations run as for a user saving the form.",
		build_schema=_create_schema,
		operation=CREATE,
	),
	ToolDefinition(
		name="update_document",
		action_type=UPDATE,
		title="Update a document",
		description="Change fields of an existing record and save it. Submitted documents only accept fields marked 'allow on submit'.",
		build_schema=_update_schema,
		operation=UPDATE,
	),
	ToolDefinition(
		name="apply_workflow_action",
		action_type=UPDATE,
		title="Apply a workflow action",
		description="Move a document through its approval workflow (e.g. Approve, Reject). Only actions your role may take are accepted.",
		build_schema=_workflow_schema,
		operation=UPDATE,
	),
	ToolDefinition(
		name="delete_document",
		action_type=DELETE,
		title="Delete a document",
		description="Permanently delete a record. Submitted documents must be cancelled first. This cannot be undone.",
		build_schema=_doc_ref_schema,
		operation=DELETE,
		destructive=True,
		idempotent=True,
	),
	ToolDefinition(
		name="submit_document",
		action_type=SUBMIT,
		title="Submit a document",
		description="Submit a Draft document (docstatus 0 → 1), posting its accounting/stock effects. Submitted documents can no longer be edited.",
		build_schema=_doc_ref_schema,
		operation=SUBMIT,
	),
	ToolDefinition(
		name="cancel_document",
		action_type=CANCEL,
		title="Cancel a document",
		description="Cancel a submitted document (docstatus 1 → 2), reversing its effects.",
		build_schema=_doc_ref_schema,
		operation=CANCEL,
		destructive=True,
	),
	ToolDefinition(
		name="list_reports",
		action_type=REPORT,
		title="List available reports",
		description="List the reports you may run, with their type and notes from the administrator.",
		build_schema=_no_args_schema,
		needs_reports=True,
		read_only=True,
		idempotent=True,
	),
	ToolDefinition(
		name="describe_report",
		action_type=REPORT,
		title="Describe a report",
		description="Show a report's filters (fieldname, type, mandatory) and any values fixed by the administrator. Call before run_report.",
		build_schema=_describe_report_schema,
		needs_reports=True,
		read_only=True,
		idempotent=True,
	),
	ToolDefinition(
		name="run_report",
		action_type=REPORT,
		title="Run a report",
		description="Run a report with filters and get its columns and rows (truncated to the row limit; check truncated/total_rows).",
		build_schema=_run_report_schema,
		needs_reports=True,
		read_only=True,
		idempotent=True,
	),
)

BUILTIN_BY_NAME = {tool.name: tool for tool in BUILTIN_TOOLS}
