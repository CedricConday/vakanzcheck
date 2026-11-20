"""Ask Claude through the `claude` CLI on a Claude subscription, with no API key.

The same approach as screengate (see README, Related): one `claude -p` per call, with a fresh session,
no tools, no MCP servers, no settings files, an empty working directory and the
caller's system prompt in place of Claude Code's, so nothing but the prompt
reaches the model. Structured output comes back through --json-schema.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile

MODEL = "claude-opus-5-5"


class CliError(RuntimeError):
    pass


class Subscription:
    def __init__(self, model: str = MODEL, binary: str = "claude", timeout: float = 600.0,
                 effort: str | None = None):
        self.model = model
        self.binary = shutil.which(binary) or binary
        self.timeout = timeout
        self.effort = effort
        self.cwd = tempfile.mkdtemp(prefix="vakanzcheck-cli-")
        self.calls = 0

    def command(self, system: str, schema: dict) -> list[str]:
        cmd = [self.binary, "-p", "--model", self.model, "--system-prompt", system,
               "--tools", "", "--strict-mcp-config", "--setting-sources", "",
               "--no-session-persistence", "--output-format", "json",
               "--json-schema", json.dumps(schema, ensure_ascii=False)]
        if self.effort:
            cmd += ["--effort", self.effort]
        return cmd

    def __call__(self, system: str, prompt: str, schema: dict) -> tuple[dict, dict]:
        self.calls += 1
        proc = subprocess.run(self.command(system, schema), check=False, input=prompt,
                              capture_output=True, text=True, timeout=self.timeout, cwd=self.cwd)
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise CliError(f"claude -p exit {proc.returncode}: {proc.stderr[-500:]}") from e
        if out.get("is_error"):
            raise CliError(f"claude -p failed: {str(out.get('result'))[:500]}")
        raw = out.get("structured_output")
        if not isinstance(raw, dict):
            raise CliError(f"no structured output (stop: {out.get('stop_reason')})")
        meta = {"requested_model": self.model,
                "served_by": list((out.get("modelUsage") or {}).keys()),
                "cost_usd": out.get("total_cost_usd")}
        return raw, meta
