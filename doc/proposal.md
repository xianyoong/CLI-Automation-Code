# .NET SDK CLI Test Automation Tool — Requirements Document (V1)

> **Document Version:** 1.0  
> **Date:** 2026-05-22  
> **Status:** Draft / Proposal

---

## 1. Overview

A standalone Windows desktop application that automates .NET SDK CLI testing with real-time visual proof of command execution. The tool types commands into a visible terminal window (via Win32 ConPTY), captures output for assertion, logs all results to SQLite, and provides reporting/export capabilities.

### 1.1 Goals

- Provide **visual proof** that CLI commands are physically typed and executed (for SDK/MAUI behavior verification)
- Automate repetitive dotnet CLI test scenarios without manual intervention
- Enable non-developers to manage test cases via a GUI (no source code editing)
- Produce auditable logs and exportable reports

### 1.2 Target Users

- .NET SDK testers who need to verify CLI behavior with visual evidence
- Single user, single machine usage

### 1.3 Target Platform

- Windows 10/11 x64 only

---

## 2. Architecture

### 2.1 Technology Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.12+ |
| GUI | pywebview (wraps existing React/TypeScript frontend) |
| Backend | Flask (embedded, no visible port) |
| Frontend | React + TypeScript + Vite (existing, with Catppuccin Mocha theme) |
| Database | SQLite (WAL mode) |
| Automation | Win32 ConPTY + SendMessage API |
| Packaging | PyInstaller (.exe, single-file or one-folder) |
| Win32 APIs | `ctypes` / `pywin32` for ConPTY, SendMessage, window management |

### 2.2 Application Structure

```
┌───────────────────────────────────────────────┐
│  pywebview Window (Chromium/Edge WebView2)    │
│  ┌─────────────────────────────────────────┐  │
│  │  React Frontend (Catppuccin Mocha)      │  │
│  │  - Test List / Runner / History views   │  │
│  │  - Visual Step Builder (form-based)     │  │
│  └─────────────────────────────────────────┘  │
│                  ▲ SSE / REST API             │
│                  ▼                            │
│  ┌─────────────────────────────────────────┐  │
│  │  Flask Backend (embedded)               │  │
│  │  - Test management CRUD                 │  │
│  │  - Execution orchestration              │  │
│  │  - SDK version management               │  │
│  └─────────────────────────────────────────┘  │
│                  ▲                            │
│                  ▼                            │
│  ┌─────────────────────────────────────────┐  │
│  │  Automation Engine                      │  │
│  │  - ConPTY (visible terminal + output)   │  │
│  │  - Win32 SendMessage (visual typing)    │  │
│  │  - Notepad integration (file verify)    │  │
│  └─────────────────────────────────────────┘  │
│                  ▲                            │
│                  ▼                            │
│  ┌─────────────────────────────────────────┐  │
│  │  SQLite Database                        │  │
│  │  - test_cases, test_runs, results       │  │
│  │  - step_results, command logs           │  │
│  └─────────────────────────────────────────┘  │
└───────────────────────────────────────────────┘
```

### 2.3 Project Folder Structure

```
CLI-Automation-Code/
├── src/
│   ├── app.py                  # Entry point — pywebview + Flask startup
│   ├── backend/
│   │   ├── __init__.py
│   │   ├── server.py           # Flask routes (REST API)
│   │   ├── database.py         # SQLite connection & schema
│   │   ├── models.py           # Data models / DB operations
│   │   └── sdk_manager.py      # .NET SDK version switching logic
│   ├── engine/
│   │   ├── __init__.py
│   │   ├── executor.py         # Test orchestration (run/cancel/stream)
│   │   ├── conpty.py           # ConPTY wrapper (create terminal, read output)
│   │   ├── win32_typing.py     # SendMessage/PostMessage keystroke injection
│   │   ├── notepad.py          # Notepad open/type/verify integration
│   │   └── assertions.py       # Output assertion helpers
│   └── test_definitions/       # Built-in YAML test case files
│       ├── 01_csharp_basic.yaml
│       └── ...
├── frontend/                   # React/TypeScript frontend (existing)
│   ├── src/
│   ├── package.json
│   └── vite.config.ts
├── doc/
│   ├── proposal.md             # This document
│   └── img/                    # UI mockup screenshots
├── build.spec                  # PyInstaller build specification
├── pyproject.toml              # Python project metadata & dependencies
└── README.md
```

