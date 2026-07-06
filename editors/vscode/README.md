# MyCode for VS Code

Run the local-first MyCode coding agent from a workspace Session tree and Agent
view. The extension supports streaming responses, tool activity, cancellation,
permission decisions, and asking about the current editor selection.

Install `mycode-ai-cli` in the same local or remote environment as the VS Code
workspace, configure its provider/API key, then open the MyCode activity view.
Use the `mycode.executable` setting when the command is not on the extension
host's PATH.

MyCode's path checks, command blacklist, MCP trust, and confirmations are basic
protections, not a security sandbox. Use containers or restricted OS users for
real isolation.
