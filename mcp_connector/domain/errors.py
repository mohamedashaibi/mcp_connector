class McpDomainError(Exception):
	"""Base for errors that are safe to show to the MCP client verbatim."""


class PolicyDenied(McpDomainError):
	"""The caller's MCP access profile does not allow this."""


class InvalidArguments(McpDomainError):
	"""Tool arguments failed validation."""


class NotFound(McpDomainError):
	"""The requested record does not exist or is outside the caller's scope."""


class ErpFailure(McpDomainError):
	"""An ERP-side failure translated by the infrastructure layer.

	kind is one of: permission, not_found, validation, conflict.
	"""

	def __init__(self, kind: str, message: str):
		super().__init__(message)
		self.kind = kind
