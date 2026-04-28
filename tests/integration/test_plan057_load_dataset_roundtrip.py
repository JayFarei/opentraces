from __future__ import annotations

import json
import os
import subprocess
import sys

from opentraces.core.datasets import append_rows, create_dataset, dataset_path


def test_local_dataset_loads_with_huggingface_datasets_without_opentraces_pythonpath(tmp_path):
    create_dataset(
        "loadable-intents",
        workflow_skill="loadable-curator",
        workflow_digest="sha256:workflow",
    )
    row = {
        "source_trace_id": "trace-1",
        "source_unit_id": "tu:trace-1:trace",
        "summary": "Loadable public row.",
    }
    append_rows("loadable-intents", [row], run_id="run_load")

    script = (
        "import json, sys\n"
        "from datasets import load_dataset\n"
        "ds = load_dataset(sys.argv[1], split='train')\n"
        "print(json.dumps({'rows': ds.num_rows, 'columns': ds.column_names, 'row': ds[0]}, sort_keys=True))\n"
    )
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["HF_DATASETS_CACHE"] = str(tmp_path / "hf-cache")
    result = subprocess.run(
        [sys.executable, "-c", script, str(dataset_path("loadable-intents"))],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    payload = json.loads(result.stdout.splitlines()[-1])
    assert payload == {
        "columns": ["source_trace_id", "source_unit_id", "summary"],
        "row": row,
        "rows": 1,
    }
