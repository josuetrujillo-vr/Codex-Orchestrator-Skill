"""Codex JSONL worker. Authentication and permission policy remain with the CLI."""

from .base import ProviderAdapter, ProviderError, json_events, positive_seconds


class CodexAdapter(ProviderAdapter):
    provider = "codex"

    def _preflight(self, workspace):
        help_output = self._invoke(["exec", "--help"], workspace, timeout=15, stdin="")
        if help_output.timed_out or help_output.returncode != 0 or "--json" not in help_output.stdout:
            return self._status(True, False, "installed Codex CLI lacks a usable exec --json interface")
        auth = self._invoke(["login", "status"], workspace, timeout=15, stdin="")
        if auth.timed_out or auth.returncode != 0:
            return self._status(True, False, "Codex CLI authentication is unavailable; configure it locally")
        # No documented machine-readable model catalog in this CLI surface.
        # Explicit model/profile selections are validated by Codex itself.
        return self._status(True, True)

    def _run(self, task, prompt_text, workspace, trace, started):
        if task.get("agent_id") or self.agent_id:
            raise ProviderError("Codex exec has no agent selector; configure a profile instead")
        args = ["exec", "--json", "--cd", str(workspace)]
        model = task.get("model_id") or self.model_id
        profile = task.get("profile") or self.profile
        for flag, value in (("--model", model), ("--profile", profile)):
            if value is not None:
                if not isinstance(value, str) or not value.strip():
                    raise ProviderError(f"{flag} must be a non-empty configured identifier")
                args.extend([flag, value])
        args.append("-")
        timeout = positive_seconds(task.get("timeout_seconds", self.timeout))
        if self.herdr_transport is not None:
            output = self._invoke_herdr(
                args, task, workspace, stdin=prompt_text, trace=trace,
                timeout=timeout,
            )
        else:
            trace.dispatched = True
            output = self._invoke(args, workspace, stdin=prompt_text, trace=trace,
                                  timeout=timeout)
        events, malformed = json_events(output.stdout, "type")
        runtime_ref = None
        runtime_status = None
        response = ""
        terminal = None
        turn_started = False
        stream_error = None
        for event in events:
            kind = event["type"]
            if kind == "turn.started":
                if turn_started or terminal:
                    malformed = True
                turn_started = True
                runtime_status = kind
            elif kind == "thread.started":
                if terminal:
                    malformed = True
                reference = event.get("thread_id")
                if not isinstance(reference, str) or not reference:
                    malformed = True
                elif runtime_ref is not None and runtime_ref != reference:
                    malformed = True
                else:
                    runtime_ref = reference
            elif kind == "item.completed":
                if terminal:
                    malformed = True
                item = event.get("item")
                if not isinstance(item, dict):
                    malformed = True
                elif item.get("type") == "agent_message":
                    if not isinstance(item.get("text"), str):
                        malformed = True
                    else:
                        response = item["text"]
            elif kind in {"turn.completed", "turn.failed"}:
                if terminal is not None:
                    malformed = True
                terminal = kind
                runtime_status = kind
                if kind == "turn.failed":
                    stream_error = event.get("error") or "Codex turn failed"
            elif kind == "error":
                stream_error = event.get("message") or "Codex reported a stream error"
        outcome = "unknown"
        error = stream_error
        if output.timed_out:
            error = "CLI timeout; worker effects may continue; reconcile before retrying"
        elif malformed:
            error = "malformed or inconsistent Codex JSONL stream"
        elif terminal == "turn.failed":
            outcome = "failed"
        elif terminal == "turn.completed" and output.returncode == 0 and not stream_error:
            if runtime_ref and response and turn_started:
                outcome = "succeeded"
            else:
                error = "terminal event lacks a thread reference or final assistant response"
        elif terminal == "turn.completed":
            error = "terminal event conflicts with CLI exit status or stream error"
        else:
            error = error or "no confirmed terminal turn event; reconcile before retrying"
        return self._result(started, trace, runtime_ref=runtime_ref,
                            runtime_status=runtime_status, execution_outcome=outcome,
                            response=response, error=error)
