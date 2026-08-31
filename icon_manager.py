#!/home/user/venv/bin/python
import configparser
import logging
import os
import re
from pathlib import Path
from typing import List, Optional

import icoextract
import psutil
from PyQt5 import QtGui, QtWidgets
from PyQt5.QtGui import QIcon

try:
    from icoextract import IconExtractor, IconExtractorError
except ImportError:
    IconExtractor = None
    IconExtractorError = Exception

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
        cp.read(path, encoding="utf-8")
        if "Desktop Entry" in cp:
            entry = cp["Desktop Entry"]
            for k in ("Exec", "Icon", "Name", "StartupWMClass"):
                if k in entry:
                    result[k] = entry[k].strip()
    except Exception:
        logger.exception("Error parsing .desktop file %s", path)
    return result


# Module-level cache: maps app_key -> list of matching .desktop Paths.
# Also caches the full directory scan so we only glob once per session.
_desktop_all_entries: Optional[List[tuple]] = None  # list of (Path, dict)
_desktop_key_cache: dict = {}  # app_key -> List[Path]


def _get_all_desktop_entries() -> List[tuple]:
    """Scan all DESKTOP_DIRS once and cache the results."""
    global _desktop_all_entries
    if _desktop_all_entries is not None:
        return _desktop_all_entries
    entries = []
    seen = set()
    for d in DESKTOP_DIRS:
        try:
            if not d.exists():
                continue
            for p in d.glob("*.desktop"):
                if str(p) in seen:
                    continue
                seen.add(str(p))
                info = _parse_desktop_file(p)
                entries.append((p, info))
        except Exception:
            logger.exception("Error scanning %s", d)
    _desktop_all_entries = entries
    return entries


def _find_desktop_entries_by_key(app_key: str) -> List[Path]:
    if not app_key:
        return []
    if app_key in _desktop_key_cache:
        return _desktop_key_cache[app_key]

    app_key_lower = app_key.lower()
    candidates: List[Path] = []

    # 1) direct filename match (app_key.desktop), check quickly before full scan
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
        _desktop_key_cache[app_key] = candidates
        return candidates

    # 2) search the cached full scan
    for p, info in _get_all_desktop_entries():
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

    _desktop_key_cache[app_key] = candidates
    return candidates


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
        logger.exception("Error loading theme icon for %s", icon_val)

    try:
        base = Path(icon_val).stem
        q = QIcon.fromTheme(base)
        if not q.isNull():
            return q
    except Exception:
        pass

    return None


def _winepath_to_linux(pid: str, win_path: str) -> Optional[str]:
    """Best-effort conversion of a Windows-style exe path (as seen in a Wine/
    Proton process's cmdline, e.g. "C:\\Games\\Foo\\foo.exe") to the real
    Linux path inside that process's Wine prefix."""
    if win_path.startswith("/") and os.path.exists(win_path):
        return win_path  # some launchers (Lutris/Proton) already pass a Linux path

    prefix = None
    try:
        with open(f"/proc/{pid}/environ", "rb") as f:
            env = f.read().decode(errors="ignore")
        for var in env.split("\0"):
            if var.startswith("STEAM_COMPAT_DATA_PATH="):
                prefix = os.path.join(var.split("=", 1)[1], "pfx")
                break
            if var.startswith("WINEPREFIX="):
                prefix = var.split("=", 1)[1]
                break
    except Exception as exc:
        logger.debug(
            "icoextract: /proc/%s/environ not readable (%s: %s). Yama "
            "ptrace_scope (see `cat /proc/sys/kernel/yama/ptrace_scope`)",
            pid, type(exc).__name__, exc,
        )
        return None
    if not prefix:
        logger.debug(
            "icoextract: no WINEPREFIX/STEAM_COMPAT_DATA_PATH in environ of pid %s found",
            pid,
        )
        return None

    rel = win_path.split(":", 1)[-1].replace("\\", "/").lstrip("/")
    drive = win_path[0].lower() if ":" in win_path else "c"
    candidates = (
        os.path.join(prefix, "dosdevices", f"{drive}:", rel),
        os.path.join(prefix, "drive_c", rel),
    )
    for candidate in candidates:
        candidate = os.path.realpath(candidate)
        if os.path.exists(candidate):
            return candidate
    logger.debug(
        "icoextract: none of these paths exists for win_path=%r prefix=%r: %s",
        win_path, prefix, candidates,
    )
    return None


