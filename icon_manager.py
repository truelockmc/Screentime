#!/home/user/venv/bin/python
from typing import Optional, List
import os
import re
from pathlib import Path
import subprocess
import logging
import psutil
import configparser

from PyQt5 import QtWidgets, QtGui
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import QByteArray
from PyQt5.QtGui import QPixmap

logger = logging.getLogger(__name__)

# Desktop dirs to search for .desktop files on Linux (include flatpak export dirs)
DESKTOP_DIRS: List[Path] = [
    Path.home() / ".local" / "share" / "applications",
    Path("/usr/share/applications"),
    Path("/usr/local/share/applications"),
    Path("/var/lib/flatpak/exports/share/applications"),
    Path.home() / ".local" / "share" / "flatpak" / "exports" / "share" / "applications",
]

def _parse_desktop_file(path: Path) -> dict:
    """Return keys 'Exec', 'Icon', 'Name', 'StartupWMClass' when present."""
    result = {}
    try:
        cp = configparser.ConfigParser(interpolation=None)
        cp.read(path, encoding='utf-8')
        if 'Desktop Entry' in cp:
            entry = cp['Desktop Entry']
            for k in ('Exec', 'Icon', 'Name', 'StartupWMClass'):
                if k in entry:
                    result[k] = entry[k].strip()
    except Exception:
        logger.exception("Fehler beim Parsen der .desktop-Datei %s", path)
    return result

def _find_desktop_entries_by_key(app_key: str) -> List[Path]:
    if not app_key:
        return []

    app_key_lower = app_key.lower()
    candidates: List[Path] = []

    # 1) direct filename match (app_key.desktop)
    for d in DESKTOP_DIRS:
        try:
            if not d.exists():
                continue
            p = d / f"{app_key}.desktop"
            if p.exists():
                candidates.append(p)
        except Exception:
            continue
    if candidates:
        return candidates

    # 2) scan directories for other matches (Name, StartupWMClass, Exec, filename contains)
    scanned: List[Path] = []
    for d in DESKTOP_DIRS:
        try:
            if not d.exists():
                continue
            for p in d.glob("*.desktop"):
                if p in scanned:
                    continue
                scanned.append(p)
                info = _parse_desktop_file(p)
                name = info.get("Name", "")
                if name and name.lower() == app_key_lower:
                    candidates.append(p)
                    continue
                swc = info.get("StartupWMClass", "")
                if swc and swc.lower() == app_key_lower:
                    candidates.append(p)
                    continue
                fname = p.stem.lower()
                if app_key_lower in fname or fname.startswith(app_key_lower):
                    candidates.append(p)
                    continue
                execv = info.get("Exec", "")
                if execv and app_key_lower in execv.lower():
                    candidates.append(p)
                    continue
        except Exception:
            logger.exception("Fehler beim Scannen von %s", d)
            continue

    seen = set()
    out = []
    for p in candidates:
        if str(p) not in seen:
            out.append(p)
            seen.add(str(p))
    return out

def _icon_from_desktop_entry(desktop_path: Path) -> Optional[QIcon]:
    info = _parse_desktop_file(desktop_path)
    icon_val = info.get("Icon")
    if not icon_val:
        return None

    try:
        icon_path = Path(icon_val)
        if icon_path.is_absolute() and icon_path.exists():
            q = QIcon(str(icon_path))
            if not q.isNull():
                return q
        candidate = desktop_path.parent / icon_val
        if candidate.exists():
            q = QIcon(str(candidate))
            if not q.isNull():
                return q
    except Exception:
        pass

    try:
        name = icon_val
        q = QIcon.fromTheme(name)
        if not q.isNull():
            return q
    except Exception:
        logger.exception("Fehler beim Laden von Theme-Icon für %s", icon_val)

    try:
        base = Path(icon_val).stem
        q = QIcon.fromTheme(base)
        if not q.isNull():
            return q
    except Exception:
        pass

    return None

