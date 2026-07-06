from names import join_names

def test_join():
    assert join_names(["Ada", "Lin"]) == "Ada, Lin"
