# Copyright (c) 2026, Waleed AboHashima and Contributors
# License: MIT. See LICENSE
"""Tests for the switches, which are the part an administrator actually reasons about.

These need a site - the settings are a Single and the policy reads it - so unlike the
renderer's tests they only run under `bench run-tests`. The property they protect is the
one the README promises and the tool list exists to keep: a tool that cannot succeed is
never advertised. Advertising one is worse than refusing it, because the model spends a
turn discovering the refusal and reports the capability to the user in the meantime.
"""

import unittest

import frappe

from context.mcp.policy import Policy
from context.mcp.registry import available

SWITCHES = ("allow_write", "allow_submit", "allow_delete")

# Saved and cleared alongside the switches. These tests are about the write hierarchy,
# and a site that happens to carry an allowlist would otherwise fail them for an
# unrelated reason.
SCOPE = ("allowed_doctypes", "denied_doctypes")

WRITE_TOOLS = {"frappe_create_document", "frappe_update_document"}
ESCALATIONS = {"frappe_submit_document", "frappe_cancel_document", "frappe_delete_document"}


class TestSwitches(unittest.TestCase):
	def setUp(self):
		self.original = {
			key: frappe.db.get_single_value("Context Settings", key) for key in SWITCHES + SCOPE
		}
		for key in SCOPE:
			frappe.db.set_single_value("Context Settings", key, "")

	def tearDown(self):
		for key, value in self.original.items():
			frappe.db.set_single_value("Context Settings", key, value)
		frappe.db.commit()
		frappe.clear_cache()

	def _advertised(self, write, submit, delete) -> set[str]:
		for key, value in zip(SWITCHES, (write, submit, delete)):
			frappe.db.set_single_value("Context Settings", key, value)
		frappe.clear_cache()
		return {tool.name for tool in available(Policy())}

	def test_submit_and_delete_need_write_to_be_advertised(self):
		# The state this prevents: ten tools, with submit, cancel and delete present and
		# create and update missing. Every one of the three fails on `assert_writable`.
		advertised = self._advertised(write=0, submit=1, delete=1)
		self.assertFalse(advertised & ESCALATIONS)
		self.assertFalse(advertised & WRITE_TOOLS)

	def test_write_alone_advertises_only_create_and_update(self):
		advertised = self._advertised(write=1, submit=0, delete=0)
		self.assertTrue(WRITE_TOOLS <= advertised)
		self.assertFalse(advertised & ESCALATIONS)

	def test_each_switch_adds_its_own_tools(self):
		self.assertTrue({"frappe_submit_document", "frappe_cancel_document"} <= self._advertised(1, 1, 0))
		self.assertIn("frappe_delete_document", self._advertised(1, 1, 1))

	def test_every_advertised_tool_can_actually_pass_its_gate(self):
		# The general form of the bug, asserted over all eight combinations rather than
		# the one that was reported.
		for write in (0, 1):
			for submit in (0, 1):
				for delete in (0, 1):
					advertised = self._advertised(write, submit, delete)
					policy = Policy()
					gates = (
						("frappe_create_document", policy.assert_writable),
						("frappe_submit_document", policy.assert_submittable),
						("frappe_delete_document", policy.assert_deletable),
					)
					for name, gate in gates:
						if name not in advertised:
							continue
						with self.subTest(switches=(write, submit, delete), tool=name):
							gate("ToDo")  # ordinary business data, on every site

	def test_saving_with_write_off_clears_the_hidden_escalations(self):
		# `depends_on` hides the two checkboxes; it does not clear them. Without this the
		# database keeps a 1 under a checkbox nobody can see.
		for key, value in zip(SWITCHES, (0, 1, 1)):
			frappe.db.set_single_value("Context Settings", key, value)
		frappe.clear_cache()

		settings = frappe.get_doc("Context Settings")
		settings.save(ignore_permissions=True)

		self.assertFalse(settings.allow_submit)
		self.assertFalse(settings.allow_delete)
