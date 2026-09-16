#!/usr/bin/env python3
"""Sync Canvas course files + assignments into a local folder tree.

Auth is a session cookie read straight out of a local Chromium profile
(default ~/.canvas-chrome-profile), so no browser has to be running for the
sync itself. When the session expires, log in once in that profile:

    python3 canvas_sync.py --login     # opens the profile browser
    # ...sign in with SSO + 2FA, close the browser...

Data layout (under data_root from config.json):

    2026A/MNE3007-CAD-CAM/lectures/Lecture05.pdf
    2026A/MNE3007-CAD-CAM/assignments.md    <- due dates, points, submitted
    _logs/sync-202609.log                   <- one block per run

Files already on disk with the same size are skipped, so re-running is cheap
and daily automation is safe. Exit codes: 0 = ok (even if nothing new),
3 = Canvas session expired.

Requirements: python3, the `cryptography` package, curl, any Chromium.
"""
import argparse, glob, json, mimetypes, os, re, shutil, sqlite3, subprocess
import sys, tempfile, time
from datetime import datetime

HERE = os.path.dirname(os.path.realpath(__file__))   # realpath: works through symlinks

EXT = {'application/pdf': '.pdf', 'application/zip': '.zip', 'text/html': '.html',
       'image/jpeg': '.jpg',
       'application/vnd.openxmlformats-officedocument.wordprocessingml.document': '.docx'}


def load_config(path):
    with open(path) as f:
        cfg = json.load(f)
    for k in ('host', 'cookie_profile', 'data_root'):
        cfg[k] = os.path.expanduser(cfg[k])
    return cfg


def do_login(cfg):
    """Relaunch the profile browser headed so the user can do SSO + 2FA."""
    pattern = cfg.get('chrome_path') or '~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome'
    cands = sorted(glob.glob(os.path.expanduser(pattern)))
    if not cands:
        sys.exit(f'no chromium matches {pattern} — set "chrome_path" in config.json')
    chrome = cands[-1]
    os.environ.setdefault('DISPLAY', ':0')
    print(f'opening {chrome} — sign in, then close the browser')
    os.execv(chrome, [chrome, f"--user-data-dir={cfg['cookie_profile']}",
                      f"https://{cfg['host']}/"])


def cookie_jar(host, profile):
    """Decrypt the Canvas session cookies out of the Chromium profile.

    Linux Chromium encrypts cookies with a fixed key (peanuts/saltysalt,
    AES-128-CBC); Chrome >= 130 prefixes a 32-byte domain hash on the
    plaintext. The DB is copied (with its WAL) so nothing needs to be locked.
    """
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    key = PBKDF2HMAC(algorithm=hashes.SHA1(), length=16,
                     salt=b'saltysalt', iterations=1).derive(b'peanuts')
    iv = b' ' * 16

    def dec(blob):
        if not blob: return ''
        if blob[:3] not in (b'v10', b'v11'): return blob.decode('utf8', 'replace')
        c = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        p = c.update(blob[3:]) + c.finalize()
        p = p[:-p[-1]] if p and p[-1] <= 16 else p
        try: return p.decode('utf8')
        except UnicodeDecodeError: return p[32:].decode('utf8', 'replace')

    cookies = profile + '/Default/Cookies'
    tmp = tempfile.mkdtemp()
    db = os.path.join(tmp, 'c.db'); shutil.copy(cookies, db)
    for suffix in ('-wal', '-shm'):            # pick up cookies written this session
        if os.path.exists(cookies + suffix): shutil.copy(cookies + suffix, db + suffix)
    domain = '.'.join(host.split('.')[-2:])
    rows = sqlite3.connect(db).execute(
        "SELECT host_key,name,encrypted_value,value,path FROM cookies WHERE host_key LIKE ?",
        (f'%{domain}',)).fetchall()
    jar = os.path.join(tmp, 'cookies.txt')
    with open(jar, 'w') as f:
        f.write('# Netscape HTTP Cookie File\n')
        for h, n, ev, v, path in rows:
            flag = 'TRUE' if h.startswith('.') else 'FALSE'
            f.write(f"{h}\t{flag}\t{path or '/'}\tTRUE\t0\t{n}\t{v or dec(ev)}\n")
    return jar


