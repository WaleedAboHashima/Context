# Copyright (c) 2026, Waleed AboHashima and Contributors
# License: MIT. See LICENSE
"""The tool surface.

Two decisions here are worth more than the rest of the file.

**DocType is a parameter, not a tool.** A Frappe site has between four hundred and two
thousand DocTypes. Generating a tool per DocType — the obvious move, and what several
database MCP servers do — produces a `tools/list` response large enough to displace the
conversation that needed it. Twelve tools that take a `doctype` argument cover the same
ground at a fixed cost, and `search_doctypes` plus `describe_doctype` are how the agent
finds its way instead.

**The advertised toolset follows the settings.** With writes disabled, the write tools
are absent from `tools/list` rather than present and failing. An agent cannot be tempted
by a tool it cannot see, does not spend a turn discovering the refusal, and the list
stays short for the read-only deployments that are the common case.

Every read goes through `frappe.get_list` and every write through the document API, so
Frappe's permissions, user permissions and match conditions apply exactly as they do in
the desk. `frappe.get_all` appears once, for enumerating DocTypes, and its results are
filtered by `has_permission` immediately afterwards — it bypasses permissions by design
and must never be used on business data.
"""

from __future__ import annotations

from typing import Any, Callable

import frappe
from frappe import _

from . import meta as meta_module
from .policy import Policy
from .render import render_document, render_pagination, render_rows

OPERATORS = [
	"=",
	"!=",
	">",
	"<",
	">=",
	"<=",
	"like",
	"not like",
	"in",
	"not in",
	"between",
	"is",
]

# Filters take a named form rather than Frappe's positional triples, because models
# emit named objects far more reliably than nested arrays, and the translation is
# four lines.
FILTER_SCHEMA = {
	"type": "array",
	"description": "Conditions, combined with AND.",
	"items": {
		"type": "object",
		"properties": {
			"field": {"type": "string", "description": 'Fieldname, e.g. "status".'},
			"operator": {
				"type": "string",
				"enum": OPERATORS,
				"default": "=",
				"description": (
					'"like" takes SQL wildcards ("%steel%"). "in"/"not in" take an array. '
					'"between" takes a two-element array. "is" takes "set" or "not set".'
				),
			},
			"value": {"description": "The value to compare against."},
		},
		"required": ["field", "value"],
	},
}

DOCTYPE_SCHEMA = {"type": "string", "description": 'Exact DocType name, e.g. "Sales Order".'}


def _filters(raw: Any) -> list[list[Any]] | None:
	"""Translate the named form into what the query builder takes."""
	if not raw:
		return None
	converted: list[list[Any]] = []
	for entry in raw:
		if not isinstance(entry, dict) or "field" not in entry:
			frappe.throw(
				_('Each filter must be an object with "field", "operator" and "value".'),
				frappe.ValidationError,
			)
		converted.append([entry["field"], entry.get("operator") or "=", entry.get("value")])
	return converted


def _permitted_count(doctype: str, filters: list[list[Any]] | None) -> int | None:
	"""A count that respects permissions.

	`frappe.db.count` would be shorter and would ignore user permissions and match
	conditions, which on a site where a salesperson only sees their own territory is the
	difference between a true answer and a leak. Aggregating through `get_list` keeps the
	same restrictions the list itself is under.

	The aggregate has to be expressed as `{"COUNT": "*"}`; the string form
	`"count(name) as total"` is rejected outright by the query builder as an injection
	risk. The returned key is read positionally rather than by name, because it is the
	SQL the builder emitted (`COUNT(*)`) and not a name this code chose.

	Returns None only when the DocType genuinely cannot be counted — a single, or a
	virtual DocType with no table behind it. A real failure is raised, because a count
	that quietly reports "not countable" when the query was simply wrong is worse than
	no count at all: it reads as a fact about the DocType.
	"""
	meta = frappe.get_meta(doctype)
	if meta.issingle:
		return None

	rows = frappe.get_list(doctype, filters=filters or [], fields=[{"COUNT": "*"}])
	if not rows:
		return 0

	first = rows[0]
	values = list(first.values()) if hasattr(first, "values") else []
	return int(values[0]) if values else 0


# ---------------------------------------------------------------------------
# Handlers. Each returns the text the model will read.
# ---------------------------------------------------------------------------


