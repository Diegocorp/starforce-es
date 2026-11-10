#!/usr/bin/env bash
# Parche en español de Mega Man Star Force Legacy Collection: desinstalador para Steam Deck y Linux.
# Uso: abre una terminal en esta carpeta y escribe:  bash Desinstalar_SteamDeck.sh
cd "$(dirname "$0")" || exit 1
PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ] || ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    echo "No encontré Python 3.11 o una versión más reciente (el Steam Deck ya lo trae; en otro Linux, instálalo con el gestor de paquetes)."
    read -r -p "Presiona Enter para salir. " _
    exit 1
fi
"$PY" -I -B -X utf8 programa/instalador.py desinstalar "$@"
status=$?
echo
read -r -p "Presiona Enter para terminar. " _
exit $status
