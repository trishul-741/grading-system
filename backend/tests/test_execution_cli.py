import json

from app.execution import cli


def test_cli_rejects_invalid_request_without_exposing_path(tmp_path, monkeypatch, capsys):
    missing = tmp_path / "private-input.json"
    monkeypatch.setattr("sys.argv", ["execution", "--request", str(missing), "--log-file", str(tmp_path / "worker.log")])
    assert cli.main() == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "SYSTEM_ERROR"
    assert payload["error_category"] == "VALIDATION"
    assert str(missing) not in json.dumps(payload)


def test_cli_forbids_arbitrary_container_options(tmp_path, monkeypatch, capsys):
    request = tmp_path / "request.json"
    request.write_text('{"source_code":"int main() {}","image":"unsafe"}')
    monkeypatch.setattr("sys.argv", ["execution", "--request", str(request), "--log-file", str(tmp_path / "worker.log")])
    assert cli.main() == 2
    assert json.loads(capsys.readouterr().out)["error_category"] == "VALIDATION"
