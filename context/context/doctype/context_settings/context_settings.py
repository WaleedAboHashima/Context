# Copyright (c) 2026, Waleed AboHashima and Contributors
# License: MIT. See LICENSE

from frappe.model.document import Document


class ContextSettings(Document):
	def validate(self) -> None:
		"""Keep what is stored the same as what the form shows.

		`allow_submit` and `allow_delete` are hidden behind `depends_on: allow_write`,
		which stops them being *shown* but not being *stored*. Ticking submit and then
		unticking write leaves a 1 in the database under a checkbox nobody can see,
		and the settings page then disagrees with itself about what agents may do.
		"""
		if not self.allow_write:
			self.allow_submit = 0
			self.allow_delete = 0
