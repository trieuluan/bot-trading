export function liveSignals(
  auto: {
    worker_online: boolean;
    learner_error: string | null;
    auto_mode: string;
    learner_decision: {
      time: number;
      auto_mode: string;
      strategies?: { strategy: string; status: string }[];
    } | null;
  } | null,
  now = Date.now(),
) {
  const decision = auto?.learner_decision;
  if (
    !auto?.worker_online ||
    auto.learner_error ||
    !decision ||
    decision.auto_mode !== auto.auto_mode ||
    decision.time !== Math.floor(now / 900000) * 900 - 900
  )
    return [];
  return (decision.strategies ?? []).filter((row) => row.status === 'signal');
}
