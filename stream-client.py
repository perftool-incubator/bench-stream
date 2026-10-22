#!/usr/bin/env python3
# -*- mode: python; indent-tabs-mode: nil; python-indent: 4 -*-
# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=python

"""Run and validate one prepared native STREAM process."""

import json
import logging
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from stream_common import (
    COMPILER_FLAGS,
    INSTALL_DIR,
    NTIMES,
    SOURCE_SHA256,
    UPSTREAM_COMMIT,
    UPSTREAM_REPOSITORY,
    atomic_json,
    effective_parameters,
    parse_cli,
    parse_native_output,
    sha256_file,
    validate_native_summary_duration,
)


OUTPUT_PATH = Path("stream-output.txt")
MANIFEST_PATH = Path("stream-run-manifest.json")
ERROR_PATH = Path("stream-error.txt")
ACTIVE_PROCESS = None
SIGNAL_RECEIVED = None


def _epoch_time_ns():
    time_ns = getattr(time, "time_ns", None)
    if time_ns is not None:
        return time_ns()
    return int(time.time() * 1_000_000_000)


def _monotonic_time_ns():
    monotonic_ns = getattr(time, "monotonic_ns", None)
    if monotonic_ns is not None:
        return monotonic_ns()
    return int(time.monotonic() * 1_000_000_000)


def _unlink_if_exists(path):
    try:
        path.unlink()
    except FileNotFoundError:
        pass


class RunInterrupted(Exception):
    """Raised when Crucible asks the adapter process to stop."""


def handle_signal(signum, _frame):
    global SIGNAL_RECEIVED
    if SIGNAL_RECEIVED is None:
        SIGNAL_RECEIVED = signum


def raise_if_interrupted():
    if SIGNAL_RECEIVED is not None:
        raise RunInterrupted("STREAM client interrupted by signal %s" % SIGNAL_RECEIVED)


