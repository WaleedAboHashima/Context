# Copyright (c) 2026, Waleed AboHashima and Contributors
# License: MIT. See LICENSE
"""What happens when the app is installed.

One thing, once: the settings record exists so an administrator can open it. Every
switch in it is off, so installing Context changes nothing about the site until somebody
decides otherwise. An app that arrives switched on is an app nobody should install on a
production ERP.
"""

import frappe


def after_install() -> None:
	if not frappe.db.exists("Context Settings", "Context Settings"):
		doc = frappe.get_doc({"doctype": "Context Settings"})
		doc.insert(ignore_permissions=True)
		frappe.db.commit()

	print("\nContext installed. It is switched OFF until you enable it:")
	print("  Desk -> search 'Context Settings' -> tick Enabled, then choose what it may do.")
	print("  The MCP endpoint is /api/method/context.api.mcp\n")
