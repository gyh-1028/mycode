def make_cache_key(tenant: str, user: str) -> str:
    return f"{tenant}:{user}"
