#!/usr/bin/env python3
"""Pinclip — gerenciador simples de área de transferência (estilo Win+V) para X11/GTK3.

Uso:
    pinclip.py --daemon   inicia em segundo plano monitorando a área de transferência
    pinclip.py            abre a janela de histórico (inicia o daemon se não estiver rodando)
"""
import ctypes
import hashlib
import json
import os
import sys
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
from gi.repository import Gdk, GdkPixbuf, GdkX11, Gio, GLib, Gtk, Pango  # noqa: E402

APP_ID = "io.github.gustavomaritan.Pinclip"
MAX_UNPINNED = 50
DATA_DIR = os.path.join(GLib.get_user_data_dir(), "pinclip")
IMAGES_DIR = os.path.join(DATA_DIR, "images")
HISTORY_FILE = os.path.join(DATA_DIR, "history.json")
# Gerenciadores de senha (KeePassXC etc.) marcam o conteúdo com este target para não ser salvo.
SECRET_HINT_TARGET = "x-kde-passwordManagerHint"
PASTE_DELAY_MS = 150


class History:
    """Histórico persistido em JSON; imagens ficam como PNG em IMAGES_DIR."""

    def __init__(self):
        os.makedirs(IMAGES_DIR, exist_ok=True)
        self.items = []
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, encoding="utf-8") as f:
                self.items = json.load(f)

    def save(self):
        tmp = HISTORY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.items, f, ensure_ascii=False)
        os.replace(tmp, HISTORY_FILE)

    def _find(self, item_id):
        for item in self.items:
            if item["id"] == item_id:
                return item
        return None

    def add_text(self, text):
        item_id = "t-" + hashlib.sha1(text.encode("utf-8")).hexdigest()
        self._push(item_id, {"kind": "text", "text": text})

    def add_image(self, pixbuf):
        raw = pixbuf.get_pixels() + f"{pixbuf.get_width()}x{pixbuf.get_height()}".encode()
        item_id = "i-" + hashlib.sha1(raw).hexdigest()
        path = os.path.join(IMAGES_DIR, item_id + ".png")
        if not os.path.exists(path):
            pixbuf.savev(path, "png", [], [])
        self._push(item_id, {"kind": "image", "image": path})

    def _push(self, item_id, fields):
        existing = self._find(item_id)
        pinned = existing["pinned"] if existing else False
        if existing:
            self.items.remove(existing)
        self.items.insert(0, {"id": item_id, "pinned": pinned, "ts": time.time(), **fields})
        self._trim()
        self.save()

    def _trim(self):
        unpinned = [i for i in self.items if not i["pinned"]]
        for item in unpinned[MAX_UNPINNED:]:
            self._delete(item)

    def _delete(self, item):
        self.items.remove(item)
        if item["kind"] == "image":
            os.remove(item["image"])

    def remove(self, item_id):
        self._delete(self._find(item_id))
        self.save()

    def toggle_pin(self, item_id):
        item = self._find(item_id)
        item["pinned"] = not item["pinned"]
        self._trim()
        self.save()

    def clear_unpinned(self):
        for item in [i for i in self.items if not i["pinned"]]:
            self._delete(item)
        self.save()


class XPaster:
    """Simula Ctrl+V na janela focada via extensão XTest (libXtst)."""

    def __init__(self):
        self.x11 = ctypes.cdll.LoadLibrary("libX11.so.6")
        self.xtst = ctypes.cdll.LoadLibrary("libXtst.so.6")
        self.x11.XOpenDisplay.restype = ctypes.c_void_p
        self.x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.x11.XStringToKeysym.restype = ctypes.c_ulong
        self.x11.XStringToKeysym.argtypes = [ctypes.c_char_p]
        self.x11.XKeysymToKeycode.restype = ctypes.c_ubyte
        self.x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.x11.XFlush.argtypes = [ctypes.c_void_p]
        self.xtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
        self.dpy = self.x11.XOpenDisplay(None)
        if not self.dpy:
            raise RuntimeError("Não foi possível abrir o display X11")

    def _keycode(self, name):
        return self.x11.XKeysymToKeycode(self.dpy, self.x11.XStringToKeysym(name.encode()))

    def ctrl_v(self):
        ctrl, v = self._keycode("Control_L"), self._keycode("v")
        for code, down in ((ctrl, 1), (v, 1), (v, 0), (ctrl, 0)):
            self.xtst.XTestFakeKeyEvent(self.dpy, code, down, 0)
        self.x11.XFlush(self.dpy)


