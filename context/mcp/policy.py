# Copyright (c) 2026, Waleed AboHashima and Contributors
# License: MIT. See LICENSE
"""What Context will and will not do, decided before the work is done.

This layer sits *on top of* Frappe's permissions and only ever subtracts. Nothing here
can grant an agent access the signed-in user does not already have — Frappe decides
that, and Context never asks it not to. There is no `ignore_permissions` in this app.

What it adds is a second, coarser gate an administrator can reason about without
reading a role permission matrix: read-only by default, three separate switches to
open it, and a short list of DocTypes that are never writable at all.

The refusal messages matter as much as the checks. They are read by a model, which will
relay them to the person who can act, so each one names the exact setting to change.
"Permission denied" sends an agent looking for a workaround; "writes are disabled — tick
Allow write in Context Settings" ends the conversation.
"""

from __future__ import annotations

import frappe
from frappe import _

# DocTypes Context refuses to write to, whatever the settings say.
#
# These are not sensitive data — several are readable by anyone. They are the records
# that change what the *system does*: code that runs on save, fields that exist,
# permissions, and the users those permissions attach to. Writing one turns a mistake
# with a scope of one document into a mistake with a scope of the whole site, and no
# realistic agent task requires it.
#
# Deliberately not extended to reads. Reading `Custom Field` is how an agent
# understands a customised site, which is behaviour Context exists to support.
NEVER_WRITE = frozenset(
	{
		"server script",
		"client script",
		"custom field",
		"custom docperm",
		"property setter",
		"customize form",
		"doctype",
		"docfield",
		"docperm",
		"webhook",
		"scheduled job type",
		"user",
		"role",
		"role profile",
		"system settings",
		"workflow",
		"workflow state",
		"workflow action",
		"notification",
		"api access log",
		"context settings",
		"context audit log",
	}
)


class ContextDenied(frappe.ValidationError):
	"""A refusal by Context's own policy, as opposed to one by Frappe's permissions."""


def _split(value: str | None) -> list[str]:
	"""A list written by a human in a text field: commas or newlines, either way."""
	if not value:
		return []
	return [part.strip() for part in value.replace("\n", ",").split(",") if part.strip()]


class Policy:
	"""One request's view of the settings.

	Built per request from the cached single DocType, so an administrator ticking a box
	in the desk takes effect on the next tool call rather than on the next restart.
	"""

	def __init__(self) -> None:
		settings = frappe.get_cached_doc("Context Settings")

		self.enabled = bool(settings.enabled)
		self.allow_write = bool(settings.allow_write)
		self.allow_submit = bool(settings.allow_submit)
		self.allow_delete = bool(settings.allow_delete)
		self.required_role = (settings.required_role or "").strip()
		self.max_rows = max(1, min(int(settings.max_rows or 20), 200))
		self.log_tool_calls = bool(settings.log_tool_calls)
		self.log_arguments = bool(settings.log_arguments)

		# Kept twice on purpose: the lower-cased set is what comparisons use, and the
		# list as written is what a human is shown. Reporting the normalised form back to
		# an administrator ("limited to sales invoice") makes correct configuration look
		# like a typo.
		self._allowed_as_written = _split(settings.allowed_doctypes)
		self._allowed = {name.lower() for name in self._allowed_as_written}
		self._denied = NEVER_WRITE | {name.lower() for name in _split(settings.denied_doctypes)}

	# -- gates ---------------------------------------------------------------

	def assert_available(self) -> None:
		"""Whether this user may use Context at all.

		The role gate is the control an administrator reaches for first, and it is
		checked before anything else: an installed app that is switched off should
		behave as though it were not installed.
		"""
		if not self.enabled:
			frappe.throw(
				_("Context is switched off for this site. An administrator can enable it in Context Settings."),
				ContextDenied,
			)

		if frappe.session.user == "Guest":
			frappe.throw(_("Context requires an authenticated user."), frappe.AuthenticationError)

		if self.required_role and self.required_role not in frappe.get_roles():
			frappe.throw(
				_("Context is restricted to the {0} role on this site.").format(self.required_role),
				ContextDenied,
			)

	def assert_in_scope(self, doctype: str) -> None:
		"""The allowlist is total: it bounds reads as well as writes."""
		if not self._allowed:
			return
		if doctype.lower() in self._allowed:
			return
		frappe.throw(
			_(
				"{0} is outside the scope configured for Context on this site. "
				"Allowed: {1}. An administrator can widen it in Context Settings."
			).format(doctype, ", ".join(self._allowed_as_written)),
			ContextDenied,
		)

	def assert_writable(self, doctype: str) -> None:
		self.assert_in_scope(doctype)

		if not self.allow_write:
			frappe.throw(
				_("Context is read-only on this site. An administrator can tick 'Allow write' in Context Settings."),
				ContextDenied,
			)

		if doctype.lower() in self._denied:
			frappe.throw(
				_(
					"Context never writes to {0}. That DocType changes how the site itself "
					"behaves - code, schema, permissions or users - rather than its business "
					"data, so it is refused regardless of settings. Make this change in the desk."
				).format(doctype),
				ContextDenied,
			)

	def assert_submittable(self, doctype: str) -> None:
		self.assert_writable(doctype)

		if not self.allow_submit:
			frappe.throw(
				_(
					"Submitting and cancelling are disabled for Context. This is a separate "
					"setting from write because submitting posts ledger entries and moves "
					"stock, and cancelling leaves a permanent trail."
				),
				ContextDenied,
			)

	def assert_deletable(self, doctype: str) -> None:
		self.assert_writable(doctype)

		if not self.allow_delete:
			frappe.throw(
				_("Deleting is disabled for Context. Deletion has no undo, which is why it is its own setting."),
				ContextDenied,
			)

	# -- limits --------------------------------------------------------------

	def clamp_limit(self, requested: int | None) -> int:
		"""Rows per page.

		A ceiling, not a suggestion. The failure this prevents is not a slow query — it
		is an agent asking for five thousand rows, filling its context with them, and
		losing the thread of the task it was doing.
		"""
		if requested is None:
			return min(20, self.max_rows)
		try:
			value = int(requested)
		except (TypeError, ValueError):
			return min(20, self.max_rows)
		return max(1, min(value, self.max_rows))

	# -- description ---------------------------------------------------------

	def describe(self) -> str:
		"""Told to the agent once, so it does not discover the limits by hitting them."""
		scope = (
			", limited to " + ", ".join(self._allowed_as_written)
			if self._allowed
			else " (whatever this user is permitted to read)"
		)
		return "\n".join(
			[
				f"Signed in as {frappe.session.user}.",
				f"Reads: allowed{scope}.",
				f"Create/update: {'allowed' if self.allow_write else 'disabled'}.",
				f"Submit/cancel: {'allowed' if self.allow_submit else 'disabled'}.",
				f"Delete: {'allowed' if self.allow_delete else 'disabled'}.",
				f"Max rows per call: {self.max_rows}.",
				"Every call runs as the signed-in user, under that user's own Frappe permissions.",
			]
		)
