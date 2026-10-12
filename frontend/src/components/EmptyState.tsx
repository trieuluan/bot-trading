import { Radio } from 'lucide-react';
export function EmptyState({ title, description }: { title: string; description: string }) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <Radio size={22} />
      </div>
      <h3>{title}</h3>
      <p>{description}</p>
    </div>
  );
}
