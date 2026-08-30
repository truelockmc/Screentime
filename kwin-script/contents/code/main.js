// Reports PID, WM_CLASS-Equvalent (resourceClass), Window titel and the .desktop filename of the active application
// to the Screentime-App on every window change via D-Bus

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

function reportEmpty() {
    // No active window -> report pid 0 so the app does not continue reporting the last active process which is already gone
    callDBus(
        "org.screentime.ActiveWindow",
        "/ActiveWindow",
        "org.screentime.ActiveWindow",
        "SetActiveWindow",
        0, "", "", ""
    );
}

var trackedWindow = null;

function onActivated(win) {
    // If the previously active window gets closed, without a new one being activated
    if (trackedWindow) {
        try { trackedWindow.closed.disconnect(onTrackedWindowClosed); } catch (e) {}
        try { trackedWindow.captionChanged.disconnect(onTrackedCaptionChanged); } catch (e) {}
    }

    trackedWindow = win || null;

    if (trackedWindow) {
        // Some applications (wine/proton), set their real title only after activation
        trackedWindow.captionChanged.connect(onTrackedCaptionChanged);
        trackedWindow.closed.connect(onTrackedWindowClosed);
        report(trackedWindow);
    } else {
        reportEmpty();
    }
}

function onTrackedCaptionChanged() {
    if (trackedWindow) report(trackedWindow);
}

function onTrackedWindowClosed() {
    if (trackedWindow) {
        try { trackedWindow.captionChanged.disconnect(onTrackedCaptionChanged); } catch (e) {}
    }
    trackedWindow = null;
    reportEmpty();
}

workspace.windowActivated.connect(onActivated);

// Report first active window directly on script start -> application does not have to wait for first window change
onActivated(workspace.activeWindow);