---

## 3. Functional Requirements

### 3.1 Application & Packaging

| ID | Requirement | Details |
|----|------------|---------|
| APP-01 | Desktop window | pywebview window wrapping embedded Flask + React frontend |
| APP-02 | No install required | Single .exe via PyInstaller — plug and play, no Python on target |
| APP-03 | Catppuccin Mocha theme | Apply Catppuccin Mocha color palette to all UI components |
| APP-04 | Embedded server | Flask runs in-process; no visible port exposed to user |
| APP-05 | Graceful startup | Show splash/loading while Flask + WebView initialize |

### 3.2 Automation Engine

| ID | Requirement | Details |
|----|------------|---------|
| ENG-01 | ConPTY terminal | Create a Windows Pseudo Console for command execution — provides both a visible terminal window AND piped stdout/stderr for programmatic capture |
| ENG-02 | Visual typing (CMD) | Use Win32 `SendMessage`/`PostMessage` to inject keystrokes into the ConPTY terminal — commands appear as real-time typing for visual proof |
| ENG-03 | Visual typing (Notepad) | For `write_file` steps: open Notepad, type content via SendMessage for visual proof, then save. Programmatically read the saved file to assert content correctness |
| ENG-04 | Output capture | Read ConPTY output pipe to capture stdout/stderr for assertion and logging |
| ENG-05 | SDK version switching | Modify `global.json` in the test working directory and prepend the selected SDK path to `PATH` for the ConPTY process environment |
| ENG-06 | Stop/abandon mid-run | User can cancel execution at any time; terminate ConPTY process, mark remaining tests as "skipped" |
| ENG-07 | Error handling | On step failure: mark current test case as FAIL, then automatically proceed to next test case |
| ENG-08 | Timeout | Per-step configurable timeout (default 120s); kill process and mark FAIL on timeout |
| ENG-09 | Sequential execution | Test cases execute one at a time (no parallel execution — avoids ConPTY/window conflicts) |

### 3.3 Test Case Management

| ID | Requirement | Details |
|----|------------|---------|
| TC-01 | Add test case via UI | "Add Test" button opens a form-based visual step builder |
| TC-02 | Edit test case via UI | "Edit" button on each test case opens the step builder pre-filled |
| TC-03 | Delete test case via UI | "Delete" button with confirmation; only user-created tests can be deleted |
| TC-04 | Visual step builder | Form-based editor for steps — dropdowns for step type, text fields for command/path/content, no raw JSON editing |
| TC-05 | Built-in tests (read-only) | Loaded from YAML files; cannot be deleted, but can be deselected |
| TC-06 | User tests (in SQLite) | Created/edited/deleted via UI; stored in SQLite database |
| TC-07 | Test categories | Group tests by category (e.g., "C# Basic", "C# Web", "F#") |
| TC-08 | Select/deselect | Checkbox per test + "Select All" / "Deselect All" per category |
| TC-09 | Step types supported | `command` (with timeout, expected_exit_code, assert_output_contains) and `write_file` (with path, content) |

### 3.4 Reporting & Audit

| ID | Requirement | Details |
|----|------------|---------|
| RPT-01 | Command logging | Every command executed, its stdout/stderr, exit code, and duration logged to SQLite |
| RPT-02 | Export as Markdown | "Export to Markdown" button generates a `.md` file with full run details |
| RPT-03 | History page | Lists all past runs with: date/time, status (completed/cancelled/failed), pass/fail/skip counts |
| RPT-04 | Run detail view | Click a history entry to see per-test-case results and step-level logs |
| RPT-05 | Real-time streaming | During execution, live-stream command output to the Terminal View via SSE |

### 3.5 .NET SDK Version Management

| ID | Requirement | Details |
|----|------------|---------|
| SDK-01 | Detect installed SDKs | Scan default install paths (`C:\Program Files\dotnet\sdk\*`) and list available versions |
| SDK-02 | Select active SDK | User picks an SDK version from dropdown before running tests |
| SDK-03 | Apply via global.json | Write/update `global.json` in the test working directory with selected version |
| SDK-04 | PATH manipulation | Prepend the selected SDK's dotnet path to the ConPTY process `PATH` environment variable |
| SDK-05 | Display active SDK | Show currently selected SDK version in the header/status bar |

---

## 4. Non-Functional Requirements

