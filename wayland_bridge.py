"""
wayland_bridge.py

Receives window activation events from the KWin script (screentime-kwin)
via D-Bus and makes them available to the rest of the app.

Only works under Plasma/KWin (X11 AND Wayland), other
Wayland compositors (GNOME/Mutter, Sway, ...) would require a different
extension type thingy, since there is no cross-platform protocol that exposes
the "active window" title/PID on wayland.

D-Bus:
    Service:   org.screentime.ActiveWindow
    Object:    /ActiveWindow
    Interface: org.screentime.ActiveWindow
    Method:    SetActiveWindow(int pid, string wm_class, string caption, string desktop_file)
"""

import logging
import time

from PyQt5 import QtCore, QtDBus

logger = logging.getLogger(__name__)

SERVICE_NAME = "org.screentime.ActiveWindow"
OBJECT_PATH = "/ActiveWindow"
INTERFACE_NAME = "org.screentime.ActiveWindow"


class _ActiveWindowAdaptor(QtDBus.QDBusAbstractAdaptor):
    """D-Bus Adapter: explicitly sets the interface name (QDBusConnection.
        registerObject alone would otherwise derive the interface name from the Python
        class name, not from INTERFACE_NAME)."""

    QtCore.Q_CLASSINFO("D-Bus Interface", INTERFACE_NAME)

    def __init__(self, receiver: "_ActiveWindowReceiver"):
        super().__init__(receiver)
        self.setAutoRelaySignals(True)
        self._receiver = receiver

    @QtCore.pyqtSlot(int, str, str, str)
    def SetActiveWindow(self, pid: int, wm_class: str, caption: str, desktop_file: str):
        self._receiver.SetActiveWindow(pid, wm_class, caption, desktop_file)


class _ActiveWindowReceiver(QtCore.QObject):
    """Gets called via D-Bus from the KWin-Script."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pid = None
        self._wm_class = None
        self._caption = None
        self._desktop_file = None
        self._last_update = 0.0

    @QtCore.pyqtSlot(int, str, str, str)
    def SetActiveWindow(self, pid: int, wm_class: str, caption: str, desktop_file: str):
        self._pid = pid if pid > 0 else None
        self._wm_class = wm_class or None
        self._caption = caption or None
        self._desktop_file = desktop_file or None
        self._last_update = time.monotonic()
        logger.debug(
            "wayland_bridge: active window pid=%s wm_class=%s caption=%s desktop_file=%s",
            self._pid, self._wm_class, self._caption, self._desktop_file,
        )

    def snapshot(self):
        return {
            "wm_pid": str(self._pid) if self._pid else None,
            "wm_class": self._wm_class,
            "wm_name": self._caption,
            "desktop_file": self._desktop_file,
        }

    @property
    def has_data(self) -> bool:
        # 0.0 means: never received an Event -> Script probably
        # not installed/active, fallback to "Wayland PC" Entry
        return self._last_update > 0.0

    @property
    def has_active_window(self) -> bool:
        # Script explicitly reports pid=0/empty strings, if NO window is active anymore.
        # This is an active, valid State ("nothing active") and not the same as has_data=False ("Script never sends data")
        return self._pid is not None or self._wm_class or self._caption or self._desktop_file

    @property
    def seconds_since_update(self) -> float:
        if self._last_update == 0.0:
            return float("inf")
        return time.monotonic() - self._last_update


_receiver: "_ActiveWindowReceiver | None" = None


def start() -> "_ActiveWindowReceiver | None":
    """
    Registers the D-Bus service. Must be called AFTER QApplication/QCoreApplication exists.
    Gives back the Receiver or None if registration failed (e.g. QtBus is missing.)
    """
    global _receiver

    bus = QtDBus.QDBusConnection.sessionBus()
    if not bus.isConnected():
        logger.warning("wayland_bridge: Session Bus unreachable.")
        return None

    _receiver = _ActiveWindowReceiver()
    _adaptor = _ActiveWindowAdaptor(_receiver)  # noqa: F841 lives on _receiver as a parent
    _receiver._adaptor = _adaptor  # Hold reference so python does not GC it xD

    if not bus.registerObject(OBJECT_PATH, _receiver):
        logger.warning("wayland_bridge: registerObject failed: %s",
                        bus.lastError().message())
        return None

    if not bus.registerService(SERVICE_NAME):
        logger.warning("wayland_bridge: registerService failed (%s): %s",
                        SERVICE_NAME, bus.lastError().message())
        return None

    logger.info("wayland_bridge: D-Bus service %s ready.", SERVICE_NAME)
    return _receiver


def get_receiver() -> "_ActiveWindowReceiver | None":
    return _receiver
