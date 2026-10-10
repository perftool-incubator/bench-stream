#!/usr/bin/env python3
# -*- mode: python; indent-tabs-mode: nil; python-indent: 4 -*-
# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=python

"""Runtime parameter and native-output validation for the STREAM adapter."""

import argparse
import hashlib
import json
import math
import os
import re
from pathlib import Path


UPSTREAM_REPOSITORY = "https://github.com/redhat-performance/streams-wrapper"
UPSTREAM_COMMIT = "8932e7bfa6cf1c99360c10549887eeb1d457d27e"
SOURCE_RELATIVE_PATH = "streams/stream_omp_5_10.c"
SOURCE_SHA256 = "8daae19bde612b31a556004d180c2a7baeb77a96015e08717cc1995b4f05c334"
SOURCE_LABEL = "STREAM-derived: unmodified STREAM 5.10 OpenMP C source"
INSTALL_DIR = Path(os.environ.get("STREAM_INSTALL_DIR", "/usr/local/libexec/stream"))
DEFAULT_TEST_TYPE = "triad"
DEFAULT_THREADS = 1
DEFAULT_ARRAY_MIB = 512
DEFAULT_CPU_NODE = "none"
DEFAULT_MEMORY_NODE = "none"
DEFAULT_DEADLINE = 1800
NTIMES = 100
COMPILER_FLAGS = ("-O3", "-fopenmp", "-mcmodel=medium", "-m64")
ARRAY_MIB_VARIANTS = (256, 512, 1024, 2048)
MAX_THREADS = 4096
MIN_DEADLINE = 30
MAX_DEADLINE = 3600
TEST_TYPES = ("copy", "scale", "add", "triad")
KERNELS = {
    "copy": {"native_label": "Copy", "metric_type": "copy-MB-sec", "bytes_per_element": 16},
    "scale": {"native_label": "Scale", "metric_type": "scale-MB-sec", "bytes_per_element": 16},
    "add": {"native_label": "Add", "metric_type": "add-MB-sec", "bytes_per_element": 24},
    "triad": {"native_label": "Triad", "metric_type": "triad-MB-sec", "bytes_per_element": 24},
}


class StrictArgumentParser(argparse.ArgumentParser):
    """Turn invalid CLI input into an adapter error that can be manifested."""

    def error(self, message):
        raise ValueError(message)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def parse_cli(argv):
    parser = StrictArgumentParser(description="Run the prepared STREAM 5.10 workload")
    parser.add_argument("--test-type", choices=TEST_TYPES, default=DEFAULT_TEST_TYPE)
    parser.add_argument("--threads", type=int, default=DEFAULT_THREADS)
    parser.add_argument("--array-mib", type=int, default=DEFAULT_ARRAY_MIB)
    parser.add_argument("--cpu-node", default=DEFAULT_CPU_NODE)
    parser.add_argument("--memory-node", default=DEFAULT_MEMORY_NODE)
    parser.add_argument("--deadline", type=int, default=DEFAULT_DEADLINE)
    args, unknown = parser.parse_known_args(argv)
    if unknown:
        raise ValueError("unsupported STREAM option(s): " + " ".join(unknown))
    if not 1 <= args.threads <= MAX_THREADS:
        raise ValueError("threads must be between 1 and %d" % MAX_THREADS)
    if args.array_mib <= 0:
        raise ValueError("array-mib must be a positive integer")
    if not MIN_DEADLINE <= args.deadline <= MAX_DEADLINE:
        raise ValueError(
            "deadline must be between %d and %d seconds" % (MIN_DEADLINE, MAX_DEADLINE)
        )
    for name in ("cpu_node", "memory_node"):
        value = getattr(args, name)
        if value.lower() == "none":
            setattr(args, name, "none")
        elif not re.fullmatch(r"[0-9]+", value):
            raise ValueError("%s must be 'none' or a nonnegative NUMA node ID" % name.replace("_", "-"))
        else:
            setattr(args, name, str(int(value)))
    return args


def effective_parameters(args):
    return {
        "native_source": "standard-stream-5.10-openmp-c",
        "test_type": args.test_type,
        "threads": args.threads,
        "array_mib_per_array": args.array_mib,
        "ntimes": NTIMES,
        "cpu_node": args.cpu_node,
        "memory_node": args.memory_node,
        "deadline_seconds": args.deadline,
        "result_label": "STREAM-derived",
    }


