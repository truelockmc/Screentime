#!/home/user/venv/bin/python
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Steam integration helpers
# ---------------------------------------------------------------------------

# Common Steam library root locations. Additional paths are read from
# libraryfolders.vdf at runtime.
_STEAM_DEFAULT_ROOTS = [
    Path.home() / ".steam" / "steam" / "steamapps",
    Path.home() / ".local" / "share" / "Steam" / "steamapps",
]

# Where Steam stores per-game icons after the first launch.
_STEAM_ICON_DIRS = [
    Path.home() / ".local" / "share" / "icons" / "hicolor" / "256x256" / "apps",
    Path.home() / ".local" / "share" / "icons" / "hicolor" / "128x128" / "apps",
    Path.home() / ".local" / "share" / "icons" / "hicolor" / "64x64" / "apps",
    Path.home() / ".local" / "share" / "icons" / "hicolor" / "32x32" / "apps",
]


_steam_library_paths_cache: Optional[list] = None


def _steam_library_paths():
    """Return all steamapps directory paths, including extra Steam libraries."""
    global _steam_library_paths_cache
    if _steam_library_paths_cache is not None:
        return _steam_library_paths_cache
    paths = []
    for root in _STEAM_DEFAULT_ROOTS:
        if root.exists() and root not in paths:
            paths.append(root)

    # Parse libraryfolders.vdf for additional Steam library locations.
    for root in list(paths):
        vdf = root / "libraryfolders.vdf"
        if not vdf.exists():
            continue
        try:
            text = vdf.read_text(encoding="utf-8", errors="ignore")
            for m in re.finditer(r'"path"\s+"([^"]+)"', text):
                extra = Path(m.group(1)) / "steamapps"
                if extra.exists() and extra not in paths:
                    paths.append(extra)
        except Exception:
            pass
    _steam_library_paths_cache = paths
    return paths


def _get_steam_game_info(app_id: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (game_name, icon_path) for a Steam app ID, or (None, None)."""
    game_name: Optional[str] = None
    icon_path: Optional[str] = None

    # 1) Game name from appmanifest_{id}.acf
    for lib in _steam_library_paths():
        manifest = lib / f"appmanifest_{app_id}.acf"
        if manifest.exists():
            try:
                content = manifest.read_text(encoding="utf-8", errors="ignore")
                m = re.search(r'"name"\s+"([^"]+)"', content)
                if m:
                    game_name = m.group(1)
                    break
            except Exception:
                pass

    # 2) Icon from the per-user Steam icon cache
    for icon_dir in _STEAM_ICON_DIRS:
        candidate = icon_dir / f"steam_icon_{app_id}.png"
        if candidate.exists():
            icon_path = str(candidate)
            break

    return game_name, icon_path


# ---------------------------------------------------------------------------
# AppMapping
# ---------------------------------------------------------------------------


class AppMapping:
    def __init__(self, path):
        self.path = path
        self.mapping = {}
        # Cache for dynamic Steam lookups so we don't re-read files every
        # second while a game is running.
        self._steam_cache: dict = {}
        self._proc_steam_cache: dict = {}
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.mapping = json.load(f)
        except FileNotFoundError:
            logger.info("map.json not found, using raw app names")
            self.mapping = {}
        except Exception:
            logger.exception("Failed to load map.json")
            self.mapping = {}

    def resolve(self, raw_name: str) -> Tuple[str, Optional[str]]:
        # 1) Explicit entry in map.json always wins.
        entry = self.mapping.get(raw_name)
        if entry:
            display = entry.get("display_name", raw_name)
            icon = entry.get("icon")
            # If the icon path is set but doesn't exist, fall through to
            # dynamic lookup below so Steam icons still work.
            if icon and not Path(icon).exists():
                icon = None
            if icon:
                return display, icon
            # display_name was set but icon needs dynamic lookup, try Steam
            app_id = self._find_steam_app_id_for_process(raw_name)
            if app_id:
                _, dyn_icon = self._get_steam_info_cached(app_id)
                return display, dyn_icon
            return display, None

        # 2) Dynamic Steam lookup for keys like "steam_app_123456" that
        #    window_resolver produces when it detects a Proton/Wine game.
        if raw_name.startswith("steam_app_"):
            app_id = raw_name[len("steam_app_") :]
            if app_id.isdigit():
                game_name, icon_path = self._get_steam_info_cached(app_id)
                display = game_name if game_name else raw_name
                return display, icon_path

        # 3) Native Steam game not in map.json, try to identify via SteamAppId
        #    from the running process (e.g. "aces", "Fishards.x86_64").
        app_id = self._find_steam_app_id_for_process(raw_name)
        if app_id:
            game_name, icon_path = self._get_steam_info_cached(app_id)
            display = game_name if game_name else raw_name
            return display, icon_path

        return raw_name, None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_steam_info_cached(
        self, app_id: str
    ) -> Tuple[Optional[str], Optional[str]]:
        if app_id not in self._steam_cache:
            self._steam_cache[app_id] = _get_steam_game_info(app_id)
        return self._steam_cache[app_id]

    def _find_steam_app_id_for_process(self, name: str) -> Optional[str]:
        """Scan /proc for a running process matching `name` and return its SteamAppId."""
        # Use a short-lived cache (cleared when app switches) to avoid
        # scanning /proc every second for the same process name.
        if name in self._proc_steam_cache:
            return self._proc_steam_cache[name] or None
        import glob

        name_lower = name.lower()
        found_id: Optional[str] = None
        for pid_dir in glob.glob("/proc/[0-9]*/"):
            try:
                pid = pid_dir.rstrip("/").split("/")[-1]
                exe = os.readlink(f"/proc/{pid}/exe")
                if os.path.basename(exe).lower() != name_lower:
                    continue
                with open(f"/proc/{pid}/environ", "rb") as f:
                    env = f.read().decode(errors="ignore")
                for var in env.split("\x00"):
                    if var.startswith("SteamAppId="):
                        val = var.split("=", 1)[1].strip()
                        if val and val.isdigit() and val != "0":
                            found_id = val
                            break
                if found_id:
                    break
            except Exception:
                pass
        self._proc_steam_cache[name] = found_id or ""
        return found_id

    def save_entry(self, raw_key: str, entry: dict):
        """Persist a single entry to map.json and update in-memory mapping."""
        if not isinstance(self.mapping, dict):
            self.mapping = {}
        self.mapping[raw_key] = entry
        try:
            import json

            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.mapping, f, indent=2, ensure_ascii=False)
        except Exception:
            logger.exception("Failed to save map.json")
