#!/home/user/venv/bin/python
import configparser
import json
import os
import re
import subprocess
from typing import Dict, List, Optional, Tuple

DESKTOP_DIRS = [
    os.path.expanduser("~/.local/share/applications"),
    "/usr/share/applications",
    "/var/lib/flatpak/exports/share/applications",
    os.path.expanduser("~/.local/share/flatpak/exports/share/applications"),
]

# WM_CLASS values that are generic Wine/Proton placeholders.
# When the active window has one of these, WM_NAME is tried first
# because it usually contains the real game title (set by the game itself).
GENERIC_WM_CLASSES = frozenset(
    {
        "steam_app_default",
        "steam_app_0",
        "wine",
        "wine64",
        "explorer.exe",
    }
)

# Wine/Proton process names that are launchers, not the actual game
WINE_PROCESSES = frozenset(
    {
        "wine64-preloader",
        "wine-preloader",
        "wine",
        "wine64",
        "wineserver",
        "wine-loader",
        "wine64-loader",
        # Common Wine background/system processes
        "winedevice.exe",
        "plugplay.exe",
        "services.exe",
        "rpcss.exe",
        "svchost.exe",
        "explorer.exe",
        # Steam/Proton wrappers
        "reaper",
        "pressure-vessel-adverb",
        "pressure-vessel-wrap",
        "steam-runtime-launcher-service",
    }
)


def _run_cmd(cmd: List[str]) -> str:
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
        return out.decode(errors="ignore")
    except Exception:
        return ""


def get_active_window_id() -> Optional[str]:
    out = _run_cmd(["xprop", "-root", "_NET_ACTIVE_WINDOW"])
    if not out:
        return None
    m = re.search(r"window id # (0x[0-9a-fA-F]+)", out)
    if m:
        return m.group(1)
    token = out.strip().split()[-1]
    return token if token else None


def _xprop_prop(win: str, prop: str) -> str:
    return _run_cmd(["xprop", "-id", win, prop]).strip()


def _parse_xprop_value(s: str) -> str:
    if "=" in s:
        return s.split("=", 1)[1].strip()
    return s


def _extract_first_quoted(s: str) -> Optional[str]:
    m = re.search(r'"([^"]+)"', s)
    return m.group(1) if m else None


def get_active_window_info() -> Dict[str, Optional[str]]:
    win = get_active_window_id()
    if not win:
        return {"window": None, "wm_class": None, "wm_pid": None, "wm_name": None}
    wm_class_raw = _xprop_prop(win, "WM_CLASS")
    wm_pid_raw = _xprop_prop(win, "_NET_WM_PID")
    wm_name_raw = _xprop_prop(win, "WM_NAME")

    wm_class = (
        _extract_first_quoted(_parse_xprop_value(wm_class_raw))
        if wm_class_raw
        else None
    )
    wm_pid = None
    if wm_pid_raw and "_NET_WM_PID" in wm_pid_raw:
        pid_val = _parse_xprop_value(wm_pid_raw)
        if pid_val and pid_val.isdigit():
            wm_pid = pid_val
    wm_name = (
        _extract_first_quoted(_parse_xprop_value(wm_name_raw)) if wm_name_raw else None
    )

    return {"window": win, "wm_class": wm_class, "wm_pid": wm_pid, "wm_name": wm_name}


def _get_steam_app_id_from_environ(pid: str) -> Optional[str]:
    """Read SteamAppId from /proc/{pid}/environ.

    Steam sets this env-var for every Proton game process, so it is the
    most reliable way to identify which game is running under Wine.
    """
    try:
        with open(f"/proc/{pid}/environ", "rb") as f:
            env = f.read().decode(errors="ignore")
        for var in env.split("\0"):
            if var.startswith("SteamAppId="):
                app_id = var.split("=", 1)[1].strip()
                if app_id and app_id.isdigit() and app_id != "0":
                    return app_id
    except Exception:
        pass
    return None


def resolve_proc_from_pid(pid: str) -> Optional[Dict[str, str]]:
    try:
        exe_path = os.readlink(f"/proc/{pid}/exe")
    except Exception:
        exe_path = None
    cmdline = None
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            raw = f.read()
            if raw:
                cmdline = raw.replace(b"\x00", b" ").decode(errors="ignore").strip()
    except Exception:
        cmdline = None
    if exe_path or cmdline:
        return {"exe": exe_path, "cmdline": cmdline}
    return None


def parse_desktop_file(path: str) -> Dict[str, str]:
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read(path, encoding="utf-8")
    except Exception:
        return {}
    if "Desktop Entry" not in cp:
        return {}
    entry = cp["Desktop Entry"]
    return {
        "StartupWMClass": entry.get("StartupWMClass", "").strip(),
        "Name": entry.get("Name", "").strip(),
        "Exec": entry.get("Exec", "").strip(),
    }


