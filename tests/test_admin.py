from admin import extract_code


def test_extract_code_strips_non_digits():
    assert extract_code("1 2 3 4 5") == "12345"
    assert extract_code("a1b2c3") == "123"
    assert extract_code("") == ""
