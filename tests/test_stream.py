import importlib.util
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(ROOT))

import stream_common as COMMON


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CLIENT = load_module("stream_client", "stream-client.py")
POST = load_module("stream_post_process", "stream-post-process.py")
NATIVE_OUTPUT = (FIXTURES / "stream-output-success.txt").read_text(encoding="utf-8")
VALID_CLI = [
    "--test-type",
    "triad",
    "--threads",
    "1",
    "--array-mib",
    "256",
    "--cpu-node",
    "none",
    "--memory-node",
    "none",
    "--deadline",
    "30",
]


def make_valid_manifest(output=NATIVE_OUTPUT):
    args = COMMON.parse_cli(VALID_CLI)
    native = COMMON.parse_native_output(output, args.array_mib, args.threads)
    kernel = native["kernels"][args.test_type]
    launch_ns = 1_000_123_456
    completion_ns = 31_000_123_456
    return {
        "status": "valid",
        "upstream_repository": COMMON.UPSTREAM_REPOSITORY,
        "upstream_commit": COMMON.UPSTREAM_COMMIT,
        "source_sha256": COMMON.SOURCE_SHA256,
        "source_modified": False,
        "result_label": "STREAM-derived",
        "requested": COMMON.effective_parameters(args),
        "process": {
            "return_code": 0,
            "timed_out": False,
            "interrupted": False,
            "launch_begin_epoch_ns": launch_ns,
            "process_completion_epoch_ns": completion_ns,
            "process_wallclock_seconds": 30.0,
        },
        "build_provenance": {
            "source_sha256": COMMON.SOURCE_SHA256,
            "upstream_commit": COMMON.UPSTREAM_COMMIT,
            "array_mib_per_array": args.array_mib,
            "ntimes": COMMON.NTIMES,
            "source_modified": False,
            "result_label": "STREAM-derived",
            "binary_sha256": "a" * 64,
        },
        "native_result": native,
        "selected_result": {
            "test_type": args.test_type,
            "native_kernel": kernel["native_label"],
            "metric_type": kernel["metric_type"],
            "value_mb_sec": kernel["best_rate_mb_sec"],
            "selection_effect": "reports the selected rate; it does not suppress other native kernels",
        },
    }


