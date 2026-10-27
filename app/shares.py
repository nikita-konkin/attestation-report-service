"""Links to a report's results that open without a login.

A teacher shares a finished report: /attestation/share#<token> shows the same sections, charts and
files without a RITM login until the link expires (1, 3 or 7 days) or its owner revokes it. A link
can also be permanent, from the start or later from the owner's list; it then lasts until revoked.
The owner's list shows each link again, opens its report on the service page and can issue a new
link for the same report when the old one must stop working.

- the token is 256 random bits and travels after «#»: browsers never send that part of the address,
  so it reaches neither the nginx logs nor a Referer header; the page passes it in X-Share-Token;
- the folder is named by the token's SHA-256; the token itself sits in share.json next to the report
  copy, so the owner can see the link again. Whoever can read that folder can read the report anyway;
  links made before the token was kept have none and can only be reissued;
- a snapshot of the report is kept in the data volume, so links survive restarts and updates; it
  holds student names and scores and is deleted as soon as the link expires or is revoked.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import shutil
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

LIFETIMES_DAYS = (1, 3, 7, None)  # None: permanent, until the owner revokes the link
MAX_PER_OWNER = 20
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")  # secrets.token_urlsafe(32)
ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")
META_FILE = "share.json"
MARKDOWN_FILE = "report.md"
DATA_FILE = "report.json"
FILES_DIR = "files"
NEW_PREFIX = ".new-"  # a share being written; renamed into place only when complete


class ShareRejected(Exception):
    pass


@dataclass(frozen=True)
class Share:
    id: str  # SHA-256 of the token: the folder name and what the owner revokes by
    folder: Path
    owner: str
    title: str
    created: float  # Unix time, not monotonic: shares outlive restarts
    expires: Optional[float]  # None for a permanent link
    token: Optional[str]  # None for links made before tokens were kept
    files: dict[str, str]  # key -> file name in FILES_DIR
    sizes: dict[str, int]

    def expired(self, now: Optional[float] = None) -> bool:
        return self.expires is not None and (time.time() if now is None else now) >= self.expires

    def file(self, key: str) -> Optional[Path]:
        name = self.files.get(key)
        return self.folder / FILES_DIR / name if name else None

    def markdown(self) -> str:
        return (self.folder / MARKDOWN_FILE).read_text(encoding="utf-8")

    def data(self) -> Optional[dict]:
        path = self.folder / DATA_FILE
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def public(self) -> dict:
        """Title and lifetime; the token goes only to the owner, see main.py."""
        return {"id": self.id, "title": self.title, "created": self.created, "expires": self.expires}


def share_id(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


class ShareStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.Lock()

    def create(
        self,
        owner: str,
        title: str,
        markdown: str,
        data: Optional[dict],
        files: dict[str, Path],
        days: Optional[int],
    ) -> tuple[str, Share]:
        """A new link to a copy of these results; returns the token (shown once) and the share."""
        if days not in LIFETIMES_DAYS:
            raise ShareRejected("Срок ссылки — 1, 3 или 7 дней либо бессрочно.")
        with self._lock:
            if len(self.owned_by(owner)) >= MAX_PER_OWNER:
                raise ShareRejected(f"У вас уже {MAX_PER_OWNER} действующих ссылок. Отзовите ненужные.")
            token = secrets.token_urlsafe(32)
            share = Share(
                id=share_id(token),
                folder=self.root / share_id(token),
                owner=owner,
                title=title,
                created=time.time(),
                expires=None if days is None else time.time() + days * 24 * 3600,
                token=token,
                files={key: path.name for key, path in files.items()},
                sizes={key: path.stat().st_size for key, path in files.items()},
            )
            draft = self.root / (NEW_PREFIX + share.id)
            (draft / FILES_DIR).mkdir(parents=True, mode=0o700)
            try:
                for key, path in files.items():
                    shutil.copyfile(path, draft / FILES_DIR / share.files[key])
                (draft / MARKDOWN_FILE).write_text(markdown, encoding="utf-8")
                if data is not None:
                    (draft / DATA_FILE).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                self._write_meta(draft, share)
                draft.rename(share.folder)
            except BaseException:
                shutil.rmtree(draft, ignore_errors=True)
                raise
        return token, share

    def _load(self, folder: Path) -> Optional[Share]:
        try:
            meta = json.loads((folder / META_FILE).read_text(encoding="utf-8"))
            return Share(
                id=folder.name,
                folder=folder,
                owner=meta["owner"],
                title=meta["title"],
                created=float(meta["created"]),
                expires=None if meta["expires"] is None else float(meta["expires"]),
                token=meta.get("token"),
                files=dict(meta["files"]),
                sizes={key: int(size) for key, size in meta["sizes"].items()},
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _shares(self) -> list[Share]:
        if not self.root.is_dir():
            return []
        found = (self._load(folder) for folder in self.root.iterdir() if ID_PATTERN.match(folder.name))
        return [share for share in found if share is not None]

    def get(self, token: str) -> Optional[Share]:
        """The live share behind a token; None for an unknown, revoked or expired one."""
        if not TOKEN_PATTERN.match(token or ""):
            return None
        share = self._load(self.root / share_id(token))
        if share is None:
            return None
        if share.expired():
            self._remove(share)
            return None
        return share

    def owned_by(self, owner: str) -> list[Share]:
        """The owner's live links, newest first."""
        now = time.time()
        shares = [share for share in self._shares() if share.owner == owner and not share.expired(now)]
        return sorted(shares, key=lambda share: share.created, reverse=True)

    def owned(self, owner: str, ident: str) -> Optional[Share]:
        """The owner's live link by its id; None for someone else's, an expired or an unknown one."""
        if not ID_PATTERN.match(ident or ""):
            return None
        share = self._load(self.root / ident)
        if share is None or share.owner != owner or share.expired():
            return None
        return share

    def reissue(self, owner: str, ident: str) -> Optional[tuple[str, Share]]:
        """A new token for the same report and lifetime: the old link stops opening at once."""
        with self._lock:
            share = self.owned(owner, ident)
            if share is None:
                return None
            token = secrets.token_urlsafe(32)
            folder = self.root / share_id(token)
            share.folder.rename(folder)
            share = replace(share, id=folder.name, folder=folder, token=token)
            self._write_meta(folder, share)
        return token, share

    def make_permanent(self, owner: str, ident: str) -> Optional[Share]:
        """Drop the expiry of the owner's live link; None when there is no such link."""
        with self._lock:
            share = self.owned(owner, ident)
            if share is None:
                return None
            share = replace(share, expires=None)
            self._write_meta(share.folder, share)
        return share

    @staticmethod
    def _write_meta(folder: Path, share: Share) -> None:
        meta = {key: getattr(share, key) for key in ("owner", "title", "created", "expires", "token", "files", "sizes")}
        draft = folder / (META_FILE + ".new")
        draft.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        draft.replace(folder / META_FILE)  # atomic: a reader sees the old expiry or the new one

    def revoke(self, owner: str, ident: str) -> bool:
        if not ID_PATTERN.match(ident or ""):
            return False
        share = self._load(self.root / ident)
        if share is None or share.owner != owner:
            return False
        self._remove(share)
        return True

    def cleanup(self) -> int:
        """Delete expired shares and drafts left by a crash; returns how many shares were removed."""
        if not self.root.is_dir():
            return 0
        now = time.time()
        with self._lock:
            for draft in self.root.glob(NEW_PREFIX + "*"):
                shutil.rmtree(draft, ignore_errors=True)
        expired = [share for share in self._shares() if share.expired(now)]
        for share in expired:
            self._remove(share)
        return len(expired)

    @staticmethod
    def _remove(share: Share) -> None:
        shutil.rmtree(share.folder, ignore_errors=True)
        logger.info("Share %s removed", share.id[:8])
