# Multisport Helper

A web page for the timing team at duathlons and triathlons that are timed on [Webscorer](https://www.webscorer.com).
It finds results where a chip scan was missed or a tap landed on the wrong leg, and suggests the times to key into Webscorer.

For example, a racer whose T2 exit wasn't read ends up with a 21-minute T2 and a 23-second run.
The helper spots this, says the T2 exit tap is missing, and estimates it at about 9:12:54.
It also points to any unassigned chip read on the T2 reader around that time.

It is for use on the day only. It stores the Webscorer API details and the last race ID, and nothing about the race or the racers.
It never sends texts and never changes anything in Webscorer. Webscorer's API is read-only, so the team makes the fixes there.

## What it checks

Each distance is checked with its own legs, taken from Webscorer's split names.
An aquathlon (swim, T1, run) and a triathlon (swim, T1, bike, T2, run) in the same event are checked separately, whatever their lap numbers.

- **Can't be right** means one of these:
  - a transition is longer than the swim, bike or run next to it,
  - a swim, bike or run is under 40% of the usual time,
  - a transition is under 15% of the usual time.
- **Worth a look** means one of these:
  - a transition is over 3× the usual time and more than 3 minutes over it,
  - a split is missing,
  - the taps fit the racer's other legs much better another way.

"Usual" is the middle (median) time for that leg in that distance. The thresholds can be changed on the Settings tab.

## How it suggests a fix

It reads all of a racer's raw taps from Webscorer's `fasttaps` feed and tries every way of matching them to the leg ends. It then picks the reading that best fits:

- the usual leg times, scaled to the racer's own pace,
- the reader each leg end is normally recorded on,
- the fewest changes.

A missed tap is estimated by splitting the gap between the taps either side in proportion to the usual leg times.
When two readings are close, for example "the bike end was missed" against "the T2 exit was missed", both are shown.
It never suggests deleting a chip read that came from the right reader just because a leg was slow.

Once a fix is keyed into Webscorer, the next refresh sees the corrected splits and the problem disappears.

## Install (Raspberry Pi OS, Debian, Ubuntu)

```
git clone <this repository> ~/Multisport_Helper
cd ~/Multisport_Helper
./install.sh
```

This runs it as the `multisport` service on port **8081**, and it starts at boot.
It can run alongside the Race Results app on 8080.

| Command | What it does |
|---|---|
| `./install.sh --port 8091` | Uses another port |
| `./install.sh --no-service` | Sets up Python only; start it with `./run.sh` |
| `./install.sh --uninstall` | Removes the service and keeps your settings |

Update with `git pull && ./install.sh`. Logs: `journalctl -u multisport -f`.

## First-time setup

1. Open `http://<computer name>.local:8081`.
2. On **Settings**, enter the Webscorer API ID, plus the API private key (apipriv) if your account uses one, then choose **Save settings**.
3. Enter the race ID at the top, which is the number after `raceid=` in the results link, and choose **Test**.

## On the day

1. Enter the race ID at the top and choose **Load**.
2. Turn **Live Update** on. It fetches results and taps every 20 seconds by default.
3. Work through the **Problems** tab. Each card shows the splits now and the suggested splits, what to change in Webscorer, and the racer's raw taps.
4. Choose **Done** when a racer is fixed or checked. The card comes back if their times change.
5. The **All finishers** tab shows every racer's legs, with suspect legs highlighted.

A page password can be set on the Settings tab. Without one, anyone on the same network can open the page.

## Security

The Webscorer details and the page password hash live in `data/config.json` (mode 0600).
That file is git-ignored, and the pre-commit hook in `.githooks/` refuses commits that include it.
The API key is never sent back to the browser.

## Development

```
.venv/bin/python -m unittest discover -s tests -t .
```

The tests build small made-up races, so no real names or credentials are involved.
To try the app against saved data, set `MULTISPORT_WEBSCORER_URL` to a local server that answers `/race` and `/fasttaps` with Webscorer-style JSON.
