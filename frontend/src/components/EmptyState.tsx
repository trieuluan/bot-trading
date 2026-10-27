import { Radio } from 'lucide-react';
export function EmptyState({ title, description }: { title: string; description: string }) {
  return (
    <div className="empty grid min-h-24 place-content-center gap-2 p-4 text-center text-xs text-text-muted">
      <div className="empty-icon inline-flex size-9 shrink-0 items-center justify-center rounded-md bg-surface-3 text-bull">
        <Radio size={22} />
      </div>
      <h3>{title}</h3>
      <p>{description}</p>
    </div>
  );
}
