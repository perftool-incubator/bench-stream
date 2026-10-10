#!/usr/bin/env python3
# -*- mode: python; indent-tabs-mode: nil; python-indent-level: 4 -*-
# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=python

"""Revalidate one STREAM sample and index only its selected native score."""

import json
import math
import os
import re
import sys
from pathlib import Path

from stream_common import (
    KERNELS,
    NTIMES,
    SOURCE_SHA256,
    UPSTREAM_COMMIT,
    UPSTREAM_REPOSITORY,
    effective_parameters,
    parse_cli,
    parse_native_output,
    validate_native_summary_duration,
)


MANIFEST = Path("stream-run-manifest.json")
OUTPUT = Path("stream-output.txt")
NANOSECONDS_PER_MILLISECOND = 1_000_000


def _required_int(value, label, minimum=1):
    if type(value) is not int or value < minimum:
        raise ValueError("STREAM manifest %s is missing or invalid" % label)
    return value


def validate_manifest_output(manifest, output_text):
    if manifest.get("status") != "valid":
        raise ValueError("STREAM run manifest is not valid")
    if (
        manifest.get("upstream_repository") != UPSTREAM_REPOSITORY
        or manifest.get("upstream_commit") != UPSTREAM_COMMIT
        or manifest.get("source_sha256") != SOURCE_SHA256
        or manifest.get("source_modified") is not False
    ):
        raise ValueError("STREAM manifest does not match the pinned unmodified source")
    if manifest.get("result_label") != "STREAM-derived":
        raise ValueError("STREAM manifest is missing the required result variant label")

    requested = manifest.get("requested")
    if not isinstance(requested, dict):
        raise ValueError("STREAM manifest has no effective runtime parameters")
    test_type = requested.get("test_type")
    if test_type not in KERNELS:
        raise ValueError("STREAM manifest has an unknown selected test-type")
    threads = requested.get("threads")
    array_mib = requested.get("array_mib_per_array")
    if type(threads) is not int or not 1 <= threads <= 4096:
        raise ValueError("STREAM manifest has an invalid thread count")
    if type(array_mib) is not int or array_mib <= 0:
        raise ValueError("STREAM manifest has an invalid prepared array size")
    if requested.get("ntimes") != NTIMES:
        raise ValueError("STREAM manifest has an invalid fixed native pass count")

    process = manifest.get("process")
    if not isinstance(process, dict):
        raise ValueError("STREAM manifest has no native process record")
    if (
        process.get("return_code") != 0
        or process.get("timed_out") is not False
        or process.get("interrupted") is not False
    ):
        raise ValueError("STREAM native process did not complete cleanly")
    launch_ns = _required_int(process.get("launch_begin_epoch_ns"), "launch timestamp")
    completion_ns = _required_int(process.get("process_completion_epoch_ns"), "completion timestamp")
    if completion_ns <= launch_ns:
        raise ValueError("STREAM process timestamps are not ordered")
    process_seconds = process.get("process_wallclock_seconds")
    if (
        isinstance(process_seconds, bool)
        or not isinstance(process_seconds, (int, float))
        or not math.isfinite(process_seconds)
        or process_seconds <= 0
    ):
        raise ValueError("STREAM process wall-clock duration is missing or invalid")
    epoch_elapsed_seconds = (completion_ns - launch_ns) / 1_000_000_000
    clock_tolerance_seconds = max(0.010, process_seconds * 0.001)
    if abs(epoch_elapsed_seconds - process_seconds) > clock_tolerance_seconds:
        raise ValueError(
            "STREAM epoch timestamps differ from monotonic process duration by %.6f seconds (tolerance %.6f)"
            % (abs(epoch_elapsed_seconds - process_seconds), clock_tolerance_seconds)
        )

    provenance = manifest.get("build_provenance")
    if not isinstance(provenance, dict):
        raise ValueError("STREAM build provenance is missing")
    if (
        provenance.get("source_sha256") != SOURCE_SHA256
        or provenance.get("upstream_commit") != UPSTREAM_COMMIT
        or provenance.get("array_mib_per_array") != array_mib
        or provenance.get("ntimes") != NTIMES
        or provenance.get("source_modified") is not False
        or provenance.get("result_label") != "STREAM-derived"
    ):
        raise ValueError("STREAM build provenance does not match the requested prepared workload")
    if not re.fullmatch(r"[0-9a-f]{64}", str(provenance.get("binary_sha256", ""))):
        raise ValueError("STREAM build provenance has no valid binary digest")

    parsed = parse_native_output(output_text, array_mib, threads, return_code=0)
    validate_native_summary_duration(parsed, process_seconds)
    native = manifest.get("native_result")
    if not isinstance(native, dict):
        raise ValueError("STREAM manifest has no parsed native result")
    if native != parsed:
        raise ValueError("STREAM manifest native result does not match the retained raw output")

    selected = manifest.get("selected_result")
    expected_kernel = parsed["kernels"][test_type]
    if not isinstance(selected, dict):
        raise ValueError("STREAM manifest has no selected result")
    expected_selected = {
        "test_type": test_type,
        "native_kernel": expected_kernel["native_label"],
        "metric_type": expected_kernel["metric_type"],
        "value_mb_sec": expected_kernel["best_rate_mb_sec"],
        "selection_effect": "reports the selected rate; it does not suppress other native kernels",
    }
    if selected != expected_selected:
        raise ValueError("STREAM selected result does not match the native result and requested test-type")

    launch_ms = launch_ns // NANOSECONDS_PER_MILLISECOND
    # Round outwards so the millisecond interval still contains the full
    # process envelope represented by the nanosecond timestamps.
    completion_ms = (completion_ns + NANOSECONDS_PER_MILLISECOND - 1) // NANOSECONDS_PER_MILLISECOND
    if completion_ms <= launch_ms:
        raise ValueError("STREAM score-support interval is shorter than one millisecond")
    return {
        "test_type": test_type,
        "metric_type": expected_kernel["metric_type"],
        "value_mb_sec": expected_kernel["best_rate_mb_sec"],
        "begin_ms": launch_ms,
        "end_ms": completion_ms,
    }


