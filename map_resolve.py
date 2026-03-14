#!/home/user/venv/bin/python
import json
import logging
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


def _steam_library_paths():
    """Return all steamapps directory paths, including extra Steam libraries."""
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
            content = vdf.read_text(encoding="utf-8", errors="ignore")
            for m in re.finditer(r'"path"\s+"([^"]+)"', content):
                extra = Path(m.group(1)) / "steamapps"
                if extra.exists() and extra not in paths:
                    paths.append(extra)
        except Exception:
            pass
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
            return entry.get("display_name", raw_name), entry.get("icon")

        # 2) Dynamic Steam lookup for keys like "steam_app_123456" that
        #    window_resolver produces when it detects a Proton/Wine game.
        if raw_name.startswith("steam_app_"):
            app_id = raw_name[len("steam_app_") :]
            if app_id.isdigit():
                if app_id not in self._steam_cache:
                    self._steam_cache[app_id] = _get_steam_game_info(app_id)
                game_name, icon_path = self._steam_cache[app_id]
                # Gracefully fall back if the appmanifest isn't found yet.
                display = game_name if game_name else raw_name
                return display, icon_path

        return raw_name, None
