import unittest

from multisport import analysis as A
from tests import helpers as H


def find(result, bib):
    return next((p for p in result["problems"] if p["bib"] == str(bib)), None)


def ends(problem):
    return {e["end"].split(" · ")[-1]: e for e in problem["suggestion"]["ends"]}


class TimeParsing(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(A.parse_time("1:02:03.4"), 3723.4)
        self.assertEqual(A.parse_time("5:56.8"), 356.8)
        self.assertEqual(A.parse_time("23.1"), 23.1)
        self.assertEqual(A.parse_time("08:32:36.200"), 30756.2)
        for blank in ("-", "", None, "DNF"):
            self.assertIsNone(A.parse_time(blank))

    def test_format_round_trip(self):
        self.assertEqual(A.fmt(3723.4), "1:02:03.4")
        self.assertEqual(A.fmt(59.96), "1:00.0")
        self.assertEqual(A.fmt_tod(33185.9), "9:13:05.9")

    def test_transition_names(self):
        self.assertTrue(A.is_transition("Transition 2"))
        self.assertTrue(A.is_transition("T1"))
        self.assertFalse(A.is_transition("Run from pool"))
        self.assertEqual(A.short_leg("Transition 2"), "T2")


class CleanField(unittest.TestCase):
    def test_no_false_alarms(self):
        racers, taps = H.field()
        result = A.analyse(H.race({"Sprint": racers}), {"FastTaps": taps})
        self.assertEqual(result["problems"], [])
        dist = result["distances"][0]
        self.assertEqual([l["short"] for l in dist["legs"]], ["Swim", "T1", "Bike", "T2", "Run"])
        self.assertEqual(dist["finishers"], 14)


class MissedT2Exit(unittest.TestCase):
    """T2 exit missed, and the finish reader's read became 'Lap 4', leaving a long T2 and a tiny run."""

    def setUp(self):
        racers, taps = H.field()
        true_legs = [560, 85, 2300, 57, 1400]
        true_cum = H.legs_to_cum(true_legs)
        # Webscorer: a finish-area chip read 23 s before a hand finish tap, T2 exit never read
        shown = true_cum[:3] + [true_cum[4], true_cum[4] + 23]
        racers.append(H.racer(900, "Sam Example", H.TRI, shown))
        taps += H.taps_for(900, "Sam Example", true_cum[:3], H.TRI_READERS, H.TRI_LABELS, seq_start=5000,
                           extra=[(true_cum[4], "10.0.0.2", "Lap 4"), (true_cum[4] + 23, "", "Lap 5")])
        self.true_cum = true_cum
        self.result = A.analyse(H.race({"Sprint": racers}), {"FastTaps": taps})

    def test_flagged_as_error(self):
        p = find(self.result, 900)
        self.assertIsNotNone(p)
        self.assertEqual(p["level"], "error")
        self.assertTrue(any("T2" in r and "longer than the run" in r for r in p["reasons"]))
        self.assertEqual(p["bad_legs"], [3, 4])

    def test_suggests_the_missing_t2_exit(self):
        p = find(self.result, 900)
        e = ends(p)
        self.assertEqual(e["T2 exit"]["kind"], "estimate")
        self.assertTrue(e["T2 exit"]["end"].startswith("Lap 4"))
        est = A.parse_time(e["T2 exit"]["new_elapsed"].replace("≈ ", ""))
        self.assertLess(abs(est - self.true_cum[3]), 60)  # within a minute of the real crossing
        self.assertEqual(e["Bike end"]["kind"], "same")
        # race clock (Webscorer's "Time tap") = wave start on the race clock + time since start
        clock = A.parse_time(e["T2 exit"]["new_clock"].replace("≈ ", ""))
        self.assertAlmostEqual(clock, H.WAVE + est, delta=1)  # the estimate is shown to whole seconds
        self.assertEqual(e["Bike end"]["now_clock"], A.fmt(H.WAVE + self.true_cum[2]))
        lap = A.parse_time(e["T2 exit"]["new_lap"].replace("≈ ", ""))
        self.assertLess(abs(lap - 57), 60)  # the T2 duration the new tap gives
        self.assertTrue(p["suggestion"]["remove"][0]["clock"])
        # the early finish-area read is listed for deletion
        self.assertEqual(len(p["suggestion"]["remove"]), 1)
        self.assertIn("second read", p["suggestion"]["remove"][0]["why"])

    def test_timeline_marks_the_odd_reader(self):
        p = find(self.result, 900)
        lap4 = next(t for t in p["timeline"] if t["label"] == "Lap 4")
        self.assertTrue(lap4["odd_reader"])


class MissedT1ExitShiftsEverything(unittest.TestCase):
    """T1 exit missed and every later tap moved up one leg: T1 looks like the bike."""

    def test_shift_is_undone(self):
        racers, taps = H.field()
        true = H.legs_to_cum([620, 95, 2500, 65, 1550])
        shown = [true[0], true[2], true[3], true[4], true[4] + 0.1]
        racers.append(H.racer(901, "Alex Sample", H.TRI, shown))
        taps += H.taps_for(901, "Alex Sample", [true[0], None, true[2], true[3], true[4]],
                           H.TRI_READERS, H.TRI_LABELS, seq_start=6000)
        result = A.analyse(H.race({"Sprint": racers}), {"FastTaps": taps})
        p = find(result, 901)
        self.assertEqual(p["level"], "error")
        new = [A.parse_time(e["new_elapsed"].replace("≈ ", "")) for e in p["suggestion"]["ends"]]
        for got, want in zip(new, true):
            self.assertLess(abs(got - want), 60)
        self.assertEqual(ends(p)["T1 exit"]["kind"], "estimate")


class ReaderDecidesWhichTapWasMissed(unittest.TestCase):
    """Bike end or T2 exit? The one tap there came from the T2-exit reader, so the bike end was missed."""

    def test_reader_evidence(self):
        racers, taps = H.field()
        true = H.legs_to_cum([600, 90, 2400, 70, 1500])
        shown = [true[0], true[1], None, true[3], true[4]]
        racers.append(H.racer(902, "Jo Test", H.TRI, shown))
        taps += H.taps_for(902, "Jo Test", shown, H.TRI_READERS, H.TRI_LABELS, seq_start=7000)
        result = A.analyse(H.race({"Sprint": racers}), {"FastTaps": taps})
        p = find(result, 902)
        self.assertIsNotNone(p)
        e = ends(p)
        self.assertEqual(e["Bike end"]["kind"], "estimate")
        self.assertEqual(e["T2 exit"]["kind"], "same")


class MixedDistances(unittest.TestCase):
    """An aquathlon (3 legs, taps Lap 1, Lap 2, Lap 5) checked alongside a triathlon in one event."""

    def test_each_distance_uses_its_own_legs(self):
        tri, taps = H.field()
        aqua = []
        for i in range(8):
            cum = H.legs_to_cum([600 + i * 20, 60 + i * 3, 1500 + i * 40])
            aqua.append(H.racer(300 + i, f"Aqua {i}", H.AQUA, cum, place=str(i + 1)))
            taps += H.taps_for(300 + i, f"Aqua {i}", cum, H.AQUA_READERS, H.AQUA_LABELS, seq_start=8000 + i * 10)
        true = H.legs_to_cum([640, 70, 1580])
        shown = [true[2], true[2] + 0.2, true[2] + 0.4]  # all three taps at the finish
        aqua.append(H.racer(399, "Pat Placeholder", H.AQUA, shown, place="9"))
        result = A.analyse(H.race({"Sprint": tri, "Aquathlon": aqua}), {"FastTaps": taps})
        names = {d["name"]: [l["short"] for l in d["legs"]] for d in result["distances"]}
        self.assertEqual(names["Aquathlon"], ["Swim", "T1", "Run"])
        self.assertEqual(names["Sprint"], ["Swim", "T1", "Bike", "T2", "Run"])
        p = find(result, 399)
        self.assertIsNotNone(p)
        self.assertEqual(p["distance"], "Aquathlon")
        self.assertEqual(len(p["legs"]), 3)
        self.assertFalse(any(q["distance"] == "Sprint" for q in result["problems"]))


class MissingSplitsAndStatuses(unittest.TestCase):
    def test_missing_split_is_reported(self):
        racers, taps = H.field()
        true = H.legs_to_cum([600, 90, 2400, 70, 1500])
        racers.append(H.racer(903, "Lee Dummy", H.TRI, [true[0], None, true[2], true[3], true[4]]))
        result = A.analyse(H.race({"Sprint": racers}), None)  # works without raw taps too
        p = find(result, 903)
        self.assertIsNotNone(p)
        self.assertTrue(any("T1 exit" in r for r in p["reasons"]))
        self.assertFalse(result["taps_available"])

    def test_widespread_missing_split_is_grouped(self):
        racers, taps = H.field()
        for i in range(6):
            true = H.legs_to_cum([610 + i, 90, 2400, 70, 1500])
            racers.append(H.racer(950 + i, f"Missing {i}", H.TRI, [None, true[1], true[2], true[3], true[4]]))
        result = A.analyse(H.race({"Sprint": racers}), {"FastTaps": taps})
        grouped = [p for p in result["problems"] if p.get("grouped")]
        self.assertEqual(len(grouped), 6)
        self.assertTrue(result["distances"][0]["notes"])

    def test_dns_and_dnf_are_ignored(self):
        racers, taps = H.field()
        racers.append({"Place": "-", "Bib": "904", "Name": "Not Started", "Time": "DNS", "LapTimes": []})
        racers.append({"Place": "-", "Bib": "905", "Name": "Stopped", "Time": "DNF", "LapTimes": []})
        result = A.analyse(H.race({"Sprint": racers}), {"FastTaps": taps})
        self.assertEqual(result["problems"], [])
        self.assertEqual(result["counts"].get("DNS"), 1)

    def test_on_course_long_t1_is_caught(self):
        racers, taps = H.field()
        true = H.legs_to_cum([600, 90, 2400])
        # still on course: T1 exit missed, so "T1" runs from swim end to bike end
        racers.append(H.racer(906, "Kim Course", H.TRI, [true[0], true[2], None, None, None]))
        taps += H.taps_for(906, "Kim Course", [true[0], None, true[2]], H.TRI_READERS, H.TRI_LABELS, seq_start=9000)
        result = A.analyse(H.race({"Sprint": racers}), {"FastTaps": taps})
        p = find(result, 906)
        self.assertIsNotNone(p)
        self.assertEqual(p["status"], "on course")
        self.assertEqual(ends(p)["T1 exit"]["kind"], "estimate")

    def test_long_transition_with_good_taps_is_only_worth_a_look(self):
        racers, taps = H.field()
        true = H.legs_to_cum([600, 90 * 3.5, 2400, 70, 1500])  # T1 over 5 minutes, every tap on the right reader
        racers.append(H.racer(907, "Slow Transition", H.TRI, true))
        taps += H.taps_for(907, "Slow Transition", true, H.TRI_READERS, H.TRI_LABELS, seq_start=9100)
        p = find(A.analyse(H.race({"Sprint": racers}), {"FastTaps": taps}), 907)
        self.assertEqual(p["level"], "check")
        self.assertIsNone(p["suggestion"])  # never suggest deleting a genuine read
        self.assertTrue(any("genuinely slow" in r for r in p["reasons"]))

    def test_thresholds_can_be_changed(self):
        racers, taps = H.field()
        true = H.legs_to_cum([600, 90 * 2.6, 2400, 70, 1500])  # T1 about 4 minutes
        racers.append(H.racer(908, "Steady Transition", H.TRI, true))
        taps += H.taps_for(908, "Steady Transition", true, H.TRI_READERS, H.TRI_LABELS, seq_start=9200)
        default = A.analyse(H.race({"Sprint": racers}), {"FastTaps": taps})
        self.assertIsNone(find(default, 908))
        strict = A.analyse(H.race({"Sprint": racers}), {"FastTaps": taps},
                           {"transition_multiple": 2, "transition_extra_min": 1})
        self.assertIsNotNone(find(strict, 908))

if __name__ == "__main__":
    unittest.main()
