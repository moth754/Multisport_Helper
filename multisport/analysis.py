"""Find missed or misplaced timing taps in multisport results and suggest the fix.

Everything here is pure: it takes Webscorer's race JSON (and, when available, the raw
fasttaps JSON) and returns plain dicts for the web page. Nothing is stored.

Webscorer's LapTimes are the results as they stand now, including any corrections the
timing team has already keyed in, so problems are judged on those. The raw taps are
extra evidence: a read that isn't being used, or which reader a tap came from.
"""

import hashlib
import math
import re
import statistics
from collections import Counter, defaultdict

DEFAULTS = {
    "transition_multiple": 3.0,   # transition over this many times the field's usual transition...
    "transition_extra_min": 3.0,  # ...and at least this many minutes over it
    "discipline_min_pct": 40,     # swim/bike/run under this % of the field's usual time
    "transition_min_pct": 15,     # transition under this % of the usual transition
}

MATCH = 0.6          # seconds: a tap and a split this close are the same moment
DUPLICATE = 30.0     # seconds: a second read this close to a kept one is a duplicate
MISSING_COST = 6.0   # cost of saying a tap was missed
DROP_COST = 3.0      # cost of ignoring a real chip read
DROP_MANUAL = 2.0    # cost of ignoring a hand tap
DROP_DUP = 0.3       # cost of ignoring a duplicate read
CHANGE_COST = 0.5    # tiny preference for leaving a split as it is
READER_MAX = 8.0     # most a tap can cost for coming from the 'wrong' reader
LABEL_COST = 2.0     # a tap Webscorer labelled as a different lap
ALT_GAP = 3.0        # another reading this close in cost is shown as 'also possible'
ONCOURSE_DROP = 15.0 # ignoring an on-course racer's latest tap
MIN_GAIN = 8.0       # a different reading of the taps must be this much better to be suggested
PARKED_CHIP = 30     # an unassigned chip read this often is a spare chip lying by a mat

_TRANSITION = re.compile(r"transition|^t\d\b", re.I)


# ------------------------------------------------------------------ time helpers
def parse_time(text):
    """'1:02:03.4', '5:56.8', '23.1', '08:32:36.200' -> seconds; '-', '' or None -> None."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    text = str(text).strip()
    if not text or not re.fullmatch(r"\d+(:\d+){0,2}(\.\d+)?", text):
        return None
    total = 0.0
    for part in text.split(":"):
        total = total * 60 + float(part)
    return total


def _split(seconds, places):
    """(whole seconds, '.d' fraction) after rounding, so 59.96 becomes 60 and '.0'."""
    scaled = round(abs(seconds) * 10 ** places)
    whole, frac = divmod(scaled, 10 ** places)
    return whole, (f".{frac:0{places}d}" if places else "")


def fmt(seconds, places=1):
    """Seconds -> '1:02:03.4' or '5:56.8' (a duration or elapsed race time)."""
    if seconds is None:
        return "–"
    sign = "-" if seconds < 0 else ""
    whole, frac = _split(seconds, places)
    h, rem = divmod(whole, 3600)
    m, s = divmod(rem, 60)
    return f"{sign}{h}:{m:02d}:{s:02d}{frac}" if h else f"{sign}{m}:{s:02d}{frac}"


def fmt_tod(seconds, places=1):
    """Seconds since midnight -> '9:33:05.9'."""
    if seconds is None:
        return "–"
    whole, frac = _split(seconds % 86400, places)
    h, rem = divmod(whole % 86400, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}{frac}"


def is_transition(name):
    return bool(_TRANSITION.search(name or ""))


def short_leg(name):
    """'Transition 2' -> 'T2' for tight table headings; other names unchanged."""
    m = re.fullmatch(r"\s*transition\s*(\d+)\s*", name or "", re.I)
    return f"T{m.group(1)}" if m else name


def _bib(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def _text(value):
    return "" if value is None else str(value).strip()


# ------------------------------------------------------------------ reading the race
def _racer_status(racer, cum):
    time_text = _text(racer.get("Time")).upper()
    if time_text in ("DNS", "DNF", "DSQ", "DQ"):
        return time_text
    if parse_time(racer.get("Time")) is not None and _text(racer.get("Place")) not in ("", "-"):
        return "finished"
    if any(c is not None for c in cum):
        return "on course"
    return "not started"


def _layout(racers):
    """The distance's usual legs, and which splits are never timed (merged into the next leg)."""
    tuples = Counter(tuple(_text(l.get("LapName")) for l in r.get("LapTimes") or []) for r in racers)
    tuples.pop((), None)
    if not tuples:
        return None
    names = list(tuples.most_common(1)[0][0])
    blank = Counter()
    count = 0
    for r in racers:
        laps = r.get("LapTimes") or []
        if tuple(_text(l.get("LapName")) for l in laps) != tuple(names):
            continue
        count += 1
        for i, lap in enumerate(laps):
            if parse_time(lap.get("RaceTime")) is None:
                blank[i] += 1
    # a split that almost nobody has (e.g. a swim that is only timed together with the run
    # from the pool) is part of the course layout, not a missed tap
    structural = {i for i in range(len(names) - 1) if count and blank[i] > count * 0.85}
    return {"names": names, "structural": structural}


