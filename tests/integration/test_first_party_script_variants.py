"""Generated first-party command instructions follow the saved script variant."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tests.extensions.git.test_git_extension import _write_config
from tests.extensions.git.test_git_extension_python_parity import _run_py, _setup_py_project
from tests.parity_helpers import run

VARIANTS = {"sh": "bash", "ps": "powershell", "py": "python"}
COMMANDS = {
    "git-initialize": ("git", "initialize-repo"),
    "git-feature": ("git", "create-new-feature-branch"),
    "git-commit": ("git", "auto-commit"),
    "agent-context-update": ("agent-context", "update-agent-context"),
}


def _script_path(pack: str, command: str, variant: str) -> str:
    name = command.replace("-", "_") if variant == "py" else command
    suffix = "ps1" if variant == "ps" else variant
    return f".specify/extensions/{pack}/scripts/{VARIANTS[variant]}/{name}.{suffix}"


@pytest.fixture
def external_extension(tmp_path: Path) -> Path:
    source = tmp_path / "external"
    source.mkdir()
    (source / "extension.yml").write_text(
        yaml.safe_dump({
            "schema_version": "1.0",
            "extension": {
                "id": "external-probe",
                "name": "External Probe",
                "version": "1.0.0",
                "description": "Exercise external script selection",
            },
            "requires": {"speckit_version": ">=1"},
            "provides": {"commands": [
                {"name": "speckit.external-probe.run", "file": "run.md"},
                {"name": "speckit.external-probe.shell", "file": "shell.md"},
            ]},
        }),
        encoding="utf-8",
    )
    scripts = {}
    for variant, content in [
        ("sh", "#!/bin/sh\nprintf 'external-sh\\n'\n"),
        ("ps", "Write-Output 'external-ps'\n"),
        ("py", "print('external-py')\n"),
    ]:
        relative = _script_path("external-probe", "marker", variant).split("/", 3)[3]
        path = source / relative
        path.parent.mkdir(parents=True)
        path.write_text(content, encoding="utf-8")
        scripts[variant] = relative
    for name, entries in [
        ("run", scripts),
        ("shell", {variant: scripts[variant] for variant in ("sh", "ps")}),
    ]:
        (source / f"{name}.md").write_text(
            "---\n"
            + yaml.safe_dump({"description": "Print a local marker", "scripts": entries})
            + "---\n\nRun `{SCRIPT}` from project root.\n",
            encoding="utf-8",
        )
    return source


@pytest.mark.parametrize("variant", ["sh", "ps", "py"])
def test_generated_commands_use_the_saved_script_variant(
    tmp_path: Path, external_extension: Path, variant: str,
) -> None:
    specify = [sys.executable, "-c", "from specify_cli import main; main()"]
    result = run(specify + [
        "init", "project", "--integration", "copilot", "--script", variant,
        "--extension", "git", "--extension", "agent-context",
        "--non-interactive", "--ignore-agent-tools",
    ], tmp_path, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    project = tmp_path / "project"
    result = run(specify + ["extension", "add", "--dev", str(external_extension)], project, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((project / ".specify/init-options.json").read_text(encoding="utf-8"))["script"] == variant

    def assert_variant(skill: str, pack: str, script: str, expected: str) -> None:
        path = project / ".github" / "skills" / f"speckit-{skill}" / "SKILL.md"
        generated = path.read_text(encoding="utf-8")
        references = {key for key in VARIANTS if _script_path(pack, script, key) in generated}
        assert references == {expected}, path
        assert (project / _script_path(pack, script, expected)).is_file()

    for skill, (pack, script) in COMMANDS.items():
        assert_variant(skill, pack, script, variant)
    assert_variant("external-probe-run", "external-probe", "marker", variant)
    shell_variant = ("ps" if os.name == "nt" else "sh") if variant == "py" else variant
    assert_variant("external-probe-shell", "external-probe", "marker", shell_variant)


@pytest.mark.parametrize("transport", ["file", "argument", "none"])
def test_python_auto_commit_honors_conventional_style(
    tmp_path: Path, transport: str,
) -> None:
    project = _setup_py_project(tmp_path)
    _write_config(
        project,
        "commit_style: conventional  # project policy\n"
        "auto_commit:\n"
        "  after_plan:\n"
        "    enabled: true\n"
        "    message: Fixed fallback must not be used\n",
    )

    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=project, encoding="utf-8").strip()

    before = git("rev-parse", "HEAD")
    change = project / "change.txt"
    change.write_text("project change\n", encoding="utf-8")
    subject = "feat: 记录 café changes"
    message_file = project / "commit-message.txt"
    args = []
    if transport == "file":
        message_file.write_text(subject + "\n", encoding="utf-8")
        args = ["--message-file", message_file.name]
    elif transport == "argument":
        args = [subject]
    result = _run_py("auto-commit", project, "after_plan", *args)

    if transport == "none":
        assert result.returncode == 1, result.stderr
        assert "conventional" in result.stderr
        assert git("rev-parse", "HEAD") == before
        assert git("diff", "--cached", "--name-only") == ""
        return

    assert result.returncode == 0, result.stderr
    assert git("log", "-1", "--format=%s") == subject
    assert not message_file.exists()
    assert message_file.name not in git("ls-tree", "-r", "--name-only", "HEAD").splitlines()
    if transport == "file":
        committed = git("rev-parse", "HEAD")
        message_file.write_text("feat: transport-only change\n", encoding="utf-8")
        result = _run_py("auto-commit", project, "after_plan", "--message-file", message_file.name)
        assert result.returncode == 0, result.stderr
        assert git("rev-parse", "HEAD") == committed
        assert not message_file.exists()
