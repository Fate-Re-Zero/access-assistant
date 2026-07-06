#!/usr/bin/env bash
# access-assistant Web API service manager (uv virtualenv)
#
# Usage:
#   ./service.sh start    # install deps + daemon supervisor + auto-restart
#   ./service.sh stop     # stop supervisor, worker, orphans, and free port
#   ./service.sh restart  # stop, install deps, then start
#   ./service.sh install  # install/update dependencies only (uv sync)
#   ./service.sh status   # show running state
#   ./service.sh logs     # tail worker log
#   ./service.sh run      # foreground (no supervisor)
#   ./service.sh once     # background single run (no auto-restart)
#
# Environment (optional):
#   SKILLS_WEB_HOST, SKILLS_WEB_PORT, SKILLS_WEB_RELOAD, RESTART_DELAY, MAX_RESTARTS
#   UV_SYNC_INDEX_URL  PyPI index for `uv sync` (default: Tsinghua mirror)

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="${ROOT_DIR}/.run"
PID_FILE="${RUN_DIR}/access-assistant-web.pid"
SUPERVISOR_PID_FILE="${RUN_DIR}/access-assistant-web.supervisor.pid"
LOG_FILE="${RUN_DIR}/access-assistant-web.log"
SUPERVISOR_LOG_FILE="${RUN_DIR}/access-assistant-web.supervisor.log"
RESTART_DELAY="${RESTART_DELAY:-3}"
MAX_RESTARTS="${MAX_RESTARTS:-0}"
DEFAULT_WEB_PORT="${SKILLS_WEB_PORT:-8000}"
UV_SYNC_INDEX_URL="${UV_SYNC_INDEX_URL:-https://pypi.tuna.tsinghua.edu.cn/simple}"

cd "$ROOT_DIR"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

ensure_uv() {
  if ! command -v uv >/dev/null 2>&1; then
    echo "error: uv not found in PATH. Install uv or add it to PATH." >&2
    exit 1
  fi
}

install_deps() {
  ensure_uv
  log "installing dependencies: uv sync --index-url ${UV_SYNC_INDEX_URL}"
  uv sync --index-url "${UV_SYNC_INDEX_URL}"
}

prepare_runtime() {
  install_deps
}

mkdir_run_dir() {
  mkdir -p "$RUN_DIR"
}

is_pid_running() {
  local pid="${1:-}"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

read_pid_file() {
  local file="$1"
  if [[ -f "$file" ]]; then
    tr -d '[:space:]' < "$file"
  fi
}

resolve_web_port() {
  if [[ -n "${SKILLS_WEB_PORT:-}" ]]; then
    echo "$SKILLS_WEB_PORT"
    return
  fi
  if [[ -f "${ROOT_DIR}/.env" ]]; then
    local line
    line="$(grep -E '^[[:space:]]*SKILLS_WEB_PORT[[:space:]]*=' "${ROOT_DIR}/.env" | tail -n1 || true)"
    if [[ "$line" =~ =[[:space:]]*\"?([0-9]+) ]]; then
      echo "${BASH_REMATCH[1]}"
      return
    fi
  fi
  echo "$DEFAULT_WEB_PORT"
}

is_access_assistant_pid() {
  local pid="$1"
  local cmdline
  cmdline="$(ps -p "$pid" -o args= 2>/dev/null || true)"
  [[ "$cmdline" == *access-assistant-web* || "$cmdline" == *access_assistant.web_api* ]]
}

find_access_assistant_pids() {
  if command -v pgrep >/dev/null 2>&1; then
    pgrep -f 'access-assistant-web|access_assistant\.web_api' 2>/dev/null || true
    return
  fi
  ps -eo pid=,args= 2>/dev/null | while read -r pid args; do
    if [[ "$args" == *access-assistant-web* || "$args" == *access_assistant.web_api* ]]; then
      echo "$pid"
    fi
  done
}

find_port_listener_pids() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    while read -r line; do
      [[ "$line" =~ pid=([0-9]+) ]] || continue
      echo "${BASH_REMATCH[1]}"
    done < <(ss -lptn "sport = :${port}" 2>/dev/null || true)
    return
  fi
  if command -v lsof >/dev/null 2>&1; then
    lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true
  fi
}

stop_pid_gracefully() {
  local pid="$1"
  local label="$2"
  if ! is_pid_running "$pid"; then
    return 0
  fi
  log "stopping ${label} (pid ${pid})"
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 20); do
    if ! is_pid_running "$pid"; then
      return 0
    fi
    sleep 0.5
  done
  log "force killing ${label} (pid ${pid})"
  kill -9 "$pid" 2>/dev/null || true
}

stop_process_tree() {
  local pid="$1"
  local label="$2"
  if ! is_pid_running "$pid"; then
    return 0
  fi
  log "stopping ${label} tree (pid ${pid})"
  local pgid=""
  pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d '[:space:]' || true)"
  if [[ -n "$pgid" && "$pgid" != "0" && "$pgid" != "1" ]]; then
    kill -TERM "-${pgid}" 2>/dev/null || kill "$pid" 2>/dev/null || true
  else
    kill "$pid" 2>/dev/null || true
  fi
  for _ in $(seq 1 20); do
    if ! is_pid_running "$pid"; then
      return 0
    fi
    sleep 0.5
  done
  if [[ -n "$pgid" && "$pgid" != "0" && "$pgid" != "1" ]]; then
    kill -KILL "-${pgid}" 2>/dev/null || true
  fi
  kill -9 "$pid" 2>/dev/null || true
}