def _legs_of(layout):
    """[(leg name, [original split indexes it covers])] after merging untimed splits."""
    legs, pending = [], []
    for i, name in enumerate(layout["names"]):
        pending.append(i)
        if i in layout["structural"]:
            continue
        legs.append((" + ".join(layout["names"][j] for j in pending), pending))
        pending = []
    return legs


def _read_racer(racer, layout, legs):
    laps = racer.get("LapTimes") or []
    same = tuple(_text(l.get("LapName")) for l in laps) == tuple(layout["names"])
    cum = [None] * len(legs)
    if same:
        for k, (_, idx) in enumerate(legs):
            cum[k] = parse_time(laps[idx[-1]].get("RaceTime"))
    finish = parse_time(racer.get("Time"))
    status = _racer_status(racer, cum)
    if status == "finished" and cum[-1] is None and finish is not None and (same or not laps):
        cum[-1] = finish
    return {
        "bib": _bib(racer.get("Bib")),
        "name": _text(racer.get("Name")) or " ".join(filter(None, (_text(racer.get("FirstName")), _text(racer.get("LastName"))))),
        "status": status,
        "time": _text(racer.get("Time")),
        "start_tod": parse_time(racer.get("StartTime")),
        "cum": cum,
        "layout_ok": same or not laps,
    }


def _leg_times(cum):
    out, prev = [], 0.0
    for c in cum:
        if c is None or prev is None:
            out.append(None)
        else:
            out.append(c - prev)
        prev = c
    return out


# ------------------------------------------------------------------ taps
def _index_taps(taps_json):
    """{bib: [tap dict]}, plus unassigned chip reads, from Webscorer's fasttaps JSON."""
    rows = []
    if isinstance(taps_json, dict):
        rows = taps_json.get("FastTaps") or []
    elif isinstance(taps_json, list):
        rows = taps_json
    by_bib = defaultdict(list)
    loose = []
    offsets = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        t = parse_time(row.get("Time tap"))
        tod = parse_time(row.get("Time tap (time of day)"))
        if t is not None and tod is not None:
            offsets.append(tod - t)
        tap = {
            "seq": _text(row.get("Seq #")),
            "label": _text(row.get("Tap")),
            "t": t,
            "tod": tod,
            "start": parse_time(row.get("Start")),
            "reader": _text(row.get("Reader")),
            "chip": _text(row.get("Chip ID")),
        }
        tap["manual"] = not tap["reader"]
        bib = _bib(row.get("Bib"))
        if bib:
            if t is not None:
                by_bib[bib].append(tap)
        elif tap["chip"] and tod is not None:
            loose.append(tap)
    for taps in by_bib.values():
        taps.sort(key=lambda x: x["t"])
    chip_counts = Counter(t["chip"] for t in loose)
    loose = [t for t in loose if chip_counts[t["chip"]] < PARKED_CHIP]
    offset = statistics.median(offsets) if offsets else None
    return by_bib, loose, offset


def _zero(taps, cum):
    """Race-clock time of this racer's start, chosen so their taps line up with their splits."""
    options = []
    for tap in taps:
        if tap["label"].lower() == "start" and tap["t"] is not None:
            options.append(tap["t"])
    options += [tap["start"] for tap in taps if tap["start"] is not None]
    options.append(0.0)
    known = [c for c in cum if c is not None]
    lap_times = [t["t"] for t in taps if t["label"].lower() != "start"]
    best, best_hits = None, -1
    for z in dict.fromkeys(options):
        hits = sum(1 for c in known if any(abs(t - z - c) < MATCH for t in lap_times))
        if hits > best_hits:
            best, best_hits = z, hits
    return best