def terminate_process_group(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait()
        return
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    # The leader may exit while a descendant in the same owned process group
    # remains alive. Signal the group after the grace period either way.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def parse_id_list(value, label):
    """Parse Linux CPU/node list syntax while rejecting malformed ranges."""
    if not value or not value.strip():
        raise ValueError("%s is empty" % label)
    result = []
    seen = set()
    for token in value.split(","):
        token = token.strip()
        if re.fullmatch(r"[0-9]+", token):
            first = last = int(token)
        else:
            match = re.fullmatch(r"([0-9]+)-([0-9]+)", token)
            if not match:
                raise ValueError("invalid %s entry %r" % (label, token))
            first, last = map(int, match.groups())
            if last < first:
                raise ValueError("descending %s range %r" % (label, token))
        for number in range(first, last + 1):
            if number not in seen:
                seen.add(number)
                result.append(number)
    return sorted(result)


def proc_status_value(key):
    status_path = Path("/proc/self/status")
    if not status_path.is_file():
        raise RuntimeError("Linux /proc/self/status is required for affinity and memory checks")
    prefix = key + ":"
    for line in status_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    raise RuntimeError("/proc/self/status does not contain %s" % key)


def effective_cpu_mask():
    if not hasattr(os, "sched_getaffinity"):
        raise RuntimeError("STREAM CPU validation requires Linux sched_getaffinity")
    affinity = sorted(os.sched_getaffinity(0))
    proc_allowed = parse_id_list(proc_status_value("Cpus_allowed_list"), "Cpus_allowed_list")
    if not affinity:
        raise RuntimeError("the STREAM client has no effective CPUs")
    if not set(affinity).issubset(proc_allowed):
        raise RuntimeError("sched_getaffinity contains CPUs outside /proc/self/status Cpus_allowed_list")
    return affinity, proc_allowed


def effective_memory_nodes():
    value = proc_status_value("Mems_allowed_list")
    return parse_id_list(value, "Mems_allowed_list"), value


def load_live_topology(required):
    toolbox_home = os.environ.get("TOOLBOX_HOME")
    if not toolbox_home:
        if required:
            raise RuntimeError("cpu-node placement requires TOOLBOX_HOME and the toolbox CPU-topology helper")
        return None
    toolbox_python = Path(toolbox_home) / "python"
    if not toolbox_python.is_dir():
        if required:
            raise RuntimeError("cpu-node placement cannot find toolbox Python modules at %s" % toolbox_python)
        return None
    if str(toolbox_python) not in sys.path:
        sys.path.insert(0, str(toolbox_python))
    try:
        from toolbox.system_cpu_topology import system_cpu_topology

        logger = logging.getLogger("stream.cpu_topology")
        logger.addHandler(logging.NullHandler())
        logger.setLevel(logging.ERROR)
        return system_cpu_topology(log=logger)
    except Exception as exc:
        if required:
            raise RuntimeError("toolbox CPU-topology discovery failed: %s" % exc)
        return None


def select_node_cpus(topology, node_id, effective_cpus, thread_count):
    candidates = []
    for cpu in effective_cpus:
        try:
            cpu_node = topology.get_node(cpu)
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        if cpu_node is not None and int(cpu_node) == node_id:
            candidates.append(cpu)
    if not candidates:
        raise ValueError(
            "cpu-node=%d has no CPUs in the process's effective allowed mask %s"
            % (node_id, effective_cpus)
        )

    candidate_set = set(candidates)
    sibling_groups = {}
    for cpu in candidates:
        try:
            siblings = tuple(
                sorted(({cpu} | set(topology.get_thread_siblings(cpu))) & candidate_set)
            )
        except (AttributeError, KeyError, TypeError, ValueError):
            siblings = (cpu,)
        if not siblings:
            siblings = (cpu,)
        sibling_groups[siblings] = siblings
    groups = [sibling_groups[key] for key in sorted(sibling_groups)]
    chosen = [group[0] for group in groups]
    if thread_count > len(chosen):
        for sibling_index in range(1, max(map(len, groups), default=1)):
            for group in groups:
                if sibling_index < len(group):
                    chosen.append(group[sibling_index])
                    if len(chosen) == thread_count:
                        break
            if len(chosen) == thread_count:
                break
    if len(chosen) < thread_count:
        raise ValueError(
            "threads=%d exceeds cpu-node=%d effective CPUs (%d) after cpuset filtering"
            % (thread_count, node_id, len(candidates))
        )
    chosen = sorted(chosen[:thread_count])
    if len(set(chosen)) != thread_count:
        raise RuntimeError("toolbox topology produced duplicate CPUs for the requested thread count")
    return candidates, chosen


def _parse_size_value(value):
    if value == "max":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def cgroup_memory_observation():
    """Read cgroup v2 memory limits from the process cgroup through its root."""
    cgroup_directory = None
    try:
        for line in Path("/proc/self/cgroup").read_text(encoding="utf-8").splitlines():
            parts = line.split(":", 2)
            if len(parts) == 3 and parts[0] == "0" and parts[1] == "":
                cgroup_directory = Path("/sys/fs/cgroup") / parts[2].lstrip("/")
                break
    except OSError:
        pass

    root = Path("/sys/fs/cgroup")
    candidates = []
    if cgroup_directory is not None:
        try:
            resolved_cgroup = cgroup_directory.resolve(strict=False)
            resolved_root = root.resolve(strict=False)
            relative = resolved_cgroup.relative_to(resolved_root)
            directory = resolved_root / relative
            while True:
                candidates.append(directory)
                if directory == resolved_root:
                    break
                directory = directory.parent
        except (OSError, ValueError):
            # Do not follow a malformed or out-of-mount path from procfs.
            candidates = []
    if root not in candidates:
        candidates.append(root)
    seen = set()
    observations = []
    for directory in candidates:
        if str(directory) in seen:
            continue
        seen.add(str(directory))
        limit_path = directory / "memory.max"
        current_path = directory / "memory.current"
        high_path = directory / "memory.high"
        try:
            raw_limit = limit_path.read_text(encoding="utf-8").strip()
            raw_current = current_path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        raw_high = None
        try:
            raw_high = high_path.read_text(encoding="utf-8").strip()
        except OSError:
            pass
        limit = _parse_size_value(raw_limit)
        high = _parse_size_value(raw_high)
        current = _parse_size_value(raw_current)
        effective_limit_candidates = [value for value in (limit, high) if value is not None]
        effective_limit = min(effective_limit_candidates) if effective_limit_candidates else None
        available = (
            max(0, effective_limit - current)
            if effective_limit is not None and current is not None
            else None
        )
        observations.append(
            {
                "path": str(directory),
                "memory_max_raw": raw_limit,
                "memory_high_raw": raw_high,
                "memory_current_raw": raw_current,
                "effective_limit_bytes": effective_limit,
                "available_bytes_estimate": available,
            }
        )
    return observations


def host_memory_available_bytes():
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        if line.startswith("MemAvailable:"):
            fields = line.split()
            if len(fields) >= 2:
                return int(fields[1]) * 1024
    raise RuntimeError("/proc/meminfo does not expose MemAvailable")


def system_memory_nodes():
    """Return sysfs nodes with memory, rather than every NUMA node."""
    node_root = Path("/sys/devices/system/node")
    path = node_root / "has_memory"
    try:
        raw_nodes = path.read_text(encoding="utf-8").strip()
    except OSError:
        raw_nodes = ""
    if raw_nodes:
        try:
            nodes = parse_id_list(raw_nodes, "sysfs has_memory")
        except ValueError:
            nodes = []
        if nodes:
            return nodes

    try:
        node_paths = list(node_root.glob("node[0-9]*"))
    except OSError as exc:
        raise RuntimeError("cannot enumerate sysfs NUMA nodes with memory") from exc
    numeric_nodes = []
    for node_path in node_paths:
        match = re.fullmatch(r"node([0-9]+)", node_path.name)
        if match:
            numeric_nodes.append((int(match.group(1)), node_path))
    if not numeric_nodes:
        raise RuntimeError("cannot inspect sysfs NUMA nodes with memory for STREAM preflight")

    nodes = []
    for node_id, node_path in numeric_nodes:
        if not node_path.is_dir():
            raise RuntimeError("cannot inspect sysfs memory node %d" % node_id)
        try:
            observation = node_memory_observation(node_id)
        except (OSError, RuntimeError) as exc:
            raise RuntimeError(
                "cannot determine whether sysfs memory node %d has memory" % node_id
            ) from exc
        if observation["mem_total_bytes_observation"] > 0:
            nodes.append(node_id)
    if nodes:
        return sorted(set(nodes))
    raise RuntimeError("cannot inspect sysfs NUMA nodes with memory for STREAM preflight")


def node_memory_observation(node_id):
    node_path = Path("/sys/devices/system/node/node%d" % node_id)
    if not node_path.is_dir():
        raise RuntimeError("memory-node=%d is not present in sysfs" % node_id)
    node_meminfo = node_path / "meminfo"
    if not node_meminfo.is_file():
        raise RuntimeError("cannot preflight memory-node=%d without sysfs node memory data" % node_id)
    values = {}
    for line in node_meminfo.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"^Node\s+%d\s+(MemFree|MemTotal):\s+([0-9]+)\s+kB$" % node_id, line.strip())
        if match:
            values[match.group(1)] = int(match.group(2)) * 1024
    node_free = values.get("MemFree")
    node_total = values.get("MemTotal")
    if node_free is None or node_total is None:
        raise RuntimeError("cannot preflight memory-node=%d: MemFree/MemTotal unavailable" % node_id)
    return {
        "node": node_id,
        "mem_free_bytes_observation": node_free,
        "mem_total_bytes_observation": node_total,
        "free_memory_preflight_is_a_snapshot": True,
    }


