import unittest

from mcp_connector.domain import oauth

SITE = "https://erp.example.com/"
ENDPOINT = "/api/method/mcp_connector.api.mcp.endpoint"


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


if __name__ == "__main__":
	unittest.main()
