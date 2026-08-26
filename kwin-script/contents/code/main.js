function report(win) {
    if (!win) return;

    var pid = win.pid || 0;
    var wmClass = win.resourceClass ? win.resourceClass.toString() : "";
    var caption = win.caption ? win.caption.toString() : "";
    var desktopFile = win.desktopFileName ? win.desktopFileName.toString() : "";

    callDBus(
        "org.screentime.ActiveWindow",
        "/ActiveWindow",
        "org.screentime.ActiveWindow",
        "SetActiveWindow",
        pid,
        wmClass,
        caption,
        desktopFile
    );
}

// report on every focus change
workspace.windowActivated.connect(report);

// report first active window directly on script start -> application does not have to wait for first window change
if (workspace.activeWindow) {
    report(workspace.activeWindow);
}