def _whoami(policy: Policy, args: dict[str, Any]) -> str:
	site = frappe.local.site
	return f"Connected to {site} over Orbit.\n\n{policy.describe()}"


def _search_doctypes(policy: Policy, args: dict[str, Any]) -> str:
	filters: dict[str, Any] = {}
	if args.get("keyword"):
		filters["name"] = ["like", f"%{args['keyword']}%"]
	if args.get("module"):
		filters["module"] = args["module"]

	# `get_all` bypasses permissions, so the result is filtered immediately below.
	# Enumerating first and filtering after is deliberate: `get_list` on DocType
	# requires read permission on DocType, which a well-scoped agent user will not
	# have, and would report "no DocTypes" on a site full of them.
	candidates = frappe.get_all(
		"DocType",
		filters=filters,
		fields=["name", "module", "is_submittable", "issingle", "istable"],
		order_by="name asc",
		limit_page_length=0,
	)

	limit = policy.clamp_limit(args.get("limit") or 40)
	rows = []
	for row in candidates:
		if len(rows) >= limit:
			break
		if not frappe.has_permission(row["name"], "read"):
			continue
		rows.append(row)

	if not rows:
		keyword = args.get("keyword")
		subject = f' "{keyword}"' if keyword else ""
		return (
			f"No DocType this user can read matches{subject}. "
			"Try a shorter keyword, or omit it to see what is available."
		)

	return (
		f"{len(rows)} DocTypes this user can read.\n\n"
		+ render_rows(rows)
		+ "\n\nis_submittable=1 means documents move through draft, submitted and cancelled. "
		"istable=1 means the DocType is a child table, reached through its parent rather than directly."
	)


def _describe_doctype(policy: Policy, args: dict[str, Any]) -> str:
	doctype = args["doctype"]
	policy.assert_in_scope(doctype)
	if not frappe.has_permission(doctype, "read"):
		frappe.throw(_("Not permitted to read {0}.").format(doctype), frappe.PermissionError)
	return meta_module.render_meta(doctype, verbose=bool(args.get("verbose")))


def _list_documents(policy: Policy, args: dict[str, Any]) -> str:
	doctype = args["doctype"]
	policy.assert_in_scope(doctype)

	filters = _filters(args.get("filters"))
	fields = args.get("fields") or meta_module.default_fields(doctype)
	limit = policy.clamp_limit(args.get("limit"))
	start = max(int(args.get("start") or 0), 0)

	rows = frappe.get_list(
		doctype,
		filters=filters or [],
		fields=fields,
		order_by=args.get("order_by") or "modified desc",
		limit_page_length=limit,
		limit_start=start,
	)

	if not rows:
		hint = (
			"Check the filter values - frappe_describe_doctype lists the valid options for Select fields."
			if filters
			else f"There may be none, or this user may not be permitted to read {doctype}."
		)
		return f"No {doctype} documents match. {hint}"

	# The total is only worth a query when it can change the reader's conclusion: a
	# partial page is by definition the last one.
	# A full page might not be the last one, so the true total is worth a query. A
	# partial page is by definition the end, and the total is arithmetic.
	if len(rows) == limit:
		try:
			total = _permitted_count(doctype, filters)
		except Exception:
			# An unknown total is reported as unknown. Guessing one would turn a first
			# page into a complete answer in the reader's mind.
			total = None
	else:
		total = start + len(rows)

	return (
		render_pagination(len(rows), start, total)
		+ "\n\n"
		+ render_rows([dict(row) for row in rows], checkboxes=meta_module.checkbox_fields(doctype))
	)


def _count_documents(policy: Policy, args: dict[str, Any]) -> str:
	doctype = args["doctype"]
	policy.assert_in_scope(doctype)

	total = _permitted_count(doctype, _filters(args.get("filters")))
	if total is None:
		return (
			f"{doctype} is a single DocType - there is one record, not a countable list. "
			f"Use frappe_get_document with name: \"{doctype}\"."
		)
	return f"{total} {doctype} document{'' if total == 1 else 's'} match."


