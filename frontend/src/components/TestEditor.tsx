import { useState } from 'react'
import { TestCase, Step, createTest, updateTest } from '../api'

interface Props {
  test: TestCase | null;
  onSave: () => void;
  onCancel: () => void;
}

export default function TestEditor({ test, onSave, onCancel }: Props) {
  const [category, setCategory] = useState(test?.category || '');
  const [title, setTitle] = useState(test?.title || '');
  const [description, setDescription] = useState(test?.description || '');
  const [machineMutating, setMachineMutating] = useState(test?.is_machine_mutating || false);
  const [stepsText, setStepsText] = useState(
    test ? JSON.stringify(test.steps, null, 2) : JSON.stringify([{ type: 'command', command: 'dotnet --info' }], null, 2)
  );
  const [error, setError] = useState('');

  const toKebabCase = (s: string) =>
    s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');

  const handleSave = async () => {
    if (!category.trim()) {
      setError('Category is required');
      return;
    }
    if (!title.trim()) {
      setError('Title is required');
      return;
    }

    let steps: Step[];
    try {
      steps = JSON.parse(stepsText);
    } catch {
      setError('Invalid JSON in steps');
      return;
    }

    const id = test ? test.id : toKebabCase(title);
    const payload = { id, category: category.trim(), title: title.trim(), description, steps, is_machine_mutating: machineMutating };

    try {
      if (test) {
        await updateTest(test.id, payload);
      } else {
        await createTest(payload);
      }
      onSave();
    } catch (e) {
      setError(`Failed to save: ${e}`);
    }
  };

  return (
    <div className="editor">
      <h2>{test ? 'Edit Test Case' : 'Add Test Case'}</h2>
      {error && <div className="error">{error}</div>}

      <div className="form-group">
        <label>Category <span className="required">*</span></label>
        <input value={category} onChange={e => setCategory(e.target.value)} placeholder="C# Console" required />
      </div>

      <div className="form-group">
        <label>Title <span className="required">*</span></label>
        <input value={title} onChange={e => setTitle(e.target.value)} placeholder="Test case title" required />
      </div>

      <div className="form-group">
        <label>Description</label>
        <textarea value={description} onChange={e => setDescription(e.target.value)} rows={2} />
      </div>

      <div className="form-group">
        <label>
          <input type="checkbox" checked={machineMutating} onChange={e => setMachineMutating(e.target.checked)} />
          Machine-mutating (modifies global state like workloads)
        </label>
      </div>

      <div className="form-group">
        <label>Steps (JSON)</label>
        <textarea className="code-textarea" value={stepsText} onChange={e => setStepsText(e.target.value)} rows={20} />
        <p className="help-text">
          Step types: <code>command</code> (with command, timeout, expected_exit_code, assert_output_contains),
          <code>write_file</code> (with path, content)
        </p>
      </div>

      <div className="form-actions">
        <button onClick={handleSave}>💾 Save</button>
        <button onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}