| ID | Requirement | Details |
|----|------------|---------|
| NFR-01 | No Python required on target | Bundled .exe includes Python runtime and all dependencies |
| NFR-02 | Single user | No authentication, no multi-user, no network access required |
| NFR-03 | Portable data | SQLite DB stored next to .exe; copy folder = copy tool + all history |
| NFR-04 | Windows 10/11 x64 | Target OS; uses Win32 APIs (ConPTY, SendMessage) not available on other platforms |
| NFR-05 | Responsive UI | UI remains interactive during test execution (backend runs in separate thread) |
| NFR-06 | Minimal resource usage | Single ConPTY process at a time; no parallel test execution |

---

## 5. Known Limitations & Constraints

| # | Limitation | Reason |
|---|-----------|--------|
| 1 | Sequential test execution only | ConPTY + visible window approach uses one terminal at a time; parallel runs would conflict |
| 2 | Windows-only | Win32 ConPTY and SendMessage APIs are Windows-specific |
| 3 | One SDK version per run | User selects SDK before running; no automatic multi-SDK matrix |

> **Note (Resolved):** The original "cannot move mouse" limitation has been eliminated.  
> - **CMD**: ConPTY pipe write sends input directly to the pseudo-console input stream — no foreground/focus required, mouse is free.  
> - **Notepad**: SendMessage posts keystrokes to the Notepad window handle — no foreground/focus required, mouse is free.  
> - The visible terminal window and Notepad still display typed characters for visual proof, but neither requires user focus.

---

## 6. Automation Engine — Technical Design

### 6.1 ConPTY Approach (Confirmed)

The application uses **Windows Pseudo Console (ConPTY)** to achieve both visual typing proof and programmatic output capture in a single execution. **Mouse remains free** — no foreground/focus required.

```
┌──────────────────┐     ConPTY pipe write (keystrokes)
│  pywebview App   │ ──────────────────────────────────┐
│  (orchestrator)  │                                    ▼
│                  │                         ┌──────────────────┐
│  Read output ◄───┼──── ConPTY pipe read ◄──│  cmd.exe window  │
│  (for assertion  │                         │  (visible to     │
│   + stream to UI)│                         │   tester)        │
└──────────────────┘                         └──────────────────┘
         │
         ▼
┌──────────────────┐
│  Terminal View   │  Real-time output display in app UI (via SSE)
│  (in pywebview)  │
└──────────────────┘
```

**Flow:**
1. Create ConPTY pseudo-console attached to a visible `cmd.exe` window
2. For each command step:
   a. Write command bytes to ConPTY **input pipe** (characters appear in terminal — visual typing, no focus needed)
   b. Read output from ConPTY **output pipe** (programmatic capture)
   c. Stream captured output to frontend Terminal View via SSE
   d. Assert on captured output (exit code, contains patterns)
3. On test failure: log failure, close current ConPTY, open new one for next test case
4. On cancel: terminate ConPTY process, mark remaining tests as skipped
5. **Mouse is free** throughout — ConPTY pipe I/O does not require window focus

### 6.2 Notepad Integration

For `write_file` steps requiring visual proof:

1. Open Notepad (`notepad.exe`) via `subprocess.Popen`
2. Find the Notepad edit control window handle via `FindWindow` / `EnumChildWindows`
3. Type file content via `SendMessage(WM_CHAR, ...)` character by character (visual proof)
4. Save via `SendMessage` (Ctrl+S) or Win32 menu commands
5. Programmatically read the saved file to assert content matches expected

### 6.3 SDK Version Switching

1. Enumerate installed SDKs: scan `C:\Program Files\dotnet\sdk\` for version folders
2. On SDK selection:
   - Write `global.json` with `{"sdk": {"version": "<selected>"}}` to test working directory
   - Set ConPTY process environment `PATH` with dotnet directory prepended
3. Verify by running `dotnet --version` as first step

---

## 7. UI/UX Specifications

### 7.1 Theme

- **Catppuccin Mocha** color palette applied globally
  - Base: `#1e1e2e` (background)
  - Surface: `#313244` (cards, panels)
  - Text: `#cdd6f4` (primary text)
  - Green: `#a6e3a1` (pass/success)
  - Red: `#f38ba8` (fail/error)
  - Peach: `#fab387` (warnings)
  - Blue: `#89b4fa` (primary buttons, links)
  - Lavender: `#b4befe` (accents)

