# MCP Connector for ERPNext / Frappe v14 and v15

Connect AI assistants (Claude, ChatGPT, Cursor, VS Code Copilot, any MCP client) to ERPNext through the
[Model Context Protocol](https://modelcontextprotocol.io), with access you control down to the
role, action type, action, DocType, field, record and report.

- **Deny by default.** Nothing is reachable until an *access profile* grants it.
- **Profiles per role or user.** Everyone holding a role (or a named integration user) gets that profile's grants. When several profiles match, each is a separate alternative — never blended.
- **Five levels of control:** action types (Read, Create, Update, Delete, Submit, Cancel, Reports, Custom) → actions (individual MCP tools) → DocTypes (per-operation matrix) → fields (all / only / all-except) and record scope (filters) → reports (with enforced fixed filters).
- **ERPNext permissions always apply on top.** Every call runs as the real ERPNext user; a profile can only narrow what that user could already do.
- **MCP Console** — a desk page to build profiles, inspect what any user's AI can do, watch activity, and generate ready-to-paste client configuration.
- **Audit trail** of every tool call, with secrets masked.

One codebase for both versions, tested on Frappe 14.100.0 / ERPNext v14.92.12 (Python 3.10) and
Frappe 15.121.1 / ERPNext v15.121.4 (Python 3.11). The app does not depend on ERPNext; it works on
any Frappe v14 or v15 site.

---

## Contents

1. [Install on your server](#1-install-on-your-server)
2. [First-time setup](#2-first-time-setup)
3. [Connect an AI client](#3-connect-an-ai-client)
4. [How access is decided](#4-how-access-is-decided)
5. [Tools the AI gets](#5-tools-the-ai-gets)
6. [Custom actions](#6-custom-actions)
7. [Security notes](#7-security-notes)
8. [Upgrade and uninstall](#8-upgrade-and-uninstall)
9. [Limitations](#9-limitations)
10. [Development and tests](#10-development-and-tests)

---

## 1. Install on your server

You need shell access to the server as the user that owns the bench (usually `frappe`), and the
bench must be on Frappe **v14** or **v15**. Replace `erp.example.com` with your site name
(`ls ~/frappe-bench/sites` shows it).

### 1.1 Give the server read access to this private repository

Pick **one** of these.

**Option A — SSH deploy key (recommended: read-only, scoped to this repo)**

```bash
# on the server, as the bench user
ssh-keygen -t ed25519 -C "erp-server mcp_connector" -f ~/.ssh/mcp_connector_deploy -N ""
cat ~/.ssh/mcp_connector_deploy.pub
```

On GitHub open **mohamedashaibi/mcp_connector → Settings → Deploy keys → Add deploy key**, paste the
public key, leave *Allow write access* **off**. Then tell SSH to use that key for this repo:

```bash
cat >> ~/.ssh/config <<'EOF'
Host github-mcp-connector
    HostName github.com
    User git
    IdentityFile ~/.ssh/mcp_connector_deploy
    IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config
ssh -T github-mcp-connector   # should greet you with "successfully authenticated"
```

Repository URL to use below: `git@github-mcp-connector:mohamedashaibi/mcp_connector.git`

**Option B — HTTPS with a fine-grained personal access token**

Create a token at GitHub → Settings → Developer settings → Fine-grained tokens with
*Repository access: only mohamedashaibi/mcp_connector* and *Contents: Read-only*.

Repository URL to use below: `https://<TOKEN>@github.com/mohamedashaibi/mcp_connector.git`
(after installing, remove the token from the stored remote — see step 1.3).

### 1.2 Get the app and install it on the site

```bash
cd ~/frappe-bench

# optional but wise: back up the site first
bench --site erp.example.com backup --with-files

bench get-app --branch main <REPOSITORY_URL>
bench --site erp.example.com install-app mcp_connector
bench --site erp.example.com clear-cache
```

Restart the application processes so the workers load the new code:

```bash
# production bench managed by supervisor (the usual setup)
sudo supervisorctl restart all
# or, if your bench user is allowed to: bench restart
```

No `bench build` is needed: the console loads its own script and styles.

### 1.3 (Option B only) remove the token from the stored remote

```bash
cd ~/frappe-bench/apps/mcp_connector
git remote -v                      # bench usually names it "upstream"
git remote set-url upstream https://github.com/mohamedashaibi/mcp_connector.git
```

For later updates you will supply the token again (or switch to a deploy key).

### 1.4 Production checklist

- **HTTPS.** Serve the site over TLS before connecting clients across the internet
  (`sudo bench setup lets-encrypt erp.example.com` on a standard bench). The API key travels in a header on every request.
- **Reverse proxy timeouts.** Large reports can take a while. If you see 502/504 on `run_report`,
  raise `proxy_read_timeout` in the site's nginx config and the gunicorn timeout
  (`bench config http_timeout 180 && bench setup supervisor && sudo supervisorctl reload`).
- **Server scripts** are only needed if you expose API Server Scripts as custom actions.
  On v14: `bench --site erp.example.com set-config server_script_enabled true`. On v15 the
  setting is bench-wide and the site-level one is ignored: `bench set-config -g server_script_enabled 1`.
- **Firewall / exposure.** The endpoint lives at `/api/method/mcp_connector.api.mcp.endpoint` on
  your existing site; nothing new to open. Restrict who can reach the site as you would for the ERPNext API.

### 1.5 Docker (frappe_docker) installs

With the official `frappe_docker` setup you bake apps into a custom image. Add this repository to
`apps.json` (a private repo needs the token in the URL, or build with an SSH secret). The app's
`main` branch serves both versions; only the ERPNext branch and the build arguments change:

| | ERPNext branch | `FRAPPE_BRANCH` | `PYTHON_VERSION` | `NODE_VERSION` |
| --- | --- | --- | --- | --- |
| v14 | `version-14` | `version-14` | `3.10.13` | `16.20.2` |
| v15 | `version-15` | `version-15` | `3.11.16` | `22.23.3` |

These are the versions inside the official `frappe/erpnext` v14.92.12 and v15.121.4 images the
app is tested on. Example for v15:

```json
[
  { "url": "https://github.com/frappe/erpnext", "branch": "version-15" },
  { "url": "https://<TOKEN>@github.com/mohamedashaibi/mcp_connector.git", "branch": "main" }
]
```

```bash
export APPS_JSON_BASE64=$(base64 -w 0 apps.json)
docker build \
  --build-arg=FRAPPE_PATH=https://github.com/frappe/frappe \
  --build-arg=FRAPPE_BRANCH=version-15 \
  --build-arg=PYTHON_VERSION=3.11.16 \
  --build-arg=NODE_VERSION=22.23.3 \
  --build-arg=APPS_JSON_BASE64=$APPS_JSON_BASE64 \
  --tag=my-erpnext:15-mcp \
  --file=images/custom/Containerfile .
```

Point your compose file at `my-erpnext:15-mcp` (or your v14 tag), restart the stack, then run once:

```bash
docker compose exec backend bench --site erp.example.com install-app mcp_connector
```

---

## 2. First-time setup

Log in as **Administrator** and open **`/app/mcp-console`** (or the *MCP Connector* workspace).
Only the Administrator can see the workspace, open the console, or read and change profiles,
actions, settings and the audit log. Other users, System Managers included, don't even see the
sidebar entry, and granting them role permissions on these DocTypes has no effect.

1. **Create an integration user** (*User → New*, type *System User*) such as
   `ai-assistant@example.com`, and give it only the ERPNext roles the AI genuinely needs
   (e.g. *Sales User*). ERPNext permissions of this user are the outer limit of what the AI can do.
2. **Create an access profile** (*Console → Profiles → New profile*):
   - **Who** — add the integration user, or a role.
   - **Capabilities** — switch on action types (start with *Read* and *Reports*) and pick actions.
   - **DocTypes** — add DocTypes one by one, or use **Add several** to pick many at once (search,
     or select a whole module such as Selling or Stock) and grant the same operations to all of them.
     Tick or untick operations per row, or click a column header to toggle it for every row. Use **Fields** to hide columns
     (salaries, bank details…) and **Scope & notes** to restrict records (e.g. `company = ACME`) and
     to leave guidance for the AI.
   - **Reports** — add reports (again with **Add several** for bulk); use **fixed filters** to pin values the AI cannot override.
   - **AI view** shows exactly which tools the profile produces.
3. **Check it** in *Console → Inspector*: pick the integration user and confirm the profiles,
   tools and DocType matrix are what you intended.

---

## 3. Connect an AI client

There are three ways in, and all of them run every request as a real ERPNext user:

- **Claude connector link** (claude.ai, Claude Desktop, Claude mobile): paste the endpoint URL into
  Claude's *Add custom connector*. Each person signs in with their own ERPNext account, so each
  gets exactly their own profile.
- **ChatGPT connector**: the same endpoint and the same per-person sign-in, added in ChatGPT as a
  custom app.
- **API key** (Claude Code, Cursor, VS Code, scripts): the tool signs in as one integration user.

### 3.1 Claude connector link

Claude's custom connectors sign people in with OAuth. The connector uses ERPNext's own OAuth
server for that, so there is nothing extra to run.

Before you start:

- Claude connects from Anthropic's servers, so the site must be reachable over **public HTTPS**.
- ERPNext must announce the same address people use: set it once with
  `bench --site erp.example.com set-config host_name https://erp.example.com`.
- The web server must pass `/.well-known/` to ERPNext. A standard bench nginx configuration does.

Then:

1. In ERPNext, open *Console → Connect → Claude connector link* and choose **Create client for
   Claude**. Copy the client ID and client secret. **Run check** confirms Claude can discover the
   sign-in documents through your address.
2. In Claude, open *Customize → Connectors*, choose **+**, then **Add custom connector**. On Team and
   Enterprise plans an owner does this in *Organization settings → Connectors → Add → Custom → Web*.
   - URL: `https://erp.example.com/api/method/mcp_connector.api.mcp.endpoint`
   - **Advanced settings**: paste the OAuth Client ID and OAuth Client Secret from step 1.
3. Each person chooses **Connect**, signs in to ERPNext and chooses **Allow**. Someone no profile
   covers gets no tools (the endpoint answers 403).

How it works: a request without credentials gets `401` with
`WWW-Authenticate: Bearer resource_metadata="…/.well-known/oauth-protected-resource/api/method/mcp_connector.api.mcp.endpoint"`.
Claude reads that document and `/.well-known/oauth-authorization-server`, runs the
authorization-code flow with PKCE (S256) against Frappe's `frappe.integrations.oauth2.authorize` and
`get_token`, and then sends `Authorization: Bearer <token>`, which Frappe validates before the
connector runs. Access tokens last an hour; Claude refreshes them on its own. The client accepts
only Claude's callbacks, `https://claude.ai/api/mcp/auth_callback` and
`https://claude.com/api/mcp/auth_callback`.

Signing in happens once. Claude keeps an access token (one hour) and a refresh token and renews
access by itself, so people do not need to stay logged in to ERPNext: logging out or closing the
browser does not disconnect Claude. The connection lasts until one of these happens:

- the person disconnects the connector in Claude, or removes it on ERPNext's `/third_party_apps` page;
- you set their *OAuth Bearer Token* to Revoked;
- you disable the ERPNext user (v15 refuses a disabled user's OAuth tokens itself; v14 still accepts
  them, so the connector refuses disabled users on its own);
- you take them out of every profile, which removes their tools at once, or pause the MCP server.

### 3.2 ChatGPT connector

ChatGPT's custom apps sign people in with OAuth as well, against the same endpoint and the same
ERPNext OAuth server. One thing differs from Claude: ChatGPT gives every connector its own redirect
URL, and ERPNext has to allow that URL before anyone can sign in through it.

Before you start, the three requirements of 3.1 apply (public HTTPS, `host_name`, `/.well-known/`).
Custom apps also need ChatGPT's *Developer mode*. Which plans have it, and which of them may run
actions that change data, is OpenAI's decision and changes often, so check their help centre.

1. In ChatGPT, open *Settings → Apps → Advanced settings* and turn on **Developer mode**. Choose
   **Create app**, name it, and paste
   `https://erp.example.com/api/method/mcp_connector.api.mcp.endpoint` as the **MCP Server URL**.
   Set **Authentication** to **OAuth** and open its advanced settings, where you enter your own
   OAuth client. Copy the redirect URL ChatGPT shows there (it looks like
   `https://chatgpt.com/connector/oauth/…`) and keep that page open.
2. In ERPNext, open *Console → Connect → ChatGPT connector*, paste the redirect URL and choose
   **Allow this redirect URL**. The first one creates the client: copy its client ID and client
   secret. **Run check** confirms ChatGPT can discover the sign-in documents through your address.
3. Back in ChatGPT, paste the client ID and client secret, then create the app.
4. Each person connects the app, signs in to ERPNext and chooses **Allow**. Someone no profile
   covers gets no tools (the endpoint answers 403).

ChatGPT renames these menus now and then; what matters is the redirect URL it shows for your app.
Each app has its own, so when you create another one (a second workspace, or an app you deleted
and made again), allow its redirect URL the same way: it joins the same client, and everyone keeps
signing in as themselves. Remove a URL you no longer use on the client's *OAuth Client* form
(**Manage in ERPNext**).

The client accepts only redirect URLs you allowed, and the console allows only ChatGPT's own:
`https://chatgpt.com/connector/oauth/…` and its shared
`https://chatgpt.com/connector_platform_oauth_redirect`. Tokens, refresh and the ways a connection
ends are the same as for Claude in 3.1.

### 3.3 API key clients

*Console → Connect → API key* generates the configuration for you. Manually:

1. Generate API keys for the integration user (*Console → Connect → Generate API key*). Keep the
   secret safe; it is shown once.
2. The endpoint is

   ```
   https://erp.example.com/api/method/mcp_connector.api.mcp.endpoint
   ```

   and every request carries `Authorization: token <api_key>:<api_secret>`.

**Claude Code**

```bash
claude mcp add --transport http erpnext https://erp.example.com/api/method/mcp_connector.api.mcp.endpoint \
  --header "Authorization: token <api_key>:<api_secret>"
```

**Claude Desktop** — *Settings → Developer → Edit Config* (`claude_desktop_config.json`), needs Node.js:

```json
{
  "mcpServers": {
    "erpnext": {
      "command": "npx",
      "args": ["-y", "mcp-remote", "https://erp.example.com/api/method/mcp_connector.api.mcp.endpoint",
               "--header", "Authorization:${AUTH_HEADER}"],
      "env": { "AUTH_HEADER": "token <api_key>:<api_secret>" }
    }
  }
}
```

**Cursor** — `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "erpnext": {
      "url": "https://erp.example.com/api/method/mcp_connector.api.mcp.endpoint",
      "headers": { "Authorization": "token <api_key>:<api_secret>" }
    }
  }
}
```

**VS Code** — `.vscode/mcp.json`:

```json
{
  "servers": {
    "erpnext": {
      "type": "http",
      "url": "https://erp.example.com/api/method/mcp_connector.api.mcp.endpoint",
      "headers": { "Authorization": "token <api_key>:<api_secret>" }
    }
  }
}
```

**Any HTTP client**

```bash
curl -X POST 'https://erp.example.com/api/method/mcp_connector.api.mcp.endpoint' \
  -H 'Authorization: token <api_key>:<api_secret>' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Responses you may see: **401** no/invalid credentials · **403** MCP paused, or no profile applies
to the user · **429** per-user rate limit hit.

---

## 4. How access is decided

A tool call succeeds only if **every** layer allows it:

| # | Layer | Configured in |
|---|---|---|
| 1 | MCP server is enabled | Settings |
| 2 | Request is authenticated as a real, enabled ERPNext user | ERPNext user + API key |
| 3 | At least one **enabled profile** lists the user or one of their roles | Profile → Who |
| 4 | The tool's **action type** is switched on in that profile | Profile → Capabilities |
| 5 | The **action** (tool) is selected in that profile and enabled globally | Profile → Capabilities, Actions |
| 6 | The **DocType operation** (or **report**) is granted in that profile | Profile → DocTypes / Reports |
| 7 | Requested **fields** are visible, and the **record** matches the scope | Profile → Fields, Scope |
| 8 | **ERPNext** role, user and field-level permissions allow it | ERPNext |

When several profiles match a user, each one is an **alternative, never a blend**: a call succeeds
only if a single profile allows the tool, its action type, the operation, every field involved and
the record's scope — together. Combining a profile that shows *all fields for territory X* with one
that shows *only customer names everywhere* therefore gives all fields for territory X and only names
elsewhere, never all fields everywhere. A report's fixed filters come from the profile that grants it.

DocTypes with an active approval workflow are submitted and cancelled only through
`apply_workflow_action` (which respects the workflow's roles); their workflow state field cannot be
written directly.

Some DocTypes are protected regardless of profiles: the connector's own configuration and audit
log, credential stores (OAuth tokens, social login keys…) and System Console are never exposed;
DocTypes that hold code, templates, schema, sharing or permissions (DocType, Property Setter, Server
Script, Print Format, Letter Head, Website and Portal Settings, Address Template, Translation, User,
Role, User Permission, DocShare, Assignment Rule, Report, Workflow…) can be granted **Read** only.

**Custom Field** is the exception: grant it Create (and Update) and the AI can add fields to DocTypes
with `create_document`. The MCP user also needs a role Frappe lets create custom fields (System
Manager by default). Before anything is saved, the connector refuses fields on protected DocTypes;
HTML, HTML Editor and Button fields; Links and Tables pointing at never-exposed DocTypes;
`fetch_from`; expressions in the `depends_on` properties (a plain fieldname is fine) and `eval:` in
any value; and `ignore_xss_filter`, `ignore_user_permissions` and `is_virtual`. A Custom Field row
cannot use Scope filters: saving a custom field alters the table and commits at once, so a scope
check afterwards could not undo it.

**Naming series** ("name new customers `C-.#####`") go through the `set_naming_series` action. Tick
it in the profile and grant Update on the DocType with no Scope and without hiding `naming_series`;
the MCP user also needs write on Document Naming Settings (System Manager by default). The new series
becomes the default, the DocType's other series stay selectable and existing records keep their
names. Customer, Supplier and Item only follow it when Selling, Buying or Stock Settings name them
by "Naming Series" — grant Update on that Settings DocType too if the AI should switch it.

---

## 5. Tools the AI gets

| Action | Type | What it does |
|---|---|---|
| `list_doctypes` | Read | DocTypes available to the caller and the operations allowed on each |
| `describe_doctype` | Read | Fields, child tables, naming, submittable flag, notes |
| `list_documents` | Read | Filtered, searchable, sorted, paged listing |
| `get_document` | Read | One record with child tables and available workflow actions |
| `count_documents` | Read | Count with filters |
| `create_document` | Create | Insert a record (Draft for submittable DocTypes) |
| `update_document` | Update | Change fields; child tables merge by row name |
| `apply_workflow_action` | Update | Approve / Reject … through the DocType's workflow |
| `set_naming_series` | Update | Makes a naming series (e.g. `C-.#####`) the default for new records of a DocType |
| `delete_document` | Delete | Delete a record |
| `submit_document` | Submit | Submit a draft |
| `cancel_document` | Cancel | Cancel a submitted document |
| `list_reports` | Reports | Reports the caller may run |
| `describe_report` | Reports | Report filters and enforced fixed filters |
| `run_report` | Reports | Run a report, truncated to the configured row limit |

Each tool's JSON schema lists only the DocTypes / reports the caller may use, so the model sees its
boundaries up front. You can rewrite any tool description in *Console → Actions*.

---

## 6. Custom actions

*Console → Actions → New custom action* exposes your own logic as an MCP tool:

- **Method** — a `@frappe.whitelist()` Python method (`my_app.api.stock_summary`) or the
  *API method* of an **API Server Script** (no deployment needed; requires `server_script_enabled`).
- **Input schema** — JSON Schema of the arguments; unknown arguments are rejected.
- **Action type** — profiles must switch this type on to use it.

A custom action runs with the caller's ERPNext permissions, but MCP DocType rules do not apply
inside your method — validate what it does. Generic CRUD and auth methods (`frappe.client.*`,
`frappe.desk.form.*`, …) cannot be exposed.

---

## 7. Security notes

- Every request is authenticated by Frappe itself (API key/secret, OAuth bearer token, or a desk
  session with CSRF token) and executes as that user.
- **Claude and ChatGPT connector sign-in** is protected by PKCE, by each client accepting only its
  own callback URLs (Claude's two, and the ChatGPT redirect URLs you allowed), and by each person's
  own ERPNext login and consent. Frappe's token endpoint does not check
  the client secret, so treat the secret as an identifier, not as a barrier. Like API keys, OAuth
  tokens can also reach ERPNext's regular REST API as that person.
- **Profiles govern what the AI does over MCP, not what the key can do elsewhere.** Whoever holds the
  API key could call ERPNext's regular REST API directly with that user's full ERPNext permissions.
  Treat the key like the user's password and give the integration user only the ERPNext roles the AI
  genuinely needs — ERPNext permissions are the outer wall; profiles narrow the AI inside it.
- Arguments are validated against strict schemas; field names, operators and sort keys are
  allow-listed, and filtering or sorting on a hidden field is refused.
- Writes run inside a savepoint: a call that fails, or would move a record outside the caller's
  scope, leaves nothing behind — its queued webhooks, background jobs and realtime events are
  dropped too.
- Every tool call is written to **MCP Audit Log** (who, what, target, result, duration, client IP),
  with passwords, tokens and keys masked. Retention is configurable. Like all of the connector's
  configuration, the audit log is visible to the Administrator only.
- Report Builder reports can only be filtered on their own columns, so hidden fields cannot be
  probed through report filters.
- Browser requests are accepted only from the site itself or origins you list in Settings.
- Per-user rate limiting (default 120 requests/minute).

---

## 8. Upgrade and uninstall

```bash
cd ~/frappe-bench
bench update --apps mcp_connector --pull          # or: git -C apps/mcp_connector pull
bench --site erp.example.com migrate
sudo supervisorctl restart all
```

`migrate` also clears the cached app hooks. If `/.well-known/oauth-protected-resource/…` shows
ERPNext's "Not Found" page after an update, run `bench --site erp.example.com clear-cache`.

**Moving a site from v14 to v15.** Nothing app-specific: the same `main` branch runs on both, so
upgrade Frappe/ERPNext as usual and `migrate`; profiles, actions and logs carry over. If you
expose API Server Scripts as custom actions, re-enable them bench-wide afterwards
(`bench set-config -g server_script_enabled 1`), because v15 ignores the site-level setting.

To remove it: `bench --site erp.example.com uninstall-app mcp_connector` (profiles and logs are
deleted with the app's DocTypes), then `bench remove-app mcp_connector`.

---

## 9. Limitations

- Stateless Streamable HTTP: one JSON response per request; no server-sent events, sessions,
  resources or prompts (tools cover the use case).
- JSON-RPC batch arrays and malformed JSON bodies are rejected by Frappe's request parser before
  the app sees them, so they return Frappe's error format instead of a JSON-RPC error.
- No dynamic client registration: create the Claude or ChatGPT client in the console and paste its
  ID and secret into that product's connector settings. Other OAuth MCP clients need their own
  OAuth Client with their callback URL.
- The ChatGPT connector follows OpenAI's published requirements for OAuth MCP servers; it has not
  been tested end to end against ChatGPT itself.
- OAuth tokens are ERPNext user tokens, not bound to the MCP endpoint as their only audience.
- When one user matches several profiles with *different* record scopes on the same DocType,
  listing merges the scoped queries in memory and can page only within the first 5,000 rows (add
  filters to reach further); the merged order emulates MariaDB's case-insensitive collation.
- Reports whose filters are built dynamically in JavaScript are described without filter details.

---

## 10. Development and tests

The `dev/` folder holds disposable Frappe v14 and v15 benches (Docker) and the verification gates.
Every bench command and Docker gate takes `--bench v14` (the default) or `--bench v15`; the two
benches are separate compose projects, so they can run side by side.

```bash
node dev/bench.mjs up                 # MariaDB + Redis + ERPNext v14 image, site mcp.localhost on :8014
node dev/bench.mjs up --bench v15     # the same on the ERPNext v15 image, on :8015
python dev/gates/unit_gate.py         # framework-free unit tests (policy engine, validators, dispatcher)
python dev/gates/static_gate.py       # layer boundaries, Python 3.10 grammar, no string-built SQL
node dev/gates/install_gate.mjs       # fresh install on a new site
node dev/gates/integration_gate.mjs   # Frappe integration suite (add --bench v15 for v15)
cd dev && npm install && cd ..        # once, for the next two gates
node dev/gates/mcp_e2e_gate.mjs       # official MCP SDK client over HTTP
node dev/gates/ui_gate.mjs            # console in Chromium: full workflow, light/dark/narrow
node dev/gates/upgrade_gate.mjs       # v14 site -> backup, restore on v15, migrate; data kept
node dev/bench.mjs down               # remove everything (per bench)
```

Code layout: `mcp_connector/domain` (pure policy rules), `mcp_connector/application` (use cases
behind ports), `mcp_connector/infrastructure` (Frappe adapters), `mcp_connector/api` (HTTP entry
points), `mcp_connector/mcp_connector` (DocTypes, console page, workspace).

License: MIT.
