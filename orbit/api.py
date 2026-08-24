# Copyright (c) 2026, Waleed AboHashima and Contributors
# License: MIT. See LICENSE
"""The HTTP surface: one endpoint.

	POST https://your-site/api/method/orbit.api.mcp

That is the whole integration. An MCP client is given that URL and a credential, and
everything else — the handshake, the tool list, the calls — happens over it.

Returning a `werkzeug` Response rather than a value is deliberate and necessary.
Frappe wraps a returned value as `{"message": ...}`, and a JSON-RPC client would reject
that envelope; `frappe/handler.py` passes a Response straight through instead, which
gives us the exact body and status code the protocol requires — including the array form
a batched request needs, which a dict-based response could not express at all.

Authentication is Frappe's, untouched. A request arrives with an API key pair, an OAuth
bearer token, or a session cookie, and `frappe.session.user` is whoever that resolves to.
Orbit adds no authentication of its own and has no service account: there is no way to
reach this endpoint as anyone other than a real user of the site, which is what makes the
permission story hold.
"""

from __future__ import annotations

import json
from typing import Any

import frappe
from werkzeug.wrappers import Response

from orbit.mcp import protocol

CONTENT_TYPE = "application/json"


def _json_response(payload: Any, status: int = 200) -> Response:
	return Response(
		json.dumps(payload, default=str),
		status=status,
		content_type=CONTENT_TYPE,
	)


@frappe.whitelist(methods=["POST", "GET", "DELETE"])
def mcp() -> Response:
	"""Streamable HTTP, without the stream.

	Orbit's tools each return a complete result, so there is nothing to stream and the
	optional SSE channel would be a moving part that never carries anything. A client
	that probes `GET` for it is answered with 405, which the transport specifies as the
	way to say "this server does not offer a stream" — an error page there presents to
	the user as a broken connector.
	"""
	method = (frappe.request.method or "POST").upper()

	if method == "GET":
		return Response(
			json.dumps(
				{
					"jsonrpc": "2.0",
					"error": {
						"code": -32601,
						"message": "Orbit does not offer a server-initiated stream. POST JSON-RPC to this URL.",
					},
				}
			),
			status=405,
			content_type=CONTENT_TYPE,
		)

	if method == "DELETE":
		# Session teardown. Orbit keeps no per-session state — every call is resolved
		# from the request's own credential — so there is nothing to tear down.
		return Response(status=204)

	raw = frappe.request.get_data(cache=False, as_text=True) if frappe.request else ""
	if not raw:
		return _json_response(
			{"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Empty request body."}},
			status=400,
		)

	try:
		message = json.loads(raw)
	except ValueError:
		return _json_response(
			{"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Body is not valid JSON."}},
			status=400,
		)

	# A batch. Notifications inside it contribute nothing to the response, and a batch
	# made entirely of notifications gets no body at all.
	if isinstance(message, list):
		replies = [reply for reply in (protocol.handle(item) for item in message) if reply is not None]
		return _json_response(replies) if replies else Response(status=202)

	reply = protocol.handle(message)
	if reply is None:
		return Response(status=202)

	return _json_response(reply)


@frappe.whitelist(methods=["GET"])
def health() -> dict[str, Any]:
	"""A plain, human-readable check that the app is installed and switched on.

	Exists because "is the connector broken or is the URL wrong" is the first question
	anyone asks, and answering it should not require speaking JSON-RPC by hand.
	"""
	from orbit.mcp.policy import Policy
	from orbit.mcp.registry import available

	try:
		policy = Policy()
		policy.assert_available()
	except Exception as exception:
		return {"ok": False, "user": frappe.session.user, "reason": str(exception)}

	return {
		"ok": True,
		"user": frappe.session.user,
		"tools": [tool.name for tool in available(policy)],
		"write": policy.allow_write,
		"submit": policy.allow_submit,
		"delete": policy.allow_delete,
	}
