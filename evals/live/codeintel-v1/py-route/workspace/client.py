from urls import user_url

def fetch_path(user_id: int) -> str:
    return user_url(user_id)
