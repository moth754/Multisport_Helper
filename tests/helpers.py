"""Build small Webscorer-shaped race and fasttaps JSON for tests. All names and bibs are made up."""

from multisport.analysis import fmt, fmt_tod

TRI = ["Swim", "Transition 1", "Bike", "Transition 2", "Run"]
AQUA = ["Swim", "Transition 1", "Run"]
# the reader that records each leg end, and Webscorer's lap label for it
TRI_READERS = ["10.0.0.3", "10.0.0.1", "10.0.0.1", "10.0.0.3", "10.0.0.2"]
TRI_LABELS = ["Lap 1", "Lap 2", "Lap 3", "Lap 4", "Lap 5"]
AQUA_READERS = ["10.0.0.3", "10.0.0.1", "10.0.0.2"]
AQUA_LABELS = ["Lap 1", "Lap 2", "Lap 5"]      # shorter races skip lap numbers
WAVE = 30 * 60.0                               # race clock at the wave start
CLOCK_ZERO = 7 * 3600.0                        # time of day when the race clock read 0


def legs_to_cum(legs):
    out, total = [], 0.0
    for leg in legs:
        total += leg
        out.append(total)
    return out


def racer(bib, name, names, cum, place="1", status_time=None):
    laps, prev = [], 0.0
    for i, (lap_name, c) in enumerate(zip(names, cum)):
        laps.append({
            "LapNumber": i + 1, "LapName": lap_name,
            "LapTime": fmt(c - prev) if c is not None and prev is not None else "-",
            "RaceTime": fmt(c) if c is not None else "-",
        })
        prev = c
    finished = cum and cum[-1] is not None and len(cum) == len(names)
    return {
        "Place": place if finished else "-", "Bib": str(bib), "Name": name,
        "Time": status_time or (fmt(cum[-1]) if finished else ""),
        "StartTime": fmt_tod(CLOCK_ZERO + WAVE), "LapTimes": laps,
    }


def race(distances, name="Test Triathlon"):
    results = []
    for dname, racers in distances.items():
        results.append({"Grouping": {"Distance": dname, "Overall": True, "LapCount": "5"}, "Racers": racers})
        results.append({"Grouping": {"Distance": dname, "Overall": False, "Gender": "Female/Male"}, "Racers": racers})
    return {"RaceInfo": {"RaceId": 1, "Name": name, "Date": "Sep 1, 2026", "CompletionState": "Live"},
            "Results": results}


def taps_for(bib, name, cum, readers, labels, seq_start=1, extra=()):
    """Raw taps for one racer: one per known leg end, plus any (race-time, reader, label) extras."""
    rows = []
    items = [(c, readers[i], labels[i]) for i, c in enumerate(cum) if c is not None] + list(extra)
    for n, (c, reader, label) in enumerate(sorted(items)):
        clock = WAVE + c
        rows.append({
            "Seq #": str(seq_start + n), "Bib": int(bib), "Chip ID": f"{int(bib):04d}", "Name": name, "Tap": label,
            "Time tap": fmt(clock), "Start": fmt(WAVE), "Reader": reader, "Antenna": 1,
            "Time tap (time of day)": fmt_tod(CLOCK_ZERO + clock),
        })
    return rows


def field(count=14, seed_legs=(600, 90, 2400, 60, 1500)):
    """A plain field of clean triathletes with slightly different paces."""
    out, taps = [], []
    for i in range(count):
        pace = 0.85 + i * 0.025
        legs = [seed_legs[0] * pace, seed_legs[1] * (0.8 + (i % 5) * 0.1), seed_legs[2] * pace,
                seed_legs[3] * (0.8 + (i % 4) * 0.15), seed_legs[4] * pace]
        cum = legs_to_cum(legs)
        bib = 100 + i
        out.append(racer(bib, f"Racer {bib}", TRI, cum, place=str(i + 1)))
        taps += taps_for(bib, f"Racer {bib}", cum, TRI_READERS, TRI_LABELS, seq_start=1000 + i * 10)
    return out, taps
