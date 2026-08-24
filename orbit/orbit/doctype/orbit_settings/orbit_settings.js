// Copyright (c) 2026, Waleed AboHashima and Contributors
// License: MIT. See LICENSE

/**
 * The Connect dialog.
 *
 * This exists because of who has to use it. The person who wants an AI agent reading
 * their ERP is a finance or operations manager, not the developer who installed the
 * app — and every guide for connecting an MCP server is written for the developer. So
 * the site hands out the connection instead of the client discovering it: you open the
 * ERP you already trust, already signed in, and it tells you exactly what to paste
 * where.
 *
 * It leads with state, not instructions. Half the support burden of anything like this
 * is somebody following correct instructions against a site where a switch is off, so
 * the dialog says what is currently on — and who you are connecting as — before it says
 * what to copy.
 */

frappe.ui.form.on("Orbit Settings", {
	refresh(frm) {
		const button = frm.add_custom_button(__("Connect your AI"), () => open_connect_dialog(frm));
		button.removeClass("btn-default").addClass("btn-primary");

		if (!frm.doc.enabled) {
			frm.dashboard.set_headline(
				__("Orbit is switched off. Nothing can reach this site until you tick Enabled.")
			);
		}
	},
});

function open_connect_dialog(frm) {
	frappe.call({
		method: "orbit.connect.connection_info",
		freeze: true,
		freeze_message: __("Checking this site..."),
		callback({ message }) {
			if (!message) return;
			render_dialog(frm, message);
		},
	});
}

function render_dialog(frm, info) {
	const dialog = new frappe.ui.Dialog({
		title: __("Connect your AI"),
		size: "large",
		fields: [{ fieldtype: "HTML", fieldname: "body" }],
		primary_action_label: __("Done"),
		primary_action: () => dialog.hide(),
	});

	dialog.fields_dict.body.$wrapper.html(build_body(info));
	wire_actions(dialog, frm, info);
	dialog.show();
}

function esc(value) {
	return frappe.utils.escape_html(String(value == null ? "" : value));
}

function build_body(info) {
	return [
		status_block(info),
		endpoint_block(info),
		discovery_block(info),
		clients_block(info),
	].join("");
}

function status_block(info) {
	const capability = [
		info.capabilities.write ? __("create and update") : null,
		info.capabilities.submit ? __("submit and cancel") : null,
		info.capabilities.delete ? __("delete") : null,
	].filter(Boolean);

	const summary = capability.length
		? __("Agents may read, and {0}.", [capability.join(", ")])
		: __("Agents may read only. Nothing can be changed.");

	const rows = [
		[__("This site"), esc(info.site)],
		[__("You are"), esc(info.user)],
		[
			__("Orbit is"),
			info.enabled
				? `<span class="text-success">${__("on")}</span>`
				: `<span class="text-danger">${__("off")}</span>`,
		],
		[__("Agents may"), esc(summary)],
		[__("Tools offered"), info.tools.length ? `${info.tools.length}` : "0"],
	];

	if (info.required_role) {
		rows.push([__("Restricted to role"), esc(info.required_role)]);
	}

	// The reason this block is first: instructions followed against a site with a switch
	// off look like broken instructions.
	const warning = info.blocked
		? `<div class="alert alert-warning" style="margin-top:12px">${esc(info.blocked)}</div>`
		: !info.enabled
			? `<div class="alert alert-warning" style="margin-top:12px">${__(
					"Tick <b>Enabled</b> and save before connecting, or the endpoint will refuse every request."
				)}</div>`
			: "";

	return `
		<div style="margin-bottom:20px">
			<table class="table table-bordered" style="margin-bottom:0">
				<tbody>
					${rows.map(([label, value]) => `<tr><td style="width:180px"><b>${label}</b></td><td>${value}</td></tr>`).join("")}
				</tbody>
			</table>
			${warning}
		</div>
	`;
}

