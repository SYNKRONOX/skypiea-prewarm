#!/usr/bin/env bash
# `make doctor`: check this machine before `make setup`, `make demo` or `make live`.
#   tools/doctor.sh [test|demo|live|all]      (default: all)
#     test  Python 3.12 (or .venv), make, git      -> enough for `make setup test lint`
#     demo  test + Docker running with >= 6 GB, kind, kubectl, helm, curl, disk, demo ports
#     live  demo + live ports (console :8090, Grafana :3001, gateways); Ollama is optional
# Prints one line per check (OK / WARN / FAIL) and, for every FAIL, the exact fix.
# Exit 0 if nothing FAILed, 1 otherwise. Never changes anything on the machine.
# Thresholds: Docker memory 6 GB and free disk 15 GB come from docs/DEMO.md ("Prerequisites"
# and "Resources"). Environment overrides: CLUSTER, REG_PORT, PF_BASE, GRAFANA_PORT,
# CONSOLE_PORT, AGENT_BASE_URL, DOCTOR_MIN_DOCKER_GB, DOCTOR_MIN_DISK_GB.
set -uo pipefail
cd "$(dirname "$0")/.."
MODE="${1:-all}"
case "$MODE" in test|demo|live|all) ;; *) echo "usage: $0 [test|demo|live|all]" >&2; exit 2 ;; esac
MIN_DOCKER_GB="${DOCTOR_MIN_DOCKER_GB:-6}"
MIN_DISK_GB="${DOCTOR_MIN_DISK_GB:-15}"
CLUSTER="${CLUSTER:-skypiea}"
REG_PORT="${REG_PORT:-5001}"
REG_NAME="${REG_NAME:-kind-registry}"
PF_BASE="${PF_BASE:-18080}"
GRAFANA_PORT="${GRAFANA_PORT:-3000}"
CONSOLE_PORT="${CONSOLE_PORT:-8090}"
AGENT_BASE_URL="${AGENT_BASE_URL:-http://localhost:11434/v1}"
fails=0; warns=0
QUIET="${DOCTOR_QUIET:-0}"   # 1: print only WARN/FAIL lines (used by make demo / make live)
ok()   { [ "$QUIET" = 1 ] || printf '  OK    %s\n' "$*"; }
warn() { printf '  WARN  %s\n' "$1"; [ -z "${2:-}" ] || printf '        -> %s\n' "$2"; warns=$((warns + 1)); }
fail() { printf '  FAIL  %s\n' "$1"; printf '        -> fix: %s\n' "$2"; fails=$((fails + 1)); }
level() { case $1 in test) echo 1 ;; demo) echo 2 ;; *) echo 3 ;; esac; }
need() { [ "$(level "$MODE")" -ge "$(level "$1")" ]; }
has() { command -v "$1" >/dev/null 2>&1; }
port_busy() {   # 0 if something listens on 127.0.0.1:$1
  if has ss; then ss -Hltn "sport = :$1" 2>/dev/null | grep -q .; return; fi
  (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
}

hdr() { [ "$QUIET" = 1 ] || echo "$*"; }
hdr "Skypiea doctor (mode: $MODE)"
hdr "Platform"
os=$(uname -s); arch=$(uname -m)
if [ "$os" = Linux ] && grep -qi microsoft /proc/version 2>/dev/null; then
  if [ "$arch" = x86_64 ]; then ok "Windows + WSL2, $arch (tested)"; else
    warn "WSL2 on $arch: untested (tested: x86_64)" "report problems as an issue"; fi
elif [ "$os" = Linux ] && [ "$arch" = x86_64 ]; then ok "Linux $arch (tested)"
elif [ "$os" = Linux ]; then warn "Linux $arch: untested (tested: x86_64)" "images are built locally, so arm64 may work; report problems as an issue"
elif [ "$os" = Darwin ]; then warn "macOS $arch: untested" "the scripts use GNU tools (sha256sum, ss); expect gaps; Linux or WSL2 is tested"
else fail "$os $arch is not supported" "use Linux x86-64 or Windows + WSL2 (Ubuntu)"; fi

hdr "Basics"
for b in make git; do
  if has "$b"; then ok "$b"; else fail "$b not found" "sudo apt-get install -y $b"; fi
done
if [ -x .venv/bin/python ] && .venv/bin/python -c 'import sys; sys.exit(sys.version_info[:2] != (3, 12))' 2>/dev/null; then
  ok ".venv with Python 3.12 (make setup done)"
elif has python3.12; then
  ok "python3.12 found ($(python3.12 -c 'import platform;print(platform.python_version())')); next: make setup"
else
  fail "Python 3.12 not found (python3.12 on PATH)" \
       "Ubuntu 24.04: sudo apt-get install -y python3.12 python3.12-venv; other systems: https://www.python.org/downloads/ or pyenv; or set PYTHON=/path/to/python3.12 for make setup"
fi
if has python3.12 && ! python3.12 -c 'import ensurepip' 2>/dev/null && [ ! -x .venv/bin/python ]; then
  fail "python3.12 has no venv/ensurepip" "sudo apt-get install -y python3.12-venv"
fi

if need demo; then
  hdr "Containers and Kubernetes"
  for b in curl sha256sum tar; do
    if has "$b"; then ok "$b"; else fail "$b not found" "sudo apt-get install -y $( [ "$b" = sha256sum ] && echo coreutils || echo "$b")"; fi
  done
  if ! has docker; then
    fail "docker not found" "install Docker Engine (https://docs.docker.com/engine/install/) or Docker Desktop with WSL2 integration enabled for this distro"
  elif ! docker info >/dev/null 2>&1; then
    fail "Docker is installed but the daemon is not reachable" \
         "start Docker Desktop (WSL2: Settings > Resources > WSL integration on) or: sudo systemctl start docker; and add yourself to the docker group (sudo usermod -aG docker \$USER, then log in again)"
  else
    ok "Docker running ($(docker version --format '{{.Server.Version}}' 2>/dev/null))"
    mem=$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo 0)
    gb=$(( ${mem:-0} / 1000000000 ))
    if [ "${mem:-0}" -ge $(( MIN_DOCKER_GB * 1000000000 )) ]; then ok "Docker memory ${gb} GB (need >= $MIN_DOCKER_GB GB)"
    else fail "Docker memory ${gb} GB (need >= $MIN_DOCKER_GB GB)" \
         "Docker Desktop: Settings > Resources > Memory; WSL2: memory=8GB under [wsl2] in %UserProfile%\\.wslconfig, then wsl --shutdown"; fi
  fi
  for b in kind kubectl helm; do
    if has "$b"; then
      case $b in
        kind) v=$(kind version 2>/dev/null | awk '{print $2}') ;;
        kubectl) v=$(kubectl version --client 2>/dev/null | awk '/Client/{print $3}') ;;
        helm) v=$(helm version --short 2>/dev/null) ;;
      esac
      ok "$b ${v:-}"
    else
      case $b in
        kind) fx="https://kind.sigs.k8s.io/docs/user/quick-start/#installation (tested v0.33.0)" ;;
        kubectl) fx="https://kubernetes.io/docs/tasks/tools/ (tested v1.37.2)" ;;
        helm) fx="https://helm.sh/docs/intro/install/ (tested v4.3.1)" ;;
      esac
      fail "$b not found" "$fx"
    fi
  done
  avail_kb=$(df -Pk . | awk 'NR==2{print $4}')
  dgb=$(( ${avail_kb:-0} / 1000000 ))
  if [ "$dgb" -ge "$MIN_DISK_GB" ]; then ok "free disk ${dgb} GB here (need >= $MIN_DISK_GB GB)"
  else fail "free disk ${dgb} GB here (need >= $MIN_DISK_GB GB)" "free space, or prune old images: docker image prune"; fi
  if [ -r /proc/meminfo ]; then
    mgb=$(awk '/MemAvailable/{print int($2/1000000)}' /proc/meminfo)
    if [ "$mgb" -ge 4 ]; then ok "available RAM ${mgb} GB"; else warn "available RAM ${mgb} GB is low" "close other programs; kind needs about 2-3 GB (docs/DEMO.md, Resources)"; fi
  fi

  hdr "Ports (127.0.0.1)"
  reg_ours=0
  if has docker && docker inspect -f '{{.State.Running}}' "$REG_NAME" 2>/dev/null | grep -q true; then reg_ours=1; fi
  if port_busy "$REG_PORT"; then
    if [ $reg_ours = 1 ]; then ok ":$REG_PORT registry $REG_NAME (ours, reused)"; else
      fail ":$REG_PORT is used by another program (needed for the kind registry)" "stop it, or use another port: REG_PORT=5002 KIND_REG=localhost:5002 make demo"; fi
  else ok ":$REG_PORT free (registry)"; fi
  if has kind && kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
    ok "kind cluster '$CLUSTER' exists (reused; make clean-cluster removes it)"
  fi
  demo_ports="$PF_BASE $((PF_BASE + 1)) $((PF_BASE + 2)) $((PF_BASE + 3)) $GRAFANA_PORT"
  busy=""
  for p in $demo_ports; do port_busy "$p" && busy="$busy :$p"; done
  if [ -z "$busy" ]; then ok "demo ports free:$(printf ' :%s' $demo_ports)"
  elif [ -n "$(ls .demo/pf/*.pid 2>/dev/null)" ]; then
    warn "demo ports in use:$busy (port-forwards of an earlier make demo; they are restarted)"
  else
    warn "demo ports in use:$busy" "make demo-clean if it is an earlier demo; otherwise pick free ports: PF_BASE=28080 GRAFANA_PORT=3300 make demo"
  fi
