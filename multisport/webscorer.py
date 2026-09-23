"""Webscorer JSON API: race results (with lap splits) and the raw timing taps."""

import os

import requests

BASE_URL = os.environ.get("MULTISPORT_WEBSCORER_URL", "https://www.webscorer.com/json").rstrip("/")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; MultisportHelper/1.0)",
    "Accept": "application/json",
    "Referer": "https://www.webscorer.com/",
}


class WebscorerError(Exception):
    pass


def _get(endpoint, race_id, api_id, token, timeout):
    """GET one Webscorer JSON endpoint. Error messages never include the URL (it carries the token)."""
    if not api_id:
        raise WebscorerError("Webscorer API ID is not set (Settings tab)")
    if not race_id:
        raise WebscorerError("Enter the Webscorer race ID first")
    params = {"raceid": race_id, "apiid": api_id}
    if token:
        params["apipriv"] = token
    try:
        response = requests.get(f"{BASE_URL}/{endpoint}", params=params, headers=HEADERS, timeout=timeout)
    except requests.Timeout:
        raise WebscorerError("Webscorer did not respond in time") from None
    except requests.ConnectionError:
        raise WebscorerError("Can't reach Webscorer: check the internet connection") from None
    except requests.RequestException as exc:
        raise WebscorerError(f"Webscorer request failed ({type(exc).__name__})") from None
    if response.status_code != 200:
        raise WebscorerError(f"Webscorer returned HTTP {response.status_code}")
    try:
        data = response.json()
    except ValueError:
        raise WebscorerError("Webscorer returned something that isn't JSON") from None
    if isinstance(data, dict) and data.get("Error"):
        raise WebscorerError(f"Webscorer: {data['Error']}")
    return data


def fetch_race(race_id, api_id, token, timeout=20):
    data = _get("race", race_id, api_id, token, timeout)
    if not isinstance(data, dict) or not isinstance(data.get("Results"), list):
        raise WebscorerError("Unexpected response from Webscorer")
    return data


def fetch_taps(race_id, api_id, token, timeout=20):
    data = _get("fasttaps", race_id, api_id, token, timeout)
    if isinstance(data, dict) and isinstance(data.get("FastTaps"), list):
        return data
    if isinstance(data, list):
        return {"FastTaps": data}
    raise WebscorerError("Unexpected taps response from Webscorer")
