from policy import can_edit

def update(role: str) -> bool:
    return can_edit(role)
