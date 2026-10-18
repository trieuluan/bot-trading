import { futuresHeaders } from '../lib/futures-symbol';
import { useEffect, useState } from 'react';
import { Activity, Brain, Dna, Power } from 'lucide-react';
import {
  exitLabel,
  HTF_LABELS,
  profileOf,
  riskText,
  strategyInfo,
  strategyName,
  strategyShortTitle,
  searchText,
  type Rule,
} from '../lib/futures-strategy-info';

type Row = {
  strategy: string;
  genome: {
    family: string;
    lookback: number;
    target_r: number;
    stop_buffer: number;
    exit_policy?: 'fixed' | 'breakeven' | 'trail' | 'structure' | 'adaptive';
    max_er?: number;
    funding_threshold?: number;
    rule?: Rule;
    exit_profile?: string;
  };
  parents: string[];
  born_at: number;
  learned_samples: number;
  learned_mean_r: number | null;
  origin: string;
  generation: number;
  promoted: boolean;
  paused?: boolean;
  shadow_only?: boolean;
  rule_text?: string | null;
  complexity?: number;
  signals: number;
  open_trades?: { id: number; side: 'long' | 'short'; entry: number; quantity: number }[];
  resolved: number;
  mean_r: number | null;
  profit_factor: number | null;
  qualification: { eligible: boolean; windows: number; trades: number };
};
type Learning = {
  enabled: boolean;
  auto_mode: 'learner' | 'paper_learning';
  stale: boolean;
  error: string | null;
  population: number;
  samples: number;
  promoted: number;
  last_decision: {
    regime: string;
    mode: string;
    selected: string | null;
    candidates: number;
  } | null;
  strategies: Row[];
};

