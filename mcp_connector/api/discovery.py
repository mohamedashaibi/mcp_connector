"""Serves the OAuth discovery documents MCP clients read before signing a person in.

    GET /.well-known/oauth-protected-resource[/api/method/mcp_connector.api.mcp.endpoint]
    GET /.well-known/oauth-authorization-server

Registered as a Frappe `page_renderer`, so the website router hands these paths to it
before its own pages. The documents are public by design: they only name URLs.
"""

import json

from frappe.utils import get_url
from frappe.website.page_renderers.base_renderer import BaseRenderer
from werkzeug.wrappers import Response

from mcp_connector.api.mcp import ENDPOINT_PATH
from mcp_connector.domain import oauth


class OAuthDiscoveryRenderer(BaseRenderer):
	def _document(self):
		if not self.path.startswith(".well-known/oauth-"):
			return None
		return oauth.discovery_document(self.path, get_url(), ENDPOINT_PATH)

	def can_render(self):
		return self._document() is not None

	def render(self):
		response = Response(json.dumps(self._document()), status=200, mimetype="application/json")
		response.headers["Access-Control-Allow-Origin"] = "*"  # browser-based MCP clients read it too
		response.headers["Cache-Control"] = "public, max-age=3600"
		return response
