"""`make watch-pods`: every pod event of both live stacks with a UTC timestamp.

    python -m demo.watch_pods [--context kind-skypiea]

Namespaces of demo/live_stacks.sh: skypiea-base (Normal) and skypiea-pred (Skypiea). One
`kubectl get pods --watch` per namespace; Ctrl-C stops both. Read-only.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import time

NAMESPACES = {"skypiea-base": "Normal", "skypiea-pred": "Skypiea"}
COLS = ("custom-columns=EVENT:.type,POD:.object.metadata.name,PHASE:.object.status.phase,"
        "READY:.object.status.containerStatuses[0].ready")


def line(ns: str, text: str) -> str:
    return f"{time.strftime('%H:%M:%S', time.gmtime())}  {NAMESPACES[ns]:<8} {ns:<13} {text}"


def follow(ctx: str, ns: str) -> subprocess.Popen:
    p = subprocess.Popen(["kubectl", "--context", ctx, "-n", ns, "get", "pods", "-w",
                          "--output-watch-events", "-o", COLS], stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True)

    def pump() -> None:
        for raw in p.stdout:
            print(line(ns, raw.rstrip()), flush=True)
    threading.Thread(target=pump, daemon=True).start()
    return p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--context", default="kind-skypiea")
    a = ap.parse_args(argv)
    print(f"watching pods of {', '.join(NAMESPACES)} (context {a.context}), UTC; Ctrl-C stops",
          flush=True)
    procs = [follow(a.context, ns) for ns in NAMESPACES]
    try:
        while all(p.poll() is None for p in procs):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            p.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
