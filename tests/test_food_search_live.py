"""Live regression tests for MyFitnessPal's real food database."""

import pytest

from mfp_mcp import server


@pytest.mark.live
def test_chicken_breast_search_returns_nonzero_results():
    server.load_environment()
    client = server.get_mfp_client()
    results = server.search_foods_web(client, "chicken breast", limit=10)

    assert len(results) > 0
    assert all(result["mfp_id"] for result in results)