### 7.2 Views (Matching Current Prototype)

1. **Tests View** — List all test cases grouped by category; checkboxes for selection; Edit button per test
2. **Terminal View** — Real-time execution output; progress indicators per test case; pass/fail/skip counters; Stop button; Export to Markdown button
3. **History View** — Table of all past runs (ID, date/time, status, summary)
4. **Add Test View** — Visual step builder form (no raw JSON); fields for Category, Title, Description, Machine-Mutating flag, and step list

### 7.3 Visual Step Builder (New)

Replaces the raw JSON editor from the prototype:

- **Step list** — ordered list of steps with drag-to-reorder
- **Add Step button** — appends a new step
- **Per-step form fields:**
  - Step type dropdown: `command` | `write_file`
  - For `command`: Command text, Timeout (seconds), Expected exit code, Assert output contains (list)
  - For `write_file`: File path, Content (multi-line text area)
- **Remove Step** button per step
- **Preview** — shows the generated JSON (read-only) for advanced users

---

## 8. Data Model

### 8.1 SQLite Schema (Retained from Prototype)

```sql
-- Test case definitions (user-created)
CREATE TABLE test_cases (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    steps TEXT NOT NULL,          -- JSON array of step objects
    is_builtin INTEGER DEFAULT 0,
    is_machine_mutating INTEGER DEFAULT 0,
    sort_order INTEGER DEFAULT 999,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Test run metadata
CREATE TABLE test_runs (
    id TEXT PRIMARY KEY,
    started_at TEXT,
    finished_at TEXT,
    status TEXT DEFAULT 'pending',  -- pending | running | completed | cancelled
    environment_info TEXT,           -- dotnet --info output
    sdk_version TEXT,                -- selected SDK version for this run
    summary TEXT                     -- JSON: {passed, failed, skipped}
);

-- Per-test-case results within a run
CREATE TABLE test_results (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    test_case_id TEXT NOT NULL,
    status TEXT DEFAULT 'pending',  -- pending | running | passed | failed | cancelled
    started_at TEXT,
    finished_at TEXT,
    log_output TEXT,
    FOREIGN KEY (run_id) REFERENCES test_runs(id),
    FOREIGN KEY (test_case_id) REFERENCES test_cases(id)
);

-- Per-step results within a test case execution
CREATE TABLE step_results (
    id TEXT PRIMARY KEY,
    test_result_id TEXT NOT NULL,
    step_index INTEGER NOT NULL,
    step_type TEXT,                 -- command | write_file | cd
    command TEXT,
    exit_code INTEGER,
    stdout TEXT,
    stderr TEXT,
    status TEXT DEFAULT 'pending', -- pending | passed | failed
    duration_ms INTEGER,
    FOREIGN KEY (test_result_id) REFERENCES test_results(id)
);
```

### 8.2 YAML Test Definition Format (Retained)

```yaml
tests:
  - id: "check-dotnet-info"
    category: "C# Basic"
    title: "Check dotnet info"
    description: "Verify dotnet SDK is installed and show version info"
    machine_mutating: false
    sort_order: 1
    steps:
      - type: command
        command: "dotnet --info"
        timeout: 30
        expected_exit_code: 0
        assert_output_contains:
          - ".NET SDK"
```

---

## 9. Dependencies

### 9.1 Python Packages

| Package | Purpose |
|---------|---------|
| `flask` | REST API backend |
| `flask-cors` | CORS support (for pywebview bridge) |
| `pywebview` | Desktop window (Chromium/Edge WebView2 wrapper) |
| `pywin32` | Win32 API access (SendMessage, ConPTY, window handles) |
| `pyyaml` | Parse YAML test definition files |
| `pyinstaller` | Build standalone .exe |

### 9.2 System Requirements (Target Machine)

- Windows 10 version 1809+ (ConPTY support) or Windows 11
- x64 architecture
- .NET SDK(s) installed (the SDKs being tested)
- Edge WebView2 Runtime (usually pre-installed on Windows 10/11)

---

## 10. Acceptance Criteria

