import { useState, useEffect, useCallback, useRef } from 'react'
import { TestCase, StreamEvent, fetchTests, startExecution, cancelExecution, streamExecution, fetchRuns, fetchEnvironment, deleteTest, TestRun } from './api'
import TestList from './components/TestList'
import TestRunner from './components/TestRunner'
import LogViewer from './components/LogViewer'
import { LogViewerHandle } from './components/LogViewer'
import ResultsSummary from './components/ResultsSummary'
import TestEditor from './components/TestEditor'

type View = 'tests' | 'running' | 'history' | 'editor';

export default function App() {
  const [view, setView] = useState<View>('tests');
  const [tests, setTests] = useState<TestCase[]>([]);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [runId, setRunId] = useState<string | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [runStatus, setRunStatus] = useState<string>('idle');
  const [summary, setSummary] = useState<{ passed: number; failed: number; skipped: number } | null>(null);
  const [testStatuses, setTestStatuses] = useState<Record<string, string>>({});
  const [runs, setRuns] = useState<TestRun[]>([]);
  const [editingTest, setEditingTest] = useState<TestCase | null>(null);
  const [sdkInfo, setSdkInfo] = useState<string | null>(null);
  const logViewerRef = useRef<LogViewerHandle>(null);

  useEffect(() => {
    fetchTests().then(setTests);
    fetchEnvironment().then(env => {
      setSdkInfo(env.exit_code === 0 ? env.output : null);
    });
  }, []);

  const handleSelectAll = useCallback((category?: string) => {
    if (category) {
      // Toggle all in this category only
      const categoryIds = tests.filter(t => t.category === category).map(t => t.id);
      setSelectedIds(prev => {
        const allSelected = categoryIds.every(id => prev.has(id));
        const next = new Set(prev);
        if (allSelected) {
          categoryIds.forEach(id => next.delete(id));
        } else {
          categoryIds.forEach(id => next.add(id));
        }
        return next;
      });
    } else {
      // Select all tests
      setSelectedIds(new Set(tests.map(t => t.id)));
    }
  }, [tests]);

  const handleDeselectAll = useCallback(() => {
    setSelectedIds(new Set());
  }, []);

  const handleToggle = useCallback((id: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const handleRun = useCallback(async () => {
    if (selectedIds.size === 0) return;
    setLogs([]);
    setSummary(null);
    setTestStatuses({});
    setRunStatus('running');
    setView('running');

    const { run_id } = await startExecution(Array.from(selectedIds));
    setRunId(run_id);

    streamExecution(run_id, (event: StreamEvent) => {
      switch (event.type) {
        case 'run_start':
          setLogs(prev => [...prev, `▶ Run started (${event.total_tests} tests)`]);
          break;
        case 'test_start':
          setLogs(prev => [...prev, `\n━━━ ${event.title} ━━━`]);
          setTestStatuses(prev => ({ ...prev, [event.test_case_id as string]: 'running' }));
          break;
        case 'step_output':
          setLogs(prev => [...prev, event.line as string]);
          break;
        case 'step_end':
          // Only show test-level pass/fail, not per-step status
          break;
        case 'test_end':
          setTestStatuses(prev => ({ ...prev, [event.test_case_id as string]: event.status as string }));
          setLogs(prev => [...prev, `  ${event.status === 'passed' ? '✅ PASSED' : '❌ FAILED'}`]);
          break;
        case 'run_end':
          setRunStatus('completed');
          setSummary(event.summary as { passed: number; failed: number; skipped: number });
          setLogs(prev => [...prev, `\n✅ Run complete: ${JSON.stringify(event.summary)}`]);
          break;
        case 'heartbeat':
          break;
      }
    });
  }, [selectedIds]);

  const handleCancel = useCallback(async () => {
    if (runId) {
      await cancelExecution(runId);
      setRunStatus('cancelled');
    }
  }, [runId]);

  const handleViewHistory = useCallback(async () => {
    const data = await fetchRuns();
    setRuns(data);
    setView('history');
  }, []);

  const handleRefreshTests = useCallback(async () => {
    const data = await fetchTests();
    setTests(data);
  }, []);

  const handleDelete = useCallback(async (test: TestCase) => {
    if (!window.confirm(`Delete test "${test.title}"?`)) return;
    try {
      await deleteTest(test.id);
      setSelectedIds(prev => { const next = new Set(prev); next.delete(test.id); return next; });
      setTests(prev => prev.filter(t => t.id !== test.id));
    } catch (e) {
      console.error('Delete failed:', e);
    }
  }, []);

  // Group tests by category
  const categories = tests.reduce<Record<string, TestCase[]>>((acc, t) => {
    if (!acc[t.category]) acc[t.category] = [];
    acc[t.category].push(t);
    return acc;
  }, {});

  return (
    <div className="app">
      <header className="header">
        <div className="header-top">
          <h1>🧪 .NET SDK Test Runner</h1>
          <nav>
            <button className={view === 'tests' ? 'active' : ''} onClick={() => setView('tests')}>Tests</button>
            <button className={view === 'running' ? 'active' : ''} onClick={() => setView('running')}>Runner</button>
            <button onClick={handleViewHistory}>History</button>
            <button onClick={() => { setEditingTest(null); setView('editor'); }}>+ Add Test</button>
          </nav>
        </div>
        {sdkInfo !== null ? (
          <div className="sdk-banner sdk-found">
            <span className="sdk-icon">✓</span>
            <span className="sdk-text">
              {(() => {
                const versionMatch = sdkInfo.match(/Version:\s+(\S+)/);
                const osMatch = sdkInfo.match(/OS Name:\s+(.+)/);
                const ridMatch = sdkInfo.match(/RID:\s+(\S+)/);
                return `SDK ${versionMatch?.[1] || 'unknown'} | ${osMatch?.[1]?.trim() || 'unknown OS'} | ${ridMatch?.[1] || ''}`;
              })()}
            </span>
          </div>
        ) : (
          <div className="sdk-banner sdk-missing">
            <span className="sdk-icon">⚠</span>
            <span className="sdk-text">No .NET SDK detected — install one and restart the app</span>
          </div>
        )}
      </header>

      <main className="main">
        {view === 'tests' && (
          <div className="tests-view">
            <div className="toolbar">
              <button onClick={() => handleSelectAll()}>Select All</button>
              <button onClick={handleDeselectAll}>Deselect All</button>
              <button className="run-btn" onClick={handleRun} disabled={selectedIds.size === 0}>
                ▶ Run Selected ({selectedIds.size})
              </button>
            </div>
            <TestList
              categories={categories}
              selectedIds={selectedIds}
              onToggle={handleToggle}
              onSelectCategory={handleSelectAll}
              onEdit={(t) => { setEditingTest(t); setView('editor'); }}
              onDelete={handleDelete}
            />
          </div>
        )}

        {view === 'running' && (
          <TestRunner
            status={runStatus}
            testStatuses={testStatuses}
            tests={tests.filter(t => selectedIds.has(t.id))}
            onCancel={handleCancel}
            onTestClick={(title) => logViewerRef.current?.scrollToTest(title)}
          >
            <LogViewer ref={logViewerRef} logs={logs} />
            {summary && <ResultsSummary summary={summary} logs={logs} testStatuses={testStatuses} tests={tests.filter(t => selectedIds.has(t.id))} />}
          </TestRunner>
        )}

        {view === 'history' && (
          <div className="history-view">
            <h2>Run History</h2>
            <table>
              <thead>
                <tr><th>ID</th><th>Started</th><th>Status</th><th>Summary</th></tr>
              </thead>
              <tbody>
                {runs.map(run => (
                  <tr key={run.id}>
                    <td>{run.id}</td>
                    <td>{run.started_at}</td>
                    <td><span className={`badge badge-${run.status}`}>{run.status}</span></td>
                    <td>{run.summary || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {view === 'editor' && (
          <TestEditor
            test={editingTest}
            onSave={() => { handleRefreshTests(); setView('tests'); }}
            onCancel={() => setView('tests')}
          />
        )}
      </main>
    </div>
  );
}
