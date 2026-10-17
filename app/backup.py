"""
Optional backup of saved rounds to a file in a GitHub repository, so rounds
survive redeploys on hosts whose disk is wiped (e.g. Render without a
persistent disk).

Enabled when BACKUP_GITHUB_TOKEN and BACKUP_GITHUB_REPO are set. Use a
separate *private* repository: the app's own repo is public, and pushing to
the branch Render deploys from would trigger a redeploy on every save.

- On startup, if the database has no rounds, rounds are restored from the file.
- After a round is saved, rounds in the file that aren't local are merged in
  first, then the combined set is written back. A failed restore can therefore
  never cause a backup to overwrite the file with fewer rounds.
- Every write is a commit, so the repository history keeps every version.
"""
import base64
import json
import logging

import requests
from flask import current_app

from app.rounds import export_rounds, import_rounds

log = logging.getLogger(__name__)
API = "https://api.github.com"
TIMEOUT = 10


class _Config:
    def __init__(self, cfg):
        self.token = cfg.get("BACKUP_GITHUB_TOKEN")
        self.repo = cfg.get("BACKUP_GITHUB_REPO")
        self.branch = cfg.get("BACKUP_GITHUB_BRANCH") or "main"
        self.path = cfg.get("BACKUP_GITHUB_PATH") or "golf-tracker/rounds.json"

    @property
    def enabled(self):
        return bool(self.token and self.repo)

    @property
    def url(self):
        return f"{API}/repos/{self.repo}/contents/{self.path}"

    def headers(self, raw=False):
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }


def _config():
    return _Config(current_app.config)


def _fetch(cfg):
    """Return (data, sha) of the backup file, or (None, None) if it doesn't exist yet."""
    meta = requests.get(cfg.url, headers=cfg.headers(), params={"ref": cfg.branch}, timeout=TIMEOUT)
    if meta.status_code == 404:
        return None, None
    meta.raise_for_status()
    sha = meta.json()["sha"]
    raw = requests.get(cfg.url, headers=cfg.headers(raw=True), params={"ref": cfg.branch}, timeout=TIMEOUT)
    raw.raise_for_status()
    return json.loads(raw.content), sha


def restore_if_empty(conn):
    """Restore rounds from the backup when the local database has none."""
    cfg = _config()
    if not cfg.enabled:
        return 0
    if conn.execute("SELECT COUNT(*) FROM rounds").fetchone()[0]:
        return 0
    try:
        data, _ = _fetch(cfg)
        added = import_rounds(conn, data) if data else 0
        log.info("Restored %d rounds from backup", added)
        return added
    except Exception:
        log.exception("Could not restore rounds from backup; continuing without them")
        return 0


def backup_now(conn):
    """Merge the remote backup into the database, then write all rounds back.
    Never raises; returns True on success."""
    cfg = _config()
    if not cfg.enabled:
        return False
    for _attempt in range(3):
        try:
            data, sha = _fetch(cfg)
            if data:
                import_rounds(conn, data)
            body = json.dumps(export_rounds(conn), indent=1, sort_keys=True).encode()
            payload = {
                "message": "Back up golf rounds",
                "content": base64.b64encode(body).decode(),
                "branch": cfg.branch,
            }
            if sha:
                payload["sha"] = sha
            r = requests.put(cfg.url, headers=cfg.headers(), json=payload, timeout=TIMEOUT)
            if r.status_code in (409, 422):
                continue  # someone else wrote first; merge again and retry
            r.raise_for_status()
            return True
        except Exception:
            log.exception("Round backup failed")
            return False
    log.error("Round backup failed after retries (conflicting writes)")
    return False
