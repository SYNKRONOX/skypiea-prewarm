# Trace schema

Defined in [`traces/schema.py`](../traces/schema.py). Only metadata is stored: no prompts,
arguments or outputs.

| Type | Field | Meaning |
|---|---|---|
| `Session` | `session_id` | Unique id of one agent episode |
| | `source` | Dataset name, e.g. `tau-bench` |
| | `group` | Task or user id, used for leak-free train/test splits |
| | `batches` | Ordered list of `Batch` |
| `Batch` | `batch_id` | Position in the session |
| | `t_emit_ms` | When the LLM emitted the batch, relative to session start |
| | `calls` | All calls emitted in that one LLM turn |
| | `node` | Optional node or pod that served it |
| `Call` | `tool` | Tool class name |
| | `service_ms` | Service time once the tool is warm |
| | `error` | Whether the call failed |

A batch with more than one call is a **parallel tool call**. `Batch.tools` gives the set of
distinct tools. `Session.tool_sequence()` flattens a session into one list, sorting calls
inside a batch by name so that the result is deterministic.

`Session.to_dict()` and `Session.from_dict()` round-trip to plain dicts for JSON or Parquet.
