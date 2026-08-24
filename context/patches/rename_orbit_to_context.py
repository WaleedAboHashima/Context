# Copyright (c) 2026, Waleed AboHashima and Contributors
# License: MIT. See LICENSE
"""The app was called Orbit until v0.2.0.

Renaming a Frappe app renames its DocTypes with it, and a DocType name is not a label
- it is the table the rows live in and the `doctype` column in `tabSingles`. Without
this, a site that upgrades gets a second, empty `Context Settings` beside its configured
`Orbit Settings`, silently reverts to every switch off, and orphans its audit trail in a
table nothing reads any more.

Runs in `pre_model_sync`, which is the only place it can work: by the time the model
sync has run, the new DocTypes exist as empty records and there is nothing left to
rename onto.

`frappe.rename_doc` does the real work - `DocType.after_rename` issues the `RENAME
TABLE` for the audit log and rewrites `tabSingles` for the settings. It skips moving
files on disk while `frappe.flags.in_patch` is set, which is correct here: the files
were renamed in the same commit that added this patch.
"""

import frappe

DOCTYPES = (
	("Orbit Settings", "Context Settings"),
	("Orbit Audit Log", "Context Audit Log"),
)


def execute() -> None:
	# A Module Def cannot be renamed - Frappe refuses anything that is not a custom
	# module - so the new one is created, the DocTypes are moved onto it, and the old
	# one is dropped once nothing points at it any more.
	if not frappe.db.exists("Module Def", "Context"):
		frappe.get_doc(
			{"doctype": "Module Def", "module_name": "Context", "app_name": "context"}
		).insert(ignore_permissions=True)

	for old, new in DOCTYPES:
		# Skipped on a fresh install, where the old name never existed, and on a site
		# where this has already run. Both are ordinary states, not failures.
		if frappe.db.exists("DocType", old) and not frappe.db.exists("DocType", new):
			# `force` because a DocType is not normally renameable. No `ignore_permissions`:
			# `frappe.rename_doc` is a narrower wrapper than the one it calls and does not
			# take it, and a patch already runs as Administrator.
			frappe.rename_doc("DocType", old, new, force=True)

		if frappe.db.exists("DocType", new):
			frappe.db.set_value("DocType", new, "module", "Context", update_modified=False)

	if frappe.db.exists("Module Def", "Orbit"):
		frappe.delete_doc("Module Def", "Orbit", force=True, ignore_permissions=True)

	frappe.db.commit()