async function call(path: string, body?: object): Promise<Learning> {
  const response = await fetch(`/api/workspaces/mexc_futures/learning${path}`, {
    method: body ? 'POST' : 'GET',
    headers: { ...futuresHeaders(), ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(10000),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error(typeof data.detail === 'string' ? data.detail : 'Yêu cầu thất bại');
  return data;
}

const fmt = (value: number | null, digits = 2) => (value === null ? '—' : value.toFixed(digits));

export function FuturesLearningPage() {
  const [data, setData] = useState<Learning | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState<'all' | 'seed' | 'child' | 'good'>('all');
  const [family, setFamily] = useState('all');
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('all');
  const [gen, setGen] = useState('all');
  const [sort, setSort] = useState('score');

  useEffect(() => {
    let active = true;
    const load = () =>
      call('')
        .then((result) => active && (setData(result), setError('')))
        .catch(
          (exc: Error) => active && setError(`Chưa tải được trạng thái bot học: ${exc.message}`),
        );
    void load();
    const timer = setInterval(load, 15000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);

  const act = (path: string, body: object) => {
    if (busy) return;
    setBusy(true);
    return call(path, body)
      .then((result) => (setData(result), setError('')))
      .catch((exc: Error) => setError(exc.message))
      .finally(() => setBusy(false));
  };

  const all = data?.strategies ?? [];
  const families = [...new Set(all.map((row) => profileOf(row.genome)))].sort();
  const generations = [...new Set(all.map((row) => row.generation))].sort((a, b) => a - b);
  const needles = searchText(query).trim().split(/\s+/).filter(Boolean);
  const selected = data?.last_decision?.selected;
  const titles = new Map(all.map((row) => [row.strategy, strategyName(row.genome)]));
  const familyName = (name: string) =>
    name === 'default' ? 'Cây luật tự sinh' : (HTF_LABELS[name] ?? name.replace(/_/g, ' '));
  const rows = all.filter(
    (row) =>
      (family === 'all' || profileOf(row.genome) === family) &&
      (status === 'all' ? !row.paused || (row.open_trades?.length ?? 0) > 0 : true) &&
      (status === 'all' ||
        status === 'all_with_paused' ||
        (status === 'holding' && (row.open_trades?.length ?? 0) > 0) ||
        (status === 'active' && !row.paused) ||
        (status === 'paused' && row.paused) ||
        (status === 'promoted' && row.promoted) ||
        (status === 'selected' && row.strategy === selected)) &&
      needles.every((needle) =>
        searchText(
          [
            strategyName(row.genome),
            familyName(profileOf(row.genome)),
            row.strategy,
            row.rule_text ?? '',
            strategyInfo(row.genome).basis,
            `g${row.generation}`,
            ...row.parents,
            ...row.parents.map((parent) => titles.get(parent) ?? ''),
          ].join(' '),
        ).includes(needle),
      ) &&
      (gen === 'all' || row.generation === Number(gen)) &&
      (filter === 'all' ||
        (filter === 'seed' && row.generation === 0) ||
        (filter === 'child' && row.generation > 0) ||
        (filter === 'good' && (row.learned_mean_r ?? -1) > 0)),
  );
  const sorted = [...rows].sort((a, b) => {
    if (sort === 'name') return strategyName(a.genome).localeCompare(strategyName(b.genome), 'vi');
    if (sort === 'samples') return b.learned_samples - a.learned_samples;
    if (sort === 'generation') return b.generation - a.generation;
    return (
      (b.learned_mean_r ?? -Infinity) - (a.learned_mean_r ?? -Infinity) ||
      a.strategy.localeCompare(b.strategy)
    );
  });
  const modes = [
    ['paper_learning', 'Học thử Paper', { enabled: true, auto_mode: 'paper_learning' }],
    ['learner', 'Chỉ đã duyệt', { enabled: true, auto_mode: 'learner' }],
  ] as const;
  const filters = [
    ['all', 'Tất cả'],
    ['good', 'R học > 0'],
    ['seed', 'Gốc'],
    ['child', 'Biến thể'],
  ] as const;
  const kpis = data
    ? [
        ['Chiến thuật', data.population],
        [
          'Gốc / Biến thể',
          `${data.strategies.filter((r) => r.generation === 0).length} / ${data.strategies.filter((r) => r.generation > 0).length}`,
        ],
        ['Mẫu shadow', data.samples],
        ['Đã duyệt', data.promoted],
        ['Bối cảnh', data.last_decision?.regime ?? '—'],
      ]
    : [];

  return (
    <section className="panel evo">
      <header className="evo-head">
        <div>
          <h2>
            <Brain size={17} /> Bot học Futures · Tiến hóa
          </h2>
          <p>
            Khung HTF: <b>H4</b> lọc xu hướng EMA50 · <b>H1</b> cấu trúc điểm tựa · <b>M15</b>{' '}
            trigger đóng nến. Cây luật tự sinh biến đổi cấu trúc và điều kiện.
          </p>
        </div>
        <span className="pill amber">Paper · không gửi lệnh sàn</span>
      </header>
      {error && (
        <p className="negative" role="alert">
          {error}
        </p>
      )}
      {data && (
        <>
          <div className="evo-kpis">
            {kpis.map(([label, value]) => (
              <div key={label}>
                <span>{label}</span>
                <strong>{value}</strong>
              </div>
            ))}
          </div>
          {(data.stale || data.error) && (
            <p className="evo-warn" role="status">
              {data.stale && 'Dữ liệu học đang chậm hoặc chưa chạy. '}
              {data.error}
            </p>
          )}
          <div className="evo-controls">
            <div className="evo-seg" role="group" aria-label="Chế độ chọn chiến thuật">
              {modes.map(([key, label, body]) => (
                <button
                  key={key}
                  disabled={busy}
                  aria-pressed={data.auto_mode === key}
                  onClick={() => act('/config', body)}
                >
                  {label}
                </button>
              ))}
            </div>
            <button
              className="evo-power"
              disabled={busy}
              aria-pressed={data.enabled}
              onClick={() =>
                act('/config', {
                  enabled: !data.enabled,
                  auto_mode: data.auto_mode || 'paper_learning',
                })
              }
            >
              <Power size={13} /> {data.enabled ? 'Đang học · tắt' : 'Đã tắt · bật học'}
            </button>
          </div>
          <p className="evo-note">
            {data.auto_mode === 'paper_learning'
              ? 'Học thử Paper: bot thử cả chiến thuật chưa kiểm chứng và cập nhật điểm từ mẫu đã đóng. Không có tín hiệu thì đứng ngoài.'
              : data.auto_mode === 'learner'
                ? 'Chỉ giao dịch chiến thuật đã qua cổng forward và được duyệt thủ công.'
                : ''}{' '}
            Đổi chế độ khi Auto Paper đang dừng.
          </p>
          <div className="evo-filters" role="group" aria-label="Lọc chiến thuật">
            {filters.map(([key, label]) => (
              <button key={key} aria-pressed={filter === key} onClick={() => setFilter(key)}>
                {label}
              </button>
            ))}
          </div>
          <div className="evo-tools">
            <label>
              Họ chiến thuật
              <select
                aria-label="Lọc theo họ"
                value={family}
                onChange={(event) => setFamily(event.target.value)}
              >
                <option value="all">Tất cả họ ({all.length})</option>
                {families.map((name) => (
                  <option key={name} value={name}>
                    {familyName(name)} ({all.filter((row) => profileOf(row.genome) === name).length}
                    )
                  </option>
                ))}
              </select>
            </label>
            <label className="evo-search">
              Tìm kiếm
              <input
                type="search"
                aria-label="Tìm chiến thuật"
                placeholder="Tìm tên, mã, luật…"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
            <label>
              Thế hệ
              <select
                aria-label="Lọc theo thế hệ"
                value={gen}
                onChange={(event) => setGen(event.target.value)}
              >
                <option value="all">Tất cả thế hệ</option>
                {generations.map((g) => (
                  <option key={g} value={String(g)}>
                    G{g}
                    {g === 0 ? ' · gốc' : ''} ({all.filter((row) => row.generation === g).length})
                  </option>
                ))}
              </select>
            </label>
            <label>
              Trạng thái
              <select value={status} onChange={(event) => setStatus(event.target.value)}>
                <option value="all">Đang dùng (ẩn ngừng thử)</option>
                <option value="all_with_paused">Tất cả kể cả ngừng thử</option>
                <option value="active">Đang thử</option>
                <option value="holding">Có lệnh đang mở</option>
                <option value="selected">Được chọn gần nhất</option>
                <option value="promoted">Đã duyệt</option>
                <option value="paused">Chỉ nhóm ngừng thử</option>
              </select>
            </label>
            <label>
              Sắp xếp
              <select value={sort} onChange={(event) => setSort(event.target.value)}>
                <option value="score">R học cao nhất</option>
                <option value="name">Tên A–Z</option>
                <option value="samples">Nhiều mẫu nhất</option>
                <option value="generation">Thế hệ mới nhất</option>
              </select>
            </label>
          </div>
          <div className="evo-result" role="status" aria-live="polite">
            <span>
              Hiển thị <b>{sorted.length}</b> / {all.length} chiến thuật
            </span>
            {(family !== 'all' ||
              query ||
              status !== 'all' ||
              gen !== 'all' ||
              filter !== 'all' ||
              sort !== 'score') && (
              <button
                onClick={() => {
                  setFamily('all');
                  setQuery('');
                  setStatus('all');
                  setGen('all');
                  setFilter('all');
                  setSort('score');
                }}
              >
                Xóa bộ lọc
              </button>
            )}
          </div>
          {sorted.length === 0 && (
            <p className="evo-empty">Không có chiến thuật khớp bộ lọc. Thử đổi họ hoặc từ khóa.</p>
          )}
          <div className="evo-grid">
            {sorted.map((row) => {
              const learned = row.learned_mean_r;
              const tone = learned === null ? '' : learned > 0 ? 'good' : 'bad';
              const info = strategyInfo(row.genome);
              const openTrades = row.open_trades ?? [];
              return (
                <article
                  key={row.strategy}
                  className={`evo-card ${tone} ${row.strategy === selected ? 'selected' : ''} ${openTrades.length ? 'holding' : ''}`}
                >
                  <div className="evo-card-top">
                    <div>
                      <strong title={strategyName(row.genome)}>
                        {strategyShortTitle(row.genome, row.strategy)}
                      </strong>
                      <code title={row.strategy}>{row.strategy.split(':')[1]}</code>
                    </div>
                    <span className={`pill ${row.generation ? 'green' : ''}`}>
                      <Dna size={11} /> G{row.generation}
                    </span>
                  </div>
                  <p className="evo-basis">
                    {openTrades.length > 0 && (
                      <span
                        className="pill evo-open-trades"
                        title={openTrades
                          .map((position) => `#${position.id} ${position.side.toUpperCase()}`)
                          .join(' · ')}
                      >
                        <Activity size={12} aria-hidden="true" /> Đang có {openTrades.length} lệnh
                        Paper
                      </span>
                    )}
                    {row.strategy === selected && (
                      <span
                        className="pill green"
                        title="Được bộ chọn ưu tiên ở nến gần nhất; không xác nhận đã mở lệnh."
                      >
                        Được chọn gần nhất
                      </span>
                    )}
                    {row.paused && (
                      <span
                        className="pill"
                        title="Chiến thuật không bị loại; chính sách thoát này chưa được bật nên không mở lệnh mới. Mẫu cũ vẫn được giữ."
                      >
                        Ngừng thử · thoát {row.genome.exit_policy ?? 'fixed'} chưa bật
                      </span>
                    )}
                    <span className="pill">{row.shadow_only ? 'Shadow' : 'Thử Paper'}</span>
                    <span className="pill blue">{exitLabel(row.genome.exit_policy)}</span>
                  </p>
                  {row.rule_text && (
                    <p className="evo-rule" title={row.rule_text}>
                      {row.rule_text}
                    </p>
                  )}
                  <div className="evo-score">
                    <b className={tone}>
                      {learned === null ? '—' : `${learned > 0 ? '+' : ''}${fmt(learned)}R`}
                    </b>
                    <span>{row.learned_samples} mẫu học (replay + shadow)</span>
                  </div>
                  <dl>
                    <div>
                      <dt>Tham số</dt>
                      <dd>
                        N{row.genome.lookback} · TP {row.genome.target_r}R · SL +
                        {row.genome.stop_buffer}
                      </dd>
                    </div>
                    <div>
                      <dt>Forward</dt>
                      <dd>
                        {row.signals} tín hiệu · {row.resolved} đóng · PF {fmt(row.profit_factor)}
                      </dd>
                    </div>
                    <div>
                      <dt>Cổng duyệt</dt>
                      <dd>
                        {row.qualification.windows} cửa sổ · {row.qualification.trades}/100 lệnh
                      </dd>
                    </div>
                    {row.parents.length > 0 && (
                      <div className="evo-parents-row">
                        <dt>Cha mẹ</dt>
                        <dd className="evo-parents-chips">
                          {row.parents.map((parent, idx) => {
                            const pRow = all.find((r) => r.strategy === parent);
                            const pTitle = titles.get(parent) ?? parent;
                            const pLabel = pRow
                              ? strategyShortTitle(pRow.genome, parent)
                              : `#${parent.split(':')[1]?.slice(0, 6)}`;
                            return (
                              <span key={parent}>
                                {idx > 0 && ' × '}
                                <span className="evo-chip-wrap">
                                  <code
                                    className="evo-parent-chip"
                                    tabIndex={0}
                                    aria-label={pTitle}
                                  >
                                    {pLabel}
                                  </code>
                                  <span className="evo-tooltip" role="tooltip">
                                    <span className="evo-tooltip-title">{pLabel}</span>
                                    <span className="evo-tooltip-body">{pTitle}</span>
                                    <span className="evo-tooltip-code">{parent}</span>
                                  </span>
                                </span>
                              </span>
                            );
                          })}
                        </dd>
                      </div>
                    )}
                  </dl>
                  <details className="evo-more">
                    <summary>Cách đánh</summary>
                    <p>
                      <b>Vào lệnh:</b> {row.rule_text ?? info.entry}
                    </p>
                    <p>
                      <b>Thoát:</b> {riskText(row.genome, info.stop)}
                    </p>
                    <p>
                      <b>Nguồn:</b>{' '}
                      {row.generation === 0
                        ? 'Chiến thuật gốc, tham số mặc định.'
                        : `Biến thể G${row.generation} do bot học sinh từ ${row.parents
                            .map((parent) => parent.split(':')[0])
                            .join(
                              ' × ',
                            )} bằng ${row.genome.family === 'rule_tree' ? 'đột biến/lai cây luật hoặc lookback' : 'đột biến/lai tham số'}; chưa chứng minh tốt hơn.`}
                    </p>
                  </details>
                  {row.promoted ? (
                    <button
                      disabled={busy}
                      onClick={() => act('/promotion', { strategy: row.strategy, promoted: false })}
                    >
                      Đã duyệt · gỡ
                    </button>
                  ) : (
                    <button
                      disabled={busy || !row.qualification.eligible}
                      onClick={() => act('/promotion', { strategy: row.strategy, promoted: true })}
                    >
                      {row.qualification.eligible ? 'Duyệt cho Auto' : 'Chưa đủ điều kiện duyệt'}
                    </button>
                  )}
                </article>
              );
            })}
          </div>
          <p className="evo-note">
            Họ gốc đổi tham số; cây luật có thể đổi điều kiện và cách ghép. Họ gốc chọn cha mẹ có
            ≥10 mẫu và R dương; cây luật xếp hạng từ ≥10 mẫu, trừ điểm theo độ phức tạp. Biến thể
            chưa chứng minh tốt hơn. Cổng duyệt: ≥100 lệnh forward, PF &gt; 1.3, ≥60% cửa sổ 30 ngày
            dương, DD &lt; 10%.
          </p>
        </>
      )}
    </section>
  );
}
