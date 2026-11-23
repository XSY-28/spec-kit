"""Exercise the generated native hook commands using only synthetic data."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory


BASELINE, CANDIDATE, POWERSHELL = (Path(arg).resolve() for arg in sys.argv[1:])
EVENTS = ("sessionStart", "userPromptSubmitted")
CONTEXT = "Project proof café 计划"


def run(argv, cwd, env, stdin=None):
    result = subprocess.run(
        argv, cwd=cwd, input=stdin, capture_output=True, encoding="utf-8",
        env=env, timeout=90,
    )
    assert result.returncode == 0, (argv, result.returncode, result.stdout, result.stderr)
    return result.stdout


def environment(executable):
    env = {k: v for k, v in os.environ.items() if not k.startswith("SPECIFY_")}
    env["PATH"] = f"{executable.parent}{os.pathsep}{env['PATH']}"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def make_extension(root):
    source = root / "event-probe"
    (source / "commands").mkdir(parents=True)
    (source / "extension.yml").write_text("""schema_version: "1.0"
extension:
  id: event-probe
  name: Event Probe
  version: "1.0.0"
  description: Local event fixture
  author: Spec Kit audit
requires:
  speckit_version: ">=0.2.0"
provides:
  commands:
    - name: speckit.event-probe.inspect
      file: commands/inspect.md
      description: Trace event input and write stdout
events:
  session_start:
    command: speckit.event-probe.inspect
  user_prompt_submit:
    command: speckit.event-probe.inspect
""", encoding="utf-8")
    (source / "commands/inspect.md").write_text("""---
description: Trace event input and write stdout
scripts:
  py: probe.py
---
Trace event input and write stdout.
""", encoding="utf-8")
    (source / "probe.py").write_text("""import json
import sys
from pathlib import Path

payload = json.load(sys.stdin)
Path("event-observed.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
print(payload["context"])
""", encoding="utf-8")
    return source


def initialize(executable, project, source):
    env = environment(executable)
    run(
        [executable, "init", str(project), "--integration", "copilot", "--script", "py",
         "--ignore-agent-tools", "--non-interactive"],
        project.parent,
        env,
    )
    run([executable, "extension", "add", "--dev", str(source)], project, env)


def verify_hooks(executable, project, scenario, *, prompt_emits_context):
    hooks_file = project / ".github/hooks/speckit.json"
    hooks = json.loads(hooks_file.read_text(encoding="utf-8"))["hooks"]
    marker = project / "event-observed.json"
    env = environment(executable)
    for event in EVENTS:
        native = hooks[event][0]["powershell"]
        payload = {"event": event, "context": CONTEXT}
        stdout = run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", native],
            project,
            env,
            json.dumps(payload),
        )
        assert json.loads(marker.read_text(encoding="utf-8")) == payload
        marker.unlink()
        if event == "sessionStart" or prompt_emits_context:
            assert json.loads(stdout) == {"additionalContext": CONTEXT + "\n"}
            outcome = "exact Unicode additionalContext"
        else:
            assert stdout == "", stdout
            outcome = "empty stdout"
        print(f"{scenario} {event}: handler received Unicode stdin, {outcome}")


with TemporaryDirectory(prefix="spec-kit-5544-") as directory:
    root = Path(directory)
    extension = make_extension(root)
    old_project = root / "existing project"
    initialize(BASELINE, old_project, extension)
    verify_hooks(BASELINE, old_project, "baseline", prompt_emits_context=True)

    run(
        [CANDIDATE, "integration", "upgrade", "copilot"],
        old_project,
        environment(CANDIDATE),
    )
    verify_hooks(CANDIDATE, old_project, "upgraded", prompt_emits_context=False)

    fresh_project = root / "fresh project"
    initialize(CANDIDATE, fresh_project, extension)
    verify_hooks(CANDIDATE, fresh_project, "fresh", prompt_emits_context=False)
