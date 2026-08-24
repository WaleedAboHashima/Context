# Orbit

Connect any AI agent to your **Frappe** or **ERPNext** site.

Install the app, tick one box, and paste one URL into Claude, ChatGPT, Cursor or any
other MCP client. The agent can then read your records, run your reports, and — only if
you allow it — draft and submit documents.

```bash
bench get-app https://github.com/WaleedAboHashima/orbit
bench --site your-site install-app orbit
```

Then: **Desk → Orbit Settings → Enabled**, and give your AI client this URL:

```
https://your-site.com/api/method/orbit.api.mcp
```

That's the whole integration. No API keys to mint and hand around, no local software to
install, nothing to run on your laptop.

---

## Why install this instead of a local MCP server

There is a standalone Node version of Orbit that runs on your own machine. It works, and
developers may prefer it. This one is better for everyone else, for four reasons:

**Nobody has to handle secrets.** Authentication is your site's own — an OAuth token or a
session. No API key gets pasted into a config file on somebody's laptop.

**Permissions are per-person, automatically.** Every call runs as the signed-in user.
The warehouse clerk's agent sees what the warehouse clerk sees. A shared API key gives
everyone one identity, which is the thing that stops a real company deploying this.

**It's configured in the desk, not in environment variables.** Checkboxes, on a settings
page, changeable by whoever administers the site.

**It works with ChatGPT.** ChatGPT only accepts remote MCP servers over HTTPS; it cannot
launch a local one. So can Claude, Cursor, VS Code and the rest.

---

## What it does well

### It doesn't drown the agent in nulls

A Sales Order has upwards of two hundred fields, nearly all empty, and its child tables
carry eighty more each. Serialising one as JSON costs a couple of thousand tokens to say
that a customer ordered twenty of something.

Orbit renders instead of serialising. Measured on real ERPNext demo data with
`bench --site your-site execute orbit.selftest.measure`, a Sales Order drops from ~1,840
tokens to ~530 and a Sales Invoice from ~2,130 to ~670 — **around 70% less**, on your own
documents rather than a benchmark:

```
Sales Order SAL-ORD-2026-00001

name: SAL-ORD-2026-00001
company: My Company (Demo)
customer: Grant Plastics Ltd.
transaction_date: 2026-02-19
grand_total: 20000.0
status: Completed

items: 1 row
  item_code | delivery_date | qty  | rate   | amount
  --------- | ------------- | ---- | ------ | -------
  SKU004    | 2026-02-19    | 20.0 | 1000.0 | 20000.0
  (86 empty or unlisted columns hidden - name the fields you need)

taxes: 0 rows

(56 empty and 4 framework fields omitted - pass verbose: true to see all 124)
```

Four rules do it:

1. **Lists and child tables become tables**, so field names are written once instead of
   once per row.
2. **Empty values are dropped**, including cleared checkboxes. Numeric zeros are kept —
   a zero outstanding amount is an answer.
3. **Child tables show the columns their grid shows.** A Sales Order Item has eighty
   fields; the desk's grid shows five, chosen by the people who use this site. Orbit
   uses those, which is why the child table above is five columns and not eighty.
4. **What was dropped is always stated.** This is what makes the other three safe — a
   model told what it cannot see will ask for it, where a model handed a silently
   trimmed document concludes the fields do not exist.

### It reads your customisations

Nothing about ERPNext is hardcoded. Orbit asks `frappe.get_meta` what fields exist, what
is mandatory, what Link fields point at, and which fields your list view shows — and uses
that last one to choose sensible defaults. A site with forty custom fields on Sales Order
works exactly as well as a fresh install.

### It turns errors into one sentence

Frappe answers a rejected write with a Python traceback. Forwarded to an agent that costs
a thousand tokens and tells it nothing it can act on. Orbit keeps the part that matters
and sends the traceback to your Error Log instead:

```
Frappe refused to save this: a validation rule failed. Row #1: Item Code is required
```

---

## The tools

Twelve, of which seven exist in the default read-only configuration. DocType is a
*parameter*, not a tool — a site has hundreds of DocTypes and one tool each would fill
the agent's context before the conversation started.

