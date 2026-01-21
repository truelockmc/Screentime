#!/home/user/venv/bin/python
"""
Windows-only IconManager
    from icon_manager_windows import IconManager
    icon_manager = IconManager()
    icon = icon_manager.get_icon_for_app("notepad.exe" oder "notepad")
"""
from typing import Optional, List
import os
from pathlib import Path
import logging

import psutil
from io import BytesIO
from PIL import Image

from PyQt5 import QtWidgets
from PyQt5.QtCore import QByteArray
from PyQt5.QtGui import QPixmap, QIcon

logger = logging.getLogger(__name__)


class AppIcon:
    def __init__(self, identifier: str, raw_bytes: Optional[bytes] = None, qicon: Optional[QIcon] = None):
        self.__identifier = identifier
        self.__raw_bytes = raw_bytes
        self.__qicon = qicon

        if self.__qicon is None and self.__raw_bytes:
            try:
                qt_bytes = QByteArray(self.__raw_bytes)
                pixmap = QPixmap()
                pixmap.loadFromData(qt_bytes)
                if not pixmap.isNull():
                    self.__qicon = QIcon(pixmap)
                else:
                    self.__qicon = None
            except Exception:
                logger.exception("Fehler beim Erstellen von QIcon aus rohen Bytes für %s", identifier)
                self.__qicon = None

    def get_bytes(self) -> Optional[bytes]:
        return self.__raw_bytes

    def get_identifier(self) -> str:
        return self.__identifier

    def get_qicon(self) -> QIcon:
        if self.__qicon is not None and not self.__qicon.isNull():
            return self.__qicon
        if self.__raw_bytes:
            try:
                qt_bytes = QByteArray(self.__raw_bytes)
                pixmap = QPixmap()
                pixmap.loadFromData(qt_bytes)
                if not pixmap.isNull():
                    q = QIcon(pixmap)
                    self.__qicon = q
                    return q
            except Exception:
                logger.exception("Fehler beim Erzeugen von QIcon aus gecachten Bytes für %s", self.__identifier)
        return QtWidgets.QApplication.style().standardIcon(QtWidgets.QStyle.SP_FileIcon)


class IconManager:
    def __init__(self):
        self.app_icons: List[AppIcon] = []

    def _cache_icon(self, identifier: str, qicon: QIcon, raw_bytes: Optional[bytes] = None):
        try:
            self.app_icons = [t for t in self.app_icons if t.get_identifier() != identifier]
        except Exception:
            pass
        try:
            self.app_icons.append(AppIcon(identifier, raw_bytes=raw_bytes, qicon=qicon))
        except Exception:
            logger.exception("Fehler beim Cachen des Icons für %s", identifier)

    def get_icon_from_exe(self, exe_path: str) -> Optional[bytes]:
        if not exe_path:
            return None
        if extraction is None:
            return None

        try:
            try:
                icon_size = getattr(extraction, "IconSize", None)
                if icon_size is not None:
                    large = getattr(icon_size, "LARGE", None) or getattr(icon_size, "large", None) or icon_size
                    raw = extraction.extract_icon(exe_path, large)
                else:
                    raw = extraction.extract_icon(exe_path)
            except TypeError:
                raw = extraction.extract_icon(exe_path)
            if not raw:
                return None.
            try:
                img = Image.frombytes("RGBA", (32, 32), raw, "raw", "BGRA")
                buffer = BytesIO()
                img.save(buffer, format="ICO")
                return buffer.getvalue()
            except Exception:
                try:
                    img = Image.open(BytesIO(raw))
                    buffer = BytesIO()
                    img.save(buffer, format="ICO")
                    return buffer.getvalue()
                except Exception:
                    return raw
        except Exception:
            logger.exception("Fehler beim Extrahieren des Icons aus %s:", exe_path)
            return None

    def _qicon_from_bytes_or_theme(self, exe_path_or_name: Optional[str], icon_bytes: Optional[bytes]) -> QIcon:
        try:
            if icon_bytes:
                qt_bytes = QByteArray(icon_bytes)
                pixmap = QPixmap()
                pixmap.loadFromData(qt_bytes)
                if not pixmap.isNull():
                    return QIcon(pixmap)
        except Exception:
            logger.exception("Fehler beim Erzeugen von QIcon aus Bytes:")

        try:
            base = os.path.basename(exe_path_or_name) if exe_path_or_name else ""
            name, _ = os.path.splitext(base)
            if name:
                q = QIcon.fromTheme(name)
                if not q.isNull():
                    return q
        except Exception:
            logger.exception("Fehler beim Laden von Theme-Icon für %s:", exe_path_or_name)

        return QtWidgets.QApplication.style().standardIcon(QtWidgets.QStyle.SP_FileIcon)

    def get_icon_for_app(self, app_name: str) -> QIcon:
        try:
            if not app_name:
                return QtWidgets.QApplication.style().standardIcon(QtWidgets.QStyle.SP_FileIcon)

            # 1) Cache
            for icon in list(self.app_icons):
                if app_name == icon.get_identifier():
                    q = icon.get_qicon()
                    if q is not None and not q.isNull():
                        return q
                    try:
                        self.app_icons.remove(icon)
                    except Exception:
                        pass
                    break

            # 2) Suche Prozesse nach name / exe basename
            for proc in psutil.process_iter(['name', 'exe', 'cmdline']):
                try:
                    pname = proc.info.get('name') or ""
                    pexe = proc.info.get('exe') or ""
                    if not pname and not pexe:
                        continue
                    if pname.lower() == app_name.lower() or Path(pexe).stem.lower() == Path(app_name).stem.lower():
                        icon_bytes = None
                        if pexe:
                            icon_bytes = self.get_icon_from_exe(pexe)
                        if icon_bytes:
                            try:
                                qt_bytes = QByteArray(icon_bytes)
                                pixmap = QPixmap()
                                pixmap.loadFromData(qt_bytes)
                                if not pixmap.isNull():
                                    qicon = QIcon(pixmap)
                                    self._cache_icon(app_name, qicon, raw_bytes=icon_bytes)
                                    return qicon
                            except Exception:
                                logger.exception("Fehler beim Erzeugen von QIcon aus Windows-Bytes für %s", app_name)
                        qicon = self._qicon_from_bytes_or_theme(pexe or pname, None)
                        self._cache_icon(app_name, qicon, raw_bytes=None)
                        return qicon
                except Exception:
                    continue

            # 3) Fallback: Theme icon by app_name
            try:
                q = QIcon.fromTheme(Path(app_name).stem)
                if q and not q.isNull():
                    self._cache_icon(app_name, q, raw_bytes=None)
                    return q
            except Exception:
                logger.exception("Fehler beim Laden von Theme-Icon für %s", app_name)

            # 4) final fallback
            fallback = QtWidgets.QApplication.style().standardIcon(QtWidgets.QStyle.SP_FileIcon)
            self._cache_icon(app_name, fallback, raw_bytes=None)
            return fallback

        except Exception:
            logger.exception("Fehler beim Abrufen des Icons für %s:", app_name)
            return QtWidgets.QApplication.style().standardIcon(QtWidgets.QStyle.SP_FileIcon)