def _get_icon_for_proc(proc: psutil.Process) -> Optional[QIcon]:
    try:
        pexe = None
        try:
            pexe = proc.exe()
        except Exception:
            try:
                pexe = os.readlink(f"/proc/{proc.pid}/exe")
            except Exception:
                pexe = None

        basename = Path(pexe).name if pexe else (proc.name() or "")
        desktop_matches = []
        for d in DESKTOP_DIRS:
            if not d.exists():
                continue
            for p in d.glob("*.desktop"):
                info = _parse_desktop_file(p)
                exec_val = info.get("Exec", "")
                exec_clean = re.sub(r"%\w", "", exec_val).strip()
                if pexe and pexe in exec_clean:
                    desktop_matches.append(p)
                elif basename and basename in exec_clean:
                    desktop_matches.append(p)
        for d in desktop_matches:
            q = _icon_from_desktop_entry(d)
            if q and not q.isNull():
                return q
        if basename:
            q = QIcon.fromTheme(Path(basename).stem)
            if not q.isNull():
                return q

        if pexe:
            p = Path(pexe).parent
            stem = Path(pexe).stem
            for ext in ("png", "svg", "xpm", "ico"):
                candidate = p / f"{stem}.{ext}"
                if candidate.exists():
                    q = QIcon(str(candidate))
                    if not q.isNull():
                        return q
    except Exception:
        logger.exception("Fehler beim Laden des Icons für Prozess %s", getattr(proc, "pid", "n/a"))
    return None

class ImprovedIconManager:
    def __init__(self):
        self.app_icons: dict[str, QIcon] = {}

    def _cache_icon(self, identifier: str, qicon: QIcon):
        self.app_icons[identifier] = qicon

    def _get_cached(self, identifier: str) -> Optional[QIcon]:
        return self.app_icons.get(identifier)

    def get_icon_for_app(self, app_name: str, icon_hint: Optional[str] = None) -> QIcon:
        try:
            if not app_name:
                return QtWidgets.QApplication.style().standardIcon(QtWidgets.QStyle.SP_FileIcon)

            # 1) cache
            cache_key = f"{app_name}|{icon_hint or ''}"
            cached = self._get_cached(cache_key)
            if cached and not cached.isNull():
                return cached
            if icon_hint:
                try:
                    p = Path(icon_hint)
                    if not p.is_absolute():
                        p = Path(__file__).parent / p
                    if p.exists():
                        q = QIcon(str(p))
                        if not q.isNull():
                            self._cache_icon(cache_key, q)
                            return q

                    q = QIcon.fromTheme(icon_hint)
                    if not q.isNull():
                        self._cache_icon(cache_key, q)
                        return q

                except Exception:
                    logger.exception("Could not load mapped icon: %s", icon_hint)

            # 2) treat as .desktop id / name / StartupWMClass
            desktop_entries = _find_desktop_entries_by_key(app_name)
            for d in desktop_entries:
                q = _icon_from_desktop_entry(d)
                if q and not q.isNull():
                    self._cache_icon(cache_key, q)
                    return q

            # 3) try processes (match name or basename)
            for proc in psutil.process_iter(['name', 'exe', 'cmdline']):
                try:
                    pname = proc.info.get('name') or ""
                    pexe = proc.info.get('exe') or ""
                    if not pname and not pexe:
                        continue
                    # compare case-insensitive to app_name
                    if pname.lower() == app_name.lower() or Path(pexe).stem.lower() == app_name.lower():
                        q = _get_icon_for_proc(proc)
                        if q and not q.isNull():
                            self._cache_icon(cache_key, q)
                            return q
                except Exception:
                    continue

            # 4) try QIcon.fromTheme using app_name or its stem
            try:
                q = QIcon.fromTheme(app_name)
                if q and not q.isNull():
                    self._cache_icon(cache_key, q)
                    return q
                q2 = QIcon.fromTheme(Path(app_name).stem)
                if q2 and not q2.isNull():
                    self._cache_icon(cache_key, q2)
                    return q2
            except Exception:
                logger.exception("Fehler beim Laden von Theme-Icon für %s", app_name)

            # 5) final fallback: standard icon
            fallback = QtWidgets.QApplication.style().standardIcon(QtWidgets.QStyle.SP_FileIcon)
            self._cache_icon(cache_key, fallback)
            return fallback

        except Exception:
            logger.exception("Fehler in ImprovedIconManager.get_icon_for_app für %s", app_name)
            return QtWidgets.QApplication.style().standardIcon(QtWidgets.QStyle.SP_FileIcon)