class RuntimeParameterTests(unittest.TestCase):
    def test_runtime_defaults_match_multiplex_empty_set_defaults(self):
        args = COMMON.parse_cli([])
        config = json.loads((ROOT / "multiplex.json").read_text(encoding="utf-8"))
        defaults = {
            item["arg"]: item["vals"]
            for item in config["presets"]["defaults"]
        }
        self.assertEqual(
            defaults,
            {
                "test-type": [COMMON.DEFAULT_TEST_TYPE],
                "threads": [str(COMMON.DEFAULT_THREADS)],
                "array-mib": [str(COMMON.DEFAULT_ARRAY_MIB)],
                "cpu-node": [COMMON.DEFAULT_CPU_NODE],
                "memory-node": [COMMON.DEFAULT_MEMORY_NODE],
                "deadline": [str(COMMON.DEFAULT_DEADLINE)],
            },
        )
        self.assertEqual(COMMON.effective_parameters(args)["test_type"], "triad")
        self.assertNotIn("essentials", config["presets"])

    def test_partial_cli_parameters_receive_runtime_fallbacks(self):
        args = COMMON.parse_cli(["--threads", "2"])
        self.assertEqual(args.threads, 2)
        self.assertEqual(args.test_type, "triad")
        self.assertEqual(args.array_mib, 512)
        self.assertEqual(args.cpu_node, "none")
        self.assertEqual(args.memory_node, "none")
        self.assertEqual(args.deadline, 1800)

    def test_accepts_each_explicit_kernel_type(self):
        for test_type in COMMON.TEST_TYPES:
            with self.subTest(test_type=test_type):
                self.assertEqual(COMMON.parse_cli(["--test-type", test_type]).test_type, test_type)

    def test_rejects_invalid_kernel_nodes_and_deadlines(self):
        for argv in (
            ["--test-type", "all"],
            ["--cpu-node", "-1"],
            ["--memory-node", "node0"],
            ["--deadline", "29"],
            ["--deadline", "3601"],
        ):
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                COMMON.parse_cli(argv)

    def test_multiplex_patterns_cover_types_prepared_sizes_and_bounds(self):
        config = json.loads((ROOT / "multiplex.json").read_text(encoding="utf-8"))
        validations = config["validations"]
        type_pattern = validations["selected_stream_kernel"]["vals"]
        array_pattern = validations["prepared_array_variant"]["vals"]
        thread_pattern = validations["supported_thread_count"]["vals"]
        deadline_pattern = validations["bounded_deadline"]["vals"]
        for value in COMMON.TEST_TYPES:
            self.assertRegex(value, re.compile(type_pattern))
        for value in ("all", "read", "bad"):
            self.assertIsNone(re.fullmatch(type_pattern, value))
        for value in ("256", "512", "1024", "2048"):
            self.assertRegex(value, re.compile(array_pattern))
        for value in ("128", "2560"):
            self.assertIsNone(re.fullmatch(array_pattern, value))
        for value in ("1", "4096"):
            self.assertRegex(value, re.compile(thread_pattern))
        for value in ("0", "4097"):
            self.assertIsNone(re.fullmatch(thread_pattern, value))
        for value in ("30", "300", "3600"):
            self.assertRegex(value, re.compile(deadline_pattern))
        for value in ("29", "3601"):
            self.assertIsNone(re.fullmatch(deadline_pattern, value))

    def test_example_runfile_sweeps_types_and_threads_client_only(self):
        runfile = json.loads((ROOT / "examples/stream.remotehosts.json").read_text(encoding="utf-8"))
        endpoint = runfile["endpoints"][0]
        self.assertEqual(endpoint["type"], "remotehosts")
        self.assertEqual(endpoint["remotes"][0]["engines"], [{"role": "client", "ids": [1]}])
        settings = endpoint["remotes"][0]["config"]["settings"]
        self.assertEqual(settings["userenv"], "alma9")
        self.assertFalse(settings["cpu-partitioning"])
        self.assertEqual(runfile["tool-params"], [])
        params = runfile["benchmarks"][0]["mv-params"]["sets"][0]["params"]
        values = {param["arg"]: param["vals"] for param in params}
        self.assertEqual(values["test-type"], list(COMMON.TEST_TYPES))
        self.assertEqual(values["threads"], ["1", "2"])
        self.assertEqual(values["array-mib"], ["256"])
        self.assertEqual(len(values["test-type"]) * len(values["threads"]), 8)

    def test_workshop_notice_uses_source_images_collectable_extension(self):
        workshop = json.loads((ROOT / "workshop.json").read_text(encoding="utf-8"))
        requirement = next(
            item for item in workshop["requirements"] if item["name"] == "stream_build_driver"
        )
        notice = next(
            item
            for item in requirement["files_info"]["files"]
            if "NOTICE" in item["src"]
        )
        self.assertEqual(
            notice,
            {
                "src": "%bench-dir%/third-party/NOTICE.txt",
                "dst": "/tmp/STREAM-NOTICE.txt",
            },
        )
        self.assertTrue((ROOT / "third-party/NOTICE.txt").is_file())
        builder = (ROOT / "stream-build.py").read_text(encoding="utf-8")
        self.assertIn('project_root / "third-party" / "NOTICE.txt"', builder)
        self.assertIn('installed_notice = install_dir / "NOTICE.txt"', builder)


