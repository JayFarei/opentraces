# single-file-python-project

Single-file Python project template for otbox simulated-user scenarios.

This template provides a minimal Python project structure that a
simulated-user scenario can be pointed at as an editable starting
state. The runner copies this directory into the box's project dir
before driving the agent binary through its prompt sequence; whatever
the agent changes ends up in the captured snapshot.

## Layout

- `src/app.py` — a one-function module (`greet`) the agent is expected
  to extend (e.g. add a `farewell` helper).
- `README.md` — this file.
