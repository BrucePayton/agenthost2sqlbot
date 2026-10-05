"""Bounded, request-bound preparation evidence, kept outside solver geometry."""

PHASES = frozenset({
    "original", "observation", "measurement", "optional_fallback", "filter_explicit",
    "filter_growth", "filter_readable", "filter_organize", "final", "rank_fallback",
    "safety_minimum", "verification_filter",
})
REASONS = frozenset({
    "measured", "observed", "fallback", "unsupported_measurement", "content_not_ready",
    "no_data", "query_capacity", "content_overflow", "budget_exhausted", "measurement_timeout",
    "measurement_error", "invalid_sizes", "preserve", "explicit", "filtered", "unchanged",
    "static_profile", "isolated_preserve", "observation_error", "observation_timeout",
})
MAX_EVENTS = 600
MAX_EVENT_SIZES = 32
MAX_TOTAL_SIZES = 4096
MAX_NUMBER = 10_000_000


def _number(value, maximum=10000, *, positive=False, integer=False):
    # Exact types exclude booleans; comparisons also reject non-finite floats.
    return (type(value) in ((int,) if integer else (int, float))
            and 0 <= value <= maximum and (not positive or value > 0))


def sanitize_preparation_diagnostics(raw, problem):
    """Return (safe sidecar, status); salvage valid fields without coercing text.

    Missing/null metadata is missing. An invalid envelope is discarded; malformed
    known fields/events are dropped and mark invalid. Truncation takes precedence
    when either the client or the server limit reports incomplete evidence.
    """
    if raw is None:
        return None, "missing"
    if (not isinstance(raw, dict) or raw.get("version") != "preparation-v1"
            or not isinstance(raw.get("events"), list)):
        return None, "invalid"

    invalid = False

    def accept(target, source, key, valid):
        nonlocal invalid
        if key in source:
            if valid(source[key]):
                target[key] = source[key]
            else:
                invalid = True

    clean = {"version": "preparation-v1", "elapsedMs": 0, "truncated": False, "events": []}
    if "elapsedMs" not in raw or "truncated" not in raw:
        invalid = True
    accept(clean, raw, "elapsedMs", lambda value: _number(value, MAX_NUMBER))
    accept(clean, raw, "truncated", lambda value: type(value) is bool)
    for key, allowed in (("mode", {"compact", "organize", "align", "reorder"}),
                         ("sizing", {"content", "preserve", "auto"})):
        accept(clean, raw, key, lambda value, allowed=allowed: isinstance(value, str) and value in allowed)

    nodes = {node.id: node for node in problem.nodes}
    parent_variants = {node.id: {shape.variant for shape in nodes[node.parentId].shapes}
                       if node.parentId is not None else {0} for node in problem.nodes}
    truncated = clean["truncated"] or len(raw["events"]) > MAX_EVENTS
    size_count = 0
    # Bound inspection as well as output, including when all entries are invalid.
    for source in raw["events"][:MAX_EVENTS]:
        if not isinstance(source, dict):
            invalid = True
            continue
        node_id, phase = (source.get(key) for key in ("nodeId", "phase"))
        if (not isinstance(node_id, str) or len(node_id) > 256 or node_id not in nodes
                or not isinstance(phase, str) or phase not in PHASES):
            invalid = True
            continue
        item = {"nodeId": node_id, "phase": phase}
        accept(item, source, "reason", lambda value: isinstance(value, str) and value in REASONS)
        for key in ("inputCount", "outputCount"):
            accept(item, source, key, lambda value: _number(value, MAX_NUMBER, integer=True))
        accept(item, source, "remainingMs", lambda value: _number(value, MAX_NUMBER))
        accept(item, source, "scopeWidth", lambda value: _number(value, positive=True))
        accept(item, source, "parentVariant", lambda value, variants=parent_variants[node_id]:
               _number(value, integer=True) and value in variants)
        accept(item, source, "truncated", lambda value: type(value) is bool)

        for key, maximum in (("minimumPixels", 100000), ("minimumGrid", 10000), ("growthFloor", 10000)):
            if key not in source:
                continue
            pair = source[key]
            if (isinstance(pair, dict)
                    and _number(pair.get("w"), maximum, positive=True)
                    and _number(pair.get("h"), maximum, positive=True)):
                item[key] = {"w": pair["w"], "h": pair["h"]}
            else:
                invalid = True

        if "sizes" in source:
            sizes = source["sizes"]
            if not isinstance(sizes, list):
                invalid = True
            else:
                limit = min(MAX_EVENT_SIZES, MAX_TOTAL_SIZES - size_count)
                item["sizes"] = []
                if len(sizes) > limit:
                    item["truncated"] = True
                for size in sizes[:limit]:
                    if (isinstance(size, dict) and _number(size.get("w"), positive=True)
                            and _number(size.get("h"), positive=True)):
                        item["sizes"].append({"w": size["w"], "h": size["h"]})
                    else:
                        invalid = True
                size_count += len(item["sizes"])

        if "original" in source:
            original = source["original"]
            if (isinstance(original, dict) and all(
                    _number(original.get(key), positive=key in ("w", "h"))
                    for key in ("x", "y", "w", "h"))):
                item["original"] = {key: original[key] for key in ("x", "y", "w", "h")}
            else:
                invalid = True
        if "explicit" in source:
            explicit = source["explicit"]
            if isinstance(explicit, dict):
                dimensions = {}
                for key in ("w", "h"):
                    accept(dimensions, explicit, key, lambda value: _number(value, positive=True))
                if dimensions:
                    item["explicit"] = dimensions
            else:
                invalid = True

        truncated |= item.get("truncated", False)
        clean["events"].append(item)

    clean["truncated"] = truncated
    return clean, "truncated" if truncated else "invalid" if invalid else "captured"
