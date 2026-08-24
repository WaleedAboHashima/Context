# Copyright (c) 2026, Waleed AboHashima and Contributors
# License: MIT. See LICENSE
"""Rendering, and the reason Context exists.

A Sales Order in ERPNext carries upwards of two hundred fields. Almost all of them
are null, zero, or framework bookkeeping, and a plain `frappe.get_doc(...).as_dict()`
serialises every one of them — three to five thousand tokens to say that a customer
ordered fourteen items. An agent that reads three such documents has spent more of its
context on punctuation and nulls than on the work it was asked to do.

Three rules, in order of how much they save:

1. **Lists render as a table, not as JSON.** Field names are written once in a header
   instead of once per row. On twenty rows of six fields that is most of the payload.
2. **Empty is omitted.** Null, empty string, empty list, and cleared checkboxes carry
   no information that "absent" does not.
3. **What was omitted is always stated.** This is the rule that makes the other two
   safe. A model told `34 empty fields omitted` knows the shape of what it cannot see
   and can ask for it; a model handed a silently trimmed document concludes the fields
   do not exist. Hiding without saying so is how a compact renderer starts producing
   confident wrong answers.

Nothing here is lossy in a way the caller cannot undo: every field is reachable by
naming it, or by asking for the document verbose. What is traded away is the default
of showing everything, which no reader wanted.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

# Framework plumbing, dropped from every rendering. Not "fields I judged
# uninteresting" — these are identical on every document and answer no question
# anyone asked. `modified` and `owner` survive, because "who touched this, and when"
# is a real question.
PLUMBING = frozenset(
	{
		"doctype",
		"idx",
		"parent",
		"parentfield",
		"parenttype",
		"modified_by",
		"naming_series",
		"_user_tags",
		"_comments",
		"_assign",
		"_liked_by",
		"_seen",
		"lft",
		"rgt",
		"old_parent",
	}
)

# Additional noise in a *child* row. In a parent document "who owns this and when was
# it changed" is a real question; on row 3 of an items table it is the same answer as
# the parent's, repeated per row, and `name` is a hash nobody will ever look up.
CHILD_PLUMBING = PLUMBING | frozenset({"name", "owner", "creation", "modified", "docstatus"})

# Long free text is truncated rather than dropped: the first line usually carries it.
MAX_TEXT = 280

# A backstop for when no preferred columns are known. A Sales Order Item has eighty
# fields; rendering all of them defeats the entire point of this module, and a reader
# who needs the eighty-first can ask for the child DocType by name.
MAX_TABLE_COLUMNS = 12

# Widest a padded column gets before it stops helping and starts costing.
MAX_COLUMN = 48

_TAG_RE = re.compile(r"<[^>]*>")


def _is_empty(value: Any) -> bool:
	if value is None:
		return True
	if isinstance(value, str):
		return value.strip() == ""
	if isinstance(value, (list, tuple, dict)):
		return len(value) == 0
	return False


def _is_cleared_checkbox(value: Any) -> bool:
	"""A checkbox that is off.

	Kept apart from `_is_empty` because the two disagree about zero on purpose: a zero
	outstanding amount is an answer, and an unticked box is not. Only a field the caller
	named as a checkbox is read this way.
	"""
	return value in (0, False, "0")


def _scalar(value: Any) -> str:
	"""One value, as short as it can be said."""
	if value is None:
		return ""
	if isinstance(value, bool):
		return "yes" if value else "no"
	if isinstance(value, str):
		text = re.sub(r"\s*\n\s*", " / ", value) if "\n" in value else value
		text = _TAG_RE.sub("", text) if "<" in text and ">" in text else text
		if len(text) > MAX_TEXT:
			return f"{text[:MAX_TEXT]}... (+{len(text) - MAX_TEXT} chars)"
		return text
	if isinstance(value, (list, dict)):
		return json.dumps(value, default=str)
	return str(value)


def _is_row_list(value: Any) -> bool:
	return isinstance(value, list) and len(value) > 0 and isinstance(value[0], dict)


def render_document(
	doc: dict[str, Any],
	child_tables: Iterable[str] = (),
	checkboxes: Iterable[str] = (),
	verbose: bool = False,
	child_row_limit: int = 5,
	child_fields: dict[str, list[str]] | None = None,
	child_checkboxes: dict[str, Iterable[str]] | None = None,
) -> str:
	"""One document as `field: value` lines.

	Chosen over JSON for single documents because at one field per line the braces and
	quoting are pure overhead, and a model reads the line form at least as reliably.
	"""
	child_tables = set(child_tables)
	checkboxes = set(checkboxes)
	child_fields = child_fields or {}
	child_checkboxes = child_checkboxes or {}

	lines: list[str] = []
	tables: list[str] = []
	omitted_empty = 0
	omitted_plumbing = 0

	for key, value in doc.items():
		if not verbose and key in PLUMBING:
			omitted_plumbing += 1
			continue

		if key in child_tables or _is_row_list(value):
			tables.append(
				_render_child_table(
					key,
					value,
					child_row_limit,
					# The *child* DocType's checkboxes. The parent's would be a different
					# DocType's fieldnames, right only where the two happen to collide.
					set(child_checkboxes.get(key, ())),
					child_fields.get(key),
					verbose,
				)
			)
			continue

		if not verbose:
			if _is_empty(value):
				omitted_empty += 1
				continue
			# A cleared checkbox is the same information as an absent one.
			if key in checkboxes and _is_cleared_checkbox(value):
				omitted_empty += 1
				continue

		lines.append(f"{key}: {_scalar(value)}")

	parts = ["\n".join(lines)] if lines else []
	if tables:
		parts.append("\n\n".join(tables))

	hidden = omitted_empty + omitted_plumbing
	if hidden:
		parts.append(
			f"({omitted_empty} empty and {omitted_plumbing} framework fields omitted "
			f"- pass verbose: true to see all {len(doc)})"
		)

	return "\n\n".join(part for part in parts if part)


def _render_child_table(
	fieldname: str,
	value: Any,
	limit: int,
	checkboxes: set[str],
	preferred: list[str] | None = None,
	verbose: bool = False,
) -> str:
	"""A child table as a count, then its first rows.

	The count comes first deliberately. "items: 47 rows" answers most questions about a
	child table on its own, and an agent that needs row 40 can ask for it — whereas 47
	expanded rows it did not ask for are 47 rows of context it cannot give back.
	"""
	if not isinstance(value, list) or not value:
		return f"{fieldname}: 0 rows"

	rows = [row for row in value if isinstance(row, dict)]
	shown = rows[:limit]
	plural = "" if len(rows) == 1 else "s"
	header = f"{fieldname}: {len(rows)} row{plural}"

	# The child DocType's own grid columns, when the caller could find them. That is
	# what the humans who use this site chose to see in the same table, which is a far
	# better answer than "all eighty fields" and costs nothing to obtain.
	body = render_rows(
		shown,
		checkboxes=checkboxes,
		preferred=preferred,
		plumbing=CHILD_PLUMBING,
		verbose=verbose,
	)
	indented = "\n".join(f"  {line}" for line in body.split("\n"))

	remaining = len(rows) - len(shown)
	more = (
		f"\n  ... {remaining} more row{'' if remaining == 1 else 's'}" if remaining else ""
	)
	return f"{header}\n{indented}{more}"


def render_rows(
	rows: list[dict[str, Any]],
	checkboxes: Iterable[str] = (),
	verbose: bool = False,
	preferred: list[str] | None = None,
	plumbing: frozenset[str] = PLUMBING,
) -> str:
	"""Rows as a pipe-delimited table.

	Columns are the union of the keys present, in first-seen order, minus the ones
	empty in *every* row — a column of nothing but blanks is a header nobody needed,
	and its absence is reported rather than assumed.
	"""
	if not rows:
		return "(no rows)"

	checkboxes = set(checkboxes)

	def blank(row: dict[str, Any], column: str) -> bool:
		"""Whether this cell carries information.

		A column of nothing but unticked checkboxes is as empty as a column of nulls -
		`is_return | 0` repeated down twenty rows is a header and a column of noise. It
		reads as populated to `_is_empty`, though, because 0 is a real number elsewhere,
		which is why the checkbox fieldnames have to be passed in and consulted here.
		"""
		value = row.get(column)
		if _is_empty(value):
			return True
		return column in checkboxes and _is_cleared_checkbox(value)

	columns: list[str] = []
	for row in rows:
		for key in row:
			if not verbose and key in plumbing:
				continue
			if key not in columns:
				columns.append(key)

	if verbose:
		useful = columns
	elif preferred:
		# The preferred columns the rows actually carry, in the order given, minus the
		# ones empty in every row. A grid column that is blank all the way down is a
		# header nobody needed, whether the grid chose it or the data did.
		useful = [
			column
			for column in preferred
			if column in columns and any(not blank(row, column) for row in rows)
		]
		# All preferred columns absent or blank would render an empty table, so fall
		# back to what the rows do carry rather than show nothing.
		if not useful:
			useful = [
				column
				for column in columns
				if any(not blank(row, column) for row in rows)
			] or columns
	else:
		useful = [
			column
			for column in columns
			if any(not blank(row, column) for row in rows)
		]

	capped = 0
	if not verbose and len(useful) > MAX_TABLE_COLUMNS:
		capped = len(useful) - MAX_TABLE_COLUMNS
		useful = useful[:MAX_TABLE_COLUMNS]

	if not useful:
		return f"({len(rows)} rows, all fields empty)"

	# A value containing the delimiter would otherwise forge a column boundary.
	body = [[_scalar(row.get(column)).replace("|", "/") for column in useful] for row in rows]

	widths = [
		min(max(len(column), *(len(cells[index]) for cells in body)), MAX_COLUMN)
		if body
		else len(column)
		for index, column in enumerate(useful)
	]

	def line(cells: list[str]) -> str:
		return " | ".join(cell.ljust(widths[index]) for index, cell in enumerate(cells)).rstrip()

	rendered = [
		line(useful),
		line(["-" * width for width in widths]),
		*(line(cells) for cells in body),
	]

	dropped = len(columns) - len(useful) - capped
	notes = []
	if dropped > 0:
		notes.append(f"{dropped} empty or unlisted column{'' if dropped == 1 else 's'}")
	if capped:
		notes.append(f"{capped} beyond the first {MAX_TABLE_COLUMNS}")
	if notes:
		rendered.append(f"({' and '.join(notes)} hidden - name the fields you need)")

	return "\n".join(rendered)


def render_pagination(returned: int, start: int, total: int | None = None) -> str:
	"""The line that tells an agent whether it has seen everything.

	Without it, twenty rows out of four hundred and twenty rows out of twenty look
	identical, and the agent that assumes the first case is complete gets the answer
	confidently wrong.
	"""
	plural = "" if returned == 1 else "s"

	if total is None:
		return f"{returned} row{plural} from offset {start}."

	end = start + returned
	if end >= total:
		return (
			f"{returned} row{plural} - rows {start + 1}-{end} of {total}. "
			"This is the end of the results."
		)

	return (
		f"{returned} row{plural} - rows {start + 1}-{end} of {total}. "
		f"{total - end} more; pass start: {end} for the next page."
	)
