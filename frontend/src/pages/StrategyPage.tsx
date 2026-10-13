import { useEffect, useState } from 'react';
import { Eye, FlaskConical } from 'lucide-react';
import { fetchMtfObservation } from '../lib/api';
import { MtfObservationChart } from '../components/MtfObservationChart';
import { formatPrice, observationStatusLabels as statusLabels } from '../lib/format';
import type { MtfObservationResponse } from '../types/trading';

const provenanceLabels = { live: 'Trực tiếp', catch_up: 'Chạy bù', historical: 'Lịch sử' };

export function StrategyPage() {
  const [obs, setObs] = useState<MtfObservationResponse | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    async function poll() {
      try {
        const res = await fetchMtfObservation();
        if (active) {
          setObs(res);
          setError('');
        }
      } catch {
        if (active) setError('Chưa tải được trạng thái quan sát MTF.');
      }
    }
    void poll();
    const interval = setInterval(poll, 15000);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, []);

  const snap = obs?.available ? obs.snapshot : null;
  return (
    <>
      <section className="panel prose mtf-observation">
        <div className="section-head">
          <div>
            <h2>Quan sát Price Action Đa Khung</h2>
            <p>4h EMA50 xu hướng → 1h breakout vùng 20 nến → 15m retest và xác nhận giữ vùng.</p>
          </div>
          <span className="pill amber">
            <Eye size={12} style={{ marginRight: 4, verticalAlign: -1 }} />
            Chỉ quan sát · không gửi lệnh
          </span>
        </div>
        <p className="micro">
          Trực tiếp và chạy bù có nhãn riêng; tín hiệu cũ chỉ dùng nghiên cứu, không mở lệnh. Auto
          Paper vẫn giữ breakout 15m cũ.
          {obs && !obs.available && ' · Chọn nguồn MEXC Futures tại Kết nối sàn để quan sát.'}
          {obs?.backlog_bars && obs.backlog_bars > 0 ? (
            <span className="negative"> · Đang xử lý bù {obs.backlog_bars} nến sau gián đoạn.</span>
          ) : null}
          {obs?.available &&
            obs.stale &&
            ' · Dữ liệu chậm/chưa đồng bộ: đây là trạng thái lưu gần nhất, không phải tín hiệu mới.'}
          {obs?.error && ` · ${obs.error}`}
          {error && ` · ${error}`}
        </p>
        <div className="research-metrics">
          <div>
            <span className="muted">Xu hướng 4h</span>
            <strong>
              {snap?.trend ? (snap.trend === 'up' ? 'TĂNG (chỉ Long)' : 'GIẢM (chỉ Short)') : '—'}
            </strong>
          </div>
          <div>
            <span className="muted">Trạng thái hiện tại</span>
            <strong>
              {snap ? (statusLabels[snap.status] ?? snap.status) : 'Đang chờ dữ liệu'}
            </strong>
          </div>
          <div>
            <span className="muted">Vùng breakout 1h</span>
            <strong>{snap?.level ? `${formatPrice(snap.level)} USDT` : '—'}</strong>
          </div>
          <div>
            <span className="muted">SL / TP tham chiếu</span>
            <strong>
              {snap?.stop && snap?.target
                ? `${formatPrice(snap.stop)} / ${formatPrice(snap.target)}`
                : 'Chưa có'}
            </strong>
          </div>
        </div>
        <div style={{ marginTop: 12 }}>
          <p>
            <strong>Lý do hiện tại:</strong> {snap?.reason ?? 'Đang tải...'}
            {snap && <span className="pill">{provenanceLabels[snap.provenance ?? 'live']}</span>}
          </p>
        </div>
        {obs?.available && <MtfObservationChart candles={obs.candles} snapshot={snap ?? null} />}
        {obs?.events && obs.events.length > 0 && (
          <>
            <h3>Nhật ký quan sát gần nhất</h3>
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Thời gian nến (UTC)</th>
                    <th>Trạng thái</th>
                    <th>Nguồn quan sát</th>
                    <th>Chiều</th>
                    <th>Mức giá</th>
                    <th>Lý do</th>
                  </tr>
                </thead>
                <tbody>
                  {obs.events.slice(0, 10).map((ev) => (
                    <tr key={ev.candle_time}>
                      <td>
                        {new Date(ev.candle_time * 1000)
                          .toISOString()
                          .slice(0, 16)
                          .replace('T', ' ')}
                      </td>
                      <td>{statusLabels[ev.snapshot.status] ?? ev.snapshot.status}</td>
                      <td>{provenanceLabels[ev.snapshot.provenance ?? 'live']}</td>
                      <td>{ev.snapshot.side?.toUpperCase() ?? '—'}</td>
                      <td>{ev.snapshot.level ? formatPrice(ev.snapshot.level) : '—'}</td>
                      <td>{ev.snapshot.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>

      <section className="panel prose">
        <div className="section-head">
          <h2>Structure Breakout (chiến lược cơ sở)</h2>
          <span className="pill amber">Chiến lược cơ sở · chưa xác nhận lợi nhuận</span>
        </div>
        <p>Chiến lược hiện tại đang dùng cho tín hiệu và Auto Paper.</p>
        <div className="rules-grid">
          {[
            [
              '01',
              'Điều kiện vào',
              'LONG khi close vượt đỉnh 20 nến; SHORT khi phá đáy 20 nến (Futures).',
            ],
            ['02', 'Stop-loss tham chiếu', 'LONG: đáy 5 nến trước. SHORT: đỉnh 5 nến trước.'],
            [
              '03',
              'Mục tiêu tham chiếu',
              'Cách giá tín hiệu 2R theo chiều lệnh. Backtest vào ở nến kế tiếp.',
            ],
          ].map(([step, title, description]) => (
            <div className="rule-card" key={step}>
              <span>{step}</span>
              <h3>{title}</h3>
              <p>{description}</p>
            </div>
          ))}
        </div>
        <div className="rule">
          <FlaskConical size={20} />
          Mở trang Backtest để đánh giá trên dữ liệu MEXC Futures với phí, trượt giá và funding. Kết
          quả lịch sử chưa xác nhận hiệu quả tương lai.
        </div>
      </section>
    </>
  );
}
