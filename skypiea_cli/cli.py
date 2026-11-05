"""skypiea - inspect and steer a running Skypiea cluster (kind or AWS) via a kubeconfig.

    skypiea status
    skypiea policy get | skypiea policy set markov1 -p tau=0.6
    skypiea sessions
    skypiea trace <session_id>
    skypiea explain <session_id>

Use --context kind-skypiea for kind, --kubeconfig ~/.kube/skypiea-aws for AWS (or the env vars
SKYPIEA_CONTEXT / SKYPIEA_KUBECONFIG). Session data lives in the gateway until the session is
purged (60 s after it ends, NFR-6).
"""

from __future__ import annotations

import json
from typing import Any

import click

from skypiea_cli.backend import DEFAULT_NS, Backend

BACKEND_FACTORY = None  # tests inject a fake backend here
DEFAULT_TTL_S = 30


def _backend(ctx: click.Context) -> Backend:
    if BACKEND_FACTORY is not None:
        return BACKEND_FACTORY()
    from skypiea_cli.backend import KubeBackend

    o = ctx.obj
    return KubeBackend(o["kubeconfig"], o["context"], o["namespace"])


def _out(ctx: click.Context, data: Any, text: str) -> None:
    click.echo(json.dumps(data, indent=2, default=str) if ctx.obj["json"] else text)


def _table(rows: list[list[Any]], header: list[str]) -> str:
    cells = [header] + [[("" if c is None else str(c)) for c in r] for r in rows]
    w = [max(len(r[i]) for r in cells) for i in range(len(header))]
    return "\n".join("  ".join(c.ljust(w[i]) for i, c in enumerate(r)).rstrip() for r in cells)


@click.group(help=__doc__)
@click.option("--kubeconfig", envvar="SKYPIEA_KUBECONFIG", default=None,
              help="kubeconfig file (AWS: ~/.kube/skypiea-aws)")
@click.option("--context", envvar="SKYPIEA_CONTEXT", default=None,
              help="kubeconfig context (kind: kind-skypiea)")
@click.option("-n", "--namespace", default=DEFAULT_NS, show_default=True)
@click.option("--json", "as_json", is_flag=True, help="machine-readable output")
@click.pass_context
def cli(ctx: click.Context, kubeconfig, context, namespace, as_json) -> None:
    ctx.obj = {"kubeconfig": kubeconfig, "context": context, "namespace": namespace,
               "json": as_json}


@cli.command(help="Tools, replicas, warm pool and the active policy.")
@click.pass_context
def status(ctx: click.Context) -> None:
    b = _backend(ctx)
    deps = b.deployments()
    try:
        state = b.controller_state()
    except RuntimeError as e:
        state, ctl_err = {}, str(e)
    else:
        ctl_err = None
    try:
        policy = b.get_policy()
    except RuntimeError:
        policy = {"policy": "unavailable (predictor not reachable)"}
    tools = [d for d in deps if d.get("tier")]
    rows = []
    for d in sorted(tools, key=lambda d: d["tool"]):
        st = state.get(d["tool"], {})
        rows.append([d["tool"], d["tier"], d["replicas"], d["ready"], st.get("copies"),
                     st.get("warm_for_s"), st.get("inflight")])
    platform = [[d["name"], f'{d["ready"]}/{d["replicas"]}'] for d in deps if not d.get("tier")]
    warm = sum(1 for d in tools if d["ready"] > 0)
    text = (f"policy: {policy.get('policy')} {policy.get('params') or ''}\n"
            f"warm pool: {warm} of {len(tools)} tools have a ready pod, "
            f"{sum(d['ready'] for d in tools)} ready pods in total\n\n"
            + _table(rows, ["tool", "tier", "replicas", "ready", "warm copies", "warm for s",
                            "in flight"])
            + "\n\n" + _table(platform, ["platform", "ready"])
            + (f"\n\ncontroller unreachable: {ctl_err}" if ctl_err else ""))
    _out(ctx, {"policy": policy, "tools": tools, "controller": state, "platform": platform},
         text)


@cli.group(help="Show or change the predictor policy.")
def policy() -> None:
    pass


@policy.command("get")
@click.pass_context
def policy_get(ctx: click.Context) -> None:
    p = _backend(ctx).get_policy()
    _out(ctx, p, f"{p.get('policy')} {p.get('params') or ''}")


