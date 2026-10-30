#!/usr/bin/env bash
# Supervise only this package's authenticated HTTP process.
set -eo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
QIANSAI_PACKAGE_ROOT="$(cd -- "$HERE/.." && pwd)"
export QIANSAI_PACKAGE_ROOT
. "$HERE/env.sh"
PORT="${PORT:-7860}"
STATE="${QIANSAI_API_STATE_DIR:-${AGENT_TMP:-/var/tmp}/qiansai-api}"
PIDFILE="$STATE/watchdog.pid"
running() {
    [ -f "$PIDFILE" ] || return 1
    pid="$(cat "$PIDFILE")"
    [[ "$pid" =~ ^[0-9]+$ ]] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    [ -r "/proc/$pid/cmdline" ] || return 1
    tr '\0' '\n' < "/proc/$pid/cmdline" | grep -Fxq "$HERE/run_api.sh"
}
case "${1:-}" in
start)
    if running; then echo "API watchdog already running"; exit 0; fi
    if [ -e "$PIDFILE" ]; then echo "Stale API pid file: $PIDFILE" >&2; exit 1; fi
    : "${FPGACHINA_TOKEN:?Set the organiser token before starting the API}"
    "$HERE/start.sh"
    mkdir -p "$STATE"
    log="$STATE/api_$(date +%Y%m%d_%H%M%S)_$$.log"
    setsid "$HERE/run_api.sh" _loop </dev/null >>"$log" 2>&1 &
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        if running; then echo "API watchdog running; log: $log"; exit 0; fi
        sleep 1
    done
    echo "API watchdog did not start; inspect $log" >&2; exit 1
    ;;
_loop)
    mkdir -p "$STATE"
    (set -o noclobber; printf '%s\n' "$$" > "$PIDFILE") || exit 1
    child=""
    trap 'if [ -n "$child" ]; then kill "$child" 2>/dev/null || :; wait "$child" 2>/dev/null || :; fi; rm -f "$PIDFILE"; exit 0' TERM INT
    while :; do
        "$HERE/start_http.sh" &
        child=$!
        wait "$child" || :
        child=""
        sleep 2
    done
    ;;
stop)
    if running; then kill "$(cat "$PIDFILE")"; echo "API watchdog stopping";
    else echo "API watchdog not running"; exit 1; fi
    ;;
status)
    if running; then echo "API watchdog running"; else echo "API watchdog not running"; exit 1; fi
    ;;
*) echo "usage: $0 start|stop|status" >&2; exit 2 ;;
esac
