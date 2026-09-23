"""Flask app: the timing-team page and its JSON API."""

import gzip
import logging
import os
import secrets
import time
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

from . import __version__, webscorer
from .config import Config, ConfigError
from .poller import Poller, State

log = logging.getLogger("multisport")
CSRF_HEADER = "multisport"


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _secret_key(data_dir):
    path = data_dir / "secret_key"
    if not path.exists():
        path.write_text(secrets.token_hex(32))
        path.chmod(0o600)
    return path.read_text().strip()


def _body():
    return request.get_json(silent=True) or {}


def default_data_dir():
    return Path(os.environ.get("MULTISPORT_DATA") or Path(__file__).resolve().parent.parent / "data")


def create_app(data_dir, start_poller=True):
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    try:
        data_dir.chmod(0o700)
    except OSError:
        pass
    config = Config(data_dir / "config.json")
    state = State()
    poller = Poller(config, state)

    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=_secret_key(data_dir),
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_HTTPONLY=True,
        PERMANENT_SESSION_LENGTH=60 * 60 * 24 * 30,
    )
    app.extensions["multisport"] = {"config": config, "state": state, "poller": poller}
    boot = secrets.token_hex(3)

    # ------------------------------------------------------------ guards
    @app.before_request
    def guard():
        if request.endpoint in ("login", "static"):
            return None
        password_hash = config.get("ui_password_hash")
        if password_hash and session.get("pw") != password_hash[-16:]:
            if request.path.startswith("/api/"):
                return jsonify(error="Please log in again"), 401
            return redirect(url_for("login", next=request.path))
        # cross-site pages can't set this header
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("X-Requested-With") != CSRF_HEADER:
            return jsonify(error="Request refused"), 403
        return None

    @app.after_request
    def finish(response):
        if request.path.startswith("/api/") or request.path == "/":
            response.headers["Cache-Control"] = "no-store"
        if (response.status_code == 200 and not response.direct_passthrough
                and response.mimetype in ("application/json", "text/html")
                and "gzip" in request.headers.get("Accept-Encoding", "")
                and "Content-Encoding" not in response.headers
                and (response.content_length or 0) > 4096):
            response.set_data(gzip.compress(response.get_data(), compresslevel=5))
            response.headers["Content-Encoding"] = "gzip"
            response.headers["Vary"] = "Accept-Encoding"
        return response

    @app.errorhandler(ApiError)
    def api_error(exc):
        return jsonify(error=str(exc)), exc.status

    for error_class in (ConfigError, webscorer.WebscorerError):
        app.register_error_handler(error_class, lambda exc: (jsonify(error=str(exc)), 400))

    @app.errorhandler(Exception)
    def unexpected(exc):
        if isinstance(exc, HTTPException):
            return exc
        log.exception("Unhandled error on %s", request.path)
        return jsonify(error=f"Internal error: {type(exc).__name__}"), 500

    # ------------------------------------------------------------ pages
    @app.get("/")
    def index():
        return render_template("index.html", version=__version__, boot=boot)

    @app.route("/login", methods=["GET", "POST"])
    def login():
        password_hash = config.get("ui_password_hash")
        if not password_hash:
            return redirect("/")
        error = None
        if request.method == "POST":
            if check_password_hash(password_hash, request.form.get("password", "")):
                session.clear()
                session.permanent = True
                session["pw"] = password_hash[-16:]
                target = request.args.get("next") or "/"
                return redirect(target if target.startswith("/") and not target.startswith("//") else "/")
            time.sleep(1)
            error = "Wrong password"
        return render_template("login.html", error=error)

    @app.get("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    # ------------------------------------------------------------ API
    @app.get("/api/status")
    def status():
        st = state.status()
        st["race_id"] = st["race_id"] or config.get("race_id")  # remembered across restarts
        st["now"] = time.time()
        st["configured"] = bool(config.get("webscorer_api_id"))
        st["auth"] = bool(config.get("ui_password_hash"))
        st["app_version"] = __version__
        return jsonify(st)

    @app.get("/api/analysis")
    def get_analysis():
        with state.lock:
            return jsonify(version=state.version, analysis=state.analysis)

    @app.post("/api/live")
    def set_live():
        on = bool(_body().get("on"))
        if on and not config.get("race_id"):
            raise ApiError("Enter a race ID and choose Load first")
        if on and not config.get("webscorer_api_id"):
            raise ApiError("Add the Webscorer API ID on the Settings tab first")
        with state.lock:
            state.live = on
        log.info("Live update %s", "ON" if on else "OFF")
        state.wake.set()
        return jsonify(state.status())

    @app.post("/api/race")
    def set_race():
        race_id = str(_body().get("race_id") or "").strip()
        if not race_id:
            raise ApiError("Enter the Webscorer race ID")
        if not race_id.isdigit():
            raise ApiError("The race ID is the number in the Webscorer results link (raceid=…)")
        return jsonify(poller.refresh(race_id))

    @app.post("/api/refresh")
    def refresh():
        if not config.get("race_id"):
            raise ApiError("Enter a race ID and choose Load first")
        return jsonify(poller.refresh())

    @app.get("/api/settings")
    def get_settings():
        return jsonify(config.public())

    @app.put("/api/settings")
    def put_settings():
        values = _body().get("values") or {}
        values = {k: v for k, v in values.items() if k in config.public() or k == "webscorer_token"}
        if "webscorer_token" in values and not str(values["webscorer_token"]).strip():
            values.pop("webscorer_token")  # blank means "keep the saved one"
        values.pop("race_id", None)
        config.update(values)
        with state.lock:
            state.digest = None  # thresholds may have changed: re-check on the next fetch
        state.wake.set()
        return jsonify(config.public())

    @app.post("/api/settings/clear-token")
    def clear_token():
        config.update({"webscorer_token": ""})
        return jsonify(config.public())

    @app.post("/api/settings/test")
    def test_settings():
        body = _body()
        race_id = str(body.get("race_id") or config.get("race_id") or "").strip()
        if not race_id:
            raise ApiError("Enter a race ID at the top to test with")
        data = webscorer.fetch_race(race_id, config.get("webscorer_api_id"), config.get("webscorer_token"))
        info = data.get("RaceInfo") or {}
        taps_ok = True
        try:
            webscorer.fetch_taps(race_id, config.get("webscorer_api_id"), config.get("webscorer_token"))
        except webscorer.WebscorerError:
            taps_ok = False
        return jsonify(name=info.get("Name") or "", date=info.get("Date") or "", taps=taps_ok)

    @app.post("/api/settings/password")
    def set_password():
        password = str(_body().get("password") or "")
        if password and len(password) < 4:
            raise ApiError("Use at least 4 characters")
        new_hash = generate_password_hash(password) if password else ""
        config.update({"ui_password_hash": new_hash})
        session.clear()
        if new_hash:
            session.permanent = True
            session["pw"] = new_hash[-16:]
        return jsonify(password_set=bool(new_hash))

    if start_poller:
        poller.start()
    return app