class NativeOutputTests(unittest.TestCase):
    def test_authentic_native_fixture_contains_and_validates_all_four_kernels(self):
        parsed = COMMON.parse_native_output(NATIVE_OUTPUT, expected_array_mib=256, expected_threads=1)
        self.assertEqual(set(parsed["kernels"]), set(COMMON.KERNELS))
        self.assertEqual(parsed["native_validation"], "Solution Validates")
        self.assertEqual(parsed["ntimes"], COMMON.NTIMES)
        self.assertEqual(parsed["threads_counted_printed"], 1)
        self.assertLess(COMMON.validate_native_summary_duration(parsed, 30.0), 30.0)

    def test_rejects_failure_missing_validation_bad_dimensions_and_team_mismatch(self):
        cases = (
            (NATIVE_OUTPUT.replace("Solution Validates:", "Failed Validation:"), "failure marker"),
            (NATIVE_OUTPUT.replace("Solution Validates:", "Validation marker absent:"), "successful validation marker"),
            (NATIVE_OUTPUT.replace("Array size = 33554432", "Array size = 33554431"), "array dimensions"),
            (NATIVE_OUTPUT.replace("Number of Threads counted = 1", "Number of Threads counted = 2"), "formed"),
            (NATIVE_OUTPUT.replace("Copy:           20307.5", "Copy:           nan"), "summary row"),
        )
        for output, expected_error in cases:
            with self.subTest(expected_error=expected_error), self.assertRaisesRegex(ValueError, expected_error):
                COMMON.parse_native_output(output, expected_array_mib=256, expected_threads=1)

    def test_rejects_nonzero_exit_even_with_valid_native_text(self):
        with self.assertRaisesRegex(ValueError, "exited with status 1"):
            COMMON.parse_native_output(NATIVE_OUTPUT, 256, 1, return_code=1)

    def test_rejects_native_summary_outside_process_duration(self):
        parsed = COMMON.parse_native_output(NATIVE_OUTPUT, 256, 1)
        with self.assertRaisesRegex(ValueError, "outside the .* process envelope"):
            COMMON.validate_native_summary_duration(parsed, 0.01)