def _get_document(policy: Policy, args: dict[str, Any]) -> str:
	doctype, name = args["doctype"], args["name"]
	policy.assert_in_scope(doctype)

	doc = frappe.get_doc(doctype, name)
	# `get_doc` does not check read permission on its own. This is the line that makes
	# the difference between an agent seeing its own records and seeing everyone's.
	doc.check_permission("read")

	return f"{doctype} {name}\n\n" + render_document(
		doc.as_dict(),
		child_tables=meta_module.child_table_fields(doctype),
		checkboxes=meta_module.checkbox_fields(doctype),
		verbose=bool(args.get("verbose")),
		child_row_limit=int(args.get("child_rows") or 5),
		child_fields=meta_module.child_grid_fields(doctype),
	)


def _run_report(policy: Policy, args: dict[str, Any]) -> str:
	from frappe.desk.query_report import run as run_report

	report_name = args["report_name"]
	filters = args.get("filters") or {}

	# `run` performs its own permission check against the Report and its ref DocType.
	# `ignore_prepared_report` is set because a prepared report returns a job id and
	# emails the result later, which is useless to an agent waiting inside a tool call.
	result = run_report(report_name=report_name, filters=filters, ignore_prepared_report=True)

	columns = result.get("columns") or []
	rows = result.get("result") or []

	if not rows:
		return (
			f"{report_name} returned no rows. Its filters are usually mandatory - "
			f"company and a date range at minimum. Columns: {_column_names(columns)}."
		)

	limit = policy.clamp_limit(args.get("limit"))
	shown = rows[:limit]
	names = _column_names(columns).split(", ")

	normalized = []
	for row in shown:
		if isinstance(row, dict):
			normalized.append(row)
		elif isinstance(row, (list, tuple)):
			normalized.append(
				{names[index] if index < len(names) else f"col_{index}": cell for index, cell in enumerate(row)}
			)

	suffix = f", showing the first {len(shown)}" if len(rows) > len(shown) else ""
	return (
		f"{report_name}: {len(rows)} row{'' if len(rows) == 1 else 's'}{suffix}.\n\n"
		+ render_rows(normalized)
	)


def _column_names(columns: list[Any]) -> str:
	names = []
	for column in columns:
		if isinstance(column, str):
			names.append(column.split(":")[0])
		elif isinstance(column, dict):
			names.append(str(column.get("label") or column.get("fieldname") or "?"))
		else:
			names.append(str(getattr(column, "label", "?")))
	return ", ".join(names) if names else "(none reported)"


def _create_document(policy: Policy, args: dict[str, Any]) -> str:
	doctype = args["doctype"]
	policy.assert_writable(doctype)

	payload = dict(args.get("doc") or {})
	payload["doctype"] = doctype

	doc = frappe.get_doc(payload)
	doc.insert()  # checks create permission, runs validations and mandatory checks

	return f"Created {doctype} {doc.name} as a draft.\n\n" + render_document(
		doc.as_dict(),
		child_tables=meta_module.child_table_fields(doctype),
		child_fields=meta_module.child_grid_fields(doctype),
	)


def _update_document(policy: Policy, args: dict[str, Any]) -> str:
	doctype, name = args["doctype"], args["name"]
	policy.assert_writable(doctype)

	patch = dict(args.get("patch") or {})
	if not patch:
		frappe.throw(_("Nothing to update - patch was empty."), frappe.ValidationError)

	doc = frappe.get_doc(doctype, name)
	doc.check_permission("write")
	doc.update(patch)
	doc.save()

	return (
		f"Updated {doctype} {name}. Changed: {', '.join(sorted(patch))}.\n\n"
		+ render_document(
			doc.as_dict(),
			child_tables=meta_module.child_table_fields(doctype),
			child_fields=meta_module.child_grid_fields(doctype),
		)
	)


def _submit_document(policy: Policy, args: dict[str, Any]) -> str:
	doctype, name = args["doctype"], args["name"]
	policy.assert_submittable(doctype)

	doc = frappe.get_doc(doctype, name)
	doc.check_permission("submit")
	doc.submit()  # runs on_submit: this is what posts the ledger and moves stock

	return f"Submitted {doctype} {name}. Its ledger and stock effects are now posted."


def _cancel_document(policy: Policy, args: dict[str, Any]) -> str:
	doctype, name = args["doctype"], args["name"]
	policy.assert_submittable(doctype)

	doc = frappe.get_doc(doctype, name)
	doc.check_permission("cancel")
	doc.cancel()

	return f"Cancelled {doctype} {name}. Its effects have been reversed."