stop_orphan_workers() {
  local pid port
  while read -r pid; do
    [[ -n "$pid" ]] || continue
    is_access_assistant_pid "$pid" || continue
    stop_process_tree "$pid" "orphan worker"
  done < <(find_access_assistant_pids | sort -u)

  local ports=()
  ports+=("$(resolve_web_port)")
  if [[ "${ports[0]}" != "8000" ]]; then
    ports+=("8000")
  fi
  if [[ "${ports[0]}" != "7777" ]]; then
    ports+=("7777")
  fi

  local seen="|"
  for port in "${ports[@]}"; do
    [[ "$seen" == *"|${port}|"* ]] && continue
    seen="${seen}${port}|"
    while read -r pid; do
      [[ -n "$pid" ]] || continue
      is_access_assistant_pid "$pid" || continue
      stop_process_tree "$pid" "listener on port ${port}"
    done < <(find_port_listener_pids "$port" | sort -u)
  done
}

stop_all() {
  mkdir_run_dir
  local worker_pid supervisor_pid
  worker_pid="$(read_pid_file "$PID_FILE")"
  supervisor_pid="$(read_pid_file "$SUPERVISOR_PID_FILE")"

  if [[ -n "$worker_pid" ]]; then
    stop_process_tree "$worker_pid" "worker"
  fi

  if [[ -n "$supervisor_pid" ]]; then
    stop_process_tree "$supervisor_pid" "supervisor"
    rm -f "$SUPERVISOR_PID_FILE"
  fi

  rm -f "$PID_FILE"
  stop_orphan_workers
  log "stopped"
}

run_worker_foreground() {
  prepare_runtime
  exec uv run access-assistant-web
}

run_worker_background() {
  prepare_runtime
  mkdir_run_dir
  if command -v setsid >/dev/null 2>&1; then
    nohup setsid uv run access-assistant-web >>"$LOG_FILE" 2>&1 &
  else
    nohup uv run access-assistant-web >>"$LOG_FILE" 2>&1 &
  fi
  echo $! >"$PID_FILE"
  log "worker started in background (pid $(cat "$PID_FILE"), log ${LOG_FILE})"
}

supervise_loop() {
  prepare_runtime
  mkdir_run_dir
  local restarts=0

  cleanup() {
    local worker_pid
    worker_pid="$(read_pid_file "$PID_FILE")"
    if [[ -n "$worker_pid" ]]; then
      stop_process_tree "$worker_pid" "worker"
    fi
    rm -f "$PID_FILE"
    exit 0
  }

  trap cleanup TERM INT

  log "supervisor started (pid $$, restart_delay=${RESTART_DELAY}s, max_restarts=${MAX_RESTARTS:-unlimited})"

  while true; do
    log "starting worker: uv run access-assistant-web"
    if command -v setsid >/dev/null 2>&1; then
      setsid uv run access-assistant-web >>"$LOG_FILE" 2>&1 &
    else
      uv run access-assistant-web >>"$LOG_FILE" 2>&1 &
    fi
    local worker_pid=$!
    echo "$worker_pid" >"$PID_FILE"
    wait "$worker_pid" || true
    local exit_code=$?
    rm -f "$PID_FILE"

    log "worker exited with code ${exit_code}"
    if [[ "$MAX_RESTARTS" != "0" && "$restarts" -ge "$MAX_RESTARTS" ]]; then
      log "max restarts (${MAX_RESTARTS}) reached, supervisor exiting"
      exit 1
    fi
    restarts=$((restarts + 1))
    log "restarting in ${RESTART_DELAY}s (restart count=${restarts})"
    sleep "$RESTART_DELAY"
  done
}

