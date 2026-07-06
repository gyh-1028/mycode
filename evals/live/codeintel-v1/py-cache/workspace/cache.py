from keys import make_cache_key

def lookup(tenant: str, user: str) -> str:
    return make_cache_key(tenant, user)
