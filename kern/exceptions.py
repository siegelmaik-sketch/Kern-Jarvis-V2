"""
Kern-Jarvis V2 — Custom Exceptions
═══════════════════════════════════
Exception hierarchy: KernError -> specific errors.
Raise low, catch high.
"""


class KernError(Exception):
    """Base exception for all Kern-Jarvis errors."""


class LLMError(KernError):
    """Raised when an LLM API call fails."""


class ConfigError(KernError):
    """Raised when configuration is missing or invalid."""


class ToolError(KernError):
    """Raised when tool execution fails."""


class ToolSecurityError(ToolError):
    """Raised when a tool operation violates security constraints."""


class MCPError(KernError):
    """Raised when MCP server communication fails."""


class WebSearchError(KernError):
    """Base exception for web search / fetch operations."""


class WebSearchAPIError(WebSearchError):
    """Raised when the search backend (SearXNG) is unreachable or returns an error."""


class WebFetchError(WebSearchError):
    """Raised when fetching or extracting a URL fails."""