# ------------------------------------------------------------------ the field
def _field(records, legs):
    """Usual time for each leg (median) and how much it varies, from clean finishers."""
    n = len(legs)
    full = [r for r in records if r["layout_ok"] and all(c is not None for c in r["cum"])]
    medians = []
    for k in range(n):
        vals = [lt[k] for lt in (_leg_times(r["cum"]) for r in full) if lt[k] and lt[k] > 0]
        medians.append(statistics.median(vals) if vals else None)
    if any(m is None for m in medians):
        # early in the day: fall back to rough shapes so the checks still run
        guess = [60.0 if is_transition(name) else 1200.0 for name, _ in legs]
        medians = [m if m is not None else g for m, g in zip(medians, guess)]
    total = sum(medians)
    spreads = []
    for k in range(n):
        res = []
        for r in full:
            lt = _leg_times(r["cum"])
            pace = r["cum"][-1] / total if total else 1.0
            if lt[k] and lt[k] > 0 and pace > 0:
                res.append(math.log(lt[k] / (medians[k] * pace)))
        floor = 0.30 if is_transition(legs[k][0]) else 0.10
        if len(res) >= 5:
            mid = statistics.median(res)
            mad = statistics.median(abs(x - mid) for x in res) * 1.4826
            spreads.append(max(mad, floor))
        else:
            spreads.append(floor * 2)
    return {"medians": medians, "spreads": spreads, "finishers": len(full)}


# ------------------------------------------------------------------ rule checks
def _checks(legs, times, field, th, severe=None):
    """Plain-English reasons this set of splits can't be right, and which legs are bad.

    `severe` (a list, if given) collects the reasons that are impossible rather than just unusual.
    """
    reasons, bad = [], set()
    severe = [] if severe is None else severe
    kinds = [is_transition(name) for name, _ in legs]
    names = [short_leg(name) for name, _ in legs]
    for k, t in enumerate(times):
        if t is None:
            continue
        med = field["medians"][k]
        if kinds[k]:
            for j in (k - 1, k + 1):
                if (0 <= j < len(times) and not kinds[j] and times[j] is not None and t > times[j]
                        and field["medians"][j] >= med * 3):
                    reasons.append(f"{names[k]} {fmt(t)} is longer than the {names[j].lower()} {fmt(times[j])}")
                    severe.append(reasons[-1])
                    bad.update((k, j))
            if t > med * th["transition_multiple"] and t - med > th["transition_extra_min"] * 60:
                reasons.append(f"{names[k]} {fmt(t)} is far longer than usual (about {fmt(med, 0)})")
                bad.add(k)
            if t < med * th["transition_min_pct"] / 100:
                reasons.append(f"{names[k]} {fmt(t)} is too quick (usual about {fmt(med, 0)})")
                severe.append(reasons[-1])
                bad.add(k)
        elif t < med * th["discipline_min_pct"] / 100:
            reasons.append(f"{names[k]} {fmt(t)} is far too quick (usual about {fmt(med, 0)})")
            severe.append(reasons[-1])
            bad.add(k)
        if t <= 0:
            reasons.append(f"{names[k]} has no time")
            bad.add(k)
    return list(dict.fromkeys(reasons)), bad


# ------------------------------------------------------------------ re-reading the taps
def _span_cost(span, first, last, expected, spreads):
    """How unlikely it is that legs first..last (inclusive) took `span` seconds."""
    exp = sum(expected[first:last + 1])
    if span <= 0 or exp <= 0:
        return 1e6
    s = sum(expected[i] * spreads[i] for i in range(first, last + 1)) / exp
    return (math.log(span / exp) / s) ** 2


def _reader_cost(reader_counts, b, cand, labels=None):
    """How unusual it is for this tap's reader (and lap label) to record leg end b."""
    tap = cand.get("tap")
    if tap is None:
        return 0.0
    cost = 0.0
    if labels and labels[b] and tap["label"] and tap["label"] != labels[b] and tap["label"] in labels:
        cost += LABEL_COST
    if not reader_counts or tap["manual"]:
        return cost
    counts = reader_counts[b]
    total = sum(counts.values())
    if total < 5:
        return 0.0
    share = (counts.get(tap["reader"], 0) + 0.5) / (total + 1)
    best = (max(counts.values()) + 0.5) / (total + 1)
    return cost + min(READER_MAX, math.log(best / share))


def _drop_cost(cand, kept_times):
    near = any(abs(cand["t"] - k) <= DUPLICATE for k in kept_times)
    if near:
        return DROP_DUP
    return DROP_MANUAL if cand["manual"] else DROP_COST