@policy.command("set")
@click.argument("name")
@click.option("-p", "--param", "params", multiple=True, help="key=value (numbers are parsed)")
@click.pass_context
def policy_set(ctx: click.Context, name: str, params: tuple[str, ...]) -> None:
    parsed: dict[str, Any] = {}
    for kv in params:
        if "=" not in kv:
            raise click.BadParameter(f"expected key=value, got {kv!r}")
        k, v = kv.split("=", 1)
        try:
            parsed[k] = json.loads(v)
        except ValueError:
            parsed[k] = v
    try:
        res = _backend(ctx).set_policy(name, parsed)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e
    _out(ctx, res, f"policy set: {res.get('policy')} {res.get('params') or ''}")


@cli.command(help="Live sessions in the gateway.")
@click.pass_context
def sessions(ctx: click.Context) -> None:
    s = _backend(ctx).sessions()
    _out(ctx, s, _table([[x["session_id"], x["calls"], x["ended"]] for x in s],
                        ["session", "calls", "ended"]) if s else "no live sessions")


def _get_session(ctx: click.Context, sid: str) -> dict[str, Any]:
    s = _backend(ctx).session(sid)
    if s is None:
        raise click.ClickException(f"session {sid} not found (unknown, or purged 60 s after it "
                                   "ended)")
    return s


@cli.command(help="Calls of a session with cold/warm, hold and added latency.")
@click.argument("session_id")
@click.pass_context
def trace(ctx: click.Context, session_id: str) -> None:
    s = _get_session(ctx, session_id)
    rows = [[c["i"], c["batch_id"], c["tool_class"], "COLD" if c["cold"] else "warm",
             "yes" if c["predicted"] else "no", c["held_ms"], c["added_ms"],
             "ok" if c["ok"] else "ERROR", c["t_start_ms"]] for c in s["calls"]]
    n_cold = sum(1 for c in s["calls"] if c["cold"])
    text = (f"session {session_id}: {len(s['calls'])} calls, {n_cold} cold"
            f"{' (ended)' if s['ended'] else ''}\n"
            + _table(rows, ["#", "batch", "tool", "start", "predicted", "held ms", "added ms",
                            "result", "t ms"]))
    _out(ctx, s, text)


def explain_lines(s: dict[str, Any], last: int) -> list[str]:
    calls, out = s["calls"], []
    for d in list(s["decisions"])[-last:]:
        i = d["after_call"]
        head = (f"after call #{i} ({calls[i]['tool_class'] if 0 <= i < len(calls) else '?'}), "
                f"t={d['t_ms']} ms:")
        nxt = calls[i + 1] if i + 1 < len(calls) else None
        if d["outcome"] != "ok":
            out.append(f"{head} predictor failed ({d['outcome'].split(':', 1)[1]}); the gateway "
                       "fell back to plain scale-to-zero (FR-4), nothing was warmed.")
        else:
            ranked = ", ".join(f"{r['tool_class']} {r['p']:.2f}" for r in d["ranked"]) or "-"
            out.append(f"{head} {d['model_version']} ranked {ranked}")
            warm = [x for x in d["decisions"] if x["warm"] and x["copies"] > 0]
            if not warm:
                out.append("   no tool reached the policy's threshold: nothing warmed.")
            for x in warm:
                ttl = f"TTL {x['ttl_s']} s" if x["ttl_s"] is not None else \
                    f"default TTL {DEFAULT_TTL_S} s"
                start = f"in {x['start_in_ms']} ms" if x["start_in_ms"] else "now"
                out.append(f"   warm {x['tool_class']}: {x['copies']} pod(s) {start}, {ttl} "
                           f"(p={x['prob']:.2f})")
        if nxt is not None:
            if nxt["predicted"] and not nxt["cold"]:
                verdict = "HIT: it was predicted and found a warm pod"
            elif nxt["predicted"]:
                verdict = "predicted, but the pod was not ready in time (cold)"
            elif nxt["cold"]:
                verdict = "MISS: not predicted, cold start"
            else:
                verdict = "not predicted, but a pod was already warm"
            out.append(f"   next call #{nxt['i']} {nxt['tool_class']}: {verdict}")
    return out


@cli.command(help="The last predictor decisions of a session, and why.")
@click.argument("session_id")
@click.option("--last", default=10, show_default=True)
@click.pass_context
def explain(ctx: click.Context, session_id: str, last: int) -> None:
    s = _get_session(ctx, session_id)
    lines = explain_lines(s, last)
    _out(ctx, {"session_id": session_id, "decisions": list(s["decisions"])[-last:],
               "explanation": lines},
         "\n".join(lines) if lines else "no predictor decisions recorded for this session")


def main() -> None:
    cli(prog_name="skypiea")
