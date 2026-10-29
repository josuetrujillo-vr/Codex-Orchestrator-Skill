#!/usr/bin/env python3
"""Run one provider argv under hc and persist its raw process output."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading


def atomic_json(path, value):
    path = Path(path)
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


def inside(path, workspace):
    resolved = Path(path).resolve()
    resolved.relative_to(Path(workspace).resolve())
    return resolved


def write_terminal(stream, text):
    try:
        stream.write(text)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "utf-8"
        try:
            stream.write(text.encode(encoding, errors="backslashreplace").decode(encoding))
        except (OSError, UnicodeError):
            return
    except OSError:
        return
    try:
        stream.flush()
    except OSError:
        pass


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        return 2
    manifest_path = Path(argv[0]).resolve()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        workspace = Path(manifest["workspace"]).resolve(strict=True)
        command = manifest["argv"]
        if (not isinstance(command, list) or not command or
                any(not isinstance(item, str) or "\0" in item for item in command)):
            return 2
        stdout_path = inside(manifest["stdout_path"], workspace)
        stderr_path = inside(manifest["stderr_path"], workspace)
        result_path = inside(manifest["result_path"], workspace)
        stdin_text = manifest.get("stdin")
        if stdin_text is not None and not isinstance(stdin_text, str):
            return 2
    except (OSError, ValueError, KeyError, TypeError):
        return 2

    manifest_path.unlink(missing_ok=True)
    status = {"task_id": str(manifest.get("task_id", "")), "started": False,
              "artifact_error": False, "returncode": None}
    try:
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        with stdout_path.open("w", encoding="utf-8", errors="replace", newline="") as out_file, \
                stderr_path.open("w", encoding="utf-8", errors="replace", newline="") as err_file:
            process = subprocess.Popen(
                command, cwd=str(workspace),
                stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
            )
            status["started"] = True
            output_lock = threading.Lock()
            artifact_error = threading.Event()

            def pump(source, artifact, terminal):
                for line in iter(source.readline, ""):
                    try:
                        artifact.write(line)
                        artifact.flush()
                    except OSError:
                        artifact_error.set()
                    with output_lock:
                        write_terminal(terminal, line)
                source.close()

            stdout_thread = threading.Thread(
                target=pump, args=(process.stdout, out_file, sys.stdout), daemon=True)
            stderr_thread = threading.Thread(
                target=pump, args=(process.stderr, err_file, sys.stderr), daemon=True)
            stdout_thread.start()
            stderr_thread.start()
            if stdin_text is not None:
                try:
                    process.stdin.write(stdin_text)
                    process.stdin.flush()
                except BrokenPipeError:
                    pass
                finally:
                    process.stdin.close()
            status["returncode"] = process.wait()
            stdout_thread.join()
            stderr_thread.join()
            status["artifact_error"] = artifact_error.is_set()
    except OSError:
        status["error"] = "provider process could not be started"
    try:
        atomic_json(result_path, status)
    except OSError:
        return 1
    return status["returncode"] if isinstance(status["returncode"], int) else 1


if __name__ == "__main__":
    sys.exit(main())
