# Copyright (c) 2026, Waleed AboHashima and Contributors
# License: MIT. See LICENSE

app_name = "orbit"
app_title = "Orbit"
app_publisher = "Waleed AboHashima"
app_description = "Connect any AI agent to Frappe and ERPNext over MCP"
app_email = "waleedsabry.abohashima@gmail.com"
app_license = "MIT"

# Orbit adds no assets, no boot payload, no document hooks and no scheduled jobs.
#
# That is the whole design. The app is one whitelisted endpoint and two DocTypes:
# installing it changes nothing about how the site looks or behaves until somebody
# connects an agent to it, and uninstalling it leaves no trace in the desk. An app
# that quietly extends `extend_bootinfo` or patches a document event is an app
# people are right to be wary of installing on a production ERP.

after_install = "orbit.install.after_install"
