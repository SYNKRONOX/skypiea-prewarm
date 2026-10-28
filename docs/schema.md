# Trace schema

Defined in [`traces/schema.py`](../traces/schema.py). Every converter produces it, the
pipeline writes it, and the predictors, replayer and simulator read it. Only metadata is
stored: no prompts, messages, arguments or tool outputs.

## Fields

| Type | Field | Meaning |
|---|---|---|
| `Session` | `session_id` | Unique id of one agent episode |
| | `source` | Dataset name, e.g. `tau_bench` |
| | `group` | Task or user id. Splits are made by group, so a group never crosses splits |
| | `has_timing` | True if the source records times. If False, every time is `null` |
| | `trace_type` | `real` for converted datasets; PRD synthetic traces use `deterministic`, `branching` or `stochastic` |
| | `batches` | Ordered list of `Batch` |
| `Batch` | `batch_id` | Position in the session, 0..n-1 |
| | `t_offset_ms` | When the LLM emitted the batch, in ms since session start (`null` without timing) |
| | `node` | Workflow node, if known (PRD traces); `null` for real datasets |
| | `calls` | All calls emitted in that one LLM turn. Empty = a node-only PRD event |
| `Call` | `tool` | Tool **class** (see `configs/tool_classes/<source>.yaml`) |
| | `raw_tool` | The source's own tool name |
| | `service_ms` | Recorded tool run time (`null` without timing) |
| | `error` | Whether the call failed |

A batch with more than one call is a **parallel tool call**. `Session.tool_sequence()`
flattens a session into one list (calls inside a batch sorted by name), and
`Session.batch_sequence()` gives the set of tools in each batch. Timing is never invented.

## Relation to the PRD trace format

The schema is a superset of the PRD v1.1 trace format (Sec. 7). `Session.to_prd()` and
`Session.from_prd()` convert losslessly.

| PRD | Ours |
|---|---|
| `session_id` | `Session.session_id` |
| `trace_type` | `Session.trace_type` |
| `events[]` | `Session.batches[]` (one event = one batch) |
| `events[].t_offset_ms` | `Batch.t_offset_ms` |
| `events[].node` | `Batch.node` |
| `events[].tool_called` | `Batch.calls[].tool`; `null` = batch with no calls |
| – | `batch_id`, `service_ms`, `error`, `raw_tool`, `group`, `source`, `has_timing` |

When exporting, a parallel batch becomes one PRD event per call, all with the batch's time.

## Files written by the pipeline

`data/processed/<source>/`:

| File | Contents |
|---|---|
| `sessions.jsonl.gz` | One `Session.to_dict()` per line, plus `"split"` |
| `calls.parquet` | One row per call: `session_id, source, group, split, has_timing, batch_id, position_in_batch, batch_size, t_offset_ms, service_ms, tool, raw_tool, error` |
| `splits.json` | `group → train / val / test` (seed 0) |
| `report.md` | Counts, timing quantiles, class shares, split sizes and everything dropped |

## Loading

```python
from traces.load import load_sessions, load_sequences, load_batches, load_calls_df, load_all

train = load_sequences("tau_bench", "train")             # list[list[str]] of tool classes
raw = load_sequences("tau_bench", "train", level="raw")  # the source's own names
sets = load_batches("tracelab", "test")                  # list[list[frozenset[str]]]
df = load_calls_df("copilot_2026", ["train", "val"])     # pandas DataFrame
mixed = load_all(["tracelab", "copilot_2026"], "train")  # list[Session]
```

Pass `root=traces.load.SAMPLES` to use the small committed samples instead.
