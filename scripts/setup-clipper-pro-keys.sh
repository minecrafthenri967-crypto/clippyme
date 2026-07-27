#!/usr/bin/env bash
#
# Fragt die API-Schlüssel ab, schreibt sie in .env und prüft sie sofort.
#
#   bash scripts/setup-clipper-pro-keys.sh
#
# Kann jederzeit erneut gestartet werden: bestehende Werte bleiben stehen, wenn
# man bei der Frage einfach Enter drückt. Repariert auch eine .env, in der eine
# Zeile auskommentiert, doppelt oder kaputt ist.
#
# Die Schlüssel werden verdeckt eingegeben und NIE ausgegeben — die Prüfung am
# Ende zeigt nur "gesetzt/fehlt" plus das Ergebnis eines echten Test-Aufrufs.

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1

ENV_FILE=".env"
TEMPLATE=".env.example"

bold=$(tput bold 2>/dev/null || true)
dim=$(tput dim 2>/dev/null || true)
red=$(tput setaf 1 2>/dev/null || true)
green=$(tput setaf 2 2>/dev/null || true)
yellow=$(tput setaf 3 2>/dev/null || true)
reset=$(tput sgr0 2>/dev/null || true)

say() { printf '%s\n' "$*"; }
ok() { printf '%s✅ %s%s\n' "$green" "$*" "$reset"; }
warn() { printf '%s⚠️  %s%s\n' "$yellow" "$*" "$reset"; }
err() { printf '%s❌ %s%s\n' "$red" "$*" "$reset"; }

# --- .env bereitstellen ----------------------------------------------------

if [[ ! -f "$ENV_FILE" ]]; then
    if [[ ! -f "$TEMPLATE" ]]; then
        err "Weder $ENV_FILE noch $TEMPLATE gefunden. Läuft das Skript im clippyme-Verzeichnis?"
        exit 1
    fi
    cp "$TEMPLATE" "$ENV_FILE"
    ok "$ENV_FILE aus $TEMPLATE erstellt."
fi
chmod 600 "$ENV_FILE" 2>/dev/null || true

# Liest den aktuellen Wert einer Variable (nur echte, nicht auskommentierte).
current_value() {
    sed -nE "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*(.*)$/\1/p" "$ENV_FILE" | tail -n1
}

# Ersetzt eine Variable: entfernt jede vorhandene Definition (auch
# auskommentierte oder doppelte) und hängt genau eine korrekte Zeile an.
set_var() {
    local name="$1" value="$2"
    sed -i -E "/^[[:space:]]*#?[[:space:]]*${name}[[:space:]]*=/d" "$ENV_FILE"
    printf '%s=%s\n' "$name" "$value" >>"$ENV_FILE"
}

# Eine .env, in die ein Schlüssel ohne Variablennamen geraten ist, ist kaputt —
# so eine nackte Zeile würde die Datei für jeden Leser unbrauchbar machen.
if grep -qE '^[[:space:]]*(sk-|AIza)[A-Za-z0-9_-]{10,}[[:space:]]*$' "$ENV_FILE"; then
    sed -i -E '/^[[:space:]]*(sk-|AIza)[A-Za-z0-9_-]{10,}[[:space:]]*$/d' "$ENV_FILE"
    warn "Eine Zeile enthielt nur einen Schlüssel ohne Variablennamen — entfernt."
fi

# Platzhalter aus einer früheren Kopier-Aktion sind kein echter Wert.
strip_placeholder() {
    local name="$1" value
    value="$(current_value "$name")"
    case "$value" in
        DEIN-* | dein-* | *DEIN_* | "<"*">") set_var "$name" "" ;;
    esac
}

# --- Abfrage ---------------------------------------------------------------

say
say "${bold}AI-Clipper Pro — API-Schlüssel einrichten${reset}"
say "${dim}Eingabe ist verdeckt. Enter ohne Eingabe = bestehenden Wert behalten.${reset}"
say

ask_key() {
    local name="$1" label="$2" url="$3" existing entered
    strip_placeholder "$name"
    existing="$(current_value "$name")"

    say "${bold}$label${reset}"
    say "  ${dim}$url${reset}"
    if [[ -n "$existing" ]]; then
        say "  ${dim}(bereits gesetzt — Enter behält den bestehenden Wert)${reset}"
    fi
    printf '  %s: ' "$name"
    read -rs entered
    printf '\n\n'

    if [[ -z "$entered" ]]; then
        if [[ -z "$existing" ]]; then
            warn "$name bleibt leer — diese Phase wird nicht laufen."
        fi
        return
    fi
    # Ein versehentlich mitkopiertes Leerzeichen/Anführungszeichen ist der
    # häufigste Grund für ein 401, das wie ein ungültiger Schlüssel aussieht.
    entered="$(printf '%s' "$entered" | tr -d '[:space:]"'"'")"
    set_var "$name" "$entered"
}

