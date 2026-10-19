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

Auth is a Canvas **personal access token** (Account → Settings → Approved
Integrations → **+ New Access Token**), sent as `Authorization: Bearer <token>`
to the ordinary Canvas REST API (`/api/v1/courses`, `/files`, `/folders`,
`/assignments`). The token is read from `$CANVAS_TOKEN` or
`~/.config/canvas-sync/token` (path overridable via `token_file` in
`config.json`), never from `config.json` itself:

```bash
mkdir -p ~/.config/canvas-sync
(umask 077; read -rsp 'Token: ' t && printf %s "$t" > ~/.config/canvas-sync/token)
```

**Fallback:** if there's no token or Canvas rejects it, the script reads the
session cookie straight out of a dedicated local Chromium profile
(`~/.canvas-chrome-profile`), decrypted with Chromium's fixed Linux key. Log
into that profile once with `python3 canvas_sync.py --login` (SSO + 2FA, then
close the browser).

If neither works, the run writes `!! AUTH FAILED` to the log and exits with
code 3. Renew the token (or `--login`) and re-run.

## Setup

python3, `curl`, and a Canvas access token. For the cookie fallback only:
`cryptography` (`pip install cryptography` or `apt install
python3-cryptography`) and any Chromium.

```bash
git clone https://github.com/michaelbasarah/canvas-sync && cd canvas-sync
# save your Canvas access token to ~/.config/canvas-sync/token (see above)
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
(default `~/personal/learning`), which is not a git repo. No tokens or
cookies ever enter the repo either: the access token lives in
`~/.config/canvas-sync/token` (mode 600), cookies inside the browser profile,
and `.gitignore` blocks token-looking files as a backstop.

## Also here

- `pdftext.py` — tiny pure-Python PDF text extractor for reading the decks
  on machines with no poppler. Handles the subset-font ToUnicode CMaps that
  turn lecture PDFs into garbage otherwise. `python3 pdftext.py file.pdf`

## License

MIT — see [LICENSE](LICENSE).
