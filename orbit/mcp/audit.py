# Copyright (c) 2026, Waleed AboHashima and Contributors
# License: MIT. See LICENSE
"""The audit trail.

An agent acting inside an ERP has to be answerable. Frappe's own version history records
that a document changed and who changed it, but it cannot say *why* — that the change came
from an agent, on whose behalf, in response to which tool call. This log closes that gap,
and it is the difference between a feature an administrator will enable and one they will
not.

Two properties it must have:

**It cannot break the request.** A failed log write is logged and swallowed. An audit
trail that turns a successful tool call into an error is worse than one that occasionally
misses a row, because the first failure mode makes people switch it off.

**It records refusals as well as successes.** A rejected write is the more interesting
half of the trail: it is where you see an agent trying something it should not.

Arguments are logged only when the site opts in, because a tool call can carry customer
data and a log is a copy of it in a second place. That is an administrator's decision to
make, not a default to inherit.
"""

from __future__ import annotations

import json
from typing import Any

import frappe

# Enough of an argument payload to reconstruct what was attempted, not enough to make
# the log a shadow copy of the database.
MAX_ARGUMENTS = 2000


def record(
	tool: str,
	arguments: dict[str, Any],
	outcome: str,
	duration_ms: int,
	error: str | None = None,
	log_arguments: bool = False,
) -> None:
	try:
		payload = ""
		if log_arguments:
			payload = json.dumps(arguments, default=str, indent=1)[:MAX_ARGUMENTS]

		frappe.get_doc(
			{
				"doctype": "Orbit Audit Log",
				"tool": tool,
				# Pulled out of the arguments so the log can be filtered by what was
				# touched, which is the question an audit is usually asked.
				"reference_doctype": str(arguments.get("doctype") or "")[:140],
				"reference_name": str(arguments.get("name") or "")[:140],
				"outcome": outcome,
				"duration_ms": duration_ms,
				"error": (error or "")[:1000],
				"arguments": payload,
			}
		).insert(ignore_permissions=True)
		# Committed separately: the trail has to survive a rollback of the work it
		# describes, and a refused write is exactly the case where that happens.
		frappe.db.commit()
	except Exception:
		frappe.log_error(title="Orbit: could not write audit log")
