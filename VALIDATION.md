# Local validation record

Validation included the local x86_64 development host and a separate build in
the cached Crucible `alma9` userenv. The local compiler was GCC 15.2.1 (Red Hat
15.2.1-7); parent-run target-userenv build evidence is described below.

## Initial default Workshop matrix before Python 3.6 fixes

After moving the benchmark requirements to Workshop's `default` entry, ran one
256 MiB Triad sample for each of the 10 upstream x86_64 userenv files not
excluded by `userenvs/ci-excludes.txt`. The sequential runs used the `remotehosts`
endpoint, one client, one thread, and no tools. Symlink aliases were omitted;
their target files (`alma10`, `fedora44`, `rhubi10`, and `stream10`) were tested.

Eight userenvs built and completed a sample:

| Userenv | Run ID | Triad (MB/s) |
|---|---|---:|
| `alma10` | `5492b043-c3a4-46dd-a894-144ab71fe7d2` | 9284.2 |
| `alma9` | `d3ed9a4f-bc04-4ed6-8f82-fde60e9521a0` | 12755.9 |
| `fedora43` | `27371861-9a0b-483e-aee9-e56cc47f71e8` | 9352.0 |
| `fedora44` | `f4ea73c7-2acd-411b-9b13-0ce8335e44cb` | 9292.1 |
| `rhubi10` | `b54de3e6-f498-4973-9f51-4e9f5cdbe063` | 13282.8 |
| `rhubi9` | `3874fdd4-270b-40aa-8771-7d67fc82855d` | 9229.9 |
| `stream10` | `224b83d1-89ea-4dde-bbd0-65b46ec7a9d1` | 13285.0 |
| `stream9` | `c575b2fa-b9cb-40f5-b3d0-1597ece9f170` | 11907.2 |

`alma8` and `rhubi8` failed during the Workshop image build, before remote
deployment. Both failures reached `stream-build.py` under Python 3.6 and raised
`TypeError: __init__() got an unexpected keyword argument 'text'` from
the script's `subprocess.check_output(..., text=True)` call, which forwards the
keyword through `subprocess.run` to Python 3.6's `Popen`. This identifies a
Python-version incompatibility in the STREAM build script for those userenvs;
it is not a benchmark execution failure. The smoke samples establish basic
build and run compatibility for the eight passing userenvs only. They are not
performance qualification.

## Post-compatibility default Workshop matrix

After replacing the Python APIs unavailable in 3.6, reran one 256 MiB Triad
sample for each of the same 10 selected x86_64 userenvs. Runs used the
`remotehosts` endpoint, one client, one thread, and no tools. The eight
userenvs that passed previously passed again. `alma8` and `rhubi8` completed
Workshop image sourcing and endpoint deployment, but timed out at the
`engine-init-begin` Roadblock before the benchmark client started (exit code 3).
Neither produced a sample, so this matrix does not verify the client clock and
file-removal fallbacks on those two Python 3.6 environments.

| Userenv | Result | Run ID | Triad (MB/s) |
|---|---|---|---:|
| `alma10` | Pass | `7367d527-41c2-40c6-8949-850b51e178de` | 12941.2 |
| `alma8` | Engine-init timeout before client | — | — |
| `alma9` | Pass | `18800144-357d-4bea-83a2-5ae704362294` | 12767.2 |
| `fedora43` | Pass | `23bf182a-cbef-4d4f-a979-5ffe2b21061a` | 12780.0 |
| `fedora44` | Pass | `35b57425-57fb-4885-a80b-7675e582ad1a` | 12783.5 |
| `rhubi10` | Pass | `2a75fda0-53b6-4e32-88e1-a90fc0c12e79` | 12821.5 |
| `rhubi8` | Engine-init timeout before client | — | — |
| `rhubi9` | Pass | `68cfb653-3de4-4b03-b711-8dbb6701bed9` | 12939.1 |
| `stream10` | Pass | `1b96ab8d-eb6e-409c-9480-f09cd9f5f680` | 13306.0 |
| `stream9` | Pass | `8398cc19-5df6-48e8-929f-b6c8e849b0d2` | 12973.1 |

The builder is now compatible with Python 3.6, and the unit suite exercises
both native and fallback clock paths plus missing-file removal behavior. A
successful runtime sample on Alma8 and RHUI8 remains unverified because engine
initialization timed out before either client launched.