class TopologyAndMemoryTests(unittest.TestCase):
    class ExcludesSelfTopology:
        groups = ((0, 1, 2, 3), (4, 5, 6, 7))

        def get_node(self, cpu):
            return 0

        def get_thread_siblings(self, cpu):
            for group in self.groups:
                if cpu in group:
                    return [member for member in group if member != cpu]
            return []

    def test_sibling_selection_adds_cpu_excluded_by_toolbox_helper(self):
        topology = self.ExcludesSelfTopology()
        _candidates, selected = CLIENT.select_node_cpus(topology, 0, list(range(8)), 2)
        self.assertEqual(selected, [0, 4])
        _candidates, selected = CLIENT.select_node_cpus(topology, 0, [1, 2, 5, 7], 3)
        self.assertEqual(selected, [1, 2, 5])
        self.assertEqual(len(selected), len(set(selected)))

    def test_rejects_threads_above_effective_node_cpus(self):
        with self.assertRaisesRegex(ValueError, "exceeds cpu-node=0 effective CPUs"):
            CLIENT.select_node_cpus(self.ExcludesSelfTopology(), 0, [0], 2)

    def test_cgroup_preflight_walks_visible_ancestors(self):
        with tempfile.TemporaryDirectory(prefix="stream-cgroup-test-") as temporary:
            temp_root = Path(temporary)
            cgroup_root = temp_root / "cgroup"
            leaf = cgroup_root / "group" / "limited" / "leaf"
            middle = cgroup_root / "group" / "limited"
            ancestor = cgroup_root / "group"
            leaf.mkdir(parents=True)
            (middle / "memory.max").write_text("max\n", encoding="ascii")
            (middle / "memory.current").write_text("900\n", encoding="ascii")
            (middle / "memory.high").write_text("1000\n", encoding="ascii")
            (ancestor / "memory.max").write_text("5000\n", encoding="ascii")
            (ancestor / "memory.current").write_text("1000\n", encoding="ascii")
            (ancestor / "memory.high").write_text("max\n", encoding="ascii")
            (leaf / "memory.max").write_text("max\n", encoding="ascii")
            (leaf / "memory.current").write_text("50\n", encoding="ascii")
            (leaf / "memory.high").write_text("max\n", encoding="ascii")
            proc_cgroup = temp_root / "proc-cgroup"
            proc_cgroup.write_text("0::group/limited/leaf\n", encoding="ascii")
            real_path = CLIENT.Path

            def routed_path(value):
                if str(value) == "/proc/self/cgroup":
                    return proc_cgroup
                if str(value) == "/sys/fs/cgroup":
                    return cgroup_root
                return real_path(value)

            with patch.object(CLIENT, "Path", side_effect=routed_path):
                observations = CLIENT.cgroup_memory_observation()

        available = {Path(item["path"]).name: item["available_bytes_estimate"] for item in observations}
        self.assertEqual(available["limited"], 100)
        self.assertEqual(available["group"], 4000)

    def test_unbound_memory_preflight_checks_restricted_allowed_nodes(self):
        with tempfile.TemporaryDirectory(prefix="stream-memory-nodes-test-") as temporary:
            temp_root = Path(temporary)
            node_root = temp_root / "sys-node"
            for node_id, free_kib, total_kib in ((0, 2048, 8192), (1, 1048576, 2097152)):
                node_path = node_root / ("node%d" % node_id)
                node_path.mkdir(parents=True)
                (node_path / "meminfo").write_text(
                    "Node %d MemTotal: %d kB\nNode %d MemFree: %d kB\n"
                    % (node_id, total_kib, node_id, free_kib),
                    encoding="ascii",
                )
            (node_root / "has_memory").write_text("0-1\n", encoding="ascii")
            real_path = CLIENT.Path

            def routed_path(value):
                raw = str(value)
                prefix = "/sys/devices/system/node"
                if raw == prefix:
                    return node_root
                if raw.startswith(prefix + "/"):
                    return node_root / raw[len(prefix) + 1:]
                return real_path(value)

            with patch.object(CLIENT, "Path", side_effect=routed_path), patch.object(
                CLIENT, "host_memory_available_bytes", return_value=100 * 1024 * 1024
            ), patch.object(CLIENT, "cgroup_memory_observation", return_value=[]):
                with self.assertRaisesRegex(ValueError, r"allowed memory-node MemFree sum.*nodes \[0\]"):
                    # Node 7 is allowed by the process mask but has no memory.
                    # Global MemAvailable is ample; node 0 alone cannot fit
                    # three arrays, so preflight must reject the run.
                    CLIENT.memory_preflight(1, "none", [0, 7])

    def test_memory_node_discovery_fails_closed_on_incomplete_fallback(self):
        with tempfile.TemporaryDirectory(prefix="stream-memory-discovery-test-") as temporary:
            temp_root = Path(temporary)
            node_root = temp_root / "sys-node"
            node0 = node_root / "node0"
            node1 = node_root / "node1"
            node0.mkdir(parents=True)
            node1.mkdir(parents=True)
            (node0 / "meminfo").write_text(
                "Node 0 MemTotal: 8192 kB\nNode 0 MemFree: 4096 kB\n",
                encoding="ascii",
            )
            # Simulate an older kernel without has_memory and a node whose
            # memory status cannot be observed. Returning only node 0 here
            # could misclassify a restricted [0] mask as unrestricted.
            real_path = CLIENT.Path

            def routed_path(value):
                raw = str(value)
                prefix = "/sys/devices/system/node"
                if raw == prefix:
                    return node_root
                if raw.startswith(prefix + "/"):
                    return node_root / raw[len(prefix) + 1:]
                return real_path(value)

            with patch.object(CLIENT, "Path", side_effect=routed_path), patch.object(
                CLIENT, "host_memory_available_bytes", return_value=100 * 1024 * 1024
            ), patch.object(CLIENT, "cgroup_memory_observation", return_value=[]):
                with self.assertRaisesRegex(RuntimeError, "cannot determine whether sysfs memory node 1"):
                    CLIENT.memory_preflight(1, "none", [0])

    def test_launch_clears_conflicting_inherited_openmp_affinity(self):
        args = COMMON.parse_cli(["--threads", "2"])
        with patch.dict(
            os.environ,
            {
                "cpu_partitioning": "0",
                "OMP_PLACES": "{1}",
                "OMP_THREAD_LIMIT": "1",
                "GOMP_CPU_AFFINITY": "1",
                "OMP_PROC_BIND": "TRUE",
            },
            clear=False,
        ), patch.object(CLIENT, "effective_cpu_mask", return_value=([0, 1], [0, 1])), patch.object(
            CLIENT, "effective_memory_nodes", return_value=([0], "0")
        ), patch.object(CLIENT, "load_live_topology", return_value=None), patch.object(
            CLIENT, "memory_preflight", return_value={"preflight_passed": True}
        ), patch.object(CLIENT, "cache_topology_observation", return_value={"status": "unknown"}):
            command, environment, _affinity, _policy, _memory, _cache = CLIENT.prepare_launch(
                args, Path("/prepared/stream")
            )

        self.assertEqual(command, ["/prepared/stream"])
        self.assertEqual(environment["OMP_NUM_THREADS"], "2")
        self.assertEqual(environment["OMP_DYNAMIC"], "FALSE")
        for name in ("OMP_PLACES", "OMP_THREAD_LIMIT", "GOMP_CPU_AFFINITY", "OMP_PROC_BIND"):
            self.assertNotIn(name, environment)


