def can_edit(role: str) -> bool:
    return role in {"admin", "editor"}
