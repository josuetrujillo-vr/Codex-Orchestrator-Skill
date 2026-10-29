"""Synchronous local CLI transport and provider-neutral records (Python 3.9+)."""

import json
import math
import os
import re
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4


OUTCOMES = frozenset({"unknown", "succeeded", "failed", "interrupted", "cancelled"})
CAPABILITIES = {"launch": True, "wait": True, "read": True,
                "cancel": False, "resume": False}
SECRET_KEY = re.compile(
    r"(?:api[_-]?key|token|password|passwd|secret|authorization|credential|cookie)", re.I)


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Redactor:
    """Remove credential fields, secret environment values and common tokens."""

    def __init__(self):
        self.secrets = sorted({value for key, value in os.environ.items()
                               if SECRET_KEY.search(key) and len(value) >= 8},
                              key=len, reverse=True)

    def text(self, value):
        value = str(value)
        for secret in self.secrets:
            value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
                       "[REDACTED PRIVATE KEY]", value, flags=re.S)
        value = re.sub(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+",
                       r"\1 [REDACTED]", value)
        value = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{20,}|"
                       r"gh[pousr]_[A-Za-z0-9_]{16,}|eyJ[A-Za-z0-9_-]+\."
                       r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\b", "[REDACTED]", value)
        value = re.sub(r"(?i)([a-z][a-z0-9+.-]*://)[^\s/@]+:[^\s/@]+@",
                       r"\1[REDACTED]@", value)
        value = re.sub(
            r"""(?is)(["\']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|authorization|cookie)["\']?\s*[:=]\s*)(["\'])(.*?)\2""",
            r"\1[REDACTED]", value)
        return re.sub(
            r"""(?ix)(["']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|authorization|cookie)["']?\s*[:=]\s*)(["']?)[^\s,;"'}]+\2""",
            r"\1[REDACTED]", value)

    def value(self, value):
        if isinstance(value, dict):
            return {key: "[REDACTED]" if SECRET_KEY.search(key) else self.value(item)
                    for key, item in value.items()}
        if isinstance(value, list):
            return [self.value(item) for item in value]
        return self.text(value) if isinstance(value, str) else value


@dataclass(frozen=True)
class ProviderStatus:
    provider: str
    installed: bool
    configured: bool
    agents: tuple = ()
    models: tuple = ()
    capabilities: dict = field(default_factory=lambda: dict(CAPABILITIES))
    reason: Optional[str] = None

    @property
    def available(self):
        return self.installed and self.configured and self.reason is None


@dataclass(frozen=True)
class ProviderResult:
    provider: str
    runtime_ref: Optional[str]
    runtime_status: Optional[str]
    execution_outcome: str
    response: str
    error: Optional[str]
    started_at: str
    finished_at: str
    trace_path: Optional[str]

    def __post_init__(self):
        if self.execution_outcome not in OUTCOMES:
            raise ValueError("invalid provider execution outcome")


@dataclass
class ProcessOutput:
    stdout: str = ""
    stderr: str = ""
    returncode: Optional[int] = None
    timed_out: bool = False


class ProviderError(ValueError):
    """A local setup, transport or structured-protocol error."""


def positive_seconds(value):
    if isinstance(value, bool):
        raise ProviderError("timeout must be a positive finite number")
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ProviderError("timeout must be a positive finite number") from None
    if not math.isfinite(result) or result <= 0:
        raise ProviderError("timeout must be a positive finite number")
    return result


def json_value(text):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ProviderError("duplicate key in provider JSON")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ProviderError("non-finite constant in provider JSON")

    try:
        value = json.loads(text, object_pairs_hook=unique, parse_constant=invalid_constant)
    except (ValueError, TypeError):
        raise ProviderError("malformed provider JSON") from None
    return value


def json_object(text):
    value = json_value(text)
    if not isinstance(value, dict):
        raise ProviderError("provider JSON must be an object")
    return value


def json_events(text, discriminator):
    events = []
    malformed = False
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            event = json_object(line)
            if not isinstance(event.get(discriminator), str) or not event[discriminator].strip():
                raise ProviderError("missing event discriminator")
            events.append(event)
        except ProviderError:
            malformed = True
    return events, malformed


class Trace:
    """Retain the structured output, changing only credential material."""

    def __init__(self, root, task_id, provider, redactor):
        root = Path(root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", str(task_id))[:80] or "task"
        self.path = root / f"{safe_id}.{provider}.{uuid4().hex}.jsonl"
        self.redactor = redactor
        self.dispatched = False
        self.runtime_ref = None
        self.runtime_status = None
        self.write_error = False
        with self.path.open("x", encoding="utf-8"):
            pass

    def append(self, record):
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(self.redactor.value(record), ensure_ascii=False) + "\n")

    def process(self, output, label):
        for line in output.stdout.splitlines():
            try:
                value = json_value(line)
            except ProviderError:
                value = {"unparsed_stdout": line}
            self.append({"source": label, "event": value})
        if output.stderr:
            self.append({"source": label, "stderr": output.stderr})
        self.append({"source": label, "returncode": output.returncode,
                     "timed_out": output.timed_out})