Local post-compatibility checks passed: all 31 tests from
`python3 -m unittest discover -s tests -v`, Python compilation for the adapter
scripts and tests, `bash -n stream-client`, and direct installed-schema checks
for the example run file, benchmark, Workshop, Multiplex, and metadata JSON.
The Crucible CLI could not initialize because its startup encountered
read-only filesystem/Podman setup errors and an invalid root registries config;
the applicable schemas were checked directly instead.

## Build and native adapter

- Built all four production variants from the pinned, unmodified STREAM 5.10 C
  file: 256, 512, 1024, and 2048 MiB per array; `NTIMES=100`; flags
  `-O3 -fopenmp -mcmodel=medium -m64`. All linked successfully. The standalone
  2048 MiB variant has 6 GiB total array BSS and was not executed in this local
  build; a deployed large-array run described below later executed both the
  1024 and 2048 MiB variants once.
- The parent also ran the production builder in a cached Crucible `alma9`
  container (image ID `5e16fd3144d5`) with network disabled and the source
  mounted read-only. All four variants built successfully using GCC 11.5.0
  (Red Hat 11.5.0-14) and `/usr/bin/python3`. Temporary container build
  artifacts were removed; the build log remains in the local validation
  checkout at `local-tests/stream/alma9-cached-toolchain-build.log`. This
  verifies the pinned builder and toolchain in the target userenv; it does not
  verify full Workshop dependency acquisition or an Alma9 native workload.
- A deployed Crucible Workshop trial acquired source and built all four
  variants at stage 7. Stages 1–6 were cached, so this verifies the fresh
  source-acquisition/package/build stage and deployed workload, not a complete
  all-stages-from-empty-cache installation. The trial is identified below by
  its baseline run ID.
- Built a separate 16 MiB-per-array variant for inexpensive placement and
  postprocessor mechanics checks. Its results are smoke evidence, not
  performance data.
- Ran all four `test-type` values at one and two threads against the 256 MiB
  prepared variant: eight native adapter invocations passed, each reporting
  that all four native kernels validated. This confirms selector parsing,
  prepared-size selection, native validation, and selected-rate handling; it
  is not a performance comparison.
- Ran a two-thread, 16 MiB-per-array CPU-node smoke on NUMA node 0. It selected
  CPUs 0 and 1 and validated all four native kernels.
- Ran a two-thread, 16 MiB-per-array explicit memory-node smoke on node 0. The
  manifest records `numactl --membind=0`; all four kernels validated. The
  postprocessor emitted the selected rate as `triad-MB-sec`, with a
  `score-support` period whose launch endpoint floors and completion endpoint
  ceils to milliseconds.
- Independently checked memory placement rejection for an unavailable or
  disallowed node, and CPU placement rejection for an unavailable node,
  unsupported partitioning, invalid thread count, and oversubscription. Those
  cases produced invalid manifests and no native score.

## Crucible integration checks

- `python3 -m unittest discover -s tests -v` passed 23 focused regression
  tests. They cover Multiplex/runtime defaults and bounds, authentic native
  output validation, selected-kernel and manifest/CLI binding, outward
  millisecond timing, topology sibling selection, restricted allowed-node
  memory preflight and fail-closed incomplete topology discovery, cgroup
  ancestor limits, Workshop notice packaging, OpenMP environment cleanup,
  executable hooks, stale-index removal, and process-group cleanup. The
  restricted-memory case is synthetic: host `MemAvailable` is ample while the
  only permitted memory-bearing node lacks enough `MemFree`; a permitted
  memoryless node is ignored. These snapshot checks do not prove OOM safety or
  account for every inherited memory policy. The suite does not execute a
  native STREAM binary.
- After adding the regression that checks the emitted stock-supported metric
  name, the 23-test suite passed again. This run includes the current
  postprocessor change and did not launch native STREAM workloads.
- The parent reran these 22 tests against staged source tree
  `38286003d183c5ddfb405efe5d12cee360f16c17` in the same cached Alma9 image,
  using `/usr/bin/python3` with network disabled and a read-only checkout.
  All passed in 5.237 seconds. This checks the target Python runtime without
  launching a native benchmark or changing the installed controller.
- The parent also reran the current 23-test suite against staged source tree
  `cbc0d0a5e1ca1357da19b2d972e8f033542d83bd`; all passed in 5.237 seconds.
  The captured output is `local-tests/stream/final-unit-check.log`.
- The example in `examples/stream.remotehosts.json` passed the installed
  Rickshaw run-file and remotehosts endpoint schema validator. It uses reserved
  placeholder host/user values, client-only `alma9`, `cpu-partitioning=false`,
  an empty `tool-params` list, and a four-type by two-thread sweep.
