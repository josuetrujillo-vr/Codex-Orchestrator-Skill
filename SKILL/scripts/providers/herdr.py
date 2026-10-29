"""Optional Herdr Core transport for persistent local terminal execution."""

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

from .base import ProcessOutput, ProviderError, ProviderStatus


@dataclass(frozen=True)
class HerdrResult:
    transport_ref: str
    stdout: str = ""
    stderr: str = ""
    returncode: int = None
    timed_out: bool = False
    dispatch_may_have_occurred: bool = True
    error: str = None

    @property
    def process_output(self):
        return ProcessOutput(self.stdout, self.stderr, self.returncode, self.timed_out)


class HerdrTransport:
    """Start one shell-free provider command in a named hc terminal."""

    def __init__(self, command=None, artifact_dir=None):
        self.command = list(command or ["hc"])
        self.artifact_dir = Path(artifact_dir) if artifact_dir is not None else None

    def _resolved(self):
        if not self.command or any(not isinstance(item, str) or not item for item in self.command):
            raise ProviderError("Herdr command must be a non-empty executable argument list")
        executable = shutil.which(self.command[0])
        if not executable:
            raise ProviderError("Herdr Core executable was not found on PATH")
        if Path(executable).suffix.lower() in {".cmd", ".bat", ".ps1"}:
            raise ProviderError("Herdr shell shims are unsupported; configure a native executable")
        return [executable, *self.command[1:]]

    def preflight(self, workspace):
        try:
            resolved = self._resolved()
        except ProviderError as exc:
            installed = bool(self.command and shutil.which(str(self.command[0])))
            return ProviderStatus("herdr", installed, False, reason=str(exc))
        try:
            result = subprocess.run(
                [*resolved, "run", "--help"], cwd=str(workspace),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                encoding="utf-8", errors="replace", timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ProviderStatus("herdr", True, False,
                                  reason="Herdr Core run help is unavailable or timed out")
        help_text = result.stdout + result.stderr
        if result.returncode != 0 or "--bg" not in help_text or "--name" not in help_text:
            return ProviderStatus("herdr", True, False,
                                  reason="installed hc lacks run --bg --name support")
        return ProviderStatus("herdr", True, True)

    @staticmethod
    def terminal_name(run_id, task_id, created_at=""):
        safe_task = re.sub(r"[^a-z0-9_-]", "-", str(task_id).lower()).strip("-") or "task"
        digest = hashlib.sha256(
            f"{run_id}:{task_id}:{created_at}".encode("utf-8")
        ).hexdigest()[:10]
        return f"h{digest}-{safe_task[:20]}"

    @staticmethod
    def _atomic_json(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                    mode="w", encoding="utf-8", newline="\n", dir=path.parent,
                    delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(value, stream, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def run(self, task_id, terminal_name, argv, workspace, timeout, stdin=None):
        workspace = Path(workspace).resolve(strict=True)
        artifact_dir = (self.artifact_dir or
                        workspace / ".orchestrator" / "artifacts").resolve()
        if not artifact_dir.is_relative_to(workspace):
            raise ProviderError("Herdr artifact directory escapes the task workspace")
        if not isinstance(argv, list) or not argv or any(
                not isinstance(item, str) or "\0" in item for item in argv):
            raise ProviderError("provider command must be a non-empty string argument list")
        if stdin is not None and not isinstance(stdin, str):
            raise ProviderError("provider stdin must be text or null")
        if not isinstance(terminal_name, str) or not re.fullmatch(
                r"[a-z][a-z0-9_-]{0,31}", terminal_name):
            raise ProviderError("Herdr terminal name is invalid")
        hc_command = self._resolved()
        artifact_dir.mkdir(parents=True, exist_ok=True)
        safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(task_id))[:64] or "task"
        stdout_path = artifact_dir / f"{safe_id}.herdr.stdout"
        stderr_path = artifact_dir / f"{safe_id}.herdr.stderr"
        result_path = artifact_dir / f"{safe_id}.herdr.result.json"
        manifest_path = artifact_dir / f"{safe_id}.herdr.manifest.json"
        for path in (stdout_path, stderr_path, result_path, manifest_path):
            if not path.resolve().is_relative_to(workspace):
                raise ProviderError("Herdr artifact path escapes the task workspace")
        result_path.unlink(missing_ok=True)
        manifest = {
            "task_id": str(task_id), "argv": argv, "workspace": str(workspace),
            "stdin": stdin, "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path), "result_path": str(result_path),
        }
        self._atomic_json(manifest_path, manifest)
        helper = Path(__file__).resolve().parents[1] / "herdr_worker.py"
        launcher = [*hc_command, "run", "--bg", "--name", terminal_name,
                    sys.executable, str(helper), str(manifest_path)]
        try:
            accepted = subprocess.run(
                launcher, cwd=str(workspace), stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, shell=False, encoding="utf-8",
                errors="replace", timeout=15,
            )
        except subprocess.TimeoutExpired:
            return HerdrResult(terminal_name, timed_out=True,
                               error="hc launch acknowledgement timed out; reconcile the named terminal")
        except OSError:
            return HerdrResult(terminal_name,
                               error="hc launch acknowledgement is uncertain; reconcile the named terminal")
        if accepted.returncode != 0:
            return HerdrResult(terminal_name,
                               error="hc did not confirm dispatch; reconcile the named terminal")
        deadline = time.monotonic() + float(timeout)
        while time.monotonic() < deadline:
            if result_path.is_file():
                try:
                    record = json.loads(result_path.read_text(encoding="utf-8"))
                    if (not isinstance(record, dict) or
                            record.get("task_id") != str(task_id) or
                            type(record.get("started")) is not bool or
                            type(record.get("artifact_error")) is not bool or
                            (record.get("returncode") is not None and
                             type(record.get("returncode")) is not int)):
                        raise ValueError("invalid helper result")
                    stdout = stdout_path.read_text(encoding="utf-8", errors="replace")
                    stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
                    if record["returncode"] is None:
                        return HerdrResult(
                            terminal_name, stdout, stderr,
                            dispatch_may_have_occurred=record["started"],
                            error=("provider outcome is uncertain; reconcile the named terminal"
                                   if record["started"] else
                                   "provider process could not be started"),
                        )
                    if record["artifact_error"]:
                        return HerdrResult(
                            terminal_name, stdout, stderr, record["returncode"],
                            dispatch_may_have_occurred=True,
                            error="provider output could not be fully persisted; reconcile the named terminal",
                        )
                    return HerdrResult(terminal_name, stdout, stderr,
                                       record["returncode"])
                except (OSError, ValueError, TypeError):
                    return HerdrResult(terminal_name,
                                       error="Herdr result sidecar is malformed; reconcile the named terminal")
            time.sleep(min(0.2, max(0, deadline - time.monotonic())))
        return HerdrResult(terminal_name, timed_out=True,
                           error="provider is still running in Herdr; reconcile before retrying")
