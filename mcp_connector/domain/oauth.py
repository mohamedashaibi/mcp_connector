"""OAuth discovery for MCP clients that sign people in, such as Claude custom connectors.

Frappe is the authorization server: its frappe.integrations.oauth2 endpoints run the login,
consent and PKCE authorization-code flow, and Frappe validates bearer tokens on every request
before the MCP endpoint runs. These rules only describe that server in the shapes MCP clients
discover: protected resource metadata (RFC 9728) and authorization server metadata (RFC 8414).
"""

PROTECTED_RESOURCE_PATH = "/.well-known/oauth-protected-resource"
AUTHORIZATION_SERVER_PATH = "/.well-known/oauth-authorization-server"
FRAPPE_OAUTH_METHODS = "/api/method/frappe.integrations.oauth2."

# Frappe's scope for acting through the API as the signed-in user.
SCOPES = ("all",)

# Where Claude (web, desktop and mobile apps) receives the authorization code.
CLAUDE_REDIRECT_URIS = (
	"https://claude.ai/api/mcp/auth_callback",
	"https://claude.com/api/mcp/auth_callback",
)
CLAUDE_CLIENT_NAME = "Claude (MCP Connector)"


def _site(site_url: str) -> str:
	return site_url.rstrip("/")


def resource_url(site_url: str, endpoint_path: str) -> str:
	return _site(site_url) + endpoint_path


def resource_metadata_url(site_url: str, endpoint_path: str) -> str:
	"""The path-aware metadata location clients derive from the endpoint URL (RFC 9728 §3.1)."""
	return _site(site_url) + PROTECTED_RESOURCE_PATH + endpoint_path


def bearer_challenge(site_url: str, endpoint_path: str) -> str:
	"""WWW-Authenticate value for a request without credentials."""
	return f'Bearer resource_metadata="{resource_metadata_url(site_url, endpoint_path)}", scope="{" ".join(SCOPES)}"'


def protected_resource_metadata(site_url: str, endpoint_path: str) -> dict:
	return {
		"resource": resource_url(site_url, endpoint_path),
		"authorization_servers": [_site(site_url)],
		"scopes_supported": list(SCOPES),
		"bearer_methods_supported": ["header"],
		"resource_name": "ERPNext MCP Connector",
	}


def authorization_server_metadata(site_url: str) -> dict:
	site = _site(site_url)
	return {
		"issuer": site,
		"authorization_endpoint": f"{site}{FRAPPE_OAUTH_METHODS}authorize",
		"token_endpoint": f"{site}{FRAPPE_OAUTH_METHODS}get_token",
		"revocation_endpoint": f"{site}{FRAPPE_OAUTH_METHODS}revoke_token",
		"response_types_supported": ["code"],
		"grant_types_supported": ["authorization_code", "refresh_token"],
		"code_challenge_methods_supported": ["S256"],
		# Frappe reads client credentials from the form body only: it would take an HTTP Basic
		# header on the token request for an API key and reject the call.
		"token_endpoint_auth_methods_supported": ["client_secret_post"],
		"scopes_supported": [*SCOPES, "openid"],
	}


def discovery_document(path: str, site_url: str, endpoint_path: str) -> "dict | None":
	"""The metadata document served at `path` (surrounding slashes ignored), or None."""
	path = "/" + path.strip("/")
	if path == AUTHORIZATION_SERVER_PATH:
		return authorization_server_metadata(site_url)
	if path in (PROTECTED_RESOURCE_PATH, PROTECTED_RESOURCE_PATH + endpoint_path):
		return protected_resource_metadata(site_url, endpoint_path)
	return None
