#!/usr/bin/env bash
# Remove o Pinclip e restaura o Super+V original do GNOME. Mantém o histórico em ~/.local/share/pinclip.
#   ./uninstall.sh
#   curl -fsSL https://raw.githubusercontent.com/gustavomaritan/pinclip/main/uninstall.sh | bash
set -euo pipefail

BIN="$HOME/.local/bin/pinclip"
KB_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/pinclip/"
KB_SCHEMA="org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$KB_PATH"

has_schema() { gsettings list-schemas 2>/dev/null | grep -qx "$1"; }

main() {
  pkill -f "^python3 $BIN --daemon\$" || true
  rm -f "$BIN" "$HOME/.config/autostart/pinclip.desktop"

  if command -v gsettings >/dev/null && has_schema org.gnome.settings-daemon.plugins.media-keys; then
    gsettings reset "$KB_SCHEMA" name
    gsettings reset "$KB_SCHEMA" command
    gsettings reset "$KB_SCHEMA" binding
    local current new
    current=$(gsettings get org.gnome.settings-daemon.plugins.media-keys custom-keybindings)
    new=$(echo "$current" | sed -e "s#'$KB_PATH'##" -e 's/\[, /[/' -e 's/, \]/]/' -e 's/, ,/,/')
    [[ "$new" == "[]" ]] && new="@as []"
    gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "$new"
    if has_schema org.gnome.shell.keybindings; then
      gsettings reset org.gnome.shell.keybindings toggle-message-tray
    fi
  fi

  echo "Pinclip removido. Histórico preservado em ~/.local/share/pinclip (apague manualmente se quiser)."
}

main "$@"