def _align(cands, n, expected, spreads, finished, current_cum, reader_counts=None, labels=None):
    """Best way to read the candidate times as the ends of the n legs.

    Returns (cost, [candidate index or None per leg end], last leg end used).
    A None means that tap was missed. Finishers must end on a real time.
    """
    times = [0.0] + [c["t"] for c in cands]
    m = len(cands)
    INF = float("inf")
    # best[b][j]: legs 1..b explained, with leg end b at candidate j (j=0 is the start)
    best = [[INF] * (m + 1) for _ in range(n + 1)]
    back = [[None] * (m + 1) for _ in range(n + 1)]
    best[0][0] = 0.0
    for b in range(1, n + 1):
        for k in range(1, m + 1):
            change = 0.0
            cur = current_cum[b - 1] if b - 1 < len(current_cum) else None
            if cur is None or abs(cur - times[k]) > MATCH:
                change = CHANGE_COST
            change += _reader_cost(reader_counts, b - 1, cands[k - 1], labels)
            for a in range(0, b):
                for j in range(0, k):
                    if best[a][j] == INF:
                        continue
                    span = times[k] - times[j]
                    cost = best[a][j] + _span_cost(span, a, b - 1, expected, spreads)
                    cost += MISSING_COST * (b - a - 1) + change
                    kept = (times[j], times[k])
                    cost += sum(_drop_cost(cands[x - 1], kept) for x in range(j + 1, k))
                    if cost < best[b][k]:
                        best[b][k] = cost
                        back[b][k] = (a, j)
    ends = range(n, n + 1) if finished else range(1, n + 1)
    choice = None
    for b in ends:
        for k in range(1, m + 1):
            if best[b][k] == INF:
                continue
            tail = [cands[x - 1] for x in range(k + 1, m + 1)]
            cost = best[b][k]
            for c in tail:
                drop = _drop_cost(c, (times[k],))
                # for someone still on course the latest tap is where they are now: it can't be ignored
                cost += drop if finished or drop == DROP_DUP else ONCOURSE_DROP
            if choice is None or cost < choice[0]:
                choice = (cost, b, k)
    if choice is None:
        return None
    cost, b, k = choice
    assign = [None] * n
    while b > 0:
        assign[b - 1] = k - 1
        a, j = back[b][k]
        b, k = a, j
    return cost, assign, choice[1]


def _assignment_cost(assign, last, cands, expected, spreads, cum, reader_counts, labels):
    """Cost of one particular reading, scored exactly as _align scores it."""
    if last < 1 or assign[last - 1] is None:
        return float("inf")
    cost, prev_b, prev_t, prev_k = 0.0, 0, 0.0, -1
    for b in range(1, last + 1):
        k = assign[b - 1]
        if k is None:
            continue
        t = cands[k]["t"]
        if k <= prev_k or t <= prev_t:
            return float("inf")
        cost += _span_cost(t - prev_t, prev_b, b - 1, expected, spreads) + MISSING_COST * (b - prev_b - 1)
        cur = cum[b - 1] if b - 1 < len(cum) else None
        if cur is None or abs(cur - t) > MATCH:
            cost += CHANGE_COST
        cost += _reader_cost(reader_counts, b - 1, cands[k], labels)
        cost += sum(_drop_cost(cands[x], (prev_t, t)) for x in range(prev_k + 1, k))
        prev_b, prev_t, prev_k = b, t, k
    cost += sum(_drop_cost(cands[x], (prev_t,)) for x in range(prev_k + 1, len(cands)))
    return cost


def _alternative(assign, last, best_cost, cands, expected, spreads, cum, reader_counts, labels):
    """A nearly-as-good reading where a neighbouring tap, not this one, was the missed one."""
    options = []
    for i in range(last):
        if assign[i] is not None:
            continue
        for j in (i - 1, i + 1):
            if not 0 <= j < last or assign[j] is None:
                continue
            alt = list(assign)
            alt[i], alt[j] = assign[j], None
            cost = _assignment_cost(alt, last, cands, expected, spreads, cum, reader_counts, labels)
            if cost - best_cost <= ALT_GAP:
                options.append((cost, alt))
    return min(options, key=lambda o: o[0])[1] if options else None


def _current_cost(cands, cum, expected, spreads, reader_counts=None, labels=None):
    """Cost of the splits exactly as Webscorer has them, scored the same way as _align."""
    cost, prev, prev_leg = 0.0, 0.0, 0
    used = set()
    last = max((i for i, c in enumerate(cum) if c is not None), default=-1)
    for i in range(last + 1):
        if cum[i] is None:
            continue
        cost += _span_cost(cum[i] - prev, prev_leg, i, expected, spreads) + MISSING_COST * (i - prev_leg)
        prev, prev_leg = cum[i], i + 1
        for x, c in enumerate(cands):
            if abs(c["t"] - cum[i]) <= MATCH:
                used.add(x)
                cost += _reader_cost(reader_counts, i, c, labels)
    kept = [c for c in cum if c is not None]
    for x, c in enumerate(cands):
        if x not in used and (not kept or c["t"] <= kept[-1] + MATCH):
            cost += _drop_cost(c, kept)
    return cost