- The first full Crucible trial stopped in Workshop before compiler execution:
  the installed Rickshaw source-images client filtered `.md` files and omitted
  `third-party/NOTICE.md`, so the copy step failed. The notice was renamed to
  `NOTICE.txt`, which the collector includes. The fresh-stage retry then
  acquired source, completed stage 7, built all four variants, and ran the
  baseline described below.
- Directly exercised the installed Rickshaw source-images collector against
  the updated benchmark tree. Its `_collect_directory()` included
  `third-party/NOTICE.txt` and excluded Markdown files; no image build was
  performed by this check.
- Python compilation and shell syntax checks passed. The Rickshaw, Workshop,
  Multiplex, and benchmark-metadata JSON documents validated against the
  installed schemas.
- Exercised the installed Multiplex CLI with an empty parameter set, a partial
  set, an explicit Copy request, a four-type sweep, explicit CPU/memory/deadline
  values, and invalid test-type/array/deadline values. Empty requests received
  the documented preset; omitted values in partial requests were left for the
  adapter defaults; explicit values and sweep order were preserved; invalid
  values were rejected.
- Ran the postprocessor against retained native output. It revalidated source
  and build provenance, requested CLI arguments, native output and validation,
  selected score, and process timing before producing the metric. Independent
  review also confirmed mismatched/omitted arguments and mutations to retained
  raw output or manifest do not produce a result index.
- Exercised the corrected postprocessor with the installed toolbox
  `CDMMetrics` writer and the authentic native-output fixture. Its generated
  compressed metric descriptor used `names.desc = STREAM-derived` for
  `triad-MB-sec`; no CommonDataModel code or mappings were changed.
- Reviewed timeout cleanup with an owned process-group fixture: TERM-ignoring
  descendants were removed after escalation. An injected signal during final
  publication left the manifest invalid.
- Independent review covered the Crucible schemas, runtime hooks, Multiplex
  defaults, OpenMP placement controls, topology selection, postprocessing, and
  result period/aggregation semantics.

## Deployed baseline, NUMA, concurrent, and large-array runs

Across the baseline, NUMA, concurrent, and large-array Crucible runs, all 26
deployed native client invocations passed native validation. Native validation,
indexing, and summary checks are reported separately below.

- **Baseline run `5e1e4ab3-8130-4ad7-ae04-54883115b60c`:** the native adapter
  and Workshop source image used staged tree
  `6bf5e359bc22b121a8754b29768e05ba057404ea`. Sixteen native samples passed
  across eight parameter groups: Copy, Scale, Add, and Triad at one and two
  threads, with two samples per group and 256 MiB per array. The first indexing
  attempt failed before writing metric documents because the postprocessor
  emitted unsupported `metric_desc.names.result-variant`. Postprocessor tree
  `b46e6dd28bb073229dc3d20505c2a293f16ebc98` changed that field to the stock
  supported `metric_desc.names.desc = STREAM-derived`; the retained native
  samples were reprocessed, with no native rerun and no CommonDataModel edits.
  All 16 native scores, default API metric records, indexed values and bounds,
  and ordinary repeated-sample summary statistics matched. The summary had
  eight complete groups with no partial samples or dropped engines.
- **NUMA run `e79498d9-a973-48b9-8920-0e8ccdaf8a0f` (adapter tree
  `b46e6dd28bb073229dc3d20505c2a293f16ebc98`):** four native samples passed
  at two threads and 512 MiB per array, with CPU node 0 and memory node
  0 or 1 (two samples per memory node). Native validation, default API records,
  indexed values and bounds, and the two ordinary repeated-sample summaries
  matched; there were no partial samples or dropped engines. Independent live
  placement review observed worker threads pinned to CPUs 0 and 1, the
  requested `bind:0` or `bind:1` memory policy, and all 393,216 observed 4 KiB
  pages (1.5 GiB across three arrays) on the requested memory node in one full
  placement snapshot per sample. These are point-in-time observations for
  these four samples, not continuous residency evidence or coverage of other
  topologies.
- **Concurrent run `197a3ac9-edf7-49e7-b8e4-74b3c9b20360` (adapter tree
  `b46e6dd28bb073229dc3d20505c2a293f16ebc98`):** four native client
  invocations passed across two clients and two repeated samples. Default API
  queries and ordinary summary statistics matched the stock aggregation for
  the actual shared-period intersections. The inclusive query-window lengths
  (`end-begin+1`) were 6,538 and 6,501 ms; the indexed weighted sums were
  82,772.58798 and 82,993.73173
  MB/s, with mean 82,883.15986 MB/s and sample standard deviation 156.37225
  MB/s. The arithmetic sums of the clients' native best rates differ from
  these time-weighted values. This verifies the stock query and summary
  behavior; it does not demonstrate simultaneous kernel bandwidth.
