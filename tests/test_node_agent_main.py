import node_agent.main as main


def test_main_uses_configured_bind_address(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Settings:
        bind_host = "127.0.0.1"
        bind_port = 9100

    monkeypatch.setattr(main, "get_agent_settings", lambda: Settings())

    def fake_run(app: str, *, host: str, port: int) -> None:
        captured["app"] = app
        captured["host"] = host
        captured["port"] = port

    monkeypatch.setattr(main.uvicorn, "run", fake_run)

    main.main()

    assert captured == {
        "app": "node_agent.main:app",
        "host": "127.0.0.1",
        "port": 9100,
    }
