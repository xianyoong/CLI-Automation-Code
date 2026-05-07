import os
import sys
import json
import uuid
import subprocess
import tempfile
import threading
import time
import sqlite3
from datetime import datetime
from queue import Queue, Empty
from typing import Dict, List, Any, Generator


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

    def start_run(self, run_id: str, tests: List[dict]):
        """Start executing tests in a background thread."""
        cancel_flag = threading.Event()
        self._cancel_flags[run_id] = cancel_flag
        self._runs[run_id] = {"status": "running", "tests": tests}
        self._event_queues[run_id] = []

        conn = get_db()
        conn.execute(
            "INSERT INTO test_runs (id, started_at, status) VALUES (?, ?, ?)",
            (run_id, datetime.now().isoformat(), "running"),
        )
        conn.commit()
        conn.close()

        thread = threading.Thread(
            target=self._execute_run, args=(run_id, tests, cancel_flag), daemon=True
        )
        thread.start()

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

    def _execute_test(
        self, run_id: str, result_id: str, steps: List[dict],
        cancel_flag: threading.Event, conn: sqlite3.Connection
    ) -> bool:
        """Execute all steps for a single test case. Returns True if all pass."""
        # Create a temp working directory for this test
        work_dir = tempfile.mkdtemp(prefix="dotnet_test_")
        current_dir = work_dir
        all_passed = True

        for idx, step in enumerate(steps):
            if cancel_flag.is_set():
                return False

            step_id = str(uuid.uuid4())[:12]
            step_type = step.get("type", "command")
            timeout = step.get("timeout", 120)
            expected_exit = step.get("expected_exit_code", 0)

            if step_type == "command":
                cmd = step["command"]
                # Handle cd commands by updating current_dir
                if cmd.strip().startswith("cd "):
                    target = cmd.strip()[3:].strip()
                    if os.path.isabs(target):
                        current_dir = target
                    else:
                        current_dir = os.path.normpath(os.path.join(current_dir, target))
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
                    continue

                # Execute the command
                start_time = time.time()
                stdout_lines = []
                stderr_lines = []
                exit_code = -1

                try:
                    # Force color output from dotnet and other CLI tools
                    env = os.environ.copy()
                    env["DOTNET_CLI_COLORS"] = "1"
                    env["DOTNET_SYSTEM_CONSOLE_ALLOW_ANSI_COLOR_REDIRECTION"] = "1"
                    env["FORCE_COLOR"] = "1"
                    env["TERM"] = "xterm-256color"

                    # Use shell=True on Windows for dotnet commands
                    proc = subprocess.Popen(
                        cmd,
                        shell=True,
                        cwd=current_dir,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        env=env,
                    )

                    # Stream stdout
                    def read_stdout():
                        for line in proc.stdout:
                            stdout_lines.append(line)
                            self._emit_event(run_id, {
                                "type": "step_output",
                                "result_id": result_id,
                                "step_index": idx,
                                "line": line.rstrip("\n"),
                            })

                    def read_stderr():
                        for line in proc.stderr:
                            stderr_lines.append(line)
                            self._emit_event(run_id, {
                                "type": "step_output",
                                "result_id": result_id,
                                "step_index": idx,
                                "line": f"[STDERR] {line.rstrip(chr(10))}",
                            })

                    t1 = threading.Thread(target=read_stdout, daemon=True)
                    t2 = threading.Thread(target=read_stderr, daemon=True)
                    t1.start()
                    t2.start()

                    # Wait with timeout and cancel support
                    deadline = time.time() + timeout
                    while proc.poll() is None:
                        if cancel_flag.is_set():
                            proc.terminate()
                            return False
                        if time.time() > deadline:
                            proc.terminate()
                            self._emit_event(run_id, {
                                "type": "step_output",
                                "result_id": result_id,
                                "step_index": idx,
                                "line": f"[TIMEOUT] Step exceeded {timeout}s timeout",
                            })
                            break
                        time.sleep(0.1)

                    t1.join(timeout=5)
                    t2.join(timeout=5)
                    exit_code = proc.returncode if proc.returncode is not None else -1

                except Exception as e:
                    stderr_lines.append(str(e))
                    self._emit_event(run_id, {
                        "type": "step_output",
                        "result_id": result_id,
                        "step_index": idx,
                        "line": f"[ERROR] {e}",
                    })

                duration_ms = int((time.time() - start_time) * 1000)

                # Determine pass/fail
                if isinstance(expected_exit, list):
                    step_passed = exit_code in expected_exit
                else:
                    step_passed = exit_code == expected_exit

                # Check output assertions if any
                if step_passed and "assert_output_contains" in step:
                    full_output = "".join(stdout_lines)
                    for pattern in step["assert_output_contains"]:
                        if pattern not in full_output:
                            step_passed = False
                            break

                status = "passed" if step_passed else "failed"
                if not step_passed:
                    all_passed = False

                conn.execute(
                    """INSERT INTO step_results (id, test_result_id, step_index, step_type, command, exit_code, stdout, stderr, status, duration_ms)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (step_id, result_id, idx, "command", cmd, exit_code,
                     "".join(stdout_lines)[:10000], "".join(stderr_lines)[:5000],
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
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(step["content"])

                conn.execute(
                    """INSERT INTO step_results (id, test_result_id, step_index, step_type, command, exit_code, stdout, status, duration_ms)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (step_id, result_id, idx, "write_file", step["path"], 0,
                     f"Wrote {len(step['content'])} bytes", "passed", 0),
                )
                conn.commit()

                self._emit_event(run_id, {
                    "type": "step_output",
                    "result_id": result_id,
                    "step_index": idx,
                    "line": f"📝 Wrote file: {step['path']}",
                })

        return all_passed

    def _capture_environment(self) -> str:
        try:
            result = subprocess.run(
                ["dotnet", "--info"], capture_output=True, text=True, timeout=30
            )
            return result.stdout
        except Exception as e:
            return f"Could not capture environment: {e}"
