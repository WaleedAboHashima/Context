# Copyright (c) 2026, Waleed AboHashima and Contributors
# License: MIT. See LICENSE

app_name = "context"
app_title = "Context"
app_publisher = "Waleed AboHashima"
app_description = "Connect any AI agent to Frappe and ERPNext over MCP"
app_email = "waleedsabry.abohashima@gmail.com"
app_license = "MIT"

# Context adds no assets, no boot payload, no document hooks and no scheduled jobs.
#
# That is the whole design. The app is one whitelisted endpoint and two DocTypes:
# installing it changes nothing about how the site looks or behaves until somebody
# connects an agent to it, and uninstalling it leaves no trace in the desk. An app
# that quietly extends `extend_bootinfo` or patches a document event is an app
# people are right to be wary of installing on a production ERP.

after_install = "context.install.after_install"

# The MCP endpoint answers an unauthenticated request with 401 plus a
# `WWW-Authenticate` header naming the site's OAuth metadata, which is what lets a
# client offer a Connect button instead of a form. The metadata itself is served by
# Frappe (`frappe.integrations.oauth2.handle_wellknown`), so there is no route to add
# here — see `context/connect.py`.
