#!/usr/bin/env python3
"""Helper de teste: coloca um texto ou uma imagem sólida na área de transferência e sai.

Uso:
    python3 dev/clip_copy.py text "conteúdo"
    python3 dev/clip_copy.py image
"""
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

# Mantém o processo vivo um pouco: no X11 o dono precisa existir enquanto o daemon lê o conteúdo.
HOLD_MS = 800


def main():
    clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    kind = sys.argv[1]
    if kind == "text":
        clipboard.set_text(sys.argv[2], -1)
    elif kind == "image":
        pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 64, 32)
        pixbuf.fill(0xFF0000FF)
        clipboard.set_image(pixbuf)
    else:
        raise SystemExit(f"tipo desconhecido: {kind} (use text|image)")
    GLib.timeout_add(HOLD_MS, Gtk.main_quit)
    Gtk.main()


if __name__ == "__main__":
    main()