def _find_exe_path_for_pid(pid: str) -> Optional[str]:
    """Look for a *.exe argument in this process's cmdline (Wine/Proton games)
    and resolve it to a real path on disk."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            args = [a.decode(errors="ignore") for a in f.read().split(b"\x00")]
    except Exception as exc:
        logger.debug("icoextract: /proc/%s/cmdline not readable (%s: %s)", pid, type(exc).__name__, exc)
        return None
    exe_args = [a for a in args if a.lower().endswith(".exe")]
    if not exe_args:
        logger.debug("icoextract: no *.exe-argument in cmdline of pid %s: %s", pid, args)
        return None
    for arg in exe_args:
        resolved = _winepath_to_linux(pid, arg)
        if resolved:
            logger.debug("icoextract: pid %s -> exe path %s", pid, resolved)
            return resolved
    logger.debug("icoextract: .exe-arguments found (%s), but none is resolvable to a real path", exe_args)
    return None


def _icon_from_exe(exe_path: str) -> Optional[QIcon]:
    """Extract the embedded icon resource from a Windows .exe/.dll via icoextract."""
    if IconExtractor is None:
        logger.debug("icoextract: package 'icoextract' not installed")
        return None
    try:
        extractor = IconExtractor(exe_path)
        ico_bytes = extractor.get_icon().getvalue()
        pixmap = QtGui.QPixmap()
        if pixmap.loadFromData(ico_bytes, "ICO") and not pixmap.isNull():
            q = QIcon()
            q.addPixmap(pixmap)
            return q
        logger.debug("icoextract: loadFromData does not deliver a valid pixmap for %s", exe_path)
    except IconExtractorError as exc:
        logger.debug("icoextract: no icon resource in %s (%s)", exe_path, exc)
    except Exception:
        logger.exception("Error extracting icon from %s", exe_path)

    # Fallback: some Windows builds (e.g. Godot/Unity) don't have a
    # PE icon resource, but ship an .ico file next to the .exe.
    try:
        p = Path(exe_path).parent
        for ico in p.glob("*.ico"):
            q = QIcon(str(ico))
            if not q.isNull():
                return q
    except Exception:
        logger.exception("Error in .ico fallback next to %s", exe_path)
    return None


def _get_exe_icon(app_name: str, pid: Optional[str]) -> Optional[QIcon]:
    """Icon for an .exe running via Wine/Proton, determined live via icoextract
    from the pid. Cached purely in-memory (via get_icon_for_app)."""
    if not pid:
        logger.debug("icoextract: no pid for '%s', cannot extract anything", app_name)
        return None
    exe_path = _find_exe_path_for_pid(pid)
    if not exe_path:
        return None

    q = _icon_from_exe(exe_path)
    if not q or q.isNull():
        logger.debug("icoextract: could not extract icon from %s", exe_path)
    return q


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
        logger.exception(
            "Error loading icon for process %s", getattr(proc, "pid", "n/a")
        )
    return None


class ImprovedIconManager:
    def __init__(self):
        self.app_icons: dict[str, QIcon] = {}

    def _cache_icon(self, identifier: str, qicon: QIcon):
        self.app_icons[identifier] = qicon

    def _get_cached(self, identifier: str) -> Optional[QIcon]:
        return self.app_icons.get(identifier)

    def get_icon_for_app(
        self,
        app_name: str,
        icon_hint: Optional[str] = None,
        pid: Optional[str] = None,
    ) -> QIcon:
        try:
            if not app_name:
                return QtWidgets.QApplication.style().standardIcon(
                    QtWidgets.QStyle.SP_FileIcon
                )

            # 1) cache (deliberately WITHOUT the volatile pid itself in the
            # key: it changes on every game launch and would otherwise
            # constantly invalidate the cache. Instead only whether a pid is
            # present at all ("p"/"") - this way an earlier failure without a
            # pid (game not running yet) is not permanently locked in: as
            # soon as the pid becomes known, the key changes and a new
            # attempt (incl. .exe extraction) is made.
            cache_key = f"{app_name}|{icon_hint or ''}|{'p' if pid else ''}"
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

            # 2b) Windows games under Wine/Proton: pull the icon live from
            # the .exe via icoextract (covers Lutris-Proton games without
            # a .desktop entry, without touching Lutris itself). Only
            # cached in-memory, i.e. requires a running pid.
            q = _get_exe_icon(app_name, pid)
            if q and not q.isNull():
                self._cache_icon(cache_key, q)
                return q

            # 3) try processes (match name or basename)
            for proc in psutil.process_iter(["name", "exe", "cmdline"]):
                try:
                    pname = proc.info.get("name") or ""
                    pexe = proc.info.get("exe") or ""
                    if not pname and not pexe:
                        continue
                    # compare case-insensitive to app_name
                    if (
                        pname.lower() == app_name.lower()
                        or Path(pexe).stem.lower() == app_name.lower()
                    ):
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
                logger.exception("Error loading theme icon for %s", app_name)

            # 5) final fallback: standard icon
            fallback = QtWidgets.QApplication.style().standardIcon(
                QtWidgets.QStyle.SP_FileIcon
            )
            self._cache_icon(cache_key, fallback)
            return fallback

        except Exception:
            logger.exception(
                "Error in ImprovedIconManager.get_icon_for_app for %s", app_name
            )
            return QtWidgets.QApplication.style().standardIcon(
                QtWidgets.QStyle.SP_FileIcon
            )