ask_key DEEPGRAM_API_KEY "1/3  Deepgram  — Transkription (Phase 2)" "https://console.deepgram.com"
ask_key DEEPSEEK_API_KEY "2/3  DeepSeek  — Bewertung (Phase 3)" "https://platform.deepseek.com"
ask_key GEMINI_API_KEY "3/3  Gemini    — Alternative zu DeepSeek" "https://aistudio.google.com/apikey"

# Der Ranker muss aktiv gesetzt sein, sonst greift der Default aus der Vorlage.
if [[ -n "$(current_value DEEPSEEK_API_KEY)" ]]; then
    set_var CLIPPER_PRO_RANKER deepseek
elif [[ -n "$(current_value GEMINI_API_KEY)" ]]; then
    set_var CLIPPER_PRO_RANKER gemini
    warn "Kein DeepSeek-Schlüssel — CLIPPER_PRO_RANKER=gemini gesetzt."
fi
[[ -n "$(current_value DEEPSEEK_API_KEY)" ]] && set_var DEEPSEEK_MODEL deepseek-chat

chmod 600 "$ENV_FILE" 2>/dev/null || true

# --- Prüfung gegen die echten Dienste --------------------------------------

say "${bold}Prüfung${reset}"

if ! command -v curl >/dev/null 2>&1; then
    warn "curl fehlt — überspringe den Verbindungstest."
    say "  ${dim}sudo apt install -y curl${reset}"
else
    check() {
        local label="$1" name="$2" code key
        shift 2
        key="$(current_value "$name")"
        if [[ -z "$key" ]]; then
            printf '  %-10s %s\n' "$label" "— nicht gesetzt"
            return
        fi
        code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 "${@/PLACEHOLDER/$key}" 2>/dev/null)"
        case "$code" in
            200) printf '  %-10s %s✅ Schlüssel gültig%s\n' "$label" "$green" "$reset" ;;
            401 | 403) printf '  %-10s %s❌ abgelehnt (HTTP %s) — Schlüssel prüfen%s\n' "$label" "$red" "$code" "$reset" ;;
            000 | "") printf '  %-10s %s⚠️  keine Verbindung%s\n' "$label" "$yellow" "$reset" ;;
            *) printf '  %-10s %s⚠️  unerwartet (HTTP %s)%s\n' "$label" "$yellow" "$code" "$reset" ;;
        esac
    }

    check "Deepgram" DEEPGRAM_API_KEY \
        -H "Authorization: Token PLACEHOLDER" https://api.deepgram.com/v1/projects
    check "DeepSeek" DEEPSEEK_API_KEY \
        -H "Authorization: Bearer PLACEHOLDER" https://api.deepseek.com/v1/models
    check "Gemini" GEMINI_API_KEY \
        -H "x-goog-api-key: PLACEHOLDER" https://generativelanguage.googleapis.com/v1beta/models
fi

say
say "${bold}Stand der .env${reset}  ${dim}(Werte werden nicht angezeigt)${reset}"
for name in DEEPGRAM_API_KEY DEEPSEEK_API_KEY GEMINI_API_KEY CLIPPER_PRO_RANKER; do
    value="$(current_value "$name")"
    case "$name" in
        CLIPPER_PRO_RANKER) printf '  %-20s %s\n' "$name" "${value:-— nicht gesetzt}" ;;
        *) printf '  %-20s %s\n' "$name" "$([[ -n $value ]] && echo "gesetzt" || echo "— fehlt")" ;;
    esac
done

say
if [[ -n "$(current_value DEEPGRAM_API_KEY)" ]] &&
    { [[ -n "$(current_value DEEPSEEK_API_KEY)" ]] || [[ -n "$(current_value GEMINI_API_KEY)" ]]; }; then
    ok "Fertig. Nächster Schritt — ein kurzes Video testen:"
    say
    say "  ${dim}clipper-pro ingest     --work-dir ~/testlauf \"<YOUTUBE-URL>\"${reset}"
    say "  ${dim}clipper-pro transcribe --work-dir ~/testlauf${reset}"
    say "  ${dim}clipper-pro rank       --work-dir ~/testlauf${reset}"
    say
    say "  ${dim}Arbeitsordner bewusst unter ~ und nicht /mnt/c — dort sperrt${reset}"
    say "  ${dim}Windows die Datei, die ffmpeg beim Rendern umschreibt.${reset}"
else
    warn "Es fehlen noch Schlüssel. Skript einfach erneut starten:"
    say "  ${dim}bash scripts/setup-clipper-pro-keys.sh${reset}"
fi
say