def _delete_document(policy: Policy, args: dict[str, Any]) -> str:
	doctype, name = args["doctype"], args["name"]
	policy.assert_deletable(doctype)

	frappe.delete_doc(doctype, name)  # checks delete permission and link integrity
	return f"Deleted {doctype} {name}."


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------


class Tool:
	def __init__(
		self,
		name: str,
		title: str,
		description: str,
		schema: dict[str, Any],
		handler: Callable[[Policy, dict[str, Any]], str],
		annotations: dict[str, Any],
		requires: str | None = None,
	) -> None:
		self.name = name
		self.title = title
		self.description = description
		self.schema = schema
		self.handler = handler
		self.annotations = annotations
		# Which setting has to be on for this tool to be advertised at all.
		self.requires = requires

	def as_json(self) -> dict[str, Any]:
		return {
			"name": self.name,
			"title": self.title,
			"description": self.description,
			"inputSchema": self.schema,
			"annotations": self.annotations,
		}


READ_ONLY = {"readOnlyHint": True, "idempotentHint": True}
MUTATING = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False}
DESTRUCTIVE = {"readOnlyHint": False, "destructiveHint": True, "idempotentHint": False}

TOOLS: list[Tool] = [
	Tool(
		"frappe_whoami",
		"Check the Orbit connection",
		"Report which Frappe user Orbit is acting as and what it is permitted to do. "
		"Call this first if anything is failing - it separates a connection problem from "
		"a permission one.",
		{"type": "object", "properties": {}},
		_whoami,
		READ_ONLY,
	),
	Tool(
		"frappe_search_doctypes",
		"Find DocTypes",
		"Search the site's DocTypes (its record types) by keyword, showing only those this "
		"user can read. Use this before anything else when you do not already know the exact "
		"DocType name - a site has hundreds, including custom ones, and names must be exact.",
		{
			"type": "object",
			"properties": {
				"keyword": {"type": "string", "description": 'Substring of the name, e.g. "invoice".'},
				"module": {"type": "string", "description": 'Restrict to one module, e.g. "Selling".'},
				"limit": {"type": "integer"},
			},
		},
		_search_doctypes,
		READ_ONLY,
	),
	Tool(
		"frappe_describe_doctype",
		"Describe a DocType",
		"Return the fields of a DocType: names, types, what Link fields point at, which are "
		"required, and which the site shows in its list view. Call this before writing a filter "
		"or creating a document - field names on a customised site are not guessable.",
		{
			"type": "object",
			"properties": {
				"doctype": DOCTYPE_SCHEMA,
				"verbose": {"type": "boolean", "description": "Include every field's type, not just a summary."},
			},
			"required": ["doctype"],
		},
		_describe_doctype,
		READ_ONLY,
	),
	Tool(
		"frappe_list_documents",
		"List documents",
		"List documents of one DocType with filters, sorting and paging. When you do not name "
		"fields, Orbit returns the ones the site itself shows in its list view - ask for more by "
		"name rather than requesting everything. The result states how many rows exist in total, "
		"so a first page is distinguishable from a complete answer.",
		{
			"type": "object",
			"properties": {
				"doctype": DOCTYPE_SCHEMA,
				"filters": FILTER_SCHEMA,
				"fields": {
					"type": "array",
					"items": {"type": "string"},
					"description": "Fieldnames to return. Omit to use the site's list-view fields.",
				},
				"order_by": {"type": "string", "description": 'e.g. "modified desc".'},
				"limit": {"type": "integer"},
				"start": {"type": "integer", "description": "Offset, for paging."},
			},
			"required": ["doctype"],
		},
		_list_documents,
		READ_ONLY,
	),
	Tool(
		"frappe_count_documents",
		"Count documents",
		"Count documents matching a filter without fetching them. Use this for 'how many' "
		"questions instead of listing and counting rows.",
		{
			"type": "object",
			"properties": {"doctype": DOCTYPE_SCHEMA, "filters": FILTER_SCHEMA},
			"required": ["doctype"],
		},
		_count_documents,
		READ_ONLY,
	),
	Tool(
		"frappe_get_document",
		"Get one document",
		"Fetch a single document by name, including its child tables. Empty and framework fields "
		"are omitted and the count of what was hidden is stated; child tables are summarised with "
		"their first rows. Pass verbose only when a field you expected is missing.",
		{
			"type": "object",
			"properties": {
				"doctype": DOCTYPE_SCHEMA,
				"name": {"type": "string", "description": 'The document name, e.g. "SO-00042".'},
				"verbose": {"type": "boolean"},
				"child_rows": {"type": "integer", "description": "Rows of each child table to show. Default 5."},
			},
			"required": ["doctype", "name"],
		},
		_get_document,
		READ_ONLY,
	),
	Tool(
		"frappe_run_report",
		"Run a report",
		"Run a Frappe or ERPNext report by name and return its rows - Stock Balance, Accounts "
		"Receivable, General Ledger and so on. Prefer this over reconstructing a report from raw "
		"documents: the report already contains the site's own logic, and one call replaces many.",
		{
			"type": "object",
			"properties": {
				"report_name": {"type": "string", "description": 'Exact report name, e.g. "Stock Balance".'},
				"filters": {
					"type": "object",
					"description": 'Report filters, e.g. {"company": "ACME", "from_date": "2026-01-01"}.',
				},
				"limit": {"type": "integer"},
			},
			"required": ["report_name"],
		},
		_run_report,
		READ_ONLY,
	),
	Tool(
		"frappe_create_document",
		"Create a document",
		"Create a new document. It is saved as a draft - creating never submits. Call "
		"frappe_describe_doctype first: required fields and link targets are site-specific, and a "
		"missing mandatory field is the most common failure.",
		{
			"type": "object",
			"properties": {
				"doctype": DOCTYPE_SCHEMA,
				"doc": {"type": "object", "description": "Field values. Child tables are arrays of row objects."},
			},
			"required": ["doctype", "doc"],
		},
		_create_document,
		MUTATING,
		requires="allow_write",
	),
	Tool(
		"frappe_update_document",
		"Update a document",
		"Change fields on an existing document. Send only the fields being changed. A submitted "
		"document will refuse most changes - that is Frappe protecting a posted record, not an "
		"Orbit restriction.",
		{
			"type": "object",
			"properties": {
				"doctype": DOCTYPE_SCHEMA,
				"name": {"type": "string"},
				"patch": {"type": "object", "description": "Only the fields to change."},
			},
			"required": ["doctype", "name", "patch"],
		},
		_update_document,
		{"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True},
		requires="allow_write",
	),
	Tool(
		"frappe_submit_document",
		"Submit a document",
		"Submit a draft. This is not a save - it runs the document's on_submit, which posts "
		"ledger entries, moves stock and locks the record. Confirm with the user before calling "
		"it on anything financial.",
		{
			"type": "object",
			"properties": {"doctype": DOCTYPE_SCHEMA, "name": {"type": "string"}},
			"required": ["doctype", "name"],
		},
		_submit_document,
		DESTRUCTIVE,
		requires="allow_submit",
	),
	Tool(
		"frappe_cancel_document",
		"Cancel a submitted document",
		"Cancel a submitted document, reversing its ledger and stock effects. The cancellation is "
		"permanent and visible in the record's history - it is not a delete and not an undo.",
		{
			"type": "object",
			"properties": {"doctype": DOCTYPE_SCHEMA, "name": {"type": "string"}},
			"required": ["doctype", "name"],
		},
		_cancel_document,
		DESTRUCTIVE,
		requires="allow_submit",
	),
	Tool(
		"frappe_delete_document",
		"Delete a document",
		"Permanently delete a document. There is no undo. Frappe will refuse if anything links to "
		"it, which is usually the right answer - prefer cancelling a submitted document over "
		"deleting it.",
		{
			"type": "object",
			"properties": {"doctype": DOCTYPE_SCHEMA, "name": {"type": "string"}},
			"required": ["doctype", "name"],
		},
		_delete_document,
		{"readOnlyHint": False, "destructiveHint": True, "idempotentHint": True},
		requires="allow_delete",
	),
]

BY_NAME = {tool.name: tool for tool in TOOLS}


def available(policy: Policy) -> list[Tool]:
	"""The tools this site advertises, which is a function of its settings."""
	return [tool for tool in TOOLS if tool.requires is None or getattr(policy, tool.requires)]
