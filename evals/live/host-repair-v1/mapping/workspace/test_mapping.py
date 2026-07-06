from mapping import invert

def test_invert():
    assert invert({"a": 1}) == {1: "a"}
