#!/usr/bin/env bash
# Instala o Pinclip: cópia em ~/.local/bin, autostart na sessão e atalho Super+V no GNOME.
#
# Duas formas de uso:
#   ./install.sh                                   a partir de um clone (usa o pinclip.py ao lado)
#   curl -fsSL https://raw.githubusercontent.com/gustavomaritan/pinclip/main/install.sh | bash
#
# PINCLIP_REF escolhe a versão baixada no modo curl (padrão: main). Ex.: PINCLIP_REF=v0.1.0.
# Rode de novo para atualizar.
set -euo pipefail

REPO="gustavomaritan/pinclip"
REF="${PINCLIP_REF:-main}"
BIN="$HOME/.local/bin/pinclip"
AUTOSTART="$HOME/.config/autostart/pinclip.desktop"
KB_PATH="/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/pinclip/"
KB_SCHEMA="org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:$KB_PATH"

die() { echo "Erro: $*" >&2; exit 1; }

check_session() {
  if [[ "${XDG_SESSION_TYPE:-}" == "wayland" ]]; then
    die "o Pinclip só funciona em sessão X11 e esta sessão é Wayland.
No Ubuntu, saia da sessão e escolha \"Ubuntu on Xorg\" na engrenagem da tela de login."
  fi
  [[ -n "${DISPLAY:-}" ]] || die "nenhum display X11 encontrado (\$DISPLAY vazio). Rode dentro da sessão gráfica."
}

check_deps() {
  local missing=()
  command -v python3 >/dev/null || missing+=("python3")
  if command -v python3 >/dev/null && ! python3 -c '
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("GdkX11", "3.0")
from gi.repository import Gtk, GdkX11' 2>/dev/null; then
    missing+=("PyGObject com GTK 3")
  fi
  ldconfig -p 2>/dev/null | grep -q 'libXtst\.so\.6' || missing+=("libXtst")
  if (( ${#missing[@]} )); then
    die "faltam dependências: ${missing[*]}.
  Ubuntu/Debian: sudo apt install python3 python3-gi gir1.2-gtk-3.0 libxtst6
  Fedora:        sudo dnf install python3 python3-gobject gtk3 libXtst
  Arch:          sudo pacman -S python python-gobject gtk3 libxtst"
  fi
}

fetch_source() {
  local dest="$1" url="https://raw.githubusercontent.com/$REPO/$REF/pinclip.py"
  if command -v curl >/dev/null; then
    curl -fsSL "$url" -o "$dest"
  elif command -v wget >/dev/null; then
    wget -qO "$dest" "$url"
  else
    die "precisa de curl ou wget para baixar o Pinclip."
  fi
  python3 -m py_compile "$dest" || die "o arquivo baixado de $url não é um Python válido."
}

install_files() {
  local here="" src
  # Com "curl | bash" o BASH_SOURCE é vazio ou aponta para um pipe, então não há clone ao lado.
  [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]] && here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [[ -n "$here" && -f "$here/pinclip.py" ]]; then
    src="$here/pinclip.py"
  else
    src="$(mktemp)"
    # Expande já: no EXIT a variável local não existe mais.
    trap "rm -f '$src'" EXIT
    echo "Baixando pinclip.py ($REPO@$REF)…"
    fetch_source "$src"
  fi

  mkdir -p "$(dirname "$BIN")" "$(dirname "$AUTOSTART")"
  # Copia em vez de linkar: o fonte pode estar num disco que não é montado no login.
  rm -f "$BIN"
  install -m 755 "$src" "$BIN"

  cat > "$AUTOSTART" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Pinclip
Comment=Histórico da área de transferência
Exec=$BIN --daemon
X-GNOME-Autostart-enabled=true
DESKTOP
}

has_schema() { gsettings list-schemas 2>/dev/null | grep -qx "$1"; }

setup_gnome_shortcut() {
  if ! command -v gsettings >/dev/null || ! has_schema org.gnome.settings-daemon.plugins.media-keys; then
    return 1
  fi
  # Super+V é usado pelo GNOME para a bandeja de notificações (Super+M continua fazendo isso).
  if has_schema org.gnome.shell.keybindings; then
    gsettings set org.gnome.shell.keybindings toggle-message-tray "['<Super>m']"
  fi

  local current new
  current=$(gsettings get org.gnome.settings-daemon.plugins.media-keys custom-keybindings)
  if [[ "$current" != *"$KB_PATH"* ]]; then
    if [[ "$current" == "@as []" || "$current" == "[]" ]]; then
      new="['$KB_PATH']"
    else
      new="${current%]}, '$KB_PATH']"
    fi
    gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "$new"
  fi
  gsettings set "$KB_SCHEMA" name "Pinclip"
  gsettings set "$KB_SCHEMA" command "$BIN"
  gsettings set "$KB_SCHEMA" binding "<Super>v"
}

restart_daemon() {
  # Espera o antigo sair de fato, senão o novo encontra a instância única (D-Bus) ainda ativa
  # e só repassa a chamada.
  pkill -f "^python3 $BIN --daemon\$" || true
  while pgrep -f "^python3 $BIN --daemon\$" >/dev/null; do sleep 0.1; done
  nohup "$BIN" --daemon >/dev/null 2>&1 &
}

main() {
  check_session
  check_deps
  install_files
  restart_daemon
  if setup_gnome_shortcut; then
    echo "Pinclip instalado. Use Super+V."
  else
    echo "Pinclip instalado, mas o atalho não foi criado (este desktop não é GNOME)."
    echo "Crie um atalho de teclado (ex.: Super+V) nas configurações do seu desktop com o comando:"
    echo "  $BIN"
  fi
}

# Tudo dentro de main: com "curl | bash", um download interrompido não executa metade do script.
main "$@"
