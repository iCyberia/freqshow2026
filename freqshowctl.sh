#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$HOME/FreqShow"
VENV="$HOME/FreqShow-venv"
PYTHON="$VENV/bin/python3"
APP="$APP_DIR/freqshow.py"
PIDFILE="$APP_DIR/freqshow.pid"
LOGFILE="$HOME/freqshow.out"
DISPLAY_NUM=":0"

is_running() {
    if [[ -f "$PIDFILE" ]]; then
        local pid
        pid="$(cat "$PIDFILE" 2>/dev/null || true)"
        if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
    fi
    return 1
}

start_app() {
    if is_running; then
        echo "FreqShow is already running (PID $(cat "$PIDFILE"))."
        exit 0
    fi

    if [[ ! -x "$PYTHON" ]]; then
        echo "Python not found at: $PYTHON"
        exit 1
    fi

    if [[ ! -f "$APP" ]]; then
        echo "App not found at: $APP"
        exit 1
    fi

    echo "Starting FreqShow..."
    nohup bash -lc "source '$VENV/bin/activate' && cd '$APP_DIR' && DISPLAY=$DISPLAY_NUM python3 '$APP'" \
        >> "$LOGFILE" 2>&1 &
    local pid=$!
    echo "$pid" > "$PIDFILE"
    sleep 2

    if kill -0 "$pid" 2>/dev/null; then
        echo "Started FreqShow (PID $pid)."
    else
        echo "FreqShow failed to start. Check: $LOGFILE"
        rm -f "$PIDFILE"
        exit 1
    fi
}

stop_app() {
    if ! is_running; then
        echo "FreqShow is not running."
        rm -f "$PIDFILE"
        return 0
    fi

    local pid
    pid="$(cat "$PIDFILE")"
    echo "Stopping FreqShow (PID $pid)..."
    kill "$pid" 2>/dev/null || true

    for _ in {1..10}; do
        if ! kill -0 "$pid" 2>/dev/null; then
            rm -f "$PIDFILE"
            echo "Stopped."
            return 0
        fi
        sleep 1
    done

    echo "Still running, forcing stop..."
    kill -9 "$pid" 2>/dev/null || true
    rm -f "$PIDFILE"
    echo "Stopped."
}

status_app() {
    if is_running; then
        echo "FreqShow is running (PID $(cat "$PIDFILE"))."
    else
        echo "FreqShow is not running."
    fi
}

case "${1:-}" in
    start)
        start_app
        ;;
    stop)
        stop_app
        ;;
    restart)
        stop_app || true
        start_app
        ;;
    status)
        status_app
        ;;
    logs)
        tail -f "$LOGFILE"
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|logs}"
        exit 1
        ;;
esac