fi

if need live; then
  hdr "Live Console"
  live_ports="$CONSOLE_PORT 3001 18180 18280"
  busy=""
  for p in $live_ports; do port_busy "$p" && busy="$busy :$p"; done
  if [ -z "$busy" ]; then ok "live ports free:$(printf ' :%s' $live_ports)"
  elif [ -f .live/console.pid ] && kill -0 "$(cat .live/console.pid)" 2>/dev/null; then
    warn "live ports in use:$busy by a running make live from this folder (it is restarted)"
  else
    warn "live ports in use:$busy" "stop the program using them (ss -ltnp | grep -E ':(8090|3001|18180|18280) '); make live needs these ports"
  fi
  if ls -d models/lgbm-support-* >/dev/null 2>&1; then ok "predictor bundles in models/"
  else warn "no predictor bundles in models/" "make fetch-models (README, Tier 2); make live needs them"; fi
  if has curl && curl -sf -m 3 "${AGENT_BASE_URL%/}/models" >/dev/null 2>&1; then
    ok "LLM endpoint ${AGENT_BASE_URL%/}/models reachable"
  else
    warn "no LLM at ${AGENT_BASE_URL%/} (optional)" "install Ollama (https://ollama.com) and: ollama pull llama3.1:8b; without it use the scripted LLM (README, Tier 2)"
  fi
fi

[ "$QUIET" = 1 ] || echo
if [ $fails -gt 0 ]; then
  echo "doctor: $fails FAIL, $warns WARN. Fix the FAIL lines above, then run make doctor again."
  exit 1
fi
[ "$QUIET" = 1 ] || echo "doctor: all required checks passed ($warns WARN)."
