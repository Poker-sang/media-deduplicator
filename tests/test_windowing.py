from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from photo_deduplicator.windowing import _drag_form


class Form:
    DeviceDpi = 144
    Handle = SimpleNamespace(ToInt64=lambda: 0x100000001)

    def __init__(self, state="maximized", left=-1920, top=0):
        self._state = state
        self.Width = 1920 if state == "maximized" else 1280
        self.Left, self.Top = left, top
        self.Bounds = SimpleNamespace(Left=left, Top=top, Width=self.Width)

    @property
    def WindowState(self):
        return self._state

    @WindowState.setter
    def WindowState(self, state):
        self._state = state
        self.Width = 1280


class WindowDragTests(TestCase):
    def test_maximized_drag_restores_size_and_keeps_cursor_anchor_on_negative_monitor(self):
        form = Form()
        user32 = Mock()
        user32.GetAsyncKeyState.return_value = -32768
        cursor = SimpleNamespace(X=-480, Y=24)
        _drag_form(form, user32, cursor, "normal", "maximized")
        self.assertEqual(form.WindowState, "normal")
        self.assertEqual(form.Width, 1280)
        self.assertEqual((form.Left, form.Top), (-1440, 0))
        user32.ReleaseCapture.assert_called_once_with()
        user32.SendMessageW.assert_called_once_with(0x100000001, 0xA1, 2, (24 << 16) | (-480 & 0xFFFF))

    def test_normal_drag_preserves_size_and_position_for_native_move_loop(self):
        form = Form("normal", 200, 150)
        user32 = Mock()
        user32.GetAsyncKeyState.return_value = 0x8000
        _drag_form(form, user32, SimpleNamespace(X=500, Y=170), "normal", "maximized")
        self.assertEqual((form.WindowState, form.Width, form.Left, form.Top), ("normal", 1280, 200, 150))
        user32.SendMessageW.assert_called_once()

    def test_releasing_mouse_before_bridge_call_does_not_restore_or_start_move(self):
        form = Form()
        user32 = Mock()
        user32.GetAsyncKeyState.return_value = 0
        _drag_form(form, user32, SimpleNamespace(X=200, Y=24), "normal", "maximized")
        self.assertEqual(form.WindowState, "maximized")
        user32.ReleaseCapture.assert_not_called()
        user32.SendMessageW.assert_not_called()