class HistoryWindow(Gtk.Window):
    def __init__(self, app):
        super().__init__(title="Área de transferência", application=app)
        self.app = app
        self.set_default_size(420, 520)
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.connect("focus-out-event", lambda *_: self.hide())
        self.connect("key-press-event", self._on_key)
        self.connect("delete-event", lambda *_: self.hide() or True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=8)
        header = Gtk.Box(spacing=6)
        self.search = Gtk.SearchEntry(placeholder_text="Buscar…")
        self.search.connect("search-changed", lambda *_: self.listbox.invalidate_filter())
        clear_btn = Gtk.Button(label="Limpar", tooltip_text="Apaga tudo que não está fixado")
        clear_btn.connect("clicked", self._on_clear)
        header.pack_start(self.search, True, True, 0)
        header.pack_start(clear_btn, False, False, 0)

        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.BROWSE)
        self.listbox.set_filter_func(self._filter)
        self.listbox.connect("row-activated", lambda _, row: self._paste(row.item))
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.add(self.listbox)

        box.pack_start(header, False, False, 0)
        box.pack_start(scroller, True, True, 0)
        self.add(box)

    def _filter(self, row):
        query = self.search.get_text().strip().lower()
        if not query:
            return True
        return row.item["kind"] == "text" and query in row.item["text"].lower()

    def refresh(self):
        for row in self.listbox.get_children():
            self.listbox.remove(row)
        # Ordem cronológica pura (mais recente no topo), fixados inclusive.
        items = self.app.history.items
        if not items:
            empty = Gtk.Label(label="Nada copiado ainda.", margin=24)
            empty.get_style_context().add_class("dim-label")
            self.listbox.add(empty)
        for item in items:
            self.listbox.add(self._make_row(item))
        self.listbox.show_all()
        first = self.listbox.get_row_at_index(0)
        if first is not None:
            self.listbox.select_row(first)

    def _make_row(self, item):
        row = Gtk.ListBoxRow()
        row.item = item
        hbox = Gtk.Box(spacing=6, margin=6)

        if item["kind"] == "text":
            preview = Gtk.Label(label=item["text"][:500], xalign=0, lines=3, max_width_chars=40)
            preview.set_line_wrap(True)
            preview.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            preview.set_ellipsize(Pango.EllipsizeMode.END)
        else:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(item["image"], 300, 110, True)
            preview = Gtk.Image.new_from_pixbuf(pixbuf)
            preview.set_halign(Gtk.Align.START)

        pin_icon = "starred-symbolic" if item["pinned"] else "non-starred-symbolic"
        pin_btn = Gtk.Button.new_from_icon_name(pin_icon, Gtk.IconSize.BUTTON)
        pin_btn.set_tooltip_text("Desafixar" if item["pinned"] else "Fixar")
        pin_btn.set_relief(Gtk.ReliefStyle.NONE)
        pin_btn.set_valign(Gtk.Align.START)
        pin_btn.connect("clicked", lambda *_: self._mutate(self.app.history.toggle_pin, item["id"]))

        del_btn = Gtk.Button.new_from_icon_name("edit-delete-symbolic", Gtk.IconSize.BUTTON)
        del_btn.set_tooltip_text("Remover")
        del_btn.set_relief(Gtk.ReliefStyle.NONE)
        del_btn.set_valign(Gtk.Align.START)
        del_btn.connect("clicked", lambda *_: self._mutate(self.app.history.remove, item["id"]))

        hbox.pack_start(preview, True, True, 0)
        hbox.pack_start(pin_btn, False, False, 0)
        hbox.pack_start(del_btn, False, False, 0)
        row.add(hbox)
        return row

    def _mutate(self, action, item_id):
        action(item_id)
        self.refresh()

    def _on_clear(self, *_):
        self.app.history.clear_unpinned()
        self.refresh()

    def _selected_item(self):
        row = self.listbox.get_selected_row()
        return getattr(row, "item", None) if row else None

    def _move_selection(self, step):
        visible = [r for r in self.listbox.get_children() if r.get_child_visible() and hasattr(r, "item")]
        if not visible:
            return
        current = self.listbox.get_selected_row()
        idx = visible.index(current) if current in visible else -1
        target = visible[max(0, min(len(visible) - 1, idx + step))]
        self.listbox.select_row(target)
        target.grab_focus()
        self.search.grab_focus_without_selecting()

    def _on_key(self, _widget, event):
        key = event.keyval
        if key == Gdk.KEY_Escape:
            self.hide()
        elif key == Gdk.KEY_Down:
            self._move_selection(1)
        elif key == Gdk.KEY_Up:
            self._move_selection(-1)
        elif key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            item = self._selected_item()
            if item:
                self._paste(item)
        elif key == Gdk.KEY_Delete and not self.search.get_text():
            item = self._selected_item()
            if item:
                self._mutate(self.app.history.remove, item["id"])
        else:
            return False
        return True

    def _paste(self, item):
        self.app.set_clipboard(item)
        self.hide()
        GLib.timeout_add(PASTE_DELAY_MS, self.app.paster_ctrl_v)

    def popup(self):
        self.search.set_text("")
        self.refresh()
        display = Gdk.Display.get_default()
        _, x, y = display.get_default_seat().get_pointer().get_position()
        geo = display.get_monitor_at_point(x, y).get_geometry()
        width, height = self.get_size()
        self.move(
            max(geo.x, min(x - width // 2, geo.x + geo.width - width)),
            max(geo.y, min(y - 20, geo.y + geo.height - height)),
        )
        self.show_all()
        self.realize()
        # Usa o timestamp do servidor X para o GNOME não bloquear o foco (focus-stealing prevention).
        self.present_with_time(GdkX11.x11_get_server_time(self.get_window()))
        self.search.grab_focus()


class PinclipApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.history = None
        self.window = None
        self.paster = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.hold()  # mantém o processo vivo mesmo sem janela visível
        self.history = History()
        self.paster = XPaster()
        self.window = HistoryWindow(self)
        self.clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        self.clipboard.connect("owner-change", self._on_owner_change)

    def do_command_line(self, command_line):
        if "--daemon" not in command_line.get_arguments()[1:]:
            self.window.popup()
        return 0

    def _on_owner_change(self, clipboard, _event):
        clipboard.request_targets(self._on_targets)

    def _on_targets(self, clipboard, atoms, *_):
        if not atoms:
            return
        if any(a.name() == SECRET_HINT_TARGET for a in atoms):
            return
        if Gtk.targets_include_image(atoms, False):
            clipboard.request_image(self._on_image)
        elif Gtk.targets_include_text(atoms):
            clipboard.request_text(self._on_text)

    def _on_text(self, _clipboard, text, *_):
        if text and text.strip():
            self.history.add_text(text)
            self._refresh_if_visible()

    def _on_image(self, _clipboard, pixbuf, *_):
        if pixbuf is not None:
            self.history.add_image(pixbuf)
            self._refresh_if_visible()

    def _refresh_if_visible(self):
        if self.window.get_visible():
            self.window.refresh()

    def set_clipboard(self, item):
        if item["kind"] == "text":
            self.clipboard.set_text(item["text"], -1)
        else:
            self.clipboard.set_image(GdkPixbuf.Pixbuf.new_from_file(item["image"]))

    def paster_ctrl_v(self):
        self.paster.ctrl_v()
        return GLib.SOURCE_REMOVE


if __name__ == "__main__":
    sys.exit(PinclipApp().run(sys.argv))
