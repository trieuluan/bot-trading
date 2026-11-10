"""Point-in-time H1 events for versioned Futures genomes."""

from .futures_rule_tree import breakout_level, matches

SIGNAL_VERSION = 2


def signal_event(rule, candles, direction, lookback, context):
    close = candles[-1].time + 900
    latest = close // 3600 * 3600
    for event_close in (latest, latest - 3600):
        if not 0 <= close - event_close <= 3600:
            continue
        event_rows = [bar for bar in candles if bar.time < event_close]
        if not event_rows or event_rows[-1].time + 900 != event_close:
            continue
        window = [bar for bar in candles if event_close - 3600 <= bar.time <= candles[-1].time]
        if [bar.time for bar in window] != list(range(event_close - 3600, close, 900)):
            continue
        h4_rule, h1_rule, trigger = rule['children']
        if not matches(h1_rule, event_rows, direction, lookback, context=context):
            continue
        if matches(h1_rule, candles, -direction, lookback, context=context):
            continue
        level = breakout_level(h1_rule, event_rows, direction, lookback)
        valid = True
        for index in range(len(event_rows) - 1, len(candles)):
            known = candles[:index + 1]
            if (not matches(h4_rule, known, direction, lookback, context=context)
                    or level is not None and direction * (known[-1].close - level) < 0):
                valid = False
                break
        if valid and matches(trigger, candles, direction, lookback, context=context):
            return {'key': f'{event_close}:{direction}', 'close': event_close,
                    'invalidation': level, 'version': SIGNAL_VERSION}
    return None


def consume_event(events, name, event):
    if not event:
        return True
    previous = events.setdefault(name, {})
    side = event['key'].rsplit(':', 1)[1]
    if previous.get(side, -1) >= event['close']:
        return False
    previous[side] = event['close']
    return True

def pending_valid(event, rule, candles, direction, lookback, context):
    if not event or not candles:
        return False
    close = candles[-1].time + 900
    start = event['close']
    if not start <= close <= start + 3600:
        return False
    known = [bar for bar in candles if start - 3600 <= bar.time < close]
    if [bar.time for bar in known] != list(range(start - 3600, close, 900)):
        return False
    level = event.get('invalidation')
    return (not any(level is not None and direction * (bar.close - level) < 0 for bar in known)
            and matches(rule['children'][0], candles, direction, lookback, context=context)
            and not matches(rule['children'][1], candles, -direction, lookback, context=context))
