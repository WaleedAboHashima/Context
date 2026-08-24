# Copyright (c) 2026, Waleed AboHashima and Contributors
# License: MIT. See LICENSE
"""The MCP wire protocol, over Frappe's request cycle.

This is a deliberately small implementation of Streamable HTTP: a single POST endpoint
that takes one JSON-RPC message and answers it. What it leaves out is the optional SSE
stream — Orbit's tools all return a complete result, so there is nothing to stream, and
a GET channel that only ever stays silent is a moving part with no purpose.

Three methods carry everything:

- `initialize` — version handshake and capability announcement.
- `tools/list` — what this site offers, which depends on its settings.
- `tools/call` — do the work.

Notifications (a message with no `id`) get no response, per the spec. Everything else
gets exactly one, and errors that belong to the *tool* are returned as a successful
result with `isError` set, not as a JSON-RPC error — the distinction matters, because a
protocol-level error tells the client Orbit is broken, while a tool error tells the model
its request was wrong and it should try differently.
"""

from __future__ import annotations

import time
from typing import Any

import frappe

from orbit import __version__

from . import audit
from .policy import Policy
from .registry import BY_NAME, available

# The revision this implementation was written against. A client asking for another is
# answered with ours rather than refused: the negotiation is meant to converge, and every
# revision so far has been compatible for a server this simple.
PROTOCOL_VERSION = "2025-06-18"

SERVER_INFO = {"name": "orbit", "version": __version__}

# JSON-RPC reserved codes.
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
	return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
	return {"jsonrpc": "2.0", "id": request_id, "result": result}


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
	"""One JSON-RPC message in, at most one out.

	Returns None for a notification, which the caller answers with an empty 202.
	"""
	if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
		return _error(None, INVALID_REQUEST, "Expected a JSON-RPC 2.0 message.")

	method = message.get("method")
	request_id = message.get("id")
	params = message.get("params") or {}

	# A notification. Nothing to answer, and answering would be a protocol violation.
	if request_id is None:
		return None

	if method == "initialize":
		return _result(
			request_id,
			{
				"protocolVersion": PROTOCOL_VERSION,
				"capabilities": {"tools": {"listChanged": False}},
				"serverInfo": SERVER_INFO,
				"instructions": (
					"Orbit exposes this Frappe/ERPNext site. Start with frappe_whoami to see what "
					"you may do, frappe_search_doctypes to find a record type, and "
					"frappe_describe_doctype before writing a filter or creating a document - "
					"field names on a customised site are not guessable."
				),
			},
		)

	if method == "ping":
		return _result(request_id, {})

	if method == "tools/list":
		policy = Policy()
		policy.assert_available()
		return _result(request_id, {"tools": [tool.as_json() for tool in available(policy)]})

	if method == "tools/call":
		return _call(request_id, params)

	return _error(request_id, METHOD_NOT_FOUND, f"Orbit does not implement {method!r}.")


def _call(request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
	name = params.get("name")
	arguments = params.get("arguments") or {}

	policy = Policy()
	policy.assert_available()

	tool = BY_NAME.get(name or "")
	if tool is None:
		return _error(request_id, METHOD_NOT_FOUND, f"No such tool: {name!r}.")

	# A tool whose switch is off is not merely refused — it was never advertised, so a
	# call to it means the client is working from a stale tool list.
	if tool not in available(policy):
		return _text_result(
			request_id,
			f"{name} is not enabled on this site. An administrator controls this in Orbit Settings.",
			is_error=True,
		)

	started = time.monotonic()
	try:
		text = tool.handler(policy, arguments)
		elapsed = int((time.monotonic() - started) * 1000)

		if policy.log_tool_calls:
			audit.record(name, arguments, "Success", elapsed, log_arguments=policy.log_arguments)

		return _text_result(request_id, text)

	except Exception as exception:
		elapsed = int((time.monotonic() - started) * 1000)
		message = _explain(exception)

		# The work is rolled back; the record that it was attempted is not.
		frappe.db.rollback()
		if policy.log_tool_calls:
			audit.record(
				name, arguments, "Refused", elapsed, error=message, log_arguments=policy.log_arguments
			)

		return _text_result(request_id, message, is_error=True)


def _text_result(request_id: Any, text: str, is_error: bool = False) -> dict[str, Any]:
	result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
	if is_error:
		result["isError"] = True
	return _result(request_id, result)


def _explain(exception: Exception) -> str:
	"""One sentence the model can act on, instead of a traceback.

	Frappe's exceptions already carry the message a human needs — `frappe.throw` text is
	exactly the "Row #1: Item Code is required" that tells an agent what to fix. What has
	to be stripped is the HTML those messages are written in, and the traceback, which
	contains nothing an agent can use and costs a thousand tokens to read.

	The traceback is not discarded entirely: unexpected errors go to the site's Error Log,
	where an administrator can find them. It is only kept out of the model's context.
	"""
	import re

	known = (
		frappe.PermissionError,
		frappe.ValidationError,
		frappe.DoesNotExistError,
		frappe.DuplicateEntryError,
		frappe.LinkValidationError,
		frappe.MandatoryError,
		frappe.AuthenticationError,
		frappe.TimestampMismatchError,
	)

	messages = [str(part) for part in (frappe.local.message_log or []) if part]
	frappe.local.message_log = []

	def clean(text: str) -> str:
		try:
			import json as _json

			parsed = _json.loads(text)
			text = str(parsed.get("message", text)) if isinstance(parsed, dict) else text
		except Exception:
			pass
		return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", text)).strip()

	detail = " ".join(clean(text) for text in messages).strip()

	if isinstance(exception, frappe.PermissionError):
		prefix = "Frappe refused this: the signed-in user lacks permission."
	elif isinstance(exception, frappe.DoesNotExistError):
		prefix = "No such record."
	elif isinstance(exception, frappe.DuplicateEntryError):
		prefix = "A record with that name already exists."
	elif isinstance(exception, frappe.TimestampMismatchError):
		prefix = "The document changed since it was read. Read it again and retry."
	elif isinstance(exception, known):
		prefix = "Frappe refused to save this: a validation rule failed."
	else:
		frappe.log_error(title="Orbit: unhandled error in a tool call")
		return (
			"Orbit hit an unexpected error. It has been written to this site's Error Log; "
			"there is nothing to retry differently."
		)

	if detail:
		return f"{prefix} {detail}"
	text = clean(str(exception))
	return f"{prefix} {text}" if text else prefix
