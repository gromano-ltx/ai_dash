def test_fixture_serves_existing_providers_endpoint(test_client, monkeypatch):
    import backend.main as main_module

    # Force auth off regardless of the developer's shell environment —
    # this test only exists to prove the DB-engine monkeypatch and app
    # lifespan wiring work, before any auth code exists.
    monkeypatch.setattr(main_module, "_DASHBOARD_PASSWORD", "")
    res = test_client.get("/api/providers")
    assert res.status_code == 200
    assert res.json() == {"providers": []}
