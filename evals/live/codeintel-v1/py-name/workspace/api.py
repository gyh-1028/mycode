from names import clean_name

def user_payload(name: str) -> dict[str, str]:
    return {"name": clean_name(name)}
