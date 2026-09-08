import json

from research_harness.cli import main


def test_validation_errors_do_not_echo_credential_values(tmp_path, capsys):
    path = tmp_path / "source.json"
    path.write_text(
        json.dumps(
            {
                "id": "x",
                "name": "x",
                "connector": "html",
                "url": "https://source.example/?api_key=should-never-be-printed",
            }
        )
    )
    assert main(["probe", str(path)]) == 1
    output = capsys.readouterr()
    assert "should-never-be-printed" not in output.err + output.out
    assert "credentials" in output.err