def memory_preflight(array_mib, memory_node, allowed_nodes):
    required_bytes = array_mib * 1024 * 1024 * 3
    host_available = host_memory_available_bytes()
    cgroups = cgroup_memory_observation()
    limits = [host_available]
    for observation in cgroups:
        available = observation.get("available_bytes_estimate")
        if available is not None:
            limits.append(available)
    conservative_available = min(limits)
    safe_available = int(conservative_available * 0.90)
    if required_bytes > safe_available:
        raise ValueError(
            "array-mib=%d requires %d bytes for three arrays; conservative available memory is %d bytes "
            "(90%% preflight ceiling %d bytes)"
            % (array_mib, required_bytes, conservative_available, safe_available)
        )

    node_observation = None
    allowed_nodes_observation = None
    if memory_node != "none":
        node_id = int(memory_node)
        if node_id not in allowed_nodes:
            raise ValueError(
                "memory-node=%d is outside /proc/self/status Mems_allowed_list %s"
                % (node_id, allowed_nodes)
            )
        node_observation = node_memory_observation(node_id)
        node_free = node_observation["mem_free_bytes_observation"]
        if required_bytes > int(node_free * 0.90):
            raise ValueError(
                "array-mib=%d requires %d bytes for three arrays, exceeding 90%% of memory-node=%d MemFree (%d bytes)"
                % (array_mib, required_bytes, node_id, node_free)
            )
    else:
        system_nodes = system_memory_nodes()
        allowed_memory_nodes = sorted(set(system_nodes) & set(allowed_nodes))
        if not allowed_memory_nodes:
            raise RuntimeError(
                "Mems_allowed_list %s has no sysfs NUMA nodes with memory"
                % allowed_nodes
            )
        if set(allowed_memory_nodes) != set(system_nodes):
            node_observations = [node_memory_observation(node) for node in allowed_memory_nodes]
            allowed_free_bytes = sum(
                observation["mem_free_bytes_observation"]
                for observation in node_observations
            )
            allowed_nodes_observation = {
                "system_memory_nodes": system_nodes,
                "allowed_memory_nodes": allowed_memory_nodes,
                "allowed_node_observations": node_observations,
                "mem_free_sum_bytes_observation": allowed_free_bytes,
                "free_memory_preflight_is_a_snapshot": True,
                "inherited_memory_policy_may_be_more_restrictive": True,
            }
            if required_bytes > int(allowed_free_bytes * 0.90):
                raise ValueError(
                    "array-mib=%d requires %d bytes for three arrays, exceeding 90%% of the allowed memory-node MemFree sum (%d bytes across nodes %s)"
                    % (array_mib, required_bytes, allowed_free_bytes, allowed_memory_nodes)
                )
    return {
        "required_array_bytes": required_bytes,
        "required_array_bytes_per_array": array_mib * 1024 * 1024,
        "host_mem_available_bytes": host_available,
        "cgroup_memory_observations": cgroups,
        "cgroup_memory_observation_status": (
            "readable cgroup-v2 memory files were included"
            if cgroups
            else "no readable cgroup-v2 memory files; host MemAvailable only"
        ),
        "conservative_available_bytes_estimate": conservative_available,
        "preflight_headroom_fraction": 0.10,
        "preflight_passed": True,
        "memory_node_observation": node_observation,
        "allowed_nodes_memory_observation": allowed_nodes_observation,
    }


