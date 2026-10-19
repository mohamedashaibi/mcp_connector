import unittest

from mcp_connector.application.console import AllowChatGPTRedirect, GetChatGPTConnector
from mcp_connector.domain import oauth
from mcp_connector.domain.errors import InvalidArguments

SITE = "https://erp.example.com/"
ENDPOINT = "/api/method/mcp_connector.api.mcp.endpoint"
CHATGPT_CALLBACK = "https://chatgpt.com/connector/oauth/abc_123-DEF"


class FakeOAuthClients:
	def __init__(self):
		self.clients = {}

	def find(self, app_name):
		return self.clients.get(app_name)

	def create(self, app_name, redirect_uris):
		self.clients[app_name] = {"name": app_name, "client_id": "id-1", "client_secret": "s", "redirect_uris": list(redirect_uris)}
		return self.clients[app_name]

	def add_redirect_uri(self, name, redirect_uri):
		self.clients[name]["redirect_uris"].append(redirect_uri)
		return self.clients[name]


class TestOAuthDiscovery(unittest.TestCase):
	def test_challenge_points_at_path_aware_resource_metadata(self):
		self.assertEqual(
			oauth.bearer_challenge(SITE, ENDPOINT),
			'Bearer resource_metadata="https://erp.example.com/.well-known/oauth-protected-resource'
			'/api/method/mcp_connector.api.mcp.endpoint", scope="all"',
		)

	def test_resource_metadata_names_the_endpoint_and_the_site_as_authorization_server(self):
		doc = oauth.protected_resource_metadata(SITE, ENDPOINT)
		self.assertEqual(doc["resource"], "https://erp.example.com" + ENDPOINT)
		self.assertEqual(doc["authorization_servers"], ["https://erp.example.com"])
		self.assertEqual(doc["bearer_methods_supported"], ["header"])

	def test_authorization_server_metadata_advertises_pkce_and_frappe_endpoints(self):
		doc = oauth.authorization_server_metadata(SITE)
		self.assertEqual(doc["issuer"], "https://erp.example.com")
		self.assertEqual(doc["code_challenge_methods_supported"], ["S256"])
		self.assertEqual(doc["token_endpoint_auth_methods_supported"], ["client_secret_post"])
		self.assertEqual(doc["authorization_endpoint"], "https://erp.example.com/api/method/frappe.integrations.oauth2.authorize")
		self.assertEqual(doc["token_endpoint"], "https://erp.example.com/api/method/frappe.integrations.oauth2.get_token")
		self.assertEqual(doc["response_types_supported"], ["code"])

	def test_openid_is_not_advertised(self):
		# ChatGPT asks for every OIDC scope it sees here, and Frappe fails to refresh a token that has
		# `openid` (403 "No permission for User"), so the connection would die when it first expires.
		self.assertEqual(oauth.authorization_server_metadata(SITE)["scopes_supported"], ["all"])

	def test_documents_are_served_only_at_their_well_known_paths(self):
		as_doc = oauth.discovery_document(".well-known/oauth-authorization-server", SITE, ENDPOINT)
		self.assertEqual(as_doc["issuer"], "https://erp.example.com")
		for path in (".well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource" + ENDPOINT + "/"):
			self.assertEqual(oauth.discovery_document(path, SITE, ENDPOINT)["resource"], "https://erp.example.com" + ENDPOINT)
		for path in (".well-known/oauth-protected-resource/api/method/frappe.client.get", ".well-known/openid-configuration", "about", ""):
			self.assertIsNone(oauth.discovery_document(path, SITE, ENDPOINT), path)

	def test_claude_callbacks_are_https_on_claude_domains(self):
		self.assertTrue(oauth.CLAUDE_REDIRECT_URIS)
		for uri in oauth.CLAUDE_REDIRECT_URIS:
			self.assertRegex(uri, r"^https://claude\.(ai|com)/api/mcp/auth_callback$")


class TestChatGPTConnector(unittest.TestCase):
	def test_only_chatgpt_connector_callbacks_are_accepted(self):
		for uri in (CHATGPT_CALLBACK, f"  {CHATGPT_CALLBACK}\n", "https://chatgpt.com/connector_platform_oauth_redirect"):
			self.assertEqual(oauth.chatgpt_redirect_uri(uri), uri.strip())
		for uri in (
			None,
			42,
			"",
			"http://chatgpt.com/connector/oauth/abc",
			"https://chatgpt.com.evil.example/connector/oauth/abc",
			"https://evil.example/https://chatgpt.com/connector/oauth/abc",
			"https://user@chatgpt.com/connector/oauth/abc",
			"https://chatgpt.com:8443/connector/oauth/abc",
			"https://chatgpt.com/connector/oauth/",
			"https://chatgpt.com/connector/oauth/..",
			"https://chatgpt.com/connector/oauth/abc/extra",
			"https://chatgpt.com/connector/oauth/abc?next=https://evil.example",
			"https://chatgpt.com/connector/oauth/abc#fragment",
			# Frappe splits the stored list on spaces: one pasted value must never become two callbacks.
			f"{CHATGPT_CALLBACK} https://evil.example/callback",
			f"{CHATGPT_CALLBACK}\nhttps://evil.example/callback",
			"https://chatgpt.com/connector/oauth/" + "a" * 201,
		):
			with self.assertRaises(InvalidArguments, msg=repr(uri)):
				oauth.chatgpt_redirect_uri(uri)

	def test_first_callback_creates_the_client_and_later_ones_join_it(self):
		clients = FakeOAuthClients()
		self.assertIsNone(GetChatGPTConnector(clients).execute()["client"])  # positive control: nothing yet
		created = AllowChatGPTRedirect(clients).execute(f" {CHATGPT_CALLBACK} ")["client"]
		self.assertEqual((created["name"], created["redirect_uris"]), (oauth.CHATGPT_CLIENT_NAME, [CHATGPT_CALLBACK]))
		other = "https://chatgpt.com/connector/oauth/second"
		for uri in (other, other, CHATGPT_CALLBACK):  # repeats change nothing
			client = AllowChatGPTRedirect(clients).execute(uri)["client"]
		self.assertEqual(client["redirect_uris"], [CHATGPT_CALLBACK, other])
		self.assertEqual(client["client_id"], created["client_id"])
		self.assertEqual(GetChatGPTConnector(clients).execute()["client"]["client_id"], created["client_id"])

	def test_a_refused_callback_creates_nothing(self):
		clients = FakeOAuthClients()
		with self.assertRaises(InvalidArguments):
			AllowChatGPTRedirect(clients).execute("https://claude.ai/api/mcp/auth_callback")
		self.assertEqual(clients.clients, {})


if __name__ == "__main__":
	unittest.main()
