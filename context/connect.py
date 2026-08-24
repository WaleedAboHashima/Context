# Copyright (c) 2026, Waleed AboHashima and Contributors
# License: MIT. See LICENSE
"""Everything the Connect dialog needs, and the one setting it may change.

The discovery story turned out to be almost entirely Frappe's already. A v16 site
publishes `/.well-known/oauth-authorization-server` and
`/.well-known/oauth-protected-resource`, supports PKCE with S256, and accepts dynamic
client registration — all of it on by default, all of it in
`frappe.integrations.oauth2`. Context does not reimplement any of that and must not: a
second OAuth surface on the same site would be a second thing to get wrong.

What was actually missing is one sentence in a header. RFC 9728 says a protected
resource announces where its metadata lives by answering an unauthenticated request
with `401` and a `WWW-Authenticate` header naming it. Without that header a client has
no way to know the site can be authorised against, so its UI can only offer to let the
user paste a client id by hand. `context.api.mcp` sends it; that is the whole fix.

This module is the other half: telling the administrator what to paste where, and
checking the two flags that the flow depends on.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _

MCP_PATH = "/api/method/context.api.mcp"


def framework_publishes_metadata() -> bool:
	"""Whether this Frappe is new enough to publish OAuth metadata.

	`handle_wellknown` and `register_client` arrived in v16. On v14 and v15 Context itself
	works — the endpoint, the tools, the permissions, the audit log are all framework
	features that have been stable for years — but there is nothing serving
	`/.well-known/oauth-authorization-server`, so a connector cannot discover how to sign
	in and only an API key will do.

	Checked by capability rather than by version number, because a backport or a fork
	makes a version comparison lie in both directions.
	"""
	try:
		from frappe.integrations import oauth2

		return hasattr(oauth2, "handle_wellknown")
	except Exception:
		return False


def site_url() -> str:
	"""The site's own origin, as the *client* saw it.

	The host is read from the request rather than from `site_config`, because a Frappe
	Cloud site answers on both its `*.frappe.cloud` name and any custom domain, and the
	URL an administrator needs is the one they are looking at.

	The scheme cannot be read the same way. Frappe's production WSGI app is not wrapped
	in `ProxyFix` — that happens only in `bench serve` — so behind nginx or any other TLS
	terminator `frappe.request.scheme` is `http`, which is what the proxy speaks to
	gunicorn and not what the browser or the MCP client spoke. Trusting it published
	`resource_metadata="http://..."` in the `WWW-Authenticate` header of every HTTPS
	site, and handed administrators an `http://` endpoint to paste into a client that
	requires TLS: the one header this app exists to send, wrong on every real deployment.

	`X-Forwarded-Proto` is the header the proxy sets to say what it was asked for, and is
	what `frappe.utils.get_url` itself consults. It is trusted here for the same reason
	Frappe trusts it — a request that reached the app at all came through the site's own
	proxy.
	"""
	if getattr(frappe.local, "request", None):
		from urllib.parse import urlparse

		parsed = urlparse(frappe.request.url)
		if parsed.netloc:
			forwarded = (frappe.get_request_header("X-Forwarded-Proto") or "").split(",")[0].strip()
			scheme = forwarded or parsed.scheme
			if scheme:
				return f"{scheme}://{parsed.netloc}"

	return frappe.utils.get_url().rstrip("/")


def endpoint() -> str:
	return f"{site_url()}{MCP_PATH}"


@frappe.whitelist()
def connection_info() -> dict[str, Any]:
	"""What to show an administrator who wants to connect an agent.

	Deliberately includes the *state* as well as the instructions. Half the support
	burden of a thing like this is people following correct instructions against a site
	where a switch is off, so the dialog says what is on before it says what to paste.
	"""
	frappe.only_for("System Manager")

	from context.mcp.policy import Policy
	from context.mcp.registry import available

	settings = frappe.get_cached_doc("Context Settings")

	supported = framework_publishes_metadata()
	if supported:
		oauth = frappe.get_cached_doc("OAuth Settings")
		discovery = {
			"auth_server_metadata": bool(oauth.show_auth_server_metadata),
			"protected_resource_metadata": bool(oauth.show_protected_resource_metadata),
			"dynamic_client_registration": bool(oauth.enable_dynamic_client_registration),
		}
	else:
		# Nothing to report and nothing that can be switched on: the endpoints do not
		# exist in this framework version.
		discovery = {}

	tools: list[str] = []
	blocked = None
	if settings.enabled:
		try:
			policy = Policy()
			policy.assert_available()
			tools = [tool.name for tool in available(policy)]
		except Exception as exception:
			blocked = str(exception)

	return {
		"endpoint": endpoint(),
		"site": site_url(),
		"enabled": bool(settings.enabled),
		"blocked": blocked,
		"capabilities": {
			"write": bool(settings.allow_write),
			"submit": bool(settings.allow_submit),
			"delete": bool(settings.allow_delete),
		},
		"required_role": settings.required_role or None,
		"tools": tools,
		"discovery": discovery,
		"framework_publishes_metadata": supported,
		"discovery_ready": supported and all(discovery.values()),
		"user": frappe.session.user,
	}


@frappe.whitelist()
def enable_discovery() -> dict[str, Any]:
	"""Turn on the OAuth metadata a connector needs to find its way in.

	Separated behind its own button and never done on install, because these are
	*site-wide* settings that outlive Context: they affect every OAuth client on the site,
	not just this app. Enabling them silently at install time would be an app changing
	something it does not own.

	`resource_name` is set only if it is still the framework's placeholder — it is what
	a user sees on the consent screen, and overwriting a name someone chose would be
	rude.
	"""
	frappe.only_for("System Manager")

	if not framework_publishes_metadata():
		frappe.throw(
			_(
				"This version of Frappe does not publish OAuth metadata - that arrived in v16. "
				"Context still works here with an API key; browser-based sign-in does not."
			)
		)

	settings = frappe.get_doc("OAuth Settings")
	changed = []

	for fieldname in (
		"show_auth_server_metadata",
		"show_protected_resource_metadata",
		"enable_dynamic_client_registration",
	):
		if not settings.get(fieldname):
			settings.set(fieldname, 1)
			changed.append(fieldname)

	if settings.resource_name in (None, "", "Frappe Framework Application"):
		settings.resource_name = frappe.local.site
		changed.append("resource_name")

	if changed:
		settings.save()
		frappe.db.commit()

	return {"changed": changed, "ok": True}
