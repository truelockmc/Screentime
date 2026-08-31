#!/home/user/venv/bin/python
"""Shared on-disk icon cache (~/.cache) for icon_manager.py and icon_manager_win.py.

Icons are looked up in the in-memory cache of each IconManager first; this
module is only the second-level, persistent cache that survives an app
restart. That is the whole point of it: without it, every restart re-runs the
expensive lookup work (scanning .desktop directories, iterating all running
processes, extracting the icon resource out of a game's .exe via icoextract,
...) for every single app again, even though the result never changes.

Storage layout is content-addressed and split into two parts:
- content/<sha1-of-png-bytes>.png: the actual icon image, stored exactly
  once no matter how many different apps resolve to it. Several unrelated
  app names commonly end up with the very same icon (e.g. a shared generic
  fallback, or the same game resolved once before its process existed and
  once after via icoextract) - without content addressing that identical
  image would be written to disk again for every such key.
- keys/<sha1-of-cache-key>.ptr: a tiny (~40 byte) text file that just points
  a lookup key at its content hash. This indirection is what makes the
  dedup possible: writing a *new* key that happens to resolve to *already
  cached* image content only costs a few bytes, not another full PNG.

Design choices to keep SSD wear as low as possible:
- Write-once: existing files (both content and pointers) are never
  rewritten, only read. Icons essentially never change.
- Content dedup (see above) means the same image is never written twice.
- Every icon is stored as one small, fixed-size PNG (see ICON_CACHE_SIZE),
  regardless of the source (embedded .ico resources can be a few hundred KB
  across multiple resolutions - we only ever persist a few KB per unique
  icon image).
- No fsync/flush: we let the OS page cache batch and coalesce the write.
  Losing an icon on power loss is harmless (it is simply looked up and
  re-cached on next start), so there is no reason to force it to disk.
- The generic Qt fallback icon (no icon could be found) is never cached -
  it's already free to reproduce and caching it would only create useless
  files without saving any real lookup work.
"""
import hashlib
import logging
import os
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QBuffer, QIODevice
from PyQt5.QtGui import QIcon, QPixmap

logger = logging.getLogger(__name__)

# Full path should be ~/.cache/screentime/icons on linux
_CACHE_SUBDIR = ("screentime", "icons")

# Fixed cache resolution: big enough to look sharp anywhere in the UI, small
# enough that a cached icon is only a few KB on disk.
ICON_CACHE_SIZE = 64

_cache_dir: Optional[Path] = None
_content_dir: Optional[Path] = None
_keys_dir: Optional[Path] = None


def get_cache_dir() -> Path:
    """Return (and lazily create) the on-disk icon cache directory, including
    its 'content' and 'keys' subdirectories."""
    global _cache_dir, _content_dir, _keys_dir
    if _cache_dir is not None:
        return _cache_dir
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    d = Path(base).joinpath(*_CACHE_SUBDIR)
    content = d / "content"
    keys = d / "keys"
    try:
        content.mkdir(parents=True, exist_ok=True)
        keys.mkdir(parents=True, exist_ok=True)
    except Exception:
        logger.exception("Could not create icon cache directory %s", d)
    _cache_dir = d
    _content_dir = content
    _keys_dir = keys
    return d


def _key_path(key: str) -> Path:
    get_cache_dir()
    digest = hashlib.sha1(key.encode("utf-8", "surrogateescape")).hexdigest()
    return _keys_dir / f"{digest}.ptr"


def _content_path(content_hash: str) -> Path:
    get_cache_dir()
    return _content_dir / f"{content_hash}.png"


def load_icon(key: str) -> Optional[QIcon]:
    """Return the cached QIcon for `key`, or None on a cache miss. Pure read,
    never writes anything to disk."""
    ptr_path = _key_path(key)
    try:
        if not ptr_path.exists():
            return None
        content_hash = ptr_path.read_text().strip()
        if not content_hash:
            return None
        content_path = _content_path(content_hash)
        if content_path.exists():
            pixmap = QPixmap(str(content_path))
            if not pixmap.isNull():
                return QIcon(pixmap)
    except Exception:
        logger.debug("Could not read icon cache entry for key %r", key, exc_info=True)
    return None


def save_icon(key: str, qicon: Optional[QIcon], size: int = ICON_CACHE_SIZE) -> None:
    """Persist `qicon` under `key`, unless that key is already cached.

    The image itself is stored content-addressed (see module docstring), so
    if another key already cached the exact same icon, this only writes a
    tiny pointer file instead of the image again.
    """
    if qicon is None or qicon.isNull():
        return
    ptr_path = _key_path(key)
    if ptr_path.exists():
        return  # this key is already mapped - skip the write entirely

    pixmap = qicon.pixmap(size, size)
    if pixmap.isNull():
        return

    # Render to PNG bytes in memory first so we can hash the actual image
    # content, not just this (app-specific) key.
    buf = QBuffer()
    try:
        buf.open(QIODevice.WriteOnly)
        if not pixmap.save(buf, "PNG"):
            return
        data = bytes(buf.data())
    except Exception:
        logger.debug("Could not encode icon for key %r", key, exc_info=True)
        return
    finally:
        buf.close()
    if not data:
        return

    content_hash = hashlib.sha1(data).hexdigest()
    content_path = _content_path(content_hash)
    if not content_path.exists():
        tmp_content = content_path.with_suffix(".tmp")
        try:
            tmp_content.write_bytes(data)
            os.replace(tmp_content, content_path)  # atomic rename
        except Exception:
            logger.debug("Could not write icon content %s", content_path, exc_info=True)
            try:
                tmp_content.unlink(missing_ok=True)
            except Exception:
                pass
            return

    # Pointer write: a few dozen bytes, even when the image itself was
    # already on disk under a different key.
    tmp_ptr = ptr_path.with_suffix(".tmp")
    try:
        tmp_ptr.write_text(content_hash)
        os.replace(tmp_ptr, ptr_path)
    except Exception:
        logger.debug("Could not write icon pointer %s", ptr_path, exc_info=True)
        try:
            tmp_ptr.unlink(missing_ok=True)
        except Exception:
            pass


def clear_all() -> None:
    """Remove every cached icon (pointers and content), e.g. after the user
    picks a custom icon"""
    get_cache_dir()
    for d in (_keys_dir, _content_dir):
        try:
            for f in d.glob("*"):
                try:
                    f.unlink()
                except Exception:
                    pass
        except Exception:
            logger.debug("Could not clear icon cache directory %s", d, exc_info=True)
