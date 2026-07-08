import os
import sys
import json
import uuid
import subprocess
import tempfile
import threading
import time
import sqlite3
import ssl
import urllib.request
import webbrowser
from datetime import datetime
from queue import Queue, Empty
from typing import Dict, List, Any, Generator


def _render_terminal_output(text: str) -> str:
    """Collapse a terminal-logger animation stream into the final rendered
    screen a real console would show, preserving SGR colors.

    The .NET SDK's modern terminal logger (forced on via MSBUILDTERMINALLOGGER)
    emits cursor-movement escapes to animate progress in place. Captured to a
    file those frames just pile up, so we replay them through a VT emulator and
    read back the final screen.

    ponytail: only kicks in when cursor-control escapes are present (the
    telltale `\\x1b[?25` show/hide-cursor codes the logger always emits), so
    plain command output passes through untouched and never gets reflowed or
    truncated to the emulator width.
    """
    if "\x1b[?25" not in text:
        return text
    try:
        import pyte
    except ImportError:
        return text

    named = {"black": 0, "red": 1, "green": 2, "brown": 3,
             "blue": 4, "magenta": 5, "cyan": 6, "white": 7}

    def sgr(char) -> str:
        parts = []
        if char.bold:
            parts.append("1")
        if char.fg != "default":
            parts.append(str(30 + named.get(char.fg, 9)))
        if char.bg != "default":
            parts.append(str(40 + named.get(char.bg, 9)))
        return ";".join(parts)

    def render_line(buf) -> str:
        cols = (max(buf) + 1) if buf else 0
        out, prev = "", ""
        for col in range(cols):
            char = buf[col]
            code = sgr(char)
            if code != prev:
                out += "\x1b[0m" + (f"\x1b[{code}m" if code else "")
                prev = code
            out += char.data
        if prev:
            out += "\x1b[0m"
        return out.rstrip()

    screen = pyte.HistoryScreen(200, 50, history=5000)
    # ponytail: Windows consoles treat LF as CR+LF (move to column 0); pyte
    # defaults to Unix bare LF, which mangles cursor-repositioned output. LNM
    # matches the real console the app is imitating.
    screen.set_mode(pyte.modes.LNM)
    pyte.Stream(screen).feed(text)
    rows = list(screen.history.top) + [screen.buffer[i] for i in range(screen.lines)]
    lines = [render_line(buf) for buf in rows]
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _get_db_path():
    if getattr(sys, '_MEIPASS', None):
        return os.path.join(os.path.dirname(sys.executable), "test_runner.db")
    return os.path.join(os.path.dirname(__file__), "test_runner.db")


DB_PATH = _get_db_path()


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


