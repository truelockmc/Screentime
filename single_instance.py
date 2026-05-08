import socket

# Note to self: Change port if it collides with something else.
_LOCK_PORT = 47823


class InstanceAlreadyRunningError(Exception):
    pass


class SingleInstance:
    """
    Binds a TCP socket on localhost:_LOCK_PORT.
    As long as the object is alive the port stays bound, which prevents a
    second process from acquiring the same lock.
    The socket is released automatically when the process exits.
    """

    def __init__(self, port: int = _LOCK_PORT):
        self._port = port
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # SO_REUSEADDR wouldn't work
        try:
            self._sock.bind(("127.0.0.1", port))
        except OSError:
            self._sock.close()
            raise InstanceAlreadyRunningError(
                f"Another instance is already running (port {port} is taken)."
            )

    def release(self):
        """Explicitly release the lock (called on clean exit)."""
        try:
            self._sock.close()
        except Exception:
            pass

    def __del__(self):
        self.release()


# ── Helpers for main.py and data_manager.py ──────────────────────────────
import os
import sys


def get_data_dir() -> str:
    if os.environ.get("APPIMAGE"):
        data_dir = os.path.join(
            os.path.expanduser("~"), ".local", "share", "Screentime"
        )
    elif getattr(sys, "frozen", False):
        data_dir = os.path.dirname(sys.executable)
    else:
        data_dir = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(data_dir, exist_ok=True)
    return data_dir
