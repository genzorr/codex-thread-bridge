import pytest

from codex_thread_bridge import reload as reload_command
from codex_thread_bridge.rpc import RpcError, TransportError


async def test_reload_sends_one_documented_request(fake_server):
    fake, socket = fake_server
    assert await reload_command.request_reload(socket) == {}
    assert [
        (method, params) for method, params in fake.calls if method == "config/mcpServer/reload"
    ] == [("config/mcpServer/reload", None)]


async def test_reload_response_loss_is_not_retried(fake_server):
    fake, socket = fake_server
    fake.drop_after = "config/mcpServer/reload"
    with pytest.raises(TransportError):
        await reload_command.request_reload(socket)
    assert fake.count("config/mcpServer/reload") == 1


def test_reload_cli_decline_sends_nothing(monkeypatch, capsys):
    calls = []

    async def request(socket):
        calls.append(socket)
        return {}

    monkeypatch.setattr(reload_command, "request_reload", request)
    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    assert reload_command.main(["--socket", "/tmp/test.sock"]) == 1
    assert calls == []
    assert "no reload request sent" in capsys.readouterr().out


def test_reload_cli_confirms_queued_refresh(monkeypatch, capsys):
    calls = []

    async def request(socket):
        calls.append(socket)
        return {}

    monkeypatch.setattr(reload_command, "request_reload", request)
    monkeypatch.setattr("builtins.input", lambda prompt: "reload")
    assert reload_command.main(["--socket", "/tmp/test.sock"]) == 0
    assert len(calls) == 1 and str(calls[0]) == "/tmp/test.sock"
    assert "refresh queued" in capsys.readouterr().out


def test_reload_cli_reports_unknown_outcome_without_retry(monkeypatch, capsys):
    calls = []

    async def request(socket):
        calls.append(socket)
        raise TransportError("response unavailable")

    monkeypatch.setattr(reload_command, "request_reload", request)
    monkeypatch.setattr("builtins.input", lambda prompt: "reload")
    assert reload_command.main(["--socket", "/tmp/test.sock"]) == 3
    assert len(calls) == 1
    assert "outcome unknown" in capsys.readouterr().err


def test_reload_cli_reports_server_rejection(monkeypatch, capsys):
    async def request(socket):
        raise RpcError("config/mcpServer/reload", {"message": "rejected"})

    monkeypatch.setattr(reload_command, "request_reload", request)
    monkeypatch.setattr("builtins.input", lambda prompt: "reload")
    assert reload_command.main(["--socket", "/tmp/test.sock"]) == 2
    assert "rejected" in capsys.readouterr().err