# ------------------------------------------------------------------ main entry point
def analyse(race_json, taps_json=None, thresholds=None):
    th = dict(DEFAULTS)
    th.update({k: v for k, v in (thresholds or {}).items() if k in DEFAULTS and v is not None})
    info = race_json.get("RaceInfo") or {}
    by_bib, loose, tod_offset = _index_taps(taps_json)
    distances, problems, counts = [], [], Counter()

    for group in race_json.get("Results") or []:
        grouping = group.get("Grouping") or {}
        if grouping.get("Overall") is not True:
            continue
        dname = _text(grouping.get("Distance")) or "Race"
        racers = group.get("Racers") or []
        layout = _layout(racers)
        if not layout:
            distances.append({"name": dname, "legs": [], "rows": [], "note": "No split times yet"})
            continue
        legs = _legs_of(layout)
        n = len(legs)
        records = [_read_racer(r, layout, legs) for r in racers]
        field = _field(records, legs)
        expected_readers, lap_labels, reader_counts = _usual_taps(records, legs, by_bib)

        rows = []
        for rec in records:
            counts[rec["status"]] += 1
            times = _leg_times(rec["cum"])
            row = {
                "bib": rec["bib"], "name": rec["name"], "status": rec["status"], "time": rec["time"],
                "legs": [fmt(t) if t is not None else "" for t in times], "bad": [], "problem": False,
            }
            rows.append(row)
            if rec["status"] not in ("finished", "on course") or not rec["layout_ok"]:
                continue
            problem = _check_racer(rec, dname, legs, field, th, by_bib.get(rec["bib"], []),
                                   loose, tod_offset, expected_readers, lap_labels, reader_counts)
            if problem:
                row["bad"] = problem["bad_legs"]
                row["problem"] = True
                problems.append(problem)
        notes = []
        finishers = sum(1 for r in records if r["status"] == "finished")
        dist_problems = [p for p in problems if p["distance"] == dname]
        for i in range(n - 1):
            hit = [p for p in dist_problems if i in p["missing"]]
            if len(hit) >= 5 and len(hit) > finishers * 0.1:
                notes.append(f"{len(hit)} of {finishers} finishers have no {_end_name(legs, i)} time. "
                             "The reader there may be missing people; they are listed together below.")
                for p in hit:
                    if p["only_missing"]:
                        p["grouped"] = _end_name(legs, i)
        distances.append({
            "name": dname,
            "notes": notes,
            "legs": [{"name": name, "short": short_leg(name), "transition": is_transition(name),
                      "usual": fmt(field["medians"][k], 0)} for k, (name, _) in enumerate(legs)],
            "finishers": field["finishers"],
            "rows": rows,
        })

    problems.sort(key=lambda p: (-p["severity"], p["distance"], _bib_sort(p["bib"])))
    return {
        "race": {"name": _text(info.get("Name")), "date": _text(info.get("Date")),
                 "sport": _text(info.get("Sport")), "state": _text(info.get("CompletionState"))},
        "distances": distances,
        "problems": problems,
        "counts": dict(counts),
        "taps_available": bool(by_bib),
    }


def _bib_sort(bib):
    return (0, int(bib)) if bib.isdigit() else (1, bib)


def _usual_taps(records, legs, by_bib):
    """Which reader and which Webscorer lap label normally record each leg end."""
    readers = [Counter() for _ in legs]
    labels = [Counter() for _ in legs]
    for rec in records:
        taps = by_bib.get(rec["bib"])
        if not taps:
            continue
        zero = _zero(taps, rec["cum"])
        for k, c in enumerate(rec["cum"]):
            if c is None:
                continue
            for tap in taps:
                if tap["label"].lower() != "start" and abs(tap["t"] - zero - c) < MATCH:
                    if tap["reader"]:
                        readers[k][tap["reader"]] += 1
                    if tap["label"]:
                        labels[k][tap["label"]] += 1
                    break
    usual_readers, usual_labels = [], []
    for k, counter in enumerate(readers):
        total = sum(counter.values())
        reader, hits = counter.most_common(1)[0] if counter else ("", 0)
        usual_readers.append(reader if total >= 3 and hits >= total * 0.6 and reader != "hand" else "")
        usual_labels.append(labels[k].most_common(1)[0][0] if labels[k] else "")
    return usual_readers, usual_labels, readers


