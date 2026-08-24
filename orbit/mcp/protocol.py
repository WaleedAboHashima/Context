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
from .policy import OrbitDenied, Policy
from .registry import BY_NAME, available

# The revision this implementation was written against, and the one it answers with when
# the client asks for something it has never heard of.
PROTOCOL_VERSION = "2025-06-18"

# Revisions this server is willing to speak. Every one of them is satisfied by the same
# three methods and the same result shapes - nothing Orbit does differs between them -
# so agreeing to the client's revision costs nothing and refusing it costs the
# connection. The spec is explicit that a client which does not support the version it
# gets back SHOULD disconnect, so answering "2025-06-18" to a client that opened with
# "2024-11-05" is a hang-up, not a negotiation.
SUPPORTED_PROTOCOL_VERSIONS = frozenset({"2024-11-05", "2025-03-26", "2025-06-18"})

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

	Nothing raises out of here. An exception escaping this function would reach the client
	as an HTTP 500 and an HTML error page, which an MCP client reports to the user as
	"the server is unreachable" — indistinguishable from a wrong URL or a dead site. A
	switched-off Orbit and a missing permission are ordinary states and have to arrive as
	readable JSON-RPC, not as a crash.
	"""
	try:
		return _dispatch(message)
	except Exception as exception:
		request_id = message.get("id") if isinstance(message, dict) else None
		if request_id is None:
			return None
		return _error(request_id, INTERNAL_ERROR, _explain(exception))


def _dispatch(message: dict[str, Any]) -> dict[str, Any] | None:
	"""The routing itself. Every exit is a value; every raise is caught above."""
	if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
		# The id is echoed when the message carried one. A client matches replies to
		# requests by id; a null id inside a batch is a reply it cannot attribute, so
		# the request it belongs to never completes.
		request_id = message.get("id") if isinstance(message, dict) else None
		return _error(request_id, INVALID_REQUEST, "Expected a JSON-RPC 2.0 message.")

	method = message.get("method")
	request_id = message.get("id")
	params = message.get("params") or {}

	# A notification. Nothing to answer, and answering would be a protocol violation.
	if request_id is None:
		return None

	if method == "initialize":
		requested = params.get("protocolVersion")
		agreed = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else PROTOCOL_VERSION
		return _result(
			request_id,
			{
				"protocolVersion": agreed,
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

	# Anything already in the message log belongs to an earlier call in this batch.
	# `_explain` reads the log to build its one sentence, so a leftover message would
	# be attached to whichever call fails next and read as part of its refusal.
	frappe.local.message_log = []

	started = time.monotonic()
	try:
		text = tool.handler(policy, arguments)
		elapsed = int((time.monotonic() - started) * 1000)

		if policy.log_tool_calls:
			audit.record(name, arguments, "Success", elapsed, log_arguments=policy.log_arguments)

		return _text_result(request_id, text)

	except Exception as exception:
		elapsed = int((time.monotonic() - started) * 1000)

		# The work is rolled back; the record that it was attempted is not. This runs
		# before `_explain`, which writes an unexpected error to the site's Error Log -
		# a write of its own, and one the rollback would be entitled to discard.
		frappe.db.rollback()
		message = _explain(exception)
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

	# Entries in `message_log` are dicts, not strings — `str()` on one yields a Python
	# repr, which is how a clean refusal turned into a wall of `__frappe_exc_id` noise.
	details: list[str] = []
	for entry in frappe.local.message_log or []:
		if isinstance(entry, dict):
			text = str(entry.get("message") or "")
		else:
			text = str(entry or "")
		text = re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", text)).strip()
		if text:
			details.append(text)
	frappe.local.message_log = []

	detail = " ".join(details).strip()

	# Checked before the Frappe families below, because OrbitDenied subclasses
	# ValidationError and would otherwise be reported as a failed validation rule. A
	# policy refusal needs no prefix: its message already names the setting to change.
	if isinstance(exception, OrbitDenied):
		return detail or str(exception)

	if isinstance(exception, frappe.PermissionError):
		prefix = "Frappe refused this: the signed-in user lacks permission."
	elif isinstance(exception, frappe.DoesNotExistError):
		prefix = "No such record."
	elif isinstance(exception, frappe.DuplicateEntryError):
		prefix = "A record with that name already exists."
	elif isinstance(exception, frappe.TimestampMismatchError):
		prefix = "The document changed since it was read. Read it again and retry."
	elif isinstance(exception, frappe.AuthenticationError):
		prefix = "Not authenticated."
	elif isinstance(exception, frappe.ValidationError):
		prefix = "Frappe refused to save this: a validation rule failed."
	else:
		frappe.log_error(title="Orbit: unhandled error in a tool call")
		return (
			"Orbit hit an unexpected error. It has been written to this site's Error Log; "
			"there is nothing to retry differently."
		)

	if detail:
		return f"{prefix} {detail}"

	text = re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", str(exception))).strip()
	return f"{prefix} {text}" if text else prefix