def load_cdm_metrics():
    toolbox_home = os.environ.get("TOOLBOX_HOME")
    if not toolbox_home:
        raise ValueError("STREAM post-processing requires TOOLBOX_HOME")
    toolbox_python = Path(toolbox_home) / "python"
    if not toolbox_python.is_dir():
        raise ValueError("STREAM post-processing cannot find %s" % toolbox_python)
    if str(toolbox_python) not in sys.path:
        sys.path.append(str(toolbox_python))
    from toolbox.cdm_metrics import CDMMetrics

    return CDMMetrics


def main(argv):
    output_dir = Path("postprocess")
    (output_dir / "post-process-data.json").unlink(missing_ok=True)
    args = parse_cli(argv)
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("requested") != effective_parameters(args):
        raise ValueError("STREAM manifest runtime parameters do not match the submitted postprocessor arguments")
    output_text = OUTPUT.read_text(encoding="utf-8", errors="replace")
    score = validate_manifest_output(manifest, output_text)

    CDMMetrics = load_cdm_metrics()
    metrics = CDMMetrics()
    metrics.log_sample(
        "0",
        {
            "source": "stream",
            "class": "throughput",
            "type": score["metric_type"],
            "default-aggregation": "sum",
        },
        {"desc": "STREAM-derived"},
        {
            "begin": score["begin_ms"],
            "end": score["end_ms"],
            "value": score["value_mb_sec"],
        },
    )
    metric_file = metrics.finish_samples()
    sample_data = {
        "rickshaw-bench-metric": {"schema": {"version": "2021.04.12"}},
        "benchmark": "stream",
        "primary-period": "score-support",
        "primary-metric": score["metric_type"],
        "periods": [{"name": "score-support", "metric-files": [metric_file]}],
    }
    output_dir.mkdir(exist_ok=True)
    (output_dir / "post-process-data.json").write_text(
        json.dumps(sample_data, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception as exc:
        print("STREAM post-processing failed: %s" % exc, file=sys.stderr)
        sys.exit(1)