def cache_topology_observation(cpus):
    """Capture advisory cache data; absence never counts as a run-rule pass."""
    domains = {}
    for cpu in cpus:
        cache_root = Path("/sys/devices/system/cpu/cpu%d/cache" % cpu)
        try:
            index_dirs = list(cache_root.glob("index*"))
        except OSError:
            continue
        for index_dir in index_dirs:
            try:
                level = int((index_dir / "level").read_text(encoding="utf-8").strip())
                shared = (index_dir / "shared_cpu_list").read_text(encoding="utf-8").strip()
                size_text = (index_dir / "size").read_text(encoding="utf-8").strip()
            except (OSError, ValueError):
                continue
            if level < 3:
                continue
            size_match = re.fullmatch(r"([0-9]+)([KMG])", size_text, re.IGNORECASE)
            if not size_match:
                continue
            multiplier = {"K": 1024, "M": 1024**2, "G": 1024**3}[size_match.group(2).upper()]
            size_bytes = int(size_match.group(1)) * multiplier
            try:
                shared_cpus = parse_id_list(shared, "cache shared_cpu_list")
            except ValueError:
                continue
            if not set(shared_cpus) & set(cpus):
                continue
            key = (level, tuple(shared_cpus))
            domains[key] = size_bytes
    if not domains:
        return {"available_cache_bytes_estimate": None, "domains": [], "status": "unknown"}
    result_domains = [
        {
            "level": level,
            "shared_cpus": list(shared),
            "size_bytes": size,
        }
        for (level, shared), size in sorted(domains.items())
    ]
    return {
        "available_cache_bytes_estimate": sum(domains.values()),
        "domains": result_domains,
        "status": "advisory-sysfs-observation; not a STREAM Run Rule assertion",
    }