function endpoint_block(info) {
	return `
		<div style="margin-bottom:20px">
			<h5>${__("The address")}</h5>
			<p class="text-muted">${__(
				"Every AI client needs this one URL. Nothing else is secret — it is your site's own address."
			)}</p>
			<div class="input-group">
				<input type="text" class="form-control" readonly value="${esc(info.endpoint)}" data-orbit="endpoint-input">
				<span class="input-group-btn">
					<button class="btn btn-default" data-orbit="copy-endpoint">${__("Copy")}</button>
				</span>
			</div>
		</div>
	`;
}

function discovery_block(info) {
	if (info.discovery_ready) {
		return `
			<div class="alert alert-success" style="margin-bottom:20px">
				${__(
					"Sign-in is ready. When someone connects, they will see this site's normal login page and authorise as themselves — so every agent acts under its own user's permissions."
				)}
			</div>
		`;
	}

	const missing = Object.keys(info.discovery)
		.filter((key) => !info.discovery[key])
		.map((key) => key.replace(/_/g, " "));

	return `
		<div class="alert alert-warning" style="margin-bottom:20px">
			<p><b>${__("Sign-in is not advertised yet.")}</b></p>
			<p>${__(
				"Without this, clients like Claude and ChatGPT cannot offer a Connect button and will ask for a client id instead. Turning it on lets them find this site's login page."
			)}</p>
			<p class="text-muted small">${__("Missing")}: ${esc(missing.join(", "))} — ${__(
				"these are site-wide OAuth settings, not Orbit's, which is why Orbit will not change them without being asked."
			)}</p>
			<button class="btn btn-sm btn-primary" data-orbit="enable-discovery">${__("Turn on sign-in")}</button>
		</div>
	`;
}

function clients_block(info) {
	const url = info.endpoint;

	const clients = [
		{
			name: "Claude Desktop / Claude on the web",
			body: __("Settings → Connectors → Add custom connector, and paste the URL above."),
		},
		{
			name: "ChatGPT",
			body: __(
				"Settings → Apps → Advanced settings → Developer mode, then add a connector with the URL above."
			),
		},
		{
			name: "Claude Code",
			code: `claude mcp add --transport http orbit ${url}`,
		},
		{
			name: "Cursor / VS Code",
			code: JSON.stringify({ mcpServers: { orbit: { url } } }, null, 2),
		},
	];

	return `
		<div>
			<h5>${__("Where to paste it")}</h5>
			${clients
				.map(
					(client, index) => `
				<div style="margin-bottom:14px">
					<b>${esc(client.name)}</b>
					${client.body ? `<div class="text-muted" style="margin-top:2px">${esc(client.body)}</div>` : ""}
					${
						client.code
							? `<div style="margin-top:6px;position:relative">
									<pre style="margin:0;padding:10px;white-space:pre-wrap;word-break:break-all">${esc(client.code)}</pre>
									<button class="btn btn-xs btn-default" data-orbit="copy-code" data-index="${index}"
										style="position:absolute;top:6px;right:6px">${__("Copy")}</button>
									<textarea data-orbit="code-${index}" style="display:none">${esc(client.code)}</textarea>
								</div>`
							: ""
					}
				</div>
			`
				)
				.join("")}
			<p class="text-muted small" style="margin-top:16px">
				${__(
					"Whoever connects signs in as themselves and sees only what their Frappe permissions already allow. Every call is recorded in Orbit Audit Log, refusals included."
				)}
			</p>
		</div>
	`;
}

function wire_actions(dialog, frm, info) {
	const $body = dialog.fields_dict.body.$wrapper;

	$body.find('[data-orbit="copy-endpoint"]').on("click", () => {
		frappe.utils.copy_to_clipboard(info.endpoint);
	});

	$body.find('[data-orbit="copy-code"]').on("click", function () {
		const index = $(this).attr("data-index");
		const text = $body.find(`[data-orbit="code-${index}"]`).val();
		frappe.utils.copy_to_clipboard(text);
	});

	$body.find('[data-orbit="enable-discovery"]').on("click", () => {
		frappe.call({
			method: "orbit.connect.enable_discovery",
			freeze: true,
			callback() {
				frappe.show_alert({ message: __("Sign-in is now advertised."), indicator: "green" });
				dialog.hide();
				open_connect_dialog(frm);
			},
		});
	});
}