class TestExecutor:
    def __init__(self):
        self._runs: Dict[str, dict] = {}
        self._event_queues: Dict[str, List[Queue]] = {}
        self._cancel_flags: Dict[str, threading.Event] = {}
        self._console_procs: Dict[str, Any] = {}  # run_id -> console_dir path

    def start_run(self, run_id: str, tests: List[dict], open_console: bool = True, sdk_version: str = None):
        """Start executing tests in a background thread."""
        cancel_flag = threading.Event()
        self._cancel_flags[run_id] = cancel_flag
        self._runs[run_id] = {"status": "running", "tests": tests, "sdk_version": sdk_version}
        self._event_queues[run_id] = []

        # Open a separate console window if requested
        if open_console:
            self._open_console(run_id)

        conn = get_db()
        conn.execute(
            "INSERT INTO test_runs (id, started_at, status, sdk_version) VALUES (?, ?, ?, ?)",
            (run_id, datetime.now().isoformat(), "running", sdk_version),
        )
        conn.commit()
        conn.close()

        thread = threading.Thread(
            target=self._execute_run, args=(run_id, tests, cancel_flag), daemon=True
        )
        thread.start()

    def _open_console(self, run_id: str):
        """Open a visible console/terminal window that executes commands."""
        try:
            # Create a directory for this run's console communication
            console_dir = os.path.join(tempfile.gettempdir(), f"test_runner_{run_id}")
            os.makedirs(console_dir, exist_ok=True)

            # Command queue file — we write commands here, console reads them
            cmd_file = os.path.join(console_dir, "commands.txt")
            done_file = os.path.join(console_dir, "done.txt")

            # Initialize empty command file
            with open(cmd_file, "w", encoding="utf-8") as f:
                f.write("")

            if sys.platform == "win32":
                # Create a batch script that polls for commands
                script_path = os.path.join(console_dir, "runner.bat")
                exec_file = os.path.join(console_dir, "exec.bat")
                with open(script_path, "w", encoding="utf-8") as f:
                    f.write("@echo off\n")
                    f.write("%SystemRoot%\\System32\\chcp.com 65001 >nul\n")  # ponytail: full path; PATH may lack System32 in the spawned console
                    f.write(f"title .NET Test Runner - Run {run_id}\n")
                    f.write("echo ========================================\n")
                    f.write("echo   .NET SDK Test Runner - Live Console\n")
                    f.write("echo ========================================\n")
                    f.write("echo.\n")
                    f.write(":loop\n")
                    f.write(f'if exist "{done_file}" goto end\n')
                    # Check if commands file has content (size > 0)
                    f.write(f'for %%A in ("{cmd_file}") do if %%~zA==0 goto wait\n')
                    # Copy commands to exec file and clear the queue
                    f.write(f'copy /y "{cmd_file}" "{exec_file}" >nul 2>&1\n')
                    f.write(f'type nul > "{cmd_file}"\n')
                    # Execute the commands
                    f.write(f'call "{exec_file}"\n')
                    f.write(":wait\n")
                    f.write("timeout /t 1 /nobreak >nul 2>&1\n")
                    f.write("goto loop\n")
                    f.write(":end\n")
                    f.write("echo.\n")
                    f.write("echo ========================================\n")
                    f.write("echo   Run complete. You may close this window.\n")
                    f.write("echo ========================================\n")
                    f.write("pause\n")

                # Launch the batch script in a new console window
                subprocess.Popen(
                    ["cmd.exe", "/c", script_path],
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                )
            else:
                import shutil
                # Create a bash script that polls for commands
                script_path = os.path.join(console_dir, "runner.sh")
                with open(script_path, "w", encoding="utf-8") as f:
                    f.write("#!/bin/bash\n")
                    f.write("echo '========================================'\n")
                    f.write("echo '  .NET SDK Test Runner - Live Console'\n")
                    f.write("echo '========================================'\n")
                    f.write("echo\n")
                    f.write(f'CMD_FILE="{cmd_file}"\n')
                    f.write(f'DONE_FILE="{done_file}"\n')
                    f.write("while true; do\n")
                    f.write('  if [ -s "$CMD_FILE" ]; then\n')
                    f.write('    while IFS= read -r line; do\n')
                    f.write('      echo\n')
                    f.write('      eval "$line"\n')
                    f.write('    done < "$CMD_FILE"\n')
                    f.write('    > "$CMD_FILE"\n')
                    f.write("  fi\n")
                    f.write('  if [ -f "$DONE_FILE" ]; then break; fi\n')
                    f.write("  sleep 0.5\n")
                    f.write("done\n")
                    f.write("echo\n")
                    f.write("echo '========================================'\n")
                    f.write("echo '  Run complete. Press Enter to close.'\n")
                    f.write("echo '========================================'\n")
                    f.write("read\n")
                os.chmod(script_path, 0o755)

                terminals = [
                    ["gnome-terminal", "--title", f"Test Runner - {run_id}", "--"],
                    ["xfce4-terminal", "--title", f"Test Runner - {run_id}", "-e"],
                    ["konsole", "--title", f"Test Runner - {run_id}", "-e"],
                    ["xterm", "-title", f"Test Runner - {run_id}", "-e"],
                ]
                for term in terminals:
                    if shutil.which(term[0]):
                        try:
                            subprocess.Popen(
                                term + [script_path],
                                start_new_session=True,
                            )
                        except Exception:
                            continue
                        break

            # Store the console directory path for communication
            self._console_procs[run_id] = console_dir
        except Exception:
            self._console_procs[run_id] = None

    def _close_console(self, run_id: str):
        """Signal the console that the run is complete."""
        console_dir = self._console_procs.pop(run_id, None)
        if console_dir and isinstance(console_dir, str):
            try:
                done_file = os.path.join(console_dir, "done.txt")
                with open(done_file, "w") as f:
                    f.write("done")
            except Exception:
                pass

    def cancel_run(self, run_id: str):
        if run_id in self._cancel_flags:
            self._cancel_flags[run_id].set()

    def stream_events(self, run_id: str) -> Generator[dict, None, None]:
        """Yield events for SSE streaming."""
        queue = Queue()
        if run_id not in self._event_queues:
            self._event_queues[run_id] = []
        self._event_queues[run_id].append(queue)

        try:
            while True:
                try:
                    event = queue.get(timeout=30)
                    if event is None:  # Sentinel for end
                        break
                    yield event
                except Empty:
                    yield {"type": "heartbeat"}
        finally:
            if run_id in self._event_queues:
                self._event_queues[run_id].remove(queue)

    def _emit_event(self, run_id: str, event: dict):
        if run_id in self._event_queues:
            for queue in self._event_queues[run_id]:
                queue.put(event)

    def _end_stream(self, run_id: str):
        if run_id in self._event_queues:
            for queue in self._event_queues[run_id]:
                queue.put(None)

    def _execute_run(self, run_id: str, tests: List[dict], cancel_flag: threading.Event):
        """Execute all tests in sequence."""
        conn = get_db()
        passed = 0
        failed = 0
        skipped = 0

        # Capture environment info
        env_info = self._capture_environment()
        conn.execute(
            "UPDATE test_runs SET environment_info=? WHERE id=?",
            (env_info, run_id),
        )
        conn.commit()

        # Record the SDK actually resolved by dotnet (a pinned version may be
        # gone from the machine and silently roll forward). Log what really runs.
        pinned = self._runs.get(run_id, {}).get("sdk_version") or None
        actual = self._resolve_sdk_version(pinned)
        if actual:
            self._runs[run_id]["sdk_version"] = actual
            conn.execute(
                "UPDATE test_runs SET sdk_version=? WHERE id=?", (actual, run_id)
            )
            conn.commit()
            if pinned and actual != pinned:
                self._queue_console_cmd(
                    run_id,
                    f"echo [warn] pinned SDK {pinned} not installed; running {actual}",
                )

        self._emit_event(run_id, {
            "type": "run_start",
            "run_id": run_id,
            "total_tests": len(tests),
            "environment": env_info,
        })

        for test in tests:
            if cancel_flag.is_set():
                skipped += 1
                continue

            test_case_id = test["id"]
            result_id = str(uuid.uuid4())[:12]
            steps = json.loads(test["steps"]) if isinstance(test["steps"], str) else test["steps"]

            conn.execute(
                "INSERT INTO test_results (id, run_id, test_case_id, status, started_at) VALUES (?, ?, ?, ?, ?)",
                (result_id, run_id, test_case_id, "running", datetime.now().isoformat()),
            )
            conn.commit()

            self._emit_event(run_id, {
                "type": "test_start",
                "test_case_id": test_case_id,
                "title": test["title"],
                "result_id": result_id,
            })

            # Send test header to console with spacing
            blank = "echo." if sys.platform == "win32" else "echo"
            title = test["title"]
            if sys.platform == "win32":
                # Title is arbitrary text echoed into a cmd batch; escape the cmd
                # metacharacters that would otherwise split the line (e.g.
                # "& .NET Standard"). Caret first so we don't double-escape the
                # carets we add.
                for ch in "^&<>|()":
                    title = title.replace(ch, "^" + ch)
            self._queue_console_cmd(run_id, blank)
            self._queue_console_cmd(run_id, f"echo ===== {title} =====")
            self._queue_console_cmd(run_id, blank)

            test_passed = self._execute_test(
                run_id, result_id, steps, cancel_flag, conn
            )

            status = "passed" if test_passed else "failed"
            if cancel_flag.is_set():
                status = "cancelled"
                skipped += 1
            elif test_passed:
                passed += 1
            else:
                failed += 1

            conn.execute(
                "UPDATE test_results SET status=?, finished_at=? WHERE id=?",
                (status, datetime.now().isoformat(), result_id),
            )
            conn.commit()

            self._emit_event(run_id, {
                "type": "test_end",
                "test_case_id": test_case_id,
                "result_id": result_id,
                "status": status,
            })

        # Finalize run
        final_status = "completed" if not cancel_flag.is_set() else "cancelled"
        summary = json.dumps({"passed": passed, "failed": failed, "skipped": skipped})
        conn.execute(
            "UPDATE test_runs SET status=?, finished_at=?, summary=? WHERE id=?",
            (final_status, datetime.now().isoformat(), summary, run_id),
        )
        conn.commit()
        conn.close()

        self._emit_event(run_id, {
            "type": "run_end",
            "status": final_status,
            "summary": {"passed": passed, "failed": failed, "skipped": skipped},
        })
        self._end_stream(run_id)

        # Cleanup
        self._cancel_flags.pop(run_id, None)
        self._close_console(run_id)

    def _execute_test(
        self, run_id: str, result_id: str, steps: List[dict],
        cancel_flag: threading.Event, conn: sqlite3.Connection
    ) -> bool:
        """Execute all steps for a single test case. Returns True if all pass."""
        # Create a temp working directory for this test
        work_dir = tempfile.mkdtemp(prefix="dotnet_test_")
        current_dir = work_dir
        all_passed = True

        # Pin SDK version via global.json if specified
        sdk_version = self._runs.get(run_id, {}).get("sdk_version") or None
        # {tfm} in step commands/content tracks the selected SDK's target framework
        # moniker (e.g. 11.0.100 -> net11.0). net48 and other literals are untouched.
        tfm = f"net{sdk_version.split('.')[0]}.0" if sdk_version else "net10.0"
        if sdk_version:
            global_json = os.path.join(work_dir, "global.json")
            with open(global_json, "w") as f:
                json.dump({"sdk": {"version": sdk_version, "rollForward": "disable"}}, f)

        for idx, step in enumerate(steps):
            if cancel_flag.is_set():
                return False

            step_id = str(uuid.uuid4())[:12]
            step_type = step.get("type", "command")
            timeout = step.get("timeout", 120)
            expected_exit = step.get("expected_exit_code", 0)

            if step_type == "command":
                cmd = step["command"].replace("{tfm}", tfm)
                # Handle cd commands by updating current_dir
                if cmd.strip().startswith("cd "):
                    prev_dir = current_dir
                    target = cmd.strip()[3:].strip()
                    if os.path.isabs(target):
                        current_dir = target
                    else:
                        current_dir = os.path.normpath(os.path.join(current_dir, target))
                    # Ensure global.json is in the new directory too
                    if sdk_version and os.path.isdir(current_dir):
                        gj = os.path.join(current_dir, "global.json")
                        if not os.path.exists(gj):
                            with open(gj, "w") as f:
                                json.dump({"sdk": {"version": sdk_version, "rollForward": "disable"}}, f)
                    conn.execute(
                        """INSERT INTO step_results (id, test_result_id, step_index, step_type, command, exit_code, stdout, status, duration_ms)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (step_id, result_id, idx, "cd", cmd, 0, f"Changed to: {current_dir}", "passed", 0),
                    )
                    conn.commit()
                    self._emit_event(run_id, {
                        "type": "step_output",
                        "result_id": result_id,
                        "step_index": idx,
                        "line": f"$ {cmd}\n  → {current_dir}",
                    })
                    # Send cd to console, echoing the prompt at the dir BEFORE cd so it matches a manual run
                    cd_cmd = f"cd /d {current_dir}" if sys.platform == "win32" else f"cd {current_dir}"
                    self._queue_console_cmd(run_id, f"echo {prev_dir}^> {cmd}" if sys.platform == "win32" else f"echo '{prev_dir}$ {cmd}'")
                    self._queue_console_cmd(run_id, cd_cmd)
                    continue

                # Long-running server step (e.g. `dotnet run`): stream output, wait
                # for readiness, optionally verify the hosted site, then terminate.
                # `run_timeout` reuses the same path for blocking GUI apps (WinForms/
                # WPF): run for N seconds so the window shows, then auto-close it.
                if step.get("long_running") or step.get("run_timeout"):
                    start_time = time.time()
                    self._emit_event(run_id, {
                        "type": "step_output",
                        "result_id": result_id,
                        "step_index": idx,
                        "line": f"$ {cmd}",
                        "is_command": True,
                    })
                    stdout_text, exit_code = self._run_long_running(
                        run_id, result_id, idx, cmd, current_dir, step, cancel_flag, timeout
                    )
                    if cancel_flag.is_set():
                        return False
                    duration_ms = int((time.time() - start_time) * 1000)
                    step_passed = (exit_code == 0)
                    status = "passed" if step_passed else "failed"
                    if not step_passed:
                        all_passed = False
                    conn.execute(
                        """INSERT INTO step_results (id, test_result_id, step_index, step_type, command, exit_code, stdout, stderr, status, duration_ms)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (step_id, result_id, idx, "command", cmd, exit_code,
                         stdout_text[:10000], "", status, duration_ms),
                    )
                    conn.commit()
                    self._emit_event(run_id, {
                        "type": "step_end",
                        "result_id": result_id,
                        "step_index": idx,
                        "status": status,
                        "exit_code": exit_code,
                        "duration_ms": duration_ms,
                    })
                    if not step_passed and not step.get("continue_on_error", False):
                        break
                    continue

                # Execute the command
                start_time = time.time()

                # Emit the command being run to the app
                self._emit_event(run_id, {
                    "type": "step_output",
                    "result_id": result_id,
                    "step_index": idx,
                    "line": f"$ {cmd}",
                    "is_command": True,
                })

                # Run command in the console window and capture output
                stdout_text, stderr_text, exit_code = self._run_in_console(
                    run_id, cmd, current_dir, timeout, cancel_flag
                )

                if cancel_flag.is_set():
                    return False

                # Emit captured output to the in-app runner
                if stdout_text:
                    for line in stdout_text.splitlines():
                        self._emit_event(run_id, {
                            "type": "step_output",
                            "result_id": result_id,
                            "step_index": idx,
                            "line": line,
                        })
                if stderr_text:
                    for line in stderr_text.splitlines():
                        self._emit_event(run_id, {
                            "type": "step_output",
                            "result_id": result_id,
                            "step_index": idx,
                            "line": f"[STDERR] {line}",
                        })

                duration_ms = int((time.time() - start_time) * 1000)

                # Determine pass/fail
                if isinstance(expected_exit, list):
                    step_passed = exit_code in expected_exit
                else:
                    step_passed = exit_code == expected_exit

                # Check output assertions if any
                if step_passed and "assert_output_contains" in step:
                    for pattern in step["assert_output_contains"]:
                        if pattern not in stdout_text:
                            step_passed = False
                            break

                status = "passed" if step_passed else "failed"
                if not step_passed:
                    all_passed = False

                conn.execute(
                    """INSERT INTO step_results (id, test_result_id, step_index, step_type, command, exit_code, stdout, stderr, status, duration_ms)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (step_id, result_id, idx, "command", cmd, exit_code,
                     stdout_text[:10000], stderr_text[:5000],
                     status, duration_ms),
                )
                conn.commit()

                self._emit_event(run_id, {
                    "type": "step_end",
                    "result_id": result_id,
                    "step_index": idx,
                    "status": status,
                    "exit_code": exit_code,
                    "duration_ms": duration_ms,
                })

                # Stop test on first failure unless continue_on_error
                if not step_passed and not step.get("continue_on_error", False):
                    break

            elif step_type == "write_file":
                filepath = os.path.join(current_dir, step["path"])
                os.makedirs(os.path.dirname(filepath), exist_ok=True)
                content = step["content"].replace("{tfm}", tfm)

                start_time = time.time()
                wrote_via_notepad = False

                if sys.platform == "win32":
                    try:
                        wrote_via_notepad = self._write_file_via_notepad(filepath, content)
                    except Exception:
                        wrote_via_notepad = False

                if not wrote_via_notepad:
                    # Fallback to direct write
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(content)

                duration_ms = int((time.time() - start_time) * 1000)
                method_label = "via Notepad" if wrote_via_notepad else "direct"

                conn.execute(
                    """INSERT INTO step_results (id, test_result_id, step_index, step_type, command, exit_code, stdout, status, duration_ms)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (step_id, result_id, idx, "write_file", step["path"], 0,
                     f"Wrote {len(content)} bytes ({method_label})", "passed", duration_ms),
                )
                conn.commit()

                self._emit_event(run_id, {
                    "type": "step_output",
                    "result_id": result_id,
                    "step_index": idx,
                    "line": f"📝 Wrote file ({method_label}): {step['path']}",
                })

        return all_passed

    def _write_file_via_notepad(self, filepath: str, content: str) -> bool:
        """
        Write file content, open it in Notepad to display, then close.
        Returns True if successful, False otherwise.
        """
        from pywinauto import Application

        # Write the actual content to the file first
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        # Open Notepad with the file so the user can see the content
        app = Application(backend="win32").start(f'notepad.exe "{filepath}"')

        try:
            # Wait for the Notepad window to be ready
            notepad_window = app.window(title_re=".*Notepad.*|.*" + os.path.basename(filepath) + ".*")
            notepad_window.wait("ready", timeout=3)

            # Brief pause so the user can see the file content
            time.sleep(0.5)

            # Close the tab (Ctrl+W)
            notepad_window.type_keys("^w", pause=0.05)
            time.sleep(0.2)

            # If Notepad asks to save, dismiss with Don't Save
            try:
                save_dialog = app.window(title_re=".*Save.*|.*Notepad.*")
                if save_dialog.exists(timeout=0.2):
                    save_dialog.type_keys("%n", pause=0.05)
            except Exception:
                pass

            return True

        except Exception:
            try:
                app.kill()
            except Exception:
                pass
            return False

    def _queue_console_cmd(self, run_id: str, command: str):
        """Write a command to the console's command queue file."""
        console_dir = self._console_procs.get(run_id)
        if not console_dir or not isinstance(console_dir, str):
            return
        try:
            cmd_file = os.path.join(console_dir, "commands.txt")
            with open(cmd_file, "a", encoding="utf-8") as f:
                f.write(command + "\n")
        except Exception:
            pass

    def _run_in_console(
        self, run_id: str, cmd: str, cwd: str, timeout: int,
        cancel_flag: threading.Event
    ) -> tuple:
        """
        Run a command in the visible console window and capture its output.
        Returns (stdout, stderr, exit_code).
        """
        console_dir = self._console_procs.get(run_id)

        if not console_dir or not isinstance(console_dir, str):
            # No console available — run directly (fallback)
            return self._run_direct(cmd, cwd, timeout, cancel_flag, run_id)

        # Temp files for capturing output and exit code
        stdout_file = os.path.join(console_dir, "stdout.tmp")
        exitcode_file = os.path.join(console_dir, "exitcode.tmp")

        # Clean up any old result files
        for f in [stdout_file, exitcode_file]:
            if os.path.exists(f):
                os.unlink(f)

        # Build wrapper commands that:
        # 1. cd to the working directory
        # 2. Run the command (output shows in console)
        # 3. Tee output to a file for the app to read
        # 4. Write exit code to a file
        cmd_file = os.path.join(console_dir, "commands.txt")
        try:
            if sys.platform == "win32":
                # Write a dedicated wrapper batch that captures exit code reliably
                wrapper_file = os.path.join(console_dir, "runcmd.bat")
                with open(wrapper_file, "w", encoding="utf-8") as wf:
                    wf.write("@echo off\n")
                    wf.write("set MSBUILDTERMINALLOGGER=on\n")
                    wf.write(f"cd /d {cwd}\n")
                    wf.write(f"echo {cwd}^> {cmd}\n")
                    # Use && and || to reliably capture success/failure
                    wf.write(f'{cmd} > "{stdout_file}" 2>&1 && (echo 0 > "{exitcode_file}") || (echo 1 > "{exitcode_file}")\n')
                    wf.write(f'type "{stdout_file}"\n')
                    wf.write("echo.\n")
                # Tell the console to call the wrapper
                with open(cmd_file, "a", encoding="utf-8") as f:
                    f.write(f'call "{wrapper_file}"\n')
            else:
                with open(cmd_file, "a", encoding="utf-8") as f:
                    f.write(f"cd {cwd}\n")
                    f.write(f"echo '{cwd}$ {cmd}'\n")
                    f.write("export MSBUILDTERMINALLOGGER=on\n")
                    f.write(f'{cmd} 2>&1 | tee "{stdout_file}"; echo $? > "{exitcode_file}"\n')
                    f.write("echo\n")
        except Exception:
            return self._run_direct(cmd, cwd, timeout, cancel_flag, run_id)

        # Wait for the exit code file to appear (means command finished)
        deadline = time.time() + timeout
        while not os.path.exists(exitcode_file):
            if cancel_flag.is_set():
                return ("", "", -1)
            if time.time() > deadline:
                return ("", "", -1)
            time.sleep(0.5)

        # Small delay to ensure files are fully written
        time.sleep(0.3)

        # Read captured output
        stdout_text = ""
        exit_code = -1

        try:
            if os.path.exists(stdout_file):
                with open(stdout_file, "r", encoding="utf-8", errors="replace") as f:
                    stdout_text = f.read()
            if os.path.exists(exitcode_file):
                with open(exitcode_file, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read().strip()
                    if content:
                        exit_code = int(content)
        except (ValueError, OSError):
            pass

        # Cleanup result files for next command
        for f in [stdout_file, exitcode_file]:
            try:
                if os.path.exists(f):
                    os.unlink(f)
            except OSError:
                pass

        return (_render_terminal_output(stdout_text), "", exit_code)

    def _run_long_running(
        self, run_id: str, result_id: str, idx: int, cmd: str, cwd: str,
        step: dict, cancel_flag: threading.Event, timeout: int
    ) -> tuple:
        """
        Start a long-running server command, stream its output live to BOTH the app
        panel and the popup console, wait for a readiness pattern, optionally
        HTTP-verify the hosted site, then terminate. Returns (stdout_text, exit_code).

        Python owns the process (clean PID kill). Each captured line is written to a
        log file that the popup console tails live via a small PowerShell script, so
        the console streams the same output as the panel in real time.
        # ponytail: live tail is Windows-only; other platforms fall back to a single
        # block dump after the step. Add a `tail -f`+sentinel loop if posix needs live.
        """
        ready_patterns = step.get("ready_pattern") or []
        if isinstance(ready_patterns, str):
            ready_patterns = [ready_patterns]
        verify_url = step.get("verify_url")
        contains = step.get("verify_contains")
        if isinstance(contains, str):
            contains = [contains]
        # After a site is confirmed up, optionally open it in the browser and hold
        # the server alive so it can be eyeballed (like the Notepad file preview).
        open_in_browser = step.get("open_in_browser", True)
        hold_seconds = step.get("hold_seconds", 10)

        console_dir = self._console_procs.get(run_id)
        console_dir = console_dir if isinstance(console_dir, str) else None
        live = bool(console_dir) and sys.platform == "win32"

        lines = []
        ready = threading.Event()
        log_fh = None
        done_flag = None

        if live:
            srv_log = os.path.join(console_dir, f"srv_{idx}_{uuid.uuid4().hex[:8]}.log")
            done_flag = srv_log + ".done"
            for p in (srv_log, done_flag):
                if os.path.exists(p):
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
            try:
                log_fh = open(srv_log, "a", encoding="utf-8")
            except OSError:
                log_fh = None
            if log_fh:
                tail_ps1 = self._write_tail_script(console_dir)
                # ponytail: full path — the spawned console's PATH may lack System32,
                # so bare `powershell` isn't found (same reason chcp uses a full path).
                ps_exe = os.path.join(
                    os.environ.get("SystemRoot", r"C:\Windows"),
                    "System32", "WindowsPowerShell", "v1.0", "powershell.exe",
                )
                self._queue_console_cmd(
                    run_id,
                    f'"{ps_exe}" -NoProfile -ExecutionPolicy Bypass -File "{tail_ps1}" "{srv_log}" "{done_flag}"',
                )

        def write_log(text):
            if log_fh:
                try:
                    log_fh.write(text)
                    log_fh.flush()
                except (OSError, ValueError):
                    pass

        write_log(f"\n{cwd}> {cmd}\n")

        def finish(exit_code):
            if log_fh:
                try:
                    log_fh.close()
                except OSError:
                    pass
            if done_flag:
                # Signals the console tailer to flush the rest and exit.
                try:
                    open(done_flag, "w").close()
                except OSError:
                    pass
            elif console_dir:
                # No live tail (posix): dump the whole block after the step.
                self._console_show_output(run_id, f"{cwd}> {cmd}\n" + "".join(lines))
            return (_render_terminal_output("".join(lines)), exit_code)

        env = os.environ.copy()
        env["DOTNET_CLI_COLORS"] = "1"
        env["FORCE_COLOR"] = "1"

        try:
            proc = subprocess.Popen(
                cmd, shell=True, cwd=cwd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", env=env,
            )
        except Exception as e:
            lines.append(str(e))
            write_log(str(e))
            return finish(1)

        def reader():
            for line in proc.stdout:
                lines.append(line)
                write_log(line)
                self._emit_event(run_id, {
                    "type": "step_output",
                    "result_id": result_id,
                    "step_index": idx,
                    "line": line.rstrip("\n"),
                })
                if not ready.is_set() and any(p in line for p in ready_patterns):
                    ready.set()

        t = threading.Thread(target=reader, daemon=True)
        t.start()

        # Timed run for blocking GUI apps (WinForms/WPF): no readiness pattern —
        # let the window render for `run_timeout` seconds, then auto-close it and
        # pass. An early exit means the app quit on its own: use its exit code so
        # a build/runtime failure (nonzero) is still reported as a failure.
        run_timeout = step.get("run_timeout")
        if run_timeout:
            deadline = time.time() + run_timeout
            while time.time() < deadline:
                if cancel_flag.is_set():
                    self._terminate_proc(proc)
                    t.join(timeout=2)
                    return finish(-1)
                if proc.poll() is not None:
                    t.join(timeout=2)
                    return finish(proc.returncode or 0)
                time.sleep(0.2)
            self._terminate_proc(proc)
            t.join(timeout=2)
            return finish(0)

        # Wait for readiness, process exit, timeout, or cancel.
        deadline = time.time() + timeout
        while not ready.is_set() and proc.poll() is None:
            if cancel_flag.is_set():
                self._terminate_proc(proc)
                t.join(timeout=2)
                return finish(-1)
            if time.time() > deadline:
                self._terminate_proc(proc)
                t.join(timeout=2)
                msg = "\n[timeout waiting for readiness]\n"
                lines.append(msg)
                write_log(msg)
                return finish(1)
            time.sleep(0.2)

        # Process died before becoming ready -> failure (build/run error).
        if not ready.is_set():
            t.join(timeout=2)
            return finish(proc.returncode or 1)

        exit_code = 0
        if verify_url:
            ok, status, body, err = self._verify_site(verify_url, contains)
            msg = f"GET {verify_url} -> HTTP {status}" + (f" | {err}" if err else "")
            self._emit_event(run_id, {
                "type": "step_output",
                "result_id": result_id,
                "step_index": idx,
                "line": ("✅ " if ok else "❌ ") + msg,
            })
            log_msg = "\n" + ("[OK] " if ok else "[FAIL] ") + msg + "\n"
            lines.append(log_msg)
            write_log(log_msg)
            if not ok:
                lines.append(body[:5000])
                write_log(body[:5000])
                exit_code = 1

        # Open the live site for visual verification, keeping the server up for a
        # short hold so it can be seen, then continue the automation.
        if verify_url and open_in_browser:
            try:
                webbrowser.open(verify_url)
            except Exception:
                pass
            hold_deadline = time.time() + hold_seconds
            while time.time() < hold_deadline and not cancel_flag.is_set():
                time.sleep(0.2)

        self._terminate_proc(proc)
        t.join(timeout=2)
        return finish(exit_code)

    def _write_tail_script(self, console_dir: str) -> str:
        """Write (idempotently) a PowerShell script that live-tails a growing log
        file to the console and exits once a done-flag file appears."""
        path = os.path.join(console_dir, "tail.ps1")
        script = (
            'param([string]$Path, [string]$DoneFlag)\n'
            '$pos = 0\n'
            'while ($true) {\n'
            '  if (Test-Path -LiteralPath $Path) {\n'
            '    try { $c = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 -ErrorAction Stop } catch { $c = $null }\n'
            '    if ($c -and $c.Length -gt $pos) { [Console]::Out.Write($c.Substring($pos)); $pos = $c.Length }\n'
            '  }\n'
            '  if (Test-Path -LiteralPath $DoneFlag) { break }\n'
            '  Start-Sleep -Milliseconds 150\n'
            '}\n'
        )
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(script)
        except OSError:
            pass
        return path

    def _console_show_output(self, run_id: str, text: str):
        """Dump arbitrary text into the popup console via a temp file + `type`.
        # ponytail: `type`/`cat` a file instead of `echo` so server output with
        # special chars (: / | & %) is shown verbatim, no shell-escaping needed.
        """
        console_dir = self._console_procs.get(run_id)
        if not console_dir or not isinstance(console_dir, str):
            return
        try:
            out_file = os.path.join(console_dir, f"srvout_{uuid.uuid4().hex[:8]}.txt")
            with open(out_file, "w", encoding="utf-8") as f:
                f.write(text)
            if sys.platform == "win32":
                self._queue_console_cmd(run_id, f'type "{out_file}"')
                self._queue_console_cmd(run_id, "echo.")
            else:
                self._queue_console_cmd(run_id, f'cat "{out_file}"')
                self._queue_console_cmd(run_id, "echo")
        except Exception:
            pass

    def _verify_site(self, url: str, contains) -> tuple:
        """One HTTP GET (a couple of tries). Returns (ok, status, body, err)."""
        # ponytail: dev HTTPS cert is self-signed -> skip TLS verification.
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        last_err = ""
        # ponytail: fixed 2 attempts, no backoff lib; server may need a beat
        # after "Now listening on". Bump the range if that proves flaky.
        for attempt in range(2):
            try:
                with urllib.request.urlopen(url, timeout=10, context=ctx) as resp:
                    status = getattr(resp, "status", resp.getcode())
                    body = resp.read().decode("utf-8", "replace")
                ok = 200 <= status < 300
                missing = [s for s in (contains or []) if s not in body]
                if ok and not missing:
                    return (True, status, body, "")
                err = f"missing {missing}" if missing else f"unexpected status {status}"
                return (False, status, body, err)
            except Exception as e:
                last_err = str(e)
                time.sleep(1)
        return (False, 0, "", last_err)

    def _terminate_proc(self, proc: subprocess.Popen):
        """Kill the process and its children."""
        try:
            if sys.platform == "win32":
                # ponytail: taskkill /T reaps the dotnet child that shell=True spawns;
                # proc.terminate() alone leaves the server listening.
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            else:
                proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _run_direct(
        self, cmd: str, cwd: str, timeout: int,
        cancel_flag: threading.Event, run_id: str
    ) -> tuple:
        """Fallback: run command directly when no console is available."""
        stdout_lines = []
        stderr_lines = []
        exit_code = -1

        try:
            env = os.environ.copy()
            env["DOTNET_CLI_COLORS"] = "1"
            env["DOTNET_SYSTEM_CONSOLE_ALLOW_ANSI_COLOR_REDIRECTION"] = "1"
            env["FORCE_COLOR"] = "1"
            env["TERM"] = "xterm-256color"
            env["MSBUILDTERMINALLOGGER"] = "on"

            proc = subprocess.Popen(
                cmd, shell=True, cwd=cwd,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", env=env,
            )

            def read_stdout():
                for line in proc.stdout:
                    stdout_lines.append(line)

            def read_stderr():
                for line in proc.stderr:
                    stderr_lines.append(line)

            t1 = threading.Thread(target=read_stdout, daemon=True)
            t2 = threading.Thread(target=read_stderr, daemon=True)
            t1.start()
            t2.start()

            deadline = time.time() + timeout
            while proc.poll() is None:
                if cancel_flag.is_set():
                    proc.terminate()
                    return ("", "", -1)
                if time.time() > deadline:
                    proc.terminate()
                    return ("", "", -1)
                time.sleep(0.1)

            t1.join(timeout=5)
            t2.join(timeout=5)
            exit_code = proc.returncode if proc.returncode is not None else -1

        except Exception as e:
            stderr_lines.append(str(e))

        return (_render_terminal_output("".join(stdout_lines)), "".join(stderr_lines), exit_code)

    def _resolve_sdk_version(self, pinned: str = None) -> str:
        """Return the SDK version dotnet actually resolves for this run.

        Resolves using the same global.json the test steps use so history logs
        the version that really executes, not a stale pinned string. Falls back
        to the unpinned default when the pinned version is no longer installed.
        """
        def dotnet_version(cwd):
            try:
                r = subprocess.run(
                    ["dotnet", "--version"], capture_output=True, text=True,
                    timeout=30, cwd=cwd,
                )
                return r.stdout.strip() if r.returncode == 0 else None
            except Exception:
                return None

        if not pinned:
            return dotnet_version(None)

        d = tempfile.mkdtemp(prefix="sdk_resolve_")
        try:
            with open(os.path.join(d, "global.json"), "w") as f:
                json.dump({"sdk": {"version": pinned, "rollForward": "disable"}}, f)
            # ponytail: unpinned fallback = version that actually runs when pinned is gone
            return dotnet_version(d) or dotnet_version(None)
        finally:
            try:
                os.remove(os.path.join(d, "global.json"))
                os.rmdir(d)
            except OSError:
                pass

    def _capture_environment(self) -> str:
        try:
            result = subprocess.run(
                ["dotnet", "--info"], capture_output=True, text=True, timeout=30
            )
            return result.stdout
        except Exception as e:
            return f"Could not capture environment: {e}"
