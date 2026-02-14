from control_plane.auth import hash_token, new_session_token, secure_compare_token


def test_token_hash_and_compare():
    token = "abc123"
    hashed = hash_token(token)
    assert secure_compare_token(token, hashed)
    assert not secure_compare_token("wrong", hashed)


def test_new_session_token_has_expiry():
    token, expiry = new_session_token(hours=1)
    assert token
    assert expiry
