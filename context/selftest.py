# Copyright (c) 2026, Waleed AboHashima and Contributors
# License: MIT. See LICENSE
"""A self-check, runnable from the bench.

    bench --site your-site execute context.selftest.run

Speaks the protocol to itself: completes the handshake, lists the tools, and runs the
read-only ones against real data on this site, printing exactly what an agent would
receive. It never passes a write tool, so it is safe on production.

It exists because "is the connector broken, or is my URL wrong, or is it a permission
problem" is the first question anyone asks, and answering it should not require an MCP
client and a debugger. Everything it exercises — policy, metadata, the query layer, the
rendering — is the same code the endpoint runs; the only thing it does not cover is the
HTTP layer in front of it.
"""

from __future__ import annotations

import json
from typing import Any

import frappe

from context.mcp import protocol


def _call(method: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
	return protocol.handle(
		{"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
	)


def _show(label: str, reply: dict[str, Any] | None) -> bool:
	print("\n" + "-" * 74)

	if reply is None:
		print(f"(no reply)  {label}")
		return False

	if "error" in reply:
		print(f"FAILED  {label}\n\n{json.dumps(reply['error'], indent=1)}")
		return False

	result = reply.get("result", {})
	failed = bool(result.get("isError"))
	print(f"{'REFUSED' if failed else 'ok'}  {label}\n")

	for block in result.get("content", []):
		if block.get("type") == "text":
			print(block["text"])

	if "tools" in result:
		for tool in result["tools"]:
			print(f"  - {tool['name']}")
	if "serverInfo" in result:
		print(f"  protocol {result.get('protocolVersion')}, server {result['serverInfo']}")

	return not failed


def run(doctype: str = "Sales Order") -> None:
	"""Exercise the read-only surface against this site."""
	print(f"\nContext self-check on {frappe.local.site} as {frappe.session.user}")

	passed = failed = 0

	def track(okay: bool) -> None:
		nonlocal passed, failed
		if okay:
			passed += 1
		else:
			failed += 1

	track(_show("initialize", _call("initialize")))

	tools = _call("tools/list")
	track(_show("tools/list", tools))

	# A disabled site is a legitimate state, not a failure — but nothing below it can
	# run, so say so plainly rather than reporting eleven refusals.
	if tools and "error" not in tools and tools.get("result", {}).get("tools") is None:
		print("\nContext is not available to this user. Nothing further to check.")
		return

	track(_show("frappe_whoami", _call("tools/call", {"name": "frappe_whoami", "arguments": {}})))
	track(
		_show(
			'frappe_search_doctypes {keyword: "sales"}',
			_call("tools/call", {"name": "frappe_search_doctypes", "arguments": {"keyword": "sales", "limit": 8}}),
		)
	)
	track(
		_show(
			f"frappe_describe_doctype {{{doctype}}}",
			_call("tools/call", {"name": "frappe_describe_doctype", "arguments": {"doctype": doctype}}),
		)
	)
	track(
		_show(
			f"frappe_count_documents {{{doctype}}}",
			_call("tools/call", {"name": "frappe_count_documents", "arguments": {"doctype": doctype}}),
		)
	)

	listing = _call("tools/call", {"name": "frappe_list_documents", "arguments": {"doctype": doctype, "limit": 5}})
	track(_show(f"frappe_list_documents {{{doctype}}}", listing))

	# The document name is scraped out of the listing rather than invented, so this
	# works on a site with data and skips cleanly on one without.
	# Rows begin after the table's dashed separator. Counting lines from the top picked
	# the header instead, and then looked up a document called "name".
	name = None
	if listing and "error" not in listing:
		for block in listing.get("result", {}).get("content", []):
			lines = block.get("text", "").split("\n")
			separator = next((i for i, line in enumerate(lines) if line.startswith("---")), None)
			if separator is None:
				continue
			for line in lines[separator + 1 :]:
				candidate = line.split("|")[0].strip()
				if candidate and not candidate.startswith("(") and " " not in candidate:
					name = candidate
					break
			if name:
				break

	if name:
		track(
			_show(
				f'frappe_get_document {{{doctype}, "{name}"}}',
				_call("tools/call", {"name": "frappe_get_document", "arguments": {"doctype": doctype, "name": name}}),
			)
		)
	else:
		print(f"\n- skipped frappe_get_document: no {doctype} documents on this site.")

	# A refusal, on purpose. In a read-only configuration this must not succeed, and the
	# message must name the setting that would change it.
	refusal = _call("tools/call", {"name": "frappe_create_document", "arguments": {"doctype": doctype, "doc": {}}})
	refused = bool(refusal and (refusal.get("result", {}).get("isError") or "error" in refusal))
	print("\n" + "-" * 74)
	print(f"{'ok' if refused else 'FAILED'}  a write is refused in this configuration\n")
	if refusal:
		for block in refusal.get("result", {}).get("content", []):
			print(block.get("text", "")[:400])
		if "error" in refusal:
			print(str(refusal["error"].get("message"))[:400])
	track(refused)

	print("\n" + "=" * 74)
	print(f"{passed} passed, {failed} failed.\n")


def measure(doctype: str = "Sales Order", name: str | None = None) -> None:
	"""How much smaller a real document from this site gets.

	    bench --site your-site execute context.selftest.measure

	The claim in the README, measured against your own data rather than asserted. Token
	figures are a divide — roughly 3.6 characters per token for dense JSON, 4 for text —
	so they are approximate; the ratio is the point and it holds under any tokenizer.
	"""
	import json

	from context.mcp import meta as meta_module
	from context.mcp.render import render_document

	if not name:
		rows = frappe.get_list(doctype, fields=["name"], limit_page_length=1, order_by="modified desc")
		if not rows:
			print(f"No {doctype} documents on this site to measure.")
			return
		name = rows[0]["name"]

	doc = frappe.get_doc(doctype, name).as_dict()
	raw = json.dumps(doc, default=str)
	rendered = render_document(
		doc,
		child_tables=meta_module.child_table_fields(doctype),
		checkboxes=meta_module.checkbox_fields(doctype),
		child_fields=meta_module.child_grid_fields(doctype),
	)

	raw_tokens = len(raw) / 3.6
	context_tokens = len(rendered) / 4

	print(f"\n{doctype} {name} on {frappe.local.site}\n")
	print(f"  as_dict JSON : {len(raw):>7,} chars  (~{raw_tokens:>6,.0f} tokens)")
	print(f"  through Context: {len(rendered):>7,} chars  (~{context_tokens:>6,.0f} tokens)")
	print(f"  saving       : {100 - context_tokens / raw_tokens * 100:>6.0f}%\n")