| Tool | |
| --- | --- |
| `frappe_whoami` | Who Orbit is acting as, and what it may do. |
| `frappe_search_doctypes` | Find record types by keyword — only those the user can read. |
| `frappe_describe_doctype` | Fields, types, link targets, what is mandatory. |
| `frappe_list_documents` | Filter, sort, page. Always reports the true total. |
| `frappe_count_documents` | "How many" without fetching rows. |
| `frappe_get_document` | One document, child tables summarised. |
| `frappe_run_report` | Any query or script report. |
| `frappe_create_document` | Create a draft. Needs **Allow create and update**. |
| `frappe_update_document` | Patch fields. Needs **Allow create and update**. |
| `frappe_submit_document` | Submit — posts to the ledger. Needs **Allow submit and cancel**. |
| `frappe_cancel_document` | Reverse a submitted document. Needs **Allow submit and cancel**. |
| `frappe_delete_document` | Permanent. Needs **Allow delete**. |

---

## Safety

Connecting an agent to a production ERP deserves more than hoping it behaves.

**Installing changes nothing.** Orbit adds no assets, no boot payload, no document hooks
and no scheduled jobs. It is one endpoint and two DocTypes, and every switch is off until
an administrator turns it on.

**Frappe's permissions are the permission model.** There is no `ignore_permissions` in
this app and no service account. Reads go through `frappe.get_list`, writes through the
document API, and each document's `check_permission` is called explicitly. Orbit only ever
subtracts.

**Three switches, not one.** Saving a draft, posting it to the ledger, and deleting it are
different decisions. An agent that drafts and never submits is a supported configuration.

**Disabled tools are absent, not failing.** With writes off, the write tools do not appear
in the agent's tool list at all. It cannot be tempted by a tool it cannot see.

**Some DocTypes are never writable.** `Server Script`, `Client Script`, `Custom Field`,
`DocType`, `User`, `Role`, `Webhook` and the rest of the records that change what the site
*does* — as opposed to its business data — are refused regardless of settings.

**Everything is logged.** Every tool call becomes an Orbit Audit Log entry, refusals
included — refusals being the interesting half. The log is read-only in the desk and
written with elevated permission, so the agent whose actions it records cannot edit it.
Arguments are logged only if you opt in, because a tool call can carry customer data.

**Restrict to a role.** The narrowest control available, and the first one to reach for.

---

## Settings

**Orbit Settings** (Single, System Manager only):

| | |
| --- | --- |
| Enabled | Off on install. While off, the endpoint refuses everything. |
| Restrict to role | Only users with this role may use Orbit at all. |
| Allow create and update | Drafts only. |
| Allow submit and cancel | Posts to the ledger; leaves a permanent trail. |
| Allow delete | No undo. |
| Allowed DocTypes | If set, the only DocTypes Orbit touches — reads included. |
| Never write these DocTypes | Added to the built-in list. |
| Max rows per call | Default 20. A ceiling on how much context one call can consume. |
| Log every tool call | On by default. |
| Include arguments in the log | Off by default. |

---

## Connecting a client

**Claude Code / Claude Desktop / Cursor** — add a remote MCP server pointing at
`https://your-site.com/api/method/orbit.api.mcp`.

**ChatGPT** — Settings → Apps → Advanced → Developer mode, then add the same URL as a
custom connector.

**Checking it works** — open `https://your-site.com/api/method/orbit.health` while signed
in. It reports the user, whether Orbit is on, and which tools are currently advertised.
That answers "is the connector broken or is my URL wrong" without speaking JSON-RPC by
hand.

---

## Protocol notes

Orbit implements MCP's Streamable HTTP transport as a single POST endpoint: `initialize`,
`ping`, `tools/list`, `tools/call`, and JSON-RPC batches. `GET` returns 405 — every tool
returns a complete result, so there is nothing to stream and a silent SSE channel would be
a moving part with no purpose.

Tool failures come back as a successful result with `isError`, not as a JSON-RPC error.
The distinction matters: a protocol error tells the client Orbit is broken, while a tool
error tells the model its request was wrong and it should try differently.

## Development

```bash
bench --site your-site run-tests --app orbit

# End to end, against real data on your own site. Read-only: it never calls a
# write tool, so it is safe on production.
bench --site your-site execute orbit.selftest.run

# Reproduce the token figures above on your own documents.
bench --site your-site execute orbit.selftest.measure
```

The renderer's tests import nothing from frappe, so they also run standalone:

```bash
cd apps/orbit && PYTHONPATH=. python3 -m unittest orbit.tests.test_render -v
```

## License

MIT
