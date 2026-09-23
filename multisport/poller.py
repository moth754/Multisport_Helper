"""In-memory state and the background thread that fetches from Webscorer while Live Update is on."""

import hashlib
import json
import logging
import threading
import time

from . import analysis, webscorer

log = logging.getLogger("multisport")


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.live = False            # always starts OFF
        self.version = 0             # bumped whenever the analysis changes
        self.analysis = None
        self.race_id = ""
        self.fetch = {"at": None, "ok_at": None, "error": None, "taps_error": None}
        self.digest = None
        self.wake = threading.Event()
        self.stopping = threading.Event()

    def status(self):
        with self.lock:
            a = self.analysis
            return {
                "live": self.live,
                "version": self.version,
                "race_id": self.race_id,
                "race": a["race"] if a else None,
                "problems": len(a["problems"]) if a else 0,
                "counts": a["counts"] if a else {},
                "fetch": dict(self.fetch),
            }


class Poller:
    def __init__(self, config, state):
        self.config = config
        self.state = state
        self._fetch_lock = threading.Lock()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, name="poller", daemon=True)
        self._thread.start()

    def _run(self):
        while not self.state.stopping.is_set():
            try:
                if self.state.live and self.config.get("race_id"):
                    self.refresh()
            except Exception:  # never let the loop die
                log.exception("Poller error")
            wait = max(5, int(self.config.get("refresh_seconds") or 20))
            self.state.wake.wait(wait)
            self.state.wake.clear()

    def refresh(self, race_id=None):
        """Fetch results and taps once and re-run the checks. Returns the status dict.

        With a new race_id, the race only switches (and is remembered) once Webscorer answers,
        so a mistyped ID leaves the current race in place.
        """
        with self._fetch_lock:
            switching = race_id is not None and str(race_id) != self.state.race_id
            race_id = str(race_id if race_id is not None else self.config.get("race_id") or "")
            api_id = self.config.get("webscorer_api_id")
            token = self.config.get("webscorer_token")
            now = time.time()
            try:
                race = webscorer.fetch_race(race_id, api_id, token)
            except webscorer.WebscorerError as exc:
                if not switching:  # a failed switch leaves the current race's status alone
                    with self.state.lock:
                        if self.state.fetch["error"] != str(exc):
                            log.warning("Fetch failed: %s", exc)
                        self.state.fetch.update(at=now, error=str(exc))
                raise
            taps, taps_error = None, None
            try:
                taps = webscorer.fetch_taps(race_id, api_id, token)
            except webscorer.WebscorerError as exc:
                taps_error = str(exc)
            digest = hashlib.sha1(json.dumps([race, taps, self.config.thresholds()], sort_keys=True,
                                             default=str).encode()).hexdigest()
            result = None
            if digest != self.state.digest or race_id != self.state.race_id:
                result = analysis.analyse(race, taps, self.config.thresholds())
            if switching:
                self.config.update({"race_id": race_id})
                log.info("Race %s loaded", race_id)
            with self.state.lock:
                if result is not None:
                    self.state.analysis = result
                    self.state.digest = digest
                    self.state.version += 1
                self.state.race_id = race_id
                self.state.fetch.update(at=now, ok_at=now, error=None, taps_error=taps_error)
            return self.state.status()

    def clear(self):
        with self.state.lock:
            self.state.analysis = None
            self.state.digest = None
            self.state.race_id = ""
            self.state.version += 1
            self.state.fetch = {"at": None, "ok_at": None, "error": None, "taps_error": None}