class ProviderAdapter(ABC):
    """Caller owns scheduling and ledger mutation; results never imply verification.

    Set artifact_dir to the run artifact directory. The fallback is
    <workspace>/.orchestrator/artifacts. command may be a native runtime plus CLI
    entrypoint, e.g. [node.exe, cli.js]. Shell/batch shims are rejected to avoid
    re-parsing prompts and JSON on Windows. Use one adapter instance per task.
    """

    provider = ""

    def __init__(self, artifact_dir=None, *, command=None, timeout=600,
                 model_id=None, agent_id=None, profile=None):
        self.artifact_dir = Path(artifact_dir) if artifact_dir is not None else None
        self.command = [command] if isinstance(command, str) else list(command or [])
        self.timeout = positive_seconds(timeout)
        self.model_id = model_id
        self.agent_id = agent_id
        self.profile = profile
        self.redactor = Redactor()

    def _command(self):
        command = self.command or [self.provider]
        executable = shutil.which(str(command[0]))
        if not executable:
            raise ProviderError("CLI executable was not found on PATH")
        if Path(executable).suffix.lower() in {".cmd", ".bat", ".ps1"}:
            raise ProviderError("shell shim unsupported; configure a native executable or runtime/CLI entrypoint")
        return [executable, *map(str, command[1:])]

    def _invoke(self, args, workspace, *, timeout=None, stdin=None, trace=None):
        try:
            process = subprocess.run(
                [*self._command(), *args], cwd=str(workspace), input=stdin,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                encoding="utf-8", errors="replace", timeout=timeout or self.timeout,
            )
            output = ProcessOutput(process.stdout, process.stderr, process.returncode)
        except subprocess.TimeoutExpired as exc:
            def decoded(value):
                return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value or ""
            output = ProcessOutput(decoded(exc.stdout), decoded(exc.stderr), timed_out=True)
        except OSError:
            raise ProviderError("CLI process could not be started in the workspace") from None
        if trace:
            try:
                trace.process(output, args[0])
            except OSError:
                trace.write_error = True
        return output

    def preflight(self) -> ProviderStatus:
        try:
            self._command()
        except ProviderError as exc:
            name = self.command[0] if self.command else self.provider
            return self._status(shutil.which(str(name)) is not None, False, str(exc))
        try:
            return self._preflight(Path.cwd())
        except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError) as exc:
            return self._status(True, False, self.redactor.text(exc))

    def _status(self, installed, configured, reason=None, *, agents=(), models=()):
        return ProviderStatus(self.provider, installed, configured,
                              tuple(self.redactor.text(item) for item in agents),
                              tuple(self.redactor.text(item) for item in models),
                              reason=self.redactor.text(reason) if reason else None)

    def run(self, task: dict, prompt_text: str, workspace: Path) -> ProviderResult:
        started = utc_now()
        trace = None
        try:
            if not isinstance(task, dict):
                raise ProviderError("task must be an object")
            workspace = Path(workspace).resolve(strict=True)
            if not workspace.is_dir() or not isinstance(prompt_text, str):
                raise ProviderError("an existing workspace and text prompt are required")
            if task.get("provider", self.provider) != self.provider:
                raise ProviderError("task provider does not match adapter")
            if task.get("runtime_ref"):
                raise ProviderError("resume unsupported; reconcile the existing runtime reference")
            required = task.get("required_capabilities", [])
            if not isinstance(required, list) or any(
                    not isinstance(item, str) or not CAPABILITIES.get(item) for item in required):
                raise ProviderError("task requires unsupported capabilities")
            self._command()
            status = self._preflight(workspace)
            if not status.available:
                raise ProviderError(status.reason or "provider is unavailable")
            root = self.artifact_dir or workspace / ".orchestrator" / "artifacts"
            trace = Trace(root, task.get("task_id", "task"), self.provider, self.redactor)
            return self._run(task, prompt_text, workspace, trace, started)
        except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError) as exc:
            # A dispatched request may still be active: keep the scope reserved.
            reference = trace.runtime_ref if trace else (
                task.get("runtime_ref") if isinstance(task, dict) else None)
            dispatched = bool(trace and trace.dispatched) or bool(reference)
            return self._result(
                started, trace, error=str(exc), runtime_ref=reference,
                runtime_status=trace.runtime_status if trace else None,
                execution_outcome="unknown" if dispatched else "failed",
            )

    def _result(self, started, trace=None, *, runtime_ref=None, runtime_status=None,
                execution_outcome="unknown", response="", error=None):
        clean = self.redactor.text
        if trace and trace.write_error:
            execution_outcome = "unknown"
            error = "trace persistence failed; reconcile worker effects before retrying"
        return ProviderResult(
            self.provider, clean(runtime_ref) if runtime_ref else None,
            clean(runtime_status) if runtime_status else None, execution_outcome,
            clean(response), clean(error) if error else None, started, utc_now(),
            str(trace.path) if trace else None,
        )

    @abstractmethod
    def _preflight(self, workspace):
        raise NotImplementedError

    @abstractmethod
    def _run(self, task, prompt_text, workspace, trace, started):
        raise NotImplementedError