def _one_match(pattern, text, label, flags=0):
    matches = re.findall(pattern, text, flags)
    if len(matches) != 1:
        raise ValueError("native STREAM output must contain exactly one %s" % label)
    return matches[0]


def _positive_finite(value, label):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("native STREAM %s must be finite and positive" % label)
    return number


def parse_native_output(text, expected_array_mib, expected_threads, return_code=0):
    """Validate the complete, unmodified native output and return all kernel data."""
    if return_code != 0:
        raise ValueError("native STREAM exited with status %d" % return_code)
    if not text.strip():
        raise ValueError("native STREAM output is empty")
    if re.search(r"Failed Validation|ERROR!|Error detected", text, re.IGNORECASE):
        raise ValueError("native STREAM output contains a failure marker")
    validation_error = _one_match(
        r"^Solution Validates: avg error less than ([0-9.eE+-]+) on all three arrays\s*$",
        text,
        "successful validation marker",
        re.MULTILINE,
    )
    epsilon = _positive_finite(validation_error, "validation epsilon")

    version = _one_match(r"^STREAM version \$Revision: ([^$]+) \$$", text, "source version", re.MULTILINE).strip()
    if version != "5.10":
        raise ValueError("native STREAM version %s does not match pinned version 5.10" % version)
    element_size = int(_one_match(r"^This system uses ([0-9]+) bytes per array element\.$", text, "element size", re.MULTILINE))
    if element_size != 8:
        raise ValueError("native STREAM did not run the prepared double-precision variant")
    array_shape = _one_match(
        r"^Array size = ([0-9]+) \(elements\), Offset = ([0-9]+) \(elements\)$",
        text,
        "array dimensions",
        re.MULTILINE,
    )
    elements, offset = map(int, array_shape)
    expected_elements = expected_array_mib * 1024 * 1024 // element_size
    if elements != expected_elements or offset != 0:
        raise ValueError(
            "native STREAM array dimensions %d/%d do not match prepared %d MiB/0 variant"
            % (elements, offset, expected_array_mib)
        )

    native_ntimes = int(_one_match(r"^Each kernel will be executed ([0-9]+) times\.$", text, "pass count", re.MULTILINE))
    if native_ntimes != NTIMES:
        raise ValueError("native STREAM pass count %d does not match prepared %d" % (native_ntimes, NTIMES))
    requested_team = int(
        _one_match(r"^Number of Threads requested = ([0-9]+)$", text, "requested thread count", re.MULTILINE)
    )
    counted_team = int(
        _one_match(r"^Number of Threads counted = ([0-9]+)$", text, "counted thread count", re.MULTILINE)
    )
    if requested_team != expected_threads or counted_team != expected_threads:
        raise ValueError(
            "native STREAM formed %d/%d threads; requested %d"
            % (requested_team, counted_team, expected_threads)
        )

    per_array_mib = _positive_finite(
        _one_match(r"^Memory per array = ([0-9.]+) MiB \(= ([0-9.]+) GiB\)\.$", text, "memory per array", re.MULTILINE)[0],
        "memory per array",
    )
    total_mib = _positive_finite(
        _one_match(r"^Total memory required = ([0-9.]+) MiB \(= ([0-9.]+) GiB\)\.$", text, "total memory", re.MULTILINE)[0],
        "total memory",
    )
    if abs(per_array_mib - expected_array_mib) > 0.051 or abs(total_mib - 3 * expected_array_mib) > 0.051:
        raise ValueError("native STREAM memory-size output does not match the selected prepared variant")

    granularity = re.search(
        r"^Your clock granularity/precision appears to be ([0-9]+) microseconds\.$",
        text,
        re.MULTILINE,
    )
    below_one = re.search(
        r"^Your clock granularity appears to be less than one microsecond\.$",
        text,
        re.MULTILINE,
    )
    if bool(granularity) == bool(below_one):
        raise ValueError("native STREAM clock granularity output is missing or ambiguous")
    clock_quantum_us = int(granularity.group(1)) if granularity else 1
    calibration_us = int(
        _one_match(r"^Each test below will take on the order of ([0-9]+) microseconds\.$", text, "timer calibration", re.MULTILINE)
    )
    calibration_ticks = int(
        _one_match(r"^\s*\(= ([0-9]+) clock ticks\)$", text, "timer calibration tick count", re.MULTILINE)
    )
    if calibration_us <= 0 or calibration_ticks <= 0:
        raise ValueError("native STREAM timer calibration must be positive")

    _one_match(
        r"^Function\s+Best Rate MB/s\s+Avg time\s+Min time\s+Max time\s*$",
        text,
        "kernel summary header",
        re.MULTILINE,
    )
    kernels = {}
    for test_type, spec in KERNELS.items():
        label = re.escape(spec["native_label"] + ":")
        row = _one_match(
            r"^%s\s+([0-9]+(?:\.[0-9]+)?)\s+([0-9]+(?:\.[0-9]+)?)\s+([0-9]+(?:\.[0-9]+)?)\s+([0-9]+(?:\.[0-9]+)?)\s*$"
            % label,
            text,
            "%s summary row" % spec["native_label"],
            re.MULTILINE,
        )
        rate, average, minimum, maximum = map(float, row)
        for value, metric in (
            (rate, "best rate"),
            (average, "average time"),
            (minimum, "minimum time"),
            (maximum, "maximum time"),
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("native STREAM %s %s must be finite and positive" % (spec["native_label"], metric))
        if minimum > average + 1e-6 or average > maximum + 1e-6:
            raise ValueError("native STREAM %s timing summary is not ordered" % spec["native_label"])

        exact_bytes = spec["bytes_per_element"] * elements
        expected_rate = exact_bytes / minimum / 1_000_000
        # The native rate is printed to 0.1 MB/s and its minimum time to 1 us.
        if minimum <= 0.5e-6:
            raise ValueError("native STREAM %s minimum time is too short for printed-rate validation" % spec["native_label"])
        tolerance = expected_rate * 0.5e-6 / (minimum - 0.5e-6) + 0.051
        if abs(rate - expected_rate) > tolerance:
            raise ValueError(
                "native STREAM %s best rate %.1f MB/s does not match bytes/minimum-time %.6f MB/s"
                % (spec["native_label"], rate, expected_rate)
            )
        kernels[test_type] = {
            "native_label": spec["native_label"],
            "metric_type": spec["metric_type"],
            "best_rate_mb_sec": rate,
            "average_time_seconds": average,
            "minimum_time_seconds": minimum,
            "maximum_time_seconds": maximum,
            "bytes_per_element_counted": exact_bytes,
            "native_rate_precision_mb_sec": 0.1,
            "native_time_precision_seconds": 0.000001,
        }

    return {
        "source_version": version,
        "array_elements_per_array": elements,
        "array_bytes_per_array": elements * element_size,
        "total_array_bytes": elements * element_size * 3,
        "array_offset_elements": offset,
        "bytes_per_element": element_size,
        "array_mib_per_array_printed": per_array_mib,
        "total_memory_mib_printed": total_mib,
        "ntimes": native_ntimes,
        "threads_requested_printed": requested_team,
        "threads_counted_printed": counted_team,
        "clock_quantum_microseconds": clock_quantum_us,
        "clock_granularity_below_one_microsecond": bool(below_one),
        "timer_calibration_microseconds": calibration_us,
        "timer_calibration_clock_ticks": calibration_ticks,
        "validation_epsilon": epsilon,
        "native_validation": "Solution Validates",
        "kernels": kernels,
    }


def validate_native_summary_duration(native, process_seconds):
    """Require the reported full-pass kernel averages to fit the process envelope."""
    if (
        isinstance(process_seconds, bool)
        or not isinstance(process_seconds, (int, float))
        or not math.isfinite(process_seconds)
        or process_seconds <= 0
    ):
        raise ValueError("STREAM process wall-clock duration is missing or invalid")
    kernels = native.get("kernels")
    if not isinstance(kernels, dict) or set(kernels) != set(KERNELS):
        raise ValueError("STREAM native result does not contain all four kernels")
    timed_kernel_seconds = (NTIMES - 1) * sum(
        item["average_time_seconds"] for item in kernels.values()
    )
    tolerance = max(0.05, process_seconds * 0.01)
    if not math.isfinite(timed_kernel_seconds) or timed_kernel_seconds > process_seconds + tolerance:
        raise ValueError(
            "STREAM reported average kernel times total %.6f seconds, outside the %.6f-second process envelope"
            % (timed_kernel_seconds, process_seconds)
        )
    return timed_kernel_seconds
