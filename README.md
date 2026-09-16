# canvas-sync

Nightly local archive of my Canvas courses — lecture files, labs, assignments and
due dates — organized on disk, so everything can be read, searched and kept long
after the semester ends and Canvas access expires.

Built against CityUHK's Canvas; any other Canvas host works by changing `host`
in `config.json`.

## What a run does

- Pulls **course files** into `<term>/<course>/{lectures,labs,admin,project,reference}/`,
  routing each file by its Canvas folder first, then filename heuristics — both
  rule sets live in `config.json`, no code edits to retune.
- Rebuilds `<course>/assignments.md`: due date, points, ✅ submitted / ⏳ days
  left / ⚠️ overdue, sorted by due date. New assignments and **moved due dates**
  are called out in the log.
- Appends a block to `_logs/sync-<month>.log` — every new file, every failure,
  and a summary line even when nothing changed, so a daily schedule proves it ran.

```
2026A/MNE3007-CAD-CAM/
├── lectures/       Lecture01.pdf …
├── labs/
├── admin/          syllabus, course site
├── reference/
├── notes/          mine — the sync never touches these
└── assignments.md
```

Runs are idempotent: a file already on disk with the same size is skipped, so
re-running is cheap and scheduling it daily is safe.

## How it works

No API token needed. The script reads the Canvas session cookie straight out of
a dedicated local Chromium profile (`~/.canvas-chrome-profile`), decrypts it
with Chromium's fixed Linux key, and talks to the ordinary Canvas REST API
(`/api/v1/courses`, `/files`, `/folders`, `/assignments`). That profile exists
only for Canvas — log into it once and the cookie lasts a few weeks.

When it expires, the run exits with code 3 and the log says so:

```bash
python3 canvas_sync.py --login    # opens the profile browser
                                  # sign in (SSO + 2FA), close it, done
```

## Setup

python3, `cryptography` (`pip install cryptography` or `apt install
python3-cryptography`), `curl`, and any Chromium.

```bash
git clone https://github.com/michaelbasarah/canvas-sync && cd canvas-sync
python3 canvas_sync.py --login          # once — sign in, close the browser
python3 canvas_sync.py --list-courses   # term ids + course codes, for config.json
$EDITOR config.json                     # host, term, course -> folder names
python3 canvas_sync.py
```

## Running daily

Linux / macOS cron:

```
30 18 * * * cd /path/to/canvas-sync && /usr/bin/python3 canvas_sync.py
```

WSL2 (this repo's home): Windows owns the clock, so use Task Scheduler —
from PowerShell, one time:

```powershell
Register-ScheduledTask -TaskName CanvasSync `
  -Trigger (New-ScheduledTaskTrigger -Daily -At 18:30) `
  -Settings (New-ScheduledTaskSettingsSet -StartWhenAvailable) `
  -Action (New-ScheduledTaskAction -Execute wsl.exe `
    -Argument '-d Ubuntu -- /usr/bin/python3 /home/me/canvas-sync/canvas_sync.py')
```

`-StartWhenAvailable` is the important bit on a laptop: if the machine is off
or asleep at 18:30, the run happens at the next boot instead of being skipped.

## What's deliberately not in this repo

The synced materials. Lecture slides and assignment briefs belong to the
lecturers, so this repo ships the tool only — downloads land in `data_root`
(default `~/personal/learning`), which is not a git repo. No cookies or
session tokens ever enter the repo either; they stay inside the browser
profile.

## Also here

- `pdftext.py` — tiny pure-Python PDF text extractor for reading the decks
  on machines with no poppler. Handles the subset-font ToUnicode CMaps that
  turn lecture PDFs into garbage otherwise. `python3 pdftext.py file.pdf`

## License

MIT — see [LICENSE](LICENSE).