def find_desktop_for_wm_class(
    wm_class: Optional[str], wm_name: Optional[str] = None
) -> Optional[Tuple[str, str]]:
    if not wm_class and not wm_name:
        return None

    candidates = []
    for d in DESKTOP_DIRS:
        if not os.path.isdir(d):
            continue
        try:
            for fn in os.listdir(d):
                if not fn.endswith(".desktop"):
                    continue
                path = os.path.join(d, fn)
                info = parse_desktop_file(path)
                if info:
                    candidates.append((fn, info))
        except Exception:
            continue

    # 1) StartupWMClass exact
    if wm_class:
        for fn, info in candidates:
            if info.get("StartupWMClass") and info["StartupWMClass"] == wm_class:
                return fn[:-8], info.get("Name") or fn[:-8]

    # 2) Name exact with wm_name
    if wm_name:
        for fn, info in candidates:
            if info.get("Name") and info["Name"] == wm_name:
                return fn[:-8], info.get("Name") or fn[:-8]

    # 3) filename contains wm_class or startswith
    if wm_class:
        lc = wm_class.lower()
        for fn, info in candidates:
            base = fn[:-8].lower()
            if lc in base or base.startswith(lc):
                return fn[:-8], info.get("Name") or fn[:-8]

    # 4) Exec contains wm_class
    if wm_class:
        for fn, info in candidates:
            if info.get("Exec") and wm_class in info["Exec"]:
                return fn[:-8], info.get("Name") or fn[:-8]

    return None


def load_mapping(path: Optional[str]) -> Dict[str, str]:
    if not path:
        return {}
    path = os.path.expanduser(path)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items()}
    except Exception:
        pass
    return {}


def get_active_app(mapping_path: Optional[str] = None) -> Dict[str, Optional[str]]:
    mapping = load_mapping(mapping_path)
    info = get_active_window_info()
    res = {
        "app_id": None,
        "app_name": None,
        "method": None,
        "wm_class": info.get("wm_class"),
        "wm_name": info.get("wm_name"),
        "wm_pid": info.get("wm_pid"),
        "proc_path": None,
    }

    # 1) PID-based
    pid = info.get("wm_pid")
    if pid:
        # Always check SteamAppId first, works for native Linux games,
        # Proton/Wine games and anything else Steam launches.
        steam_app_id = _get_steam_app_id_from_environ(pid)
        if steam_app_id:
            key = f"steam_app_{steam_app_id}"
            res["app_id"] = key
            res["app_name"] = key
            res["method"] = "steam_app_id"
            return res

        proc = resolve_proc_from_pid(pid)
        if proc:
            exe_path = proc.get("exe") or ""
            basename = os.path.basename(exe_path) if exe_path else ""
            res["proc_path"] = exe_path

            if basename and basename not in WINE_PROCESSES:
                res["app_id"] = basename
                res["app_name"] = basename
                res["method"] = "pid"
                return res

            # Wine launcher with no SteamAppId (non-Steam Wine game),
            # fall through to WM_CLASS / WM_NAME resolution below.

    # 2) Mapping (user)
    wm_class = info.get("wm_class")
    if wm_class and wm_class in mapping:
        res["app_id"] = mapping[wm_class]
        res["app_name"] = mapping[wm_class]
        res["method"] = "mapping"
        return res

    # 3) .desktop Search (Flatpak/Package)
    desktop = find_desktop_for_wm_class(wm_class, info.get("wm_name"))
    if desktop:
        app_id, app_name = desktop
        res["app_id"] = app_id
        res["app_name"] = app_name
        res["method"] = "desktop_guess"
        return res

    # 4) Fallback: WM_CLASS / WM_NAME
    wm_name = info.get("wm_name")
    if wm_class:
        # If WM_CLASS is a known generic placeholder (e.g. "Steam_App_Default"
        # used by Lutris/Wine), prefer WM_NAME which the game usually sets to
        # its real title.  Only fall back to WM_CLASS when WM_NAME is absent.
        if wm_class.lower() in GENERIC_WM_CLASSES:
            if wm_name:
                res["app_id"] = wm_name
                res["app_name"] = wm_name
                res["method"] = "wm_name"
                return res
            # WM_NAME also empty, nothing useful, fall through to unknown.
        else:
            # Wine windows often have WM_CLASS = "GameName.exe", strip suffix.
            display = wm_class[:-4] if wm_class.lower().endswith(".exe") else wm_class
            res["app_id"] = display
            res["app_name"] = display
            res["method"] = "wm_class"
            return res
    if wm_name:
        res["app_id"] = wm_name
        res["app_name"] = wm_name
        res["method"] = "wm_name"
        return res

    res["method"] = "unknown"
    return res


if __name__ == "__main__":
    import argparse
    import pprint
    import time

    parser = argparse.ArgumentParser(description="Active window resolver test")
    parser.add_argument("--mapping", help="Path to json File", default=None)
    parser.add_argument(
        "--delay",
        type=int,
        default=3,
        help="Seconds to wait before capturing (default: 3)",
    )
    args = parser.parse_args()

    print(f"Switching to the target window... capturing in {args.delay}s")
    for i in range(args.delay, 0, -1):
        print(f"  {i}...")
        time.sleep(1)
    print("Capturing!")

    out = get_active_app(args.mapping)
    pprint.pprint(out)