start_daemon() {
  mkdir_run_dir
  local supervisor_pid
  supervisor_pid="$(read_pid_file "$SUPERVISOR_PID_FILE")"
  if is_pid_running "$supervisor_pid"; then
    log "already running (supervisor pid ${supervisor_pid})"
    exit 0
  fi
  rm -f "$SUPERVISOR_PID_FILE" "$PID_FILE"
  if command -v setsid >/dev/null 2>&1; then
    nohup setsid "$0" _supervise >>"$SUPERVISOR_LOG_FILE" 2>&1 &
  else
    nohup "$0" _supervise >>"$SUPERVISOR_LOG_FILE" 2>&1 &
  fi
  echo $! >"$SUPERVISOR_PID_FILE"
  sleep 1
  log "daemon started (supervisor pid $(cat "$SUPERVISOR_PID_FILE"))"
  log "worker log: ${LOG_FILE}"
  log "supervisor log: ${SUPERVISOR_LOG_FILE}"
}

show_status() {
  mkdir_run_dir
  local worker_pid supervisor_pid web_port
  worker_pid="$(read_pid_file "$PID_FILE")"
  supervisor_pid="$(read_pid_file "$SUPERVISOR_PID_FILE")"
  web_port="$(resolve_web_port)"

  if is_pid_running "$supervisor_pid"; then
    echo "supervisor: running (pid ${supervisor_pid})"
  else
    echo "supervisor: stopped"
  fi

  if is_pid_running "$worker_pid"; then
    echo "worker: running (pid ${worker_pid})"
  else
    echo "worker: stopped"
  fi

  local orphan_pids=()
  local pid
  while read -r pid; do
    [[ -n "$pid" ]] || continue
    if [[ "$pid" != "$worker_pid" ]]; then
      orphan_pids+=("$pid")
    fi
  done < <(find_access_assistant_pids | sort -u)
  if [[ ${#orphan_pids[@]} -gt 0 ]]; then
    echo "orphan worker(s): running (pids ${orphan_pids[*]})"
  fi

  local listener_pids=()
  local port
  for port in "$web_port" 8000 7777; do
    while read -r pid; do
      [[ -n "$pid" ]] || continue
      listener_pids+=("$pid")
    done < <(find_port_listener_pids "$port" | sort -u)
  done
  if [[ ${#listener_pids[@]} -gt 0 ]]; then
    echo "port listener(s): $(printf '%s ' "${listener_pids[@]}" | sed 's/ $//') (checked ${web_port}, 8000, 7777)"
  else
    echo "port(s) ${web_port}, 8000, 7777: free"
  fi

  echo "run dir: ${RUN_DIR}"
  echo "log: ${LOG_FILE}"
  echo "supervisor log: ${SUPERVISOR_LOG_FILE}"
}

tail_logs() {
  mkdir_run_dir
  touch "$LOG_FILE"
  tail -f "$LOG_FILE"
}

usage() {
  sed -n '2,12p' "$0"
}

cmd="${1:-}"

case "$cmd" in
  start)
    start_daemon
    ;;
  stop)
    stop_all
    ;;
  restart)
    stop_all
    start_daemon
    ;;
  install)
    install_deps
    log "dependencies installed"
    ;;
  status)
    show_status
    ;;
  logs)
    tail_logs
    ;;
  run)
    run_worker_foreground
    ;;
  once)
    run_worker_background
    ;;
  _supervise)
    supervise_loop
    ;;
  *)
    usage
    exit 1
    ;;
esac
