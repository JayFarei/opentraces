# failing-python-check-project

Tiny Python project for shell-heavy debugging capture scenarios.

Run:

```bash
python3 check_app.py
```

The initial implementation is intentionally wrong so a real agent has
to observe a failing command, fix `src/app.py`, rerun the command, and
commit the result.
