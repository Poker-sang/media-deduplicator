from __future__ import annotations

import ctypes
from ctypes import wintypes


def _drag_form(form, user32, cursor, normal_state, maximized_state) -> None:
    # The bridge runs asynchronously; ignore drags whose mouse button was released.
    if not user32.GetAsyncKeyState(0x01) & 0x8000:
        return
    if form.WindowState == maximized_state:
        bounds = form.Bounds
        horizontal = max(0, min(1, (cursor.X - bounds.Left) / bounds.Width))
        title_offset = max(0, min(cursor.Y - bounds.Top, round(36 * form.DeviceDpi / 96)))
        form.WindowState = normal_state
        # WinForms bounds and cursor coordinates both use physical pixels.
        form.Left = round(cursor.X - form.Width * horizontal)
        form.Top = cursor.Y - title_offset
    user32.ReleaseCapture()
    position = (cursor.X & 0xFFFF) | ((cursor.Y & 0xFFFF) << 16)
    # Let Windows own the move loop, including capture, snapping and mouse release.
    user32.SendMessageW(form.Handle.ToInt64(), 0x00A1, 2, position)


def start_window_drag(window) -> None:
    from System import Action
    from System.Windows.Forms import Cursor, FormWindowState

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.ReleaseCapture.argtypes = []
    user32.ReleaseCapture.restype = wintypes.BOOL
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    form = window.native
    form.Invoke(Action(lambda: _drag_form(
        form, user32, Cursor.Position, FormWindowState.Normal, FormWindowState.Maximized,
    )))
