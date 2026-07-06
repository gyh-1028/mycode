"""Allow ``python -m mycode`` to invoke the CLI."""

from mycode.cli import app

if __name__ == "__main__":
    app()
