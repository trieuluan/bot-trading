import math


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def ticket_owners(intents):
    owners = {}
    for intent in intents:
        legs = intent.snapshot.get("legs") or [intent.snapshot]
        sides = {leg.get("body", {}).get("side") for leg in legs}
        consistent = len(sides) == 1 and sides <= {"buy", "sell"}
        for leg in legs:
            identifier = leg.get("position_id")
            if identifier is None:
                continue
            key = (intent.account, intent.symbol, str(identifier))
            owners[key] = None if key in owners or not consistent else (intent, leg)
    return owners


def setup_key(account, symbol, signal_time):
    return f"setup:{account}:{symbol}:{signal_time}"


def position_groups(rows):
    buckets = {}
    for row in rows:
        key = (row["symbol"], row.get("setup_id") if row.get("ownership_verified") else None)
        if key[1] is None:
            key = (row["symbol"], f"ticket:{row['symbol']}:{row['position_id']}")
        buckets.setdefault(key, []).append(row)
    groups = []
    for (_, identifier), legs in buckets.items():
        if len({leg.get("side") for leg in legs}) != 1 or legs[0].get("side") not in ("buy", "sell"):
            for leg in legs:
                isolated = {**leg, "setup_id": None, "ownership_verified": False}
                groups.append(summarize_group(f"ticket:{leg['symbol']}:{leg['position_id']}", [isolated]))
            continue
        groups.append(summarize_group(identifier, legs))
    return sorted(groups, key=lambda group: (-(group["open_time"] or 0), group["id"]))


def summarize_group(identifier, rows):
    legs = sorted(rows, key=lambda leg: (leg.get("role") != "tp1", leg["position_id"]))
    first = legs[0]
    opened = [leg for leg in legs if leg["status"] == "open"]
    status = "open" if opened else "closed" if all(leg["status"] == "closed" for leg in legs) else "history_incomplete"
    total = sum(leg["initial_volume"] for leg in legs) if all(finite(leg.get("initial_volume")) for leg in legs) else None
    remaining = sum(leg["remaining_volume"] for leg in legs) if all(finite(leg.get("remaining_volume")) for leg in legs) else None
    entry = (sum(leg["open_price"] * leg["initial_volume"] for leg in legs) / total
             if total and all(finite(leg.get("open_price")) for leg in legs) else None)
    realized = (sum(leg["pnl"] if leg.get("has_closes") else 0 for leg in legs)
                if all(finite(leg.get("initial_volume")) and (not leg.get("has_closes") or finite(leg.get("pnl")))
                       and leg["status"] != "history_incomplete" for leg in legs) else None)
    times = [leg["open_time"] for leg in legs if finite(leg.get("open_time"))]
    verified = all(leg.get("ownership_verified") for leg in legs) and first.get("side") in ("buy", "sell")
    return {"id": identifier, "symbol": first["symbol"], "signal_time": first.get("signal_time") if verified else None,
            "strategy": first.get("strategy"), "source": first["source"], "side": first.get("side"),
            "ownership_verified": verified, "status": status, "open_count": len(opened), "ticket_count": len(legs),
            "initial_volume": total, "remaining_volume": remaining, "avg_open_price": entry,
            "open_time": min(times) if times else None, "realized_pnl": realized,
            "history_incomplete": any(leg["status"] == "history_incomplete" or leg.get("initial_volume") is None for leg in legs),
            "tp1_taken": any(leg.get("role") == "tp1" and leg["status"] == "closed" and leg.get("take_profit_filled") for leg in legs),
            "close_requested": any(leg.get("close_requested") for leg in opened), "legs": legs}


def group_page(groups, offset, limit, status):
    filtered = [group for group in groups if status == "all" or (status == "bot" and group["ownership_verified"])
                or group["status"] == status]
    return {"groups": filtered[offset:offset + limit], "total": len(filtered),
            "has_more": offset + limit < len(filtered),
            "open_groups": [group for group in groups if group["status"] == "open"]}


def open_setup_keys(account, positions, owners):
    keys = set()
    for index, position in enumerate(positions):
        if position.get("position_id") is None:
            keys.add(("unidentified", index))
            continue
        symbol, identifier = position["instrument"], str(position["position_id"])
        owner = owners.get((account, symbol, identifier))
        if owner and owner[1].get("body", {}).get("side") == position.get("direction"):
            keys.add((setup_key(account, symbol, owner[0].signal_time), position["direction"]))
        else:
            keys.add((symbol, identifier))
    return keys

def open_setup_count(account, positions, owners):
    return len(open_setup_keys(account, positions, owners))
