import { Activity } from 'lucide-react';
import type { Snapshot } from '../types/trading';
import { EmptyState } from '../components/EmptyState';

export function ActivityPage({ snapshot }: { snapshot: Snapshot }) {
  return (
    <section className="panel prose">
      <div className="section-head">
        <h2>Hoạt động hệ thống</h2>
        <span className="muted">12 sự kiện gần nhất</span>
      </div>
      {snapshot.events.length ? (
        snapshot.events.map((event) => (
          <div className="event" key={event.id}>
            <Activity size={17} />
            <div>
              <strong>{event.message}</strong>
              <p>
                {new Date(
                  event.time.endsWith('Z') || event.time.includes('+')
                    ? event.time
                    : event.time + 'Z',
                ).toLocaleString('vi-VN')}
              </p>
            </div>
            <span className="pill">{event.kind}</span>
          </div>
        ))
      ) : (
        <EmptyState
          title="Chưa có sự kiện"
          description="Bật phân tích mô phỏng khi API và worker đã kết nối để bắt đầu ghi nhật ký."
        />
      )}
    </section>
  );
}