| # | Criteria |
|---|---------|
| 1 | Application launches as standalone .exe without Python installed on target |
| 2 | User can add, edit, and delete test cases via the GUI without editing source code |
| 3 | Commands are visually typed into a CMD window in real-time (visual proof) |
| 4 | File content is visually typed into Notepad (visual proof) + programmatically verified |
| 5 | User can select .NET SDK version from detected installed SDKs |
| 6 | User can stop/cancel a running test execution; remaining tests marked as skipped |
| 7 | On step failure: test case marked FAIL, execution continues to next test case |
| 8 | All commands, outputs, and results logged to SQLite |
| 9 | "Export to Markdown" produces a complete report of a test run |
| 10 | History page shows all past runs with date/time and pass/fail counts |
| 11 | UI uses Catppuccin Mocha color theme |
| 12 | Mouse remains free during automation — no foreground/focus requirement |

---

## 11. Repository Migration Plan

### 11.1 Strategy

**Full revamp** — delete all old prototype code from the repository; new code follows the folder structure defined in Section 2.3. Git history preserves the old code if needed.

### 11.2 What Gets Deleted

| Path | Description |
|------|-------------|
| `backend/` | Old Flask app (app.py, executor.py, requirements.txt, static/, test_definitions/) |
| `frontend/` | Old React frontend (will be ported, not kept in place) |
| `build.bat`, `build.sh`, `build.spec` | Old build scripts (new PyInstaller spec will be recreated) |
| `run.bat`, `run-prod.bat` | Old dev/run scripts |
| `pyproject.toml` | Old project config (new one will be created) |

### 11.3 What Gets Ported (Logic Carried Over to New Structure)

| Old Location | New Location | What's Ported |
|-------------|-------------|---------------|
| `backend/app.py` | `src/backend/server.py` | Flask routes, REST API design, SSE streaming, SPA serving |
| `backend/executor.py` | `src/engine/executor.py` | Test orchestration logic, cancel flags, event queuing, step execution flow |
| `backend/app.py` (DB init) | `src/backend/database.py` | SQLite schema, WAL mode config, DB path resolution |
| `backend/app.py` (YAML loader) | `src/backend/models.py` | YAML parsing, built-in test loading logic |
| `frontend/src/` | `frontend/src/` | React components ported with Catppuccin Mocha reskin + visual step builder additions |

### 11.4 What Gets Rewritten / Reorganized

| Component | Reason |
|-----------|--------|
| YAML test definitions | Reorganize and rewrite to reflect current .NET SDK test scenarios |
| Automation engine | Replace `subprocess.Popen` with ConPTY + Win32 SendMessage |
| Frontend theme | Reskin to Catppuccin Mocha |
| Test case editor | Replace raw JSON textarea with visual step builder |
| Build config | New `build.spec` for pywebview-based app, new `pyproject.toml` |

### 11.5 Migration Steps

1. **Create `legacy` tag** — `git tag legacy/web-prototype` on current HEAD (preserve reference)
2. **Delete old files** — Remove `backend/`, `frontend/`, `build.*`, `run*.bat`, `pyproject.toml`
3. **Scaffold new structure** — Create `src/`, `src/backend/`, `src/engine/`, `frontend/` per Section 2.3
4. **Port backend logic** — Move Flask routes → `src/backend/server.py`, executor → `src/engine/executor.py`
5. **Port frontend** — Copy React app to new `frontend/`, apply Catppuccin Mocha theme, add step builder
6. **Rewrite automation** — Implement ConPTY + SendMessage engine in `src/engine/`
7. **Rewrite test definitions** — Create new YAML files in `src/test_definitions/`
8. **New build config** — Create `pyproject.toml`, `build.spec`, dev scripts
9. **Verify** — Build .exe, run smoke test

---

## 12. Out of Scope (V1)

- Multi-SDK matrix runs (run same tests across multiple SDK versions in one batch)
- Multi-user / networked access
- Cross-platform support (macOS, Linux)
- Parallel test execution
- CI/CD integration
- Screen recording
- Auto-update mechanism

---

## 13. Glossary

| Term | Definition |
|------|-----------|
| ConPTY | Windows Pseudo Console — API for creating pseudo-terminals with both visible output and programmatic I/O |
| SendMessage | Win32 API to send window messages (used here for keystroke injection) |
| global.json | .NET SDK configuration file that pins a specific SDK version for a directory |
| pywebview | Python library that creates a native OS window containing a web view (Edge WebView2 on Windows) |
| Catppuccin Mocha | A community-driven dark color theme with warm pastel colors |
| SSE | Server-Sent Events — HTTP-based real-time streaming from server to client |
