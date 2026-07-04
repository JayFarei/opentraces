"""Allow running as: python -m opentraces"""
from opentraces.cli import main

# #209 (W1) fork-safety: capsule seal's process-parallel companion redaction
# uses multiprocessing's "spawn" start method, which re-imports this exact
# file as `__mp_main__` in every worker process (see
# ``core/capsule/companions.py``'s ``_MP_CONTEXT``). Without this guard,
# ``main()`` ran unconditionally at import time, so every worker re-entered
# the full Click CLI before running its actual (picklable, module-level)
# target function -- a ``BrokenProcessPool`` on any ``python -m opentraces``
# invocation once the parallel path engaged. The installed console-script
# entry points (``opentraces`` / ``ot``, pip's generated wrapper) already
# carry this guard; only this ``-m opentraces`` dev entry point was missing it.
if __name__ == "__main__":
    main()