def _check_racer(rec, dname, legs, field, th, taps, loose, tod_offset, expected_readers, lap_labels,
                 reader_counts=None):
    n = len(legs)
    cum = rec["cum"]
    times = _leg_times(cum)
    severe = []
    reasons, bad = _checks(legs, times, field, th, severe)
    finished = rec["status"] == "finished"
    known = [i for i, c in enumerate(cum) if c is not None]
    level = "error" if severe else "check"
    missing = []
    if finished:
        for i in range(n - 1):
            if cum[i] is None:
                missing.append(i)
                reasons.append(f"No {_end_name(legs, i)} time: that tap was missed")
                bad.update((i, i + 1))

    # candidate times: the current splits plus every lap tap for this bib
    zero = _zero(taps, cum) if taps else 0.0
    rec["clock_ok"] = bool(taps)  # race clock times are only known when Webscorer sent the raw taps
    cands = []
    for i in known:
        cands.append({"t": cum[i], "current": i, "tap": None, "manual": False})
    for tap in taps:
        if tap["label"].lower() == "start":
            continue
        t = tap["t"] - zero
        if t <= 0:
            continue
        hit = next((c for c in cands if abs(c["t"] - t) <= MATCH), None)
        if hit:
            if hit["tap"] is None:
                hit["tap"], hit["manual"] = tap, tap["manual"]
        else:
            cands.append({"t": t, "current": None, "tap": tap, "manual": tap["manual"]})
    cands.sort(key=lambda c: c["t"])
    if not cands:
        return None

    # expected leg times for this racer, scaled by their own pace
    total_med = sum(field["medians"])
    if finished and cum[-1]:
        pace = cum[-1] / total_med
    else:
        ratios = [times[k] / field["medians"][k] for k in range(n)
                  if times[k] and not is_transition(legs[k][0]) and k not in bad]
        pace = statistics.median(ratios) if ratios else 1.0
    pace = min(max(pace, 0.5), 3.0)
    expected = [m * pace for m in field["medians"]]
    spreads = field["spreads"]

    current = _current_cost(cands, cum, expected, spreads, reader_counts, lap_labels)
    aligned = _align(cands, n, expected, spreads, finished, cum, reader_counts, lap_labels)
    suggestion = None
    if aligned:
        cost, assign, last = aligned
        new_cum = _fill_missing(assign, cands, expected, last)
        changed = [i for i in range(last) if not _same(new_cum[i], cum[i])]
        if changed and (reasons or current - cost >= MIN_GAIN):
            new_times = _leg_times(new_cum[:last])
            new_reasons, _ = _checks(legs[:last], new_times, field, th)
            if len(new_reasons) < len(reasons) or current - cost >= MIN_GAIN:
                suggestion = _describe(legs, cum, new_cum, assign, cands, last, zero,
                                       tod_offset, rec, loose, expected_readers, lap_labels)
                alt = _alternative(assign, last, cost, cands, expected, spreads, cum, reader_counts, lap_labels)
                if alt:
                    alt_cum = _fill_missing(alt, cands, expected, last)
                    alt_desc = _describe(legs, cum, alt_cum, alt, cands, last, zero,
                                         tod_offset, rec, loose, expected_readers, lap_labels)
                    suggestion["alternative"] = {
                        "ends": [e for e in alt_desc["ends"] if e["kind"] in ("estimate", "moved")],
                        "legs": alt_desc["legs"],
                    }
                if level == "check" and any(r["genuine"] for r in suggestion["remove"]):
                    # the taps came from the readers that should record them: don't suggest deleting good reads
                    suggestion = None
                    if missing:
                        suggestion = _fill_only(legs, cum, cands, expected, zero, tod_offset, rec, loose,
                                                expected_readers, lap_labels)
                    elif reasons:
                        reasons.append("The chip reads all came from the right readers, so this may be a genuinely slow leg")
                elif not reasons:
                    reasons.append("Possible: these times fit the racer's other legs much better")
                    bad.update(changed)
                    bad.update(i + 1 for i in changed if i + 1 < n)

    if not reasons:
        return None
    timeline = _timeline(taps, cum, zero, tod_offset, rec, legs, expected_readers, lap_labels)
    legs_out = [{"name": short_leg(name), "full": name, "time": fmt(times[k]) if times[k] is not None else "",
                 "usual": fmt(field["medians"][k], 0), "bad": k in bad, "transition": is_transition(name)}
                for k, (name, _) in enumerate(legs)]
    sig = hashlib.sha1(repr((rec["bib"], dname, [round(c, 1) if c else None for c in cum])).encode()).hexdigest()[:12]
    worst = max((abs(math.log(max(times[k], 1) / field["medians"][k])) for k in bad
                 if k < n and times[k]), default=1.0)
    return {
        "id": f"{dname}|{rec['bib']}|{rec['name']}",
        "sig": sig,
        "bib": rec["bib"], "name": rec["name"], "distance": dname, "status": rec["status"],
        "time": rec["time"],
        "legs": legs_out,
        "bad_legs": sorted(bad),
        "reasons": reasons,
        "suggestion": suggestion,
        "timeline": timeline,
        "level": level,
        "missing": missing,
        "only_missing": level == "check" and bool(missing) and len(reasons) == len(missing),
        "severity": round((10 if level == "error" else 0) + len(reasons) + worst, 2),
    }


