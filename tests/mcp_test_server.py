"""A minimal MCP stdio server for testing mycode's MCP client integration.

Run as a subprocess via ``python tests/mcp_test_server.py``. Provides:

* Tools: ``echo(text)``, ``add(a, b)``, ``fail(message)``
* Resources: ``test://data``, ``test://count``
* Prompts: ``greet(name)``, ``summarize(topic)``

The ``fail`` tool always returns an error result, for testing error handling.
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("mycode-test")


@mcp.tool()
def echo(text: str) -> str:
    """Echo back the input text."""
    return text


@mcp.tool()
def add(a: int, b: int) -> str:
    """Add two numbers and return the result as a string."""
    return str(a + b)


@mcp.tool()
def fail(message: str = "intentional failure") -> str:
    """Always raises an error (for testing error handling)."""
    raise RuntimeError(message)


@mcp.resource("test://data")
def get_data() -> str:
    """Return fixed test data."""
    return "hello from MCP resource"


@mcp.resource("test://count")
def get_count() -> str:
    """Return a count value."""
    return "42"


@mcp.prompt()
def greet(name: str) -> str:
    """Generate a greeting prompt."""
    return f"Please greet the user named {name}."


@mcp.prompt()
def summarize(topic: str) -> str:
    """Generate a summarization prompt."""
    return f"Please summarize the following topic: {topic}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