def load_variant(array_mib):
    if platform.machine() != "x86_64":
        raise RuntimeError("the initial STREAM adapter supports x86_64 only")
    build_manifest_path = INSTALL_DIR / "build-manifest.json"
    if not build_manifest_path.is_file():
        raise RuntimeError("STREAM prepared build manifest is missing: %s" % build_manifest_path)
    build_manifest = json.loads(build_manifest_path.read_text(encoding="utf-8"))
    if build_manifest.get("upstream_commit") != UPSTREAM_COMMIT:
        raise RuntimeError("installed STREAM source revision does not match the adapter pin")
    if build_manifest.get("source_sha256") != SOURCE_SHA256:
        raise RuntimeError("installed STREAM source digest does not match the adapter pin")
    if build_manifest.get("source_modified") is not False:
        raise RuntimeError("installed STREAM build manifest does not identify unmodified source")
    if build_manifest.get("result_label") != "STREAM-derived":
        raise RuntimeError("installed STREAM build manifest is missing the required result label")
    if build_manifest.get("ntimes") != NTIMES:
        raise RuntimeError("installed STREAM build manifest has the wrong fixed pass count")
    if build_manifest.get("architecture") != platform.machine():
        raise RuntimeError("installed STREAM architecture does not match this engine")
    if build_manifest.get("compiler_flags") != list(COMPILER_FLAGS):
        raise RuntimeError("installed STREAM build flags do not match the reviewed Workshop inputs")
    for name_key, digest_key in (
        ("license_file", "license_sha256"),
        ("notice_file", "notice_sha256"),
        ("adapter_license_file", "adapter_license_sha256"),
    ):
        name = build_manifest.get(name_key)
        expected_digest = build_manifest.get(digest_key)
        if not isinstance(name, str) or Path(name).name != name:
            raise RuntimeError("STREAM build manifest has an invalid %s path" % name_key)
        license_path = INSTALL_DIR / name
        if not license_path.is_file() or sha256_file(license_path) != expected_digest:
            raise RuntimeError("installed STREAM %s is missing or differs from its build manifest" % name_key)
    variants = build_manifest.get("variants")
    if not isinstance(variants, list):
        raise RuntimeError("installed STREAM build manifest has no prepared variants")
    matches = [item for item in variants if item.get("array_mib_per_array") == array_mib]
    if len(matches) != 1:
        prepared = [item.get("array_mib_per_array") for item in variants]
        raise RuntimeError(
            "array-mib=%d is not a unique prepared variant; installed per-array sizes are %s"
            % (array_mib, prepared)
        )
    provenance = matches[0]
    binary_name = provenance.get("binary_name")
    if not isinstance(binary_name, str) or Path(binary_name).name != binary_name:
        raise RuntimeError("prepared STREAM variant has an invalid executable name")
    binary = INSTALL_DIR / binary_name
    provenance_path = INSTALL_DIR / (binary.name + ".build.json")
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise RuntimeError("prepared STREAM executable is missing or not executable: %s" % binary)
    if not provenance_path.is_file():
        raise RuntimeError("prepared STREAM variant provenance is missing: %s" % provenance_path)
    variant_provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    if variant_provenance != provenance:
        raise RuntimeError("STREAM variant provenance differs from the build manifest")
    if provenance.get("ntimes") != NTIMES:
        raise RuntimeError("prepared STREAM variant has the wrong NTIMES value")
    expected_flags = list(COMPILER_FLAGS) + [
        "-DSTREAM_ARRAY_SIZE=%d" % (array_mib * 1024 * 1024 // 8),
        "-DNTIMES=%d" % NTIMES,
    ]
    if provenance.get("compiler_flags") != expected_flags:
        raise RuntimeError("prepared STREAM variant was not built with the reviewed compiler flags")
    if provenance.get("result_label") != "STREAM-derived":
        raise RuntimeError("prepared STREAM variant is missing the required result label")
    if provenance.get("binary_sha256") != sha256_file(binary):
        raise RuntimeError("prepared STREAM binary digest does not match build provenance")
    return binary, provenance, build_manifest


def prepare_launch(args, binary):
    if os.environ.get("cpu_partitioning", "0") == "1":
        raise RuntimeError("initial STREAM support requires cpu-partitioning=false")
    allowed_cpus, proc_allowed_cpus = effective_cpu_mask()
    if args.threads > len(allowed_cpus):
        raise ValueError(
            "threads=%d exceeds the process effective CPU mask %s"
            % (args.threads, allowed_cpus)
        )
    allowed_nodes, raw_allowed_nodes = effective_memory_nodes()
    topology = load_live_topology(required=args.cpu_node != "none")
    selected_cpus = None
    node_cpus = None
    if args.cpu_node != "none":
        node_id = int(args.cpu_node)
        node_cpus, selected_cpus = select_node_cpus(topology, node_id, allowed_cpus, args.threads)
    memory = memory_preflight(args.array_mib, args.memory_node, allowed_nodes)
    cache = cache_topology_observation(selected_cpus if selected_cpus is not None else allowed_cpus)

    command = []
    if args.cpu_node != "none" or args.memory_node != "none":
        numactl = shutil.which("numactl")
        if not numactl:
            raise RuntimeError("explicit CPU or memory node placement requires Workshop-provided numactl")
        command.append(numactl)
        if selected_cpus is not None:
            command.append("--physcpubind=" + ",".join(str(cpu) for cpu in selected_cpus))
        if args.memory_node != "none":
            command.append("--membind=" + args.memory_node)
    command.append(str(binary))

    environment = os.environ.copy()
    environment["OMP_NUM_THREADS"] = str(args.threads)
    environment["OMP_DYNAMIC"] = "FALSE"
    # Avoid inherited affinity/place settings silently overriding the selected
    # node CPUs or narrowing the requested OpenMP team.
    for key in ("OMP_PLACES", "OMP_THREAD_LIMIT", "GOMP_CPU_AFFINITY", "OMP_PROC_BIND"):
        environment.pop(key, None)
    if selected_cpus is not None:
        environment["GOMP_CPU_AFFINITY"] = " ".join(str(cpu) for cpu in selected_cpus)
        environment["OMP_PROC_BIND"] = "TRUE"

    affinity = {
        "sched_getaffinity_cpus_before_launch": allowed_cpus,
        "proc_cpus_allowed_list": proc_allowed_cpus,
        "cpu_node_requested": args.cpu_node,
        "cpu_node_effective_cpus": node_cpus,
        "selected_cpus_for_native_process": selected_cpus,
        "openmp_threads": args.threads,
        "partitioning_requested": os.environ.get("cpu_partitioning", "0"),
        "partitioning_supported": False,
    }
    memory_policy = {
        "memory_node_requested": args.memory_node,
        "mems_allowed_list_raw": raw_allowed_nodes,
        "mems_allowed_nodes": allowed_nodes,
        "policy": "numactl --membind=%s" % args.memory_node
        if args.memory_node != "none"
        else "inherited current process memory policy",
        "first_touch": "native STREAM initializes arrays with parallel OpenMP loops",
    }
    return command, environment, affinity, memory_policy, memory, cache


def run_native(command, environment, deadline):
    global ACTIVE_PROCESS
    raise_if_interrupted()
    start_epoch_ns = _epoch_time_ns()
    start_monotonic_ns = _monotonic_time_ns()
    timed_out = False
    interrupted = False
    with OUTPUT_PATH.open("wb") as output:
        process = subprocess.Popen(
            command,
            stdout=output,
            stderr=subprocess.STDOUT,
            env=environment,
            start_new_session=True,
        )
        ACTIVE_PROCESS = process
        try:
            deadline_at = time.monotonic() + deadline
            while True:
                if SIGNAL_RECEIVED is not None:
                    interrupted = True
                    terminate_process_group(process)
                    return_code = process.returncode
                    break
                remaining = deadline_at - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    terminate_process_group(process)
                    return_code = process.returncode
                    break
                try:
                    return_code = process.wait(timeout=min(remaining, 0.5))
                    break
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if process.poll() is None:
                terminate_process_group(process)
            ACTIVE_PROCESS = None
    completion_epoch_ns = _epoch_time_ns()
    completion_monotonic_ns = _monotonic_time_ns()
    return {
        "command": command,
        "launch_begin_epoch_ns": start_epoch_ns,
        "process_completion_epoch_ns": completion_epoch_ns,
        "process_wallclock_seconds": (completion_monotonic_ns - start_monotonic_ns) / 1_000_000_000,
        "process_pid": process.pid,
        "process_group_id": process.pid,
        "return_code": return_code,
        "timed_out": timed_out,
        "interrupted": interrupted,
    }


def manifest_base():
    return {
        "benchmark": "stream",
        "native_source": "standard-stream-5.10-openmp-c",
        "upstream_repository": UPSTREAM_REPOSITORY,
        "upstream_commit": UPSTREAM_COMMIT,
        "source_sha256": SOURCE_SHA256,
        "source_modified": False,
        "result_label": "STREAM-derived",
        "status": "invalid",
        "requested": {},
        "timing_note": (
            "Native STREAM reports per-kernel best rates and relative times, but no absolute wall-clock "
            "endpoints for individual kernel iterations. CDM begin/end are the child-process "
            "score-support envelope; it includes array initialization, all four sequential kernels, "
            "native validation, and output, and is not a native per-kernel measurement interval. "
            "CDM epoch-millisecond endpoints are rounded outward, with at most 1 ms quantization."
        ),
        "test_type_note": (
            "test-type selects the primary result only. The unmodified native process runs and validates "
            "Copy, Scale, Add, and Triad on every invocation."
        ),
    }


def main(argv):
    global ACTIVE_PROCESS
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)
    manifest = manifest_base()
    try:
        for artifact in (OUTPUT_PATH, MANIFEST_PATH, ERROR_PATH):
            _unlink_if_exists(artifact)
        args = parse_cli(argv)
        manifest["requested"] = effective_parameters(args)
        binary, provenance, build_manifest = load_variant(args.array_mib)
        command, environment, affinity, memory_policy, memory, cache = prepare_launch(args, binary)
        manifest["build_provenance"] = provenance
        manifest["build_manifest_summary"] = {
            "architecture": build_manifest.get("architecture"),
            "compiler": build_manifest.get("compiler"),
            "compiler_version": build_manifest.get("compiler_version"),
            "compiler_release": build_manifest.get("compiler_release"),
            "compiler_flags": build_manifest.get("compiler_flags"),
        }
        manifest["cpu_affinity"] = affinity
        manifest["memory_policy"] = memory_policy
        manifest["memory_preflight"] = memory
        manifest["cache_observation"] = cache
        manifest["native_environment"] = {
            "OMP_NUM_THREADS": environment.get("OMP_NUM_THREADS"),
            "OMP_DYNAMIC": environment.get("OMP_DYNAMIC"),
            "OMP_PLACES": environment.get("OMP_PLACES"),
            "OMP_THREAD_LIMIT": environment.get("OMP_THREAD_LIMIT"),
            "GOMP_CPU_AFFINITY": environment.get("GOMP_CPU_AFFINITY"),
            "OMP_PROC_BIND": environment.get("OMP_PROC_BIND"),
            "inherited_openmp_affinity_controls_cleared": True,
        }

        process = run_native(command, environment, args.deadline)
        manifest["process"] = process
        if process["interrupted"]:
            raise RunInterrupted("STREAM native process interrupted")
        if process["timed_out"]:
            raise RuntimeError(
                "native STREAM exceeded its %d-second deadline; owned process group was terminated"
                % args.deadline
            )
        output_text = OUTPUT_PATH.read_text(encoding="utf-8", errors="replace")
        native = parse_native_output(
            output_text,
            args.array_mib,
            args.threads,
            return_code=process["return_code"],
        )
        validate_native_summary_duration(native, process["process_wallclock_seconds"])
        raise_if_interrupted()
        selected = native["kernels"][args.test_type]
        manifest["native_result"] = native
        manifest["selected_result"] = {
            "test_type": args.test_type,
            "native_kernel": selected["native_label"],
            "metric_type": selected["metric_type"],
            "value_mb_sec": selected["best_rate_mb_sec"],
            "selection_effect": "reports the selected rate; it does not suppress other native kernels",
        }
        manifest["status"] = "valid"
        raise_if_interrupted()
        atomic_json(MANIFEST_PATH, manifest)
        raise_if_interrupted()
        print(
            "STREAM completed: selected %s best rate %.1f MB/s; all four native kernels validated"
            % (selected["native_label"], selected["best_rate_mb_sec"]),
            flush=True,
        )
        return 0
    except Exception as exc:
        if ACTIVE_PROCESS is not None and ACTIVE_PROCESS.poll() is None:
            terminate_process_group(ACTIVE_PROCESS)
            ACTIVE_PROCESS = None
        message = str(exc)
        manifest["status"] = "invalid"
        manifest["error"] = message
        try:
            atomic_json(MANIFEST_PATH, manifest)
            ERROR_PATH.write_text(message + "\n", encoding="utf-8")
        except OSError as write_error:
            print("Could not preserve STREAM failure details: %s" % write_error, file=sys.stderr)
        print("STREAM error: %s" % message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
