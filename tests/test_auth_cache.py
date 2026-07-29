from mfp_mcp import auth


def test_authenticated_client_is_cached(monkeypatch):
    expected = object()
    calls = 0

    def create():
        nonlocal calls
        calls += 1
        return expected

    monkeypatch.setattr(auth, "_cached_client", None)
    monkeypatch.setattr(auth, "_create_mfp_client", create)

    assert auth.get_mfp_client() is expected
    assert auth.get_mfp_client() is expected
    assert calls == 1


def test_cached_client_can_be_cleared(monkeypatch):
    monkeypatch.setattr(auth, "_cached_client", object())
    auth.clear_cached_mfp_client()
    assert auth._cached_client is None