- **Large-array run `81bd615e-7cd2-4b05-8e7e-2075c58d3ee0`:** one deployed
  Triad sample at two threads passed native validation for each of 1024 and
  2048 MiB per array. The staged source tree was
  `cbc0d0a5e1ca1357da19b2d972e8f033542d83bd`, with production runtime and test
  files byte-identical to tree
  `b46e6dd28bb073229dc3d20505c2a293f16ebc98`. Default API records, indexed
  values and outward bounds, and ordinary summary statistics matched for both
  iterations; there were no partial results or dropped engines. The indexed
  Triad rates were 75,733.2 and 77,549.8 MB/s. Each iteration contains one
  sample, so mean/min/max equal that sample and the standard deviation is
  `null`. This verifies the large-array result path, not repeated-sample
  variability or comparative performance.
- The completed baseline, NUMA, concurrent, and large-array rates are
  mechanics evidence; they do not establish a cold-cache performance baseline
  or comparative performance. The concurrent aggregate is the stock
  period-weighted sum over shared intervals, not instantaneous combined
  kernel bandwidth.

Machine-readable review records are in the coordinating checkout's
`local-tests/stream/`: `deployed-validation-record.json`,
`baseline-indexed-check.json`, `numa-indexed-check.json`,
`numa-placement-check.json`, `concurrent-indexed-check.json`, and
`large-arrays-indexed-check.json`.

## Limits of this validation

- This host exposes cgroup v2 memory files but no finite `memory.max` or
  `memory.high`; the memory preflight therefore used host `MemAvailable` after
  walking the visible cgroup ancestors. A finite ancestor cgroup limit was not
  available to exercise locally. The live host also does not provide a
  restricted `Mems_allowed_list` case, so that guard was exercised by the
  synthetic regression fixture only.
- No full Crucible abort or interruption trial was run. Timeout, process-group
  cleanup, and signal-during-publication behavior were checked with focused
  local adapter fixtures only; those checks do not establish full harness
  abort acceptance.
- The deployed performance baseline and explicit-NUMA run exercised Crucible
  `alma9` on x86_64 and one host's topology. The post-compatibility smoke matrix
  completed one sample on all eight previously passing userenvs, but does not
  qualify them for performance claims. `alma8` and `rhubi8` now pass Workshop
  image building and endpoint deployment, then time out at engine initialization
  before the benchmark client starts. Fully uncached seven-stage Workshop image
  builds completed for `alma10`, `fedora43`, `rhubi10`, and `stream9`; no
  cold-cache or isolated base performance run has been made. Other userenvs,
  architectures, and topologies remain unqualified.
- The large-array run has one sample per size, so it does not establish
  repeated-sample behavior for 1024 or 2048 MiB arrays. Native best-rate scores
  have no absolute per-kernel timestamps.
- The installed Rickshaw revision `a01579b12f72678d69074a13189cecb3aa83eefb`
  forms same-name primary-period bounds using the client intersection
  (`begin=max(begins)`, `end=min(ends)`). Installed CommonDataModel revision
  `e592f6400f22a8fd329f94df340b56f0da3a1249` queries those stored bounds and
  computes the selected sum aggregation as a duration-weighted sum of client
  series normalized by the inclusive query-window length `end-begin+1`.
  Fully contained metric documents use their recorded `end-begin+1` duration,
  while partial documents use clipped endpoint differences without adding
  one. A synthetic call to the installed `calcAvg()` returned `29.62962963`
  for values 10 on `[100,200]` and 20 on `[120,220]` over the common
  intersection, `25.04132231` over their union, and `30` for identical
  `[100,200]` intervals. This documents the installed query's endpoint
  behavior only; it is not evidence for a live multi-client workload.
- AMD STREAM Dynamic is intentionally not built or run. Its source-only
  licensing scope remains unresolved, as recorded in `DESIGN.md`.
- All observed native results are labeled `STREAM-derived`; full STREAM Run
  Rule conformance and comparative performance are not asserted. The prior
  first-trial Workshop notice-copy failure was fixed by packaging the notice
  as `NOTICE.txt`; the recovered baseline evidence above comes from the
  successful retry, not the aborted first attempt.
