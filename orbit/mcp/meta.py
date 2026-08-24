# Copyright (c) 2026, Waleed AboHashima and Contributors
# License: MIT. See LICENSE
"""DocType metadata, and the reason this works on a customised site.

Every real Frappe deployment is customised — new DocTypes, custom fields on the
standard ones, renamed labels, altered list views. A server built by hardcoding "these
are the fields on a Sales Order" is correct on a fresh install and wrong on the second
site it meets, which is every site that matters.

Running inside the bench, this is nearly free: `frappe.get_meta` is already cached and
already merges Custom Fields and Property Setters into the picture. An out-of-process
client has to fetch the DocType record over HTTP and needs read permission on `DocType`
to do it — a permission a tightly-scoped agent user should not have. Being in-process
removes that whole problem, and it is the strongest argument for this variant of Orbit
over the standalone one.
"""

from __future__ import annotations

from typing import Any

import frappe

# Fields that exist to lay out a form and carry no data.
LAYOUT_FIELDTYPES = frozenset(
	{
		"Section Break",
		"Column Break",
		"Tab Break",
		"HTML",
		"Heading",
		"Button",
		"Fold",
		"Image",
		"Barcode",
	}
)

CONTAINER_FIELDTYPES = frozenset({"Table", "Table MultiSelect"})

# Present on every DocType. Worth naming once; not worth listing as a discovery.
STANDARD_FIELDS = ("name", "owner", "creation", "modified", "docstatus")


def data_fields(doctype: str) -> list[Any]:
	meta = frappe.get_meta(doctype)
	return [field for field in meta.fields if field.fieldtype not in LAYOUT_FIELDTYPES]


def child_table_fields(doctype: str) -> set[str]:
	return {
		field.fieldname
		for field in frappe.get_meta(doctype).fields
		if field.fieldtype in CONTAINER_FIELDTYPES
	}


def checkbox_fields(doctype: str) -> set[str]:
	return {
		field.fieldname
		for field in frappe.get_meta(doctype).fields
		if field.fieldtype == "Check"
	}


def default_fields(doctype: str) -> list[str]:
	"""The fields to fetch when the agent did not choose.

	The site's own list view, plus the identifiers a follow-up call needs. Capped,
	because a few DocTypes put a dozen columns in their list view and the point of this
	function is restraint. `*` on a Sales Order is four thousand tokens; the list view
	is two hundred, and it is what the people who use this site chose to see.
	"""
	meta = frappe.get_meta(doctype)

	chosen = ["name"]
	for field in meta.fields:
		if field.in_list_view and field.fieldtype not in CONTAINER_FIELDTYPES:
			if field.fieldtype in LAYOUT_FIELDTYPES:
				continue
			chosen.append(field.fieldname)

	names = {field.fieldname for field in meta.fields}
	if "status" in names and "status" not in chosen:
		chosen.append("status")
	for candidate in ("title", "subject"):
		if candidate in names and candidate not in chosen:
			chosen.append(candidate)
			break
	if meta.is_submittable:
		chosen.append("docstatus")
	chosen.append("modified")

	seen: list[str] = []
	for field in chosen:
		if field not in seen:
			seen.append(field)
	return seen[:12]


def render_meta(doctype: str, verbose: bool = False) -> str:
	"""The schema, written for an agent that is about to build a filter.

	Ordered by what a caller needs: what the DocType is, what is mandatory to create
	one, what the site shows in its list, and what everything else is called. Link
	targets and Select choices are inlined, because "which DocType does this point at"
	and "what values are valid here" are the two questions that otherwise cost a
	round trip each.
	"""
	meta = frappe.get_meta(doctype)
	fields = data_fields(doctype)

	traits = []
	if meta.module:
		traits.append(f"module {meta.module}")
	if meta.is_submittable:
		traits.append("submittable (draft -> submitted -> cancelled)")
	if meta.issingle:
		traits.append("single (one record, no list)")
	if meta.istable:
		traits.append("child table (reached through its parent)")

	def describe(field: Any) -> str:
		if field.fieldtype in ("Link", "Table", "Table MultiSelect") and field.options:
			kind = f"{field.fieldtype} -> {field.options}"
		elif field.fieldtype == "Select" and field.options:
			choices = " | ".join(part for part in str(field.options).split("\n") if part)
			kind = f"Select [{choices}]"
		else:
			kind = field.fieldtype

		flags = []
		if field.reqd:
			flags.append("required")
		if field.read_only:
			flags.append("read-only")
		suffix = f" - {', '.join(flags)}" if flags else ""
		return f"  {field.fieldname} ({kind}){suffix}"

	required = [field for field in fields if field.reqd]
	listed = [field for field in fields if field.in_list_view and not field.reqd]
	rest = [field for field in fields if not field.reqd and not field.in_list_view]

	sections = [
		f"{doctype}" + (f" - {', '.join(traits)}" if traits else ""),
		f"{len(fields)} data fields. Standard on every DocType: {', '.join(STANDARD_FIELDS)}.",
	]

	if required:
		sections.append("Required to create:\n" + "\n".join(describe(f) for f in required))
	if listed:
		sections.append(
			"Shown in the list view (Orbit's default fields):\n"
			+ "\n".join(describe(f) for f in listed)
		)
	if rest:
		if verbose:
			sections.append("Other fields:\n" + "\n".join(describe(f) for f in rest))
		else:
			sections.append(
				f"Other fields ({len(rest)}): "
				+ ", ".join(field.fieldname for field in rest)
				+ "\n  Pass verbose: true for their types and link targets."
			)

	return "\n\n".join(sections)
