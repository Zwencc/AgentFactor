import json
from pathlib import Path

from click.testing import CliRunner

from agentfactor.cli.main import cli


def test_install_bundled_profile_user_scope():
    runner = CliRunner()
    result = runner.invoke(cli, ["install", "developer"])
    assert result.exit_code == 0, result.output

    data = json.loads(result.output)
    destination = Path(data["path"])
    assert destination.exists()
    assert data["scope"] == "user"

    catalog_result = runner.invoke(cli, ["personas"])
    assert catalog_result.exit_code == 0, catalog_result.output
    catalog = json.loads(catalog_result.output)
    assert any(entry["name"] == data["name"] for entry in catalog["bundled"])
    assert any(entry["name"] == data["name"] and entry["scope"] == "user" for entry in catalog["installed"])


def test_install_local_profile_project_scope():
    runner = CliRunner()
    profile_body = """---
name: custom_agent
description: Custom persona for tests
---
You are a helper.
"""

    with runner.isolated_filesystem():
        profile_path = Path("custom.md")
        profile_path.write_text(profile_body)

        install_result = runner.invoke(cli, ["install", str(profile_path), "--scope", "project"])
        assert install_result.exit_code == 0, install_result.output
        data = json.loads(install_result.output)
        destination = Path(data["path"])
        assert destination.exists()
        assert destination.name == "custom.md"

        personas_result = runner.invoke(cli, ["personas", "--bundled", "--installed"])
        assert personas_result.exit_code == 0, personas_result.output
        catalog = json.loads(personas_result.output)
        assert any(entry["scope"] == "project" and entry["name"] == "custom_agent" for entry in catalog["installed"])


def test_install_requires_force_for_overwrite():
    runner = CliRunner()
    first = runner.invoke(cli, ["install", "developer"])
    assert first.exit_code == 0, first.output

    second = runner.invoke(cli, ["install", "developer"])
    assert second.exit_code != 0
    assert "Use --force to overwrite" in second.output

    forced = runner.invoke(cli, ["install", "developer", "--force"])
    assert forced.exit_code == 0, forced.output