def _fill_only(legs, cum, cands, expected, zero, tod_offset, rec, loose, readers, labels):
    """Keep every split as it is and just estimate the missing ones."""
    assign = []
    for c in cum:
        idx = next((x for x, cand in enumerate(cands) if c is not None and abs(cand["t"] - c) <= MATCH), None)
        assign.append(idx)
    last = max((i + 1 for i, a in enumerate(assign) if a is not None), default=0)
    if not last:
        return None
    new_cum = _fill_missing(assign, cands, expected, last)
    return _describe(legs, cum, new_cum, assign, cands, last, zero, tod_offset, rec, loose, readers, labels)


def _same(a, b):
    return (a is None and b is None) or (a is not None and b is not None and abs(a - b) <= MATCH)


def _fill_missing(assign, cands, expected, last):
    """Leg-end times for the chosen reading, estimating missed taps from the usual leg times."""
    n = len(assign)
    out = [None] * n
    for i in range(last):
        if assign[i] is not None:
            out[i] = cands[assign[i]]["t"]
    prev_i, prev_t = -1, 0.0
    for i in range(last):
        if out[i] is None:
            continue
        gap = list(range(prev_i + 1, i))
        if gap:
            span = out[i] - prev_t
            exp = expected[prev_i + 1:i + 1]
            total = sum(exp)
            run = prev_t
            for off, g in enumerate(gap):
                run += span * exp[off] / total
                out[g] = run
        prev_i, prev_t = i, out[i]
    return out


def _tod(t, zero, tod_offset, rec):
    if t is None:
        return None
    if tod_offset is not None:
        return t + zero + tod_offset
    if rec["start_tod"]:
        return t + rec["start_tod"]
    return None


def _end_name(legs, i):
    name = legs[i][0]
    if i == len(legs) - 1:
        return "Finish"
    if is_transition(name):
        return f"{short_leg(name)} exit"
    return f"{name} end"


def _end_label(legs, labels, i):
    """'Lap 4 · T2 exit': Webscorer's lap number (when known) and what the tap marks."""
    lap = labels[i] if i < len(labels) else ""
    return f"{lap} · {_end_name(legs, i)}" if lap else _end_name(legs, i)


def _tap_source(tap):
    if tap["manual"]:
        return f"hand tap #{tap['seq']}"
    return f"chip read #{tap['seq']} on reader {tap['reader']}"


