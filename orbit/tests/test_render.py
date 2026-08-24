# Copyright (c) 2026, Waleed AboHashima and Contributors
# License: MIT. See LICENSE
"""Tests for the renderer.

Imports nothing from frappe, deliberately: this module is pure, so its tests run under
`bench run-tests` and under a bare `python -m unittest` alike. The properties asserted
here are the ones the whole app rests on — that a rendering is small *and* honest. Small
alone is easy and produces an agent that confidently reports fields do not exist; honest
alone is the raw JSON everyone else returns.
"""

import json
import unittest

from orbit.mcp.render import render_document, render_pagination, render_rows


class TestRenderDocument(unittest.TestCase):
	def test_omits_empty_and_says_how_many(self):
		out = render_document(
			{"name": "SO-1", "customer": "ACME", "po_no": None, "address": "", "tags": []}
		)
		self.assertIn("customer: ACME", out)
		self.assertNotIn("po_no", out)
		# The count is what makes the omission safe to rely on.
		self.assertIn("3 empty", out)

	def test_drops_framework_plumbing_separately(self):
		out = render_document({"name": "SO-1", "doctype": "Sales Order", "idx": 3, "customer": "ACME"})
		self.assertNotIn("doctype:", out)
		self.assertIn("2 framework fields omitted", out)

	def test_verbose_keeps_everything(self):
		out = render_document({"name": "SO-1", "po_no": None, "doctype": "Sales Order"}, verbose=True)
		self.assertIn("po_no:", out)
		self.assertIn("doctype:", out)

	def test_numeric_zero_survives(self):
		# The bug this prevents: reporting a zero-value invoice as one with no total.
		self.assertIn("outstanding_amount: 0", render_document({"name": "SI-1", "outstanding_amount": 0}))

	def test_cleared_checkbox_is_dropped(self):
		out = render_document({"name": "SI-1", "is_return": 0, "grand_total": 0}, checkboxes={"is_return"})
		self.assertNotIn("is_return", out)
		self.assertIn("grand_total: 0", out)

	def test_child_table_leads_with_its_count(self):
		items = [{"item_code": f"IT-{i}", "qty": i} for i in range(14)]
		out = render_document({"name": "SO-1", "items": items}, child_tables={"items"}, child_row_limit=3)
		self.assertIn("items: 14 rows", out)
		self.assertIn("IT-0", out)
		self.assertNotIn("IT-9", out)
		self.assertIn("11 more rows", out)

	def test_child_rows_drop_their_own_plumbing(self):
		# owner/creation/modified/docstatus repeat the parent's answer on every row, and
		# a child row's `name` is a hash nobody will look up.
		out = render_document(
			{
				"name": "SO-1",
				"items": [
					{
						"name": "ljtu2qj48p",
						"owner": "a@b.com",
						"creation": "2026-01-01",
						"docstatus": 1,
						"item_code": "SKU1",
						"qty": 3,
					}
				],
			},
			child_tables={"items"},
		)
		self.assertIn("SKU1", out)
		self.assertNotIn("ljtu2qj48p", out)
		self.assertNotIn("a@b.com", out)

	def test_child_grid_columns_are_used_when_given(self):
		out = render_document(
			{"name": "SO-1", "items": [{"item_code": "SKU1", "qty": 3, "gross_profit": 9.0}]},
			child_tables={"items"},
			child_fields={"items": ["item_code", "qty"]},
		)
		self.assertIn("SKU1", out)
		self.assertNotIn("gross_profit", out)

	def test_long_text_is_truncated_not_dropped(self):
		out = render_document({"name": "X", "terms": "a" * 500})
		self.assertIn("chars)", out)
		self.assertLess(len(out), 500)

	def test_html_in_a_value_is_stripped(self):
		out = render_document({"name": "X", "notes": "<b>bold</b> text"})
		self.assertIn("bold text", out)
		self.assertNotIn("<b>", out)

	def test_a_realistic_document_shrinks_by_an_order_of_magnitude(self):
		doc = {
			"name": "SO-00042", "doctype": "Sales Order", "customer": "Meridian",
			"grand_total": 48250, "status": "To Deliver",
			"items": [{"item_code": f"IT-{i}", "qty": i + 1} for i in range(14)],
		}
		for i in range(150):
			doc[f"custom_field_{i}"] = None

		rendered = render_document(doc, child_tables={"items"}, child_row_limit=3)
		self.assertLess(len(rendered), len(json.dumps(doc)) / 5)


class TestRenderRows(unittest.TestCase):
	def test_field_names_appear_once_not_per_row(self):
		# The entire token argument for a table over JSON, asserted.
		out = render_rows([{"name": "SO-1", "customer": "ACME"}, {"name": "SO-2", "customer": "Globex"}])
		self.assertEqual(out.count("customer"), 1)
		self.assertIn("Globex", out)

	def test_all_empty_column_is_hidden_and_reported(self):
		out = render_rows([{"name": "SO-1", "po_no": None}, {"name": "SO-2", "po_no": ""}])
		self.assertNotIn("po_no", out)
		self.assertIn("1 empty or unlisted column hidden", out)

	def test_preferred_columns_win_over_everything_present(self):
		# The whole point of reading the child grid's own columns: an eighty-field row
		# renders as the three columns the site chose to show.
		rows = [{"item_code": "A", "qty": 2, "rate": 5.0, "warehouse": "W", "gross_profit": 1.0}]
		out = render_rows(rows, preferred=["item_code", "qty", "rate"])
		self.assertIn("item_code", out)
		self.assertNotIn("warehouse", out)
		self.assertNotIn("gross_profit", out)

	def test_preferred_columns_absent_from_the_rows_fall_back(self):
		# A grid declaring columns the query did not return must not render blank.
		out = render_rows([{"item_code": "A"}], preferred=["nonexistent"])
		self.assertIn("item_code", out)

	def test_column_count_is_capped_and_the_cap_is_stated(self):
		row = {f"field_{i}": i + 1 for i in range(30)}
		out = render_rows([row])
		self.assertIn("field_0", out)
		self.assertNotIn("field_29", out)
		self.assertIn("beyond the first 12", out)

	def test_partly_populated_column_is_kept(self):
		self.assertIn("PO-9", render_rows([{"name": "SO-1", "po_no": None}, {"name": "SO-2", "po_no": "PO-9"}]))

	def test_a_value_cannot_forge_a_column_boundary(self):
		self.assertIn("a/b", render_rows([{"name": "a|b"}]))

	def test_empty_input(self):
		self.assertEqual(render_rows([]), "(no rows)")


class TestRenderPagination(unittest.TestCase):
	def test_says_when_complete(self):
		self.assertIn("end of the results", render_pagination(4, 0, 4))

	def test_gives_the_next_offset_when_there_is_more(self):
		# Without this, 20-of-420 and 20-of-20 are indistinguishable to a reader.
		out = render_pagination(20, 0, 420)
		self.assertIn("400 more", out)
		self.assertIn("start: 20", out)

	def test_does_not_invent_a_total(self):
		out = render_pagination(7, 14)
		self.assertIn("7 rows", out)
		self.assertNotIn("more", out)


if __name__ == "__main__":
	unittest.main()
