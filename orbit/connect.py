# Copyright (c) 2026, Waleed AboHashima and Contributors
# License: MIT. See LICENSE
"""Everything the Connect dialog needs, and the one setting it may change.

The discovery story turned out to be almost entirely Frappe's already. A v16 site
publishes `/.well-known/oauth-authorization-server` and
`/.well-known/oauth-protected-resource`, supports PKCE with S256, and accepts dynamic
client registration — all of it on by default, all of it in
`frappe.integrations.oauth2`. Orbit does not reimplement any of that and must not: a
second OAuth surface on the same site would be a second thing to get wrong.

What was actually missing is one sentence in a header. RFC 9728 says a protected
resource announces where its metadata lives by answering an unauthenticated request
with `401` and a `WWW-Authenticate` header naming it. Without that header a client has
no way to know the site can be authorised against, so its UI can only offer to let the
user paste a client id by hand. `orbit.api.mcp` sends it; that is the whole fix.

This module is the other half: telling the administrator what to paste where, and
checking the two flags that the flow depends on.
"""

from __future__ import annotations

from typing import Any

import frappe
from frappe import _

MCP_PATH = "/api/method/orbit.api.mcp"


def site_url() -> str:
	"""The site's own origin, as the request saw it.

	Read from the request rather than from `site_config`, because a Frappe Cloud site
	answers on both its `*.frappe.cloud` name and any custom domain, and the URL an
	administrator needs is the one they are looking at.
	"""
	if getattr(frappe.local, "request", None):
		from urllib.parse import urlparse

		parsed = urlparse(frappe.request.url)
		if parsed.scheme and parsed.netloc:
			return f"{parsed.scheme}://{parsed.netloc}"

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

	from orbit.mcp.policy import Policy
	from orbit.mcp.registry import available

	settings = frappe.get_cached_doc("Orbit Settings")

	oauth = frappe.get_cached_doc("OAuth Settings")
	discovery = {
		"auth_server_metadata": bool(oauth.show_auth_server_metadata),
		"protected_resource_metadata": bool(oauth.show_protected_resource_metadata),
		"dynamic_client_registration": bool(oauth.enable_dynamic_client_registration),
	}

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
		"discovery_ready": all(discovery.values()),
		"user": frappe.session.user,
	}


@frappe.whitelist()
def enable_discovery() -> dict[str, Any]:
	"""Turn on the OAuth metadata a connector needs to find its way in.

	Separated behind its own button and never done on install, because these are
	*site-wide* settings that outlive Orbit: they affect every OAuth client on the site,
	not just this app. Enabling them silently at install time would be an app changing
	something it does not own.

	`resource_name` is set only if it is still the framework's placeholder — it is what
	a user sees on the consent screen, and overwriting a name someone chose would be
	rude.
	"""
	frappe.only_for("System Manager")

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