class ProcessGroupCleanupTests(unittest.TestCase):
    @unittest.skipUnless(hasattr(os, "fork") and Path("/proc").is_dir(), "requires Linux fork and procfs")
    def test_timeout_cleanup_kills_term_ignoring_descendant(self):
        with tempfile.TemporaryDirectory(prefix="stream-process-group-test-") as temporary:
            pid_file = Path(temporary) / "descendant.pid"
            process = subprocess.Popen(
                [sys.executable, str(FIXTURES / "process_group_helper.py"), str(pid_file)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and not pid_file.exists():
                    time.sleep(0.02)
                self.assertTrue(pid_file.exists(), "helper did not start its TERM-ignoring child")
                descendant = int(pid_file.read_text(encoding="ascii"))
                descendant_group = os.getpgid(descendant)
                CLIENT.terminate_process_group(process)
                self.assertIsNotNone(process.poll())
                deadline = time.monotonic() + 2
                state = self._pid_state(descendant)
                while state is not None and state != "Z" and time.monotonic() < deadline:
                    time.sleep(0.02)
                    state = self._pid_state(descendant)
                self.assertFalse(
                    state is not None and state != "Z",
                    "owned descendant survived process-group cleanup: pid=%d pgid=%s state=%s"
                    % (descendant, descendant_group, state),
                )
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=3)

    @staticmethod
    def _pid_state(pid):
        status = Path("/proc/%d/stat" % pid)
        try:
            state = status.read_text(encoding="ascii").rsplit(")", 1)[1].split()[0]
        except FileNotFoundError:
            return None
        return state


class PostprocessTests(unittest.TestCase):
    def test_valid_manifest_rechecks_output_and_selected_score(self):
        manifest = make_valid_manifest()
        score = POST.validate_manifest_output(manifest, NATIVE_OUTPUT)
        self.assertEqual(score["metric_type"], "triad-MB-sec")
        self.assertEqual(score["value_mb_sec"], 12104.4)
        self.assertEqual((score["begin_ms"], score["end_ms"]), (1000, 31001))

    def test_rejects_raw_or_manifest_mismatch(self):
        manifest = make_valid_manifest()
        bad_output = NATIVE_OUTPUT.replace("Solution Validates:", "Failed Validation:")
        with self.assertRaisesRegex(ValueError, "failure marker"):
            POST.validate_manifest_output(manifest, bad_output)
        manifest["selected_result"]["metric_type"] = "copy-MB-sec"
        with self.assertRaisesRegex(ValueError, "does not match"):
            POST.validate_manifest_output(manifest, NATIVE_OUTPUT)

    def test_direct_postprocessor_binds_cli_and_emits_selected_kernel(self):
        manifest = make_valid_manifest()

        class FakeMetrics:
            instance = None

            def __init__(self):
                self.logged = None
                FakeMetrics.instance = self

            def log_sample(self, sample_id, descriptor, metadata, sample):
                self.logged = (sample_id, descriptor, metadata, sample)

            def finish_samples(self):
                return "metric-data-0"

        with tempfile.TemporaryDirectory(prefix="stream-postprocess-test-") as temporary:
            workdir = Path(temporary)
            (workdir / "stream-run-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (workdir / "stream-output.txt").write_text(NATIVE_OUTPUT, encoding="utf-8")
            previous = Path.cwd()
            try:
                os.chdir(workdir)
                with patch.object(POST, "load_cdm_metrics", return_value=FakeMetrics):
                    POST.main(VALID_CLI)
            finally:
                os.chdir(previous)
            output = json.loads((workdir / "postprocess/post-process-data.json").read_text(encoding="utf-8"))

        self.assertEqual(output["primary-metric"], "triad-MB-sec")
        self.assertEqual(output["primary-period"], "score-support")
        self.assertEqual(FakeMetrics.instance.logged[1]["type"], "triad-MB-sec")
        # Stock CommonDataModel metric_desc.names has a `desc` keyword field;
        # keep this integration label on that supported path.
        self.assertEqual(FakeMetrics.instance.logged[2], {"desc": "STREAM-derived"})
        self.assertEqual(FakeMetrics.instance.logged[3]["begin"], 1000)
        self.assertEqual(FakeMetrics.instance.logged[3]["end"], 31001)

    def test_mismatched_cli_removes_stale_index_before_failing(self):
        manifest = make_valid_manifest()
        with tempfile.TemporaryDirectory(prefix="stream-postprocess-mismatch-") as temporary:
            workdir = Path(temporary)
            (workdir / "postprocess").mkdir()
            result_path = workdir / "postprocess/post-process-data.json"
            result_path.write_text("stale", encoding="utf-8")
            (workdir / "stream-run-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (workdir / "stream-output.txt").write_text(NATIVE_OUTPUT, encoding="utf-8")
            previous = Path.cwd()
            try:
                os.chdir(workdir)
                mismatch_cli = list(VALID_CLI)
                mismatch_cli[1] = "copy"
                with self.assertRaisesRegex(ValueError, "do not match the submitted"):
                    POST.main(mismatch_cli)
            finally:
                os.chdir(previous)
            self.assertFalse(result_path.exists())

    def test_declared_hooks_are_executable_and_direct_runtime_hook_works(self):
        for hook in ("stream-client", "stream-get-runtime", "stream-post-process.py"):
            mode = (ROOT / hook).stat().st_mode
            self.assertTrue(mode & stat.S_IXUSR, hook)
        runtime = subprocess.run(
            [str(ROOT / "stream-get-runtime"), "--deadline", "45"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(runtime.returncode, 0, runtime.stderr)
        self.assertEqual(runtime.stdout.strip(), "45")
        with tempfile.TemporaryDirectory(prefix="stream-postprocess-help-") as temporary:
            help_result = subprocess.run(
                [str(ROOT / "stream-post-process.py"), "--help"],
                cwd=temporary,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(help_result.returncode, 0, help_result.stderr)


if __name__ == "__main__":
    unittest.main()