def _describe(legs, cum, new_cum, assign, cands, last, zero, tod_offset, rec, loose, readers, labels):
    """The suggested splits, leg end by leg end, with where each time comes from."""
    def tod(t):
        v = _tod(t, zero, tod_offset, rec)
        return fmt_tod(v) if v is not None else ""

    def clock(t):
        """Webscorer's race clock ('Time tap') for a leg end, e.g. 1:19:30.0."""
        return fmt(t + zero) if t is not None and rec.get("clock_ok") else ""

    def lap(times, i):
        """Duration of the leg ending at leg end i."""
        if i >= len(times) or times[i] is None:
            return ""
        prev = 0.0 if i == 0 else times[i - 1]
        return fmt(times[i] - prev) if prev is not None else ""

    ends = []
    for i in range(len(legs)):
        now_t = cum[i] if i < len(cum) else None
        new_t = new_cum[i] if i < last else None
        row = {"end": _end_label(legs, labels, i), "now": tod(now_t) if now_t is not None else "",
               "now_elapsed": fmt(now_t) if now_t is not None else "",
               "now_clock": clock(now_t), "now_lap": lap(cum, i),
               "new": "", "new_elapsed": "", "new_clock": "", "new_lap": "",
               "kind": "same", "note": "", "chips": []}
        if i >= last:
            row["kind"] = "later"
            row["note"] = "not reached yet"
            ends.append(row)
            continue
        row["new"], row["new_elapsed"] = tod(new_t), fmt(new_t)
        row["new_clock"], row["new_lap"] = clock(new_t), lap(new_cum[:last], i)
        cand = cands[assign[i]] if assign[i] is not None else None
        if _same(new_t, now_t):
            row["kind"], row["note"] = "same", "no change"
        elif cand is None:
            est = _tod(new_t, zero, tod_offset, rec)
            row["kind"] = "estimate"
            row["new"] = "≈ " + (fmt_tod(est, 0) if est is not None else fmt(new_t, 0))
            row["new_elapsed"] = "≈ " + fmt(new_t, 0)
            row["new_clock"] = ("≈ " + row["new_clock"]) if row["new_clock"] else ""
            row["new_lap"] = ("≈ " + row["new_lap"]) if row["new_lap"] else ""
            row["note"] = "missed: estimate from the usual leg times"
            row["chips"] = _nearby_chips(loose, est, readers[i])
        else:
            row["kind"] = "moved"
            was = next((j for j, c in enumerate(cum) if c is not None and abs(c - cand["t"]) <= MATCH), None)
            if was is not None:
                row["note"] = f"the tap now used as the {_end_name(legs, was)}"
            elif cand["tap"] is not None:
                row["note"] = f"unused {_tap_source(cand['tap'])}"
        ends.append(row)

    remove = []
    kept = [new_cum[i] for i in range(last) if new_cum[i] is not None]
    for i, c in enumerate(cum):
        if c is None or any(abs(c - k) <= MATCH for k in kept):
            continue
        tap = next((x["tap"] for x in cands if x["tap"] is not None and abs(x["t"] - c) <= MATCH), None)
        why = ("a second read of the same crossing" if any(abs(c - k) <= DUPLICATE for k in kept)
               else "doesn't fit this racer's other times")
        remove.append({"tod": tod(c), "elapsed": fmt(c), "clock": clock(c), "was": _end_label(legs, labels, i),
                       "source": _tap_source(tap) if tap else "", "why": why,
                       "genuine": bool(tap and not tap["manual"] and readers[i] and tap["reader"] == readers[i]
                                       and why != "a second read of the same crossing")})

    new_times = _leg_times(new_cum[:last])
    old_times = _leg_times(cum)
    est_ends = {i for i in range(last) if assign[i] is None}
    return {
        "ends": ends,
        "remove": remove,
        "legs": [{"name": short_leg(legs[k][0]),
                  "old": fmt(old_times[k]) if old_times[k] is not None else "",
                  "new": fmt(new_times[k]) if k < last and new_times[k] is not None else "",
                  "changed": k < last and not _same(new_times[k], old_times[k]),
                  "estimate": k in est_ends or (k - 1) in est_ends}
                 for k in range(len(legs))],
        "finish_changed": last == len(legs) and not _same(new_cum[last - 1], cum[-1]),
        "new_total": fmt(new_cum[last - 1]) if last == len(legs) else "",
        "old_total": fmt(cum[-1]) if cum and cum[-1] is not None else "",
    }


def _nearby_chips(loose, tod, reader, window=90):
    if tod is None:
        return []
    hits = [t for t in loose if abs(t["tod"] - tod) <= window and (not reader or t["reader"] == reader)]
    hits.sort(key=lambda t: abs(t["tod"] - tod))
    return [{"chip": t["chip"], "tod": fmt_tod(t["tod"]), "reader": t["reader"], "seq": t["seq"]} for t in hits[:3]]


def _timeline(taps, cum, zero, tod_offset, rec, legs, readers, labels):
    out = []
    for tap in taps:
        if tap["label"].lower() == "start":
            out.append({"seq": tap["seq"], "label": "Start", "tod": fmt_tod(tap["tod"]), "clock": fmt(tap["t"]), "elapsed": "0:00.0",
                        "reader": tap["reader"] or "hand", "used": "Start", "odd_reader": False})
            continue
        t = tap["t"] - zero
        used = next((i for i, c in enumerate(cum) if c is not None and abs(c - t) <= MATCH), None)
        odd = used is not None and readers[used] and tap["reader"] and tap["reader"] != readers[used]
        out.append({"seq": tap["seq"], "label": tap["label"], "tod": fmt_tod(tap["tod"]), "clock": fmt(tap["t"]),
                    "elapsed": fmt(t),
                    "reader": tap["reader"] or "hand",
                    "used": _end_name(legs, used) if used is not None else "not used",
                    "odd_reader": bool(odd)})
    return out
