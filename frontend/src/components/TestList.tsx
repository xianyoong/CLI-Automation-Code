import { TestCase } from '../api'

interface Props {
  categories: Record<string, TestCase[]>;
  selectedIds: Set<string>;
  onToggle: (id: string) => void;
  onSelectCategory: (category: string) => void;
  onEdit: (test: TestCase) => void;
  onDelete: (test: TestCase) => void;
}

export default function TestList({ categories, selectedIds, onToggle, onSelectCategory, onEdit, onDelete }: Props) {
  return (
    <div className="test-list">
      {Object.entries(categories).map(([category, tests]) => {
        const allSelected = tests.every(t => selectedIds.has(t.id));
        return (
          <div key={category} className="category">
            <div className="category-header">
              <h3>{category}</h3>
              <button className="small-btn" onClick={() => onSelectCategory(category)}>
                {allSelected ? 'Deselect All' : 'Select All'}
              </button>
            </div>
            {tests.map(test => (
              <div key={test.id} className={`test-item ${selectedIds.has(test.id) ? 'selected' : ''}`}>
                <label>
                  <input
                    type="checkbox"
                    checked={selectedIds.has(test.id)}
                    onChange={() => onToggle(test.id)}
                  />
                  <span className="test-title">{test.title}</span>
                  {test.is_machine_mutating && <span className="badge badge-warning">⚠️ Machine</span>}
                </label>
                <div className="test-actions">
                  <button className="small-btn" onClick={() => onEdit(test)}>Edit</button>
                  {!test.is_builtin && <button className="small-btn btn-danger" onClick={(e) => { e.stopPropagation(); onDelete(test); }}>Delete</button>}
                </div>
                {test.description && <p className="test-desc">{test.description}</p>}
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}