def api(jar, host, url_or_path):
    """GET one Canvas API page -> (parsed json, next-page url or None)."""
    url = url_or_path if url_or_path.startswith('http') \
        else f"https://{host}/api/v1/{url_or_path}"
    hdr = tempfile.mktemp()
    r = subprocess.run(['curl', '-gsS', '-b', jar, '-L', '-D', hdr,
                        '-H', 'Accept: application/json', url],
                       capture_output=True, text=True)
    body = re.sub(r'^while\(1\);', '', r.stdout)
    try: data = json.loads(body)
    except Exception: data = None
    nxt = None
    try:
        for line in open(hdr, encoding='utf8', errors='replace'):
            m = re.search(r'<([^>]+)>;\s*rel="next"', line, re.I)
            if m: nxt = m.group(1)
    except FileNotFoundError: pass
    return data, nxt


def pages(jar, host, path):
    """Walk every page of a paginated Canvas API endpoint."""
    out, url = [], path
    for _ in range(50):                        # hard cap: 50 pages = 5000 items
        data, url = api(jar, host, url)
        if not isinstance(data, list): break
        out += data
        if not url: break
    return out


def download(jar, url, dest):
    """Fetch to dest.part, rename into place only on HTTP 200."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + '.part'
    r = subprocess.run(['curl', '-gsS', '-b', jar, '-L', '-o', tmp,
                        '-w', '%{http_code}', url], capture_output=True, text=True)
    if r.stdout.strip() == '200':
        os.replace(tmp, dest)
        return True
    if os.path.exists(tmp): os.remove(tmp)
    return False


def route(cfg, name, canvas_folder):
    """Pick the local subfolder for a file: Canvas folder first, then name rules."""
    cf = (canvas_folder or '').replace('course files', '').strip('/').lower()
    if cf in cfg['folder_routes']: return cfg['folder_routes'][cf]
    n = name.lower()
    for pat, dest in cfg['name_routes']:
        if re.search(pat, n): return dest
    return cfg['default_folder']


def sync_files(log, jar, host, cfg, course, cdir):
    folders = {f['id']: f['full_name']
               for f in pages(jar, host, f"courses/{course['id']}/folders?per_page=100")}
    files = pages(jar, host, f"courses/{course['id']}/files?per_page=100")
    if not isinstance(files, list): return 0, 0
    new = fail = 0
    for f in files:
        if not f.get('url'): continue
        name = f['display_name']
        if '.' not in name:
            name += EXT.get(f.get('content-type'),
                            mimetypes.guess_extension(f.get('content-type') or '') or '')
        dest = os.path.join(cdir, route(cfg, name, folders.get(f['folder_id'])))
        sz = f.get('size')
        if sz is not None and os.path.exists(dest) and os.path.getsize(dest) == sz:
            continue
        rel = os.path.relpath(dest, cdir)
        if download(jar, f['url'], dest):
            log(f"NEW  {os.path.basename(cdir)}/{rel}"); new += 1
        else:
            log(f"FAIL {os.path.basename(cdir)}/{rel}"); fail += 1
    return new, fail


def sync_assignments(log, jar, host, course, cdir, state):
    cid = course['id']
    items = pages(jar, host, f'courses/{cid}/assignments?per_page=100&include[]=submission')
    if not isinstance(items, list): return 0
    now = datetime.now().astimezone()
    known = state.setdefault(f'asg-{cid}', {})
    changes = 0
    rows = []
    for a in sorted(items, key=lambda a: (a.get('due_at') or '9999-12-31', a.get('name') or '')):
        name = a.get('name') or '(untitled)'
        due = a.get('due_at')
        aid = str(a['id'])
        if aid not in known:
            log(f'NEW  assignment [{os.path.basename(cdir)}] {name}'); changes += 1
        elif known[aid] != due:
            log(f'MOVED due date [{os.path.basename(cdir)}] {name}: '
                f'{known[aid] or "none"} -> {due or "none"}'); changes += 1
        known[aid] = due
        submitted = bool((a.get('submission') or {}).get('submitted_at'))
        if due:
            dt = datetime.fromisoformat(due.replace('Z', '+00:00')).astimezone()
            due_s = dt.strftime('%a %d %b %H:%M')
            s = (dt - now).total_seconds()
            if submitted: st = '✅ submitted'
            elif s < 0:   st = f'⚠️ overdue {int(-s // 86400) or 1}d'
            elif s >= 86400: st = f'⏳ {int(s // 86400)}d'
            else:         st = f'⏳ {int(s // 3600)}h'
        else:
            due_s = st = '—'
        pts = a.get('points_possible')
        pts = f'{pts:g}' if pts is not None else '—'
        link = a.get('html_url') or ''
        nm = f'[{name}]({link})' if link else name
        rows.append(f'| {due_s} | {nm} | {pts} | {st} |')
    md = [f'# {os.path.basename(cdir)} — assignments', '',
          f'_synced {time.strftime("%Y-%m-%d %H:%M")}_', '',
          '| Due | Assignment | Points | Status |', '|---|---|---|---|'] + rows + ['']
    tmp = os.path.join(cdir, 'assignments.md.tmp')
    os.makedirs(cdir, exist_ok=True)
    with open(tmp, 'w') as f: f.write('\n'.join(md))
    os.replace(tmp, os.path.join(cdir, 'assignments.md'))
    return changes


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--config', default=os.path.join(HERE, 'config.json'))
    p.add_argument('--login', action='store_true', help='open the profile browser to re-login')
    p.add_argument('--list-courses', action='store_true',
                   help='print active courses + term ids to help fill config.json')
    args = p.parse_args()

    cfg = load_config(args.config)
    host = cfg['host']
    if args.login: do_login(cfg)

    jar = cookie_jar(host, cfg['cookie_profile'])
    if args.list_courses:
        for c in pages(jar, host, 'courses?enrollment_state=active&per_page=100'):
            print(f"{c.get('enrollment_term_id')}\t{c['id']}\t"
                  f"{re.sub(r'^.*/', '', c.get('course_code', ''))[:12]:12}\t{c.get('name')}")
        return

    courses = pages(jar, host, 'courses?enrollment_state=active&per_page=100')
    if not isinstance(courses, list) or not courses:
        print('Canvas session expired — run: python3 canvas_sync.py --login')
        sys.exit(3)

    root = cfg['data_root']
    os.makedirs(os.path.join(root, '_logs'), exist_ok=True)
    logpath = os.path.join(root, '_logs', time.strftime('sync-%Y%m.log'))

    def log(msg=''):
        print(msg)
        with open(logpath, 'a') as f: f.write(msg + '\n')

    state_path = os.path.join(root, '.sync-state.json')
    state = json.load(open(state_path)) if os.path.exists(state_path) else {}

    log(f'== {time.strftime("%Y-%m-%d %H:%M")} ==')
    new = fail = chg = 0
    for term in cfg['terms']:
        for c in courses:
            if c.get('enrollment_term_id') != term['id']: continue
            code = re.sub(r'^\d+', '', c.get('course_code', ''))
            code = re.sub(r'^.*/', '', code)          # strip "SUBJ/2026-27" prefixes
            folder = term['courses'].get(code)
            if not folder:
                log(f'!! course not in config.json: {code} — {c.get("name")} '
                    f'(filing under {code})')
                folder = code or f"course-{c['id']}"
            cdir = os.path.join(root, term['dir'], folder)
            n, fl = sync_files(log, jar, host, cfg, c, cdir)
            ch = sync_assignments(log, jar, host, c, cdir, state)
            new, fail, chg = new + n, fail + fl, chg + ch
    json.dump(state, open(state_path, 'w'), indent=1, sort_keys=True)
    log(f'-- {new} new file(s), {chg} assignment change(s), {fail} failure(s)')


if __name__ == '__main__':
    main()
