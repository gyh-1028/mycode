def ensure_py(name: str) -> str:
    return name if name.endswith(".py") else name + ".py"
