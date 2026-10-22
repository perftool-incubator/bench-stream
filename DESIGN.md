# STREAM Crucible integration design

Status: implemented ordinary STREAM path; the AMD STREAM Dynamic source path
remains explicitly gated on source-license scope review. AMD STREAM Dynamic is
not treated as standard STREAM or silently replaced by standard STREAM plus
NUMA binding.

## Workload contract

The subproject is named `stream`. Its first supported native workload is
standard STREAM. The source is the
unmodified John McCalpin STREAM 5.10 OpenMP C implementation in the
`redhat-performance/streams-wrapper` snapshot at commit
`8932e7bfa6cf1c99360c10549887eeb1d457d27e`, file
`streams/stream_omp_5_10.c`, SHA-256
`8daae19bde612b31a556004d180c2a7baeb77a96015e08717cc1995b4f05c334`. The
wrapper repository's `LICENSE` explicitly excludes this native file; the
native file has its own STREAM run-rule license. The Workshop recipe fetches the
commit-pinned source archive, verifies the file digest, compiles only this C
file, and installs generated benchmark executables, build provenance, and
retained native/adapter license files. It does not install the wrapper, shell helpers,
GPL license file, or wrapper runtime.

The ordinary source runs Copy, Scale, Add, and Triad sequentially on every
process invocation. A run-time `test-type` parameter accepts exactly
`copy`, `scale`, `add`, or `triad`; the default is `triad`. This parameter
selects the one native rate that becomes the primary result and CDM metric. It
does not suppress execution of the other native kernels. Each output log and
manifest retains all four kernel rates and validation results. Multiplex
multi-values produce separate test entries, each with one selected primary
kernel. The CLI help, README, and manifest document this
execution-versus-reporting distinction.

The selected rate uses a distinct CDM type (`copy-MB-sec`, `scale-MB-sec`,
`add-MB-sec`, or `triad-MB-sec`) and decimal MB/s, as reported by native
STREAM. The postprocessor emits only the selected type for each sample and
sets `primary-metric` to that type. Distinct types avoid summing unlike
kernels if separate clients are assigned different test-type values. The
native STREAM best rate already represents the complete selected process and
its OpenMP threads; do not multiply by thread count. Across concurrent clients
the CDM type uses `sum` to combine same-kernel values. Rickshaw constructs a
shared same-name primary period from the common overlap of the client period
envelopes (`begin=max(client begins)`, `end=min(client ends)`), and CDM queries
use those stored bounds. Native best rates have no absolute per-kernel
timestamps, so do not infer simultaneous aggregate bandwidth by adding the
clients' peak scores. Inspect the generated period bounds and stock query
result for multi-client comparisons.

## Parameters and defaults

Multiplex exposes runtime controls only for values that the installed
executables support. `test-type`, `threads`, `array-mib`, `cpu-node`,
`memory-node`, and `deadline` are exposed. Workshop build requirements use the
`default` userenv entry as a fallback across x86_64 userenvs without a more
specific entry. Each target build environment still needs validation; other
architectures need a separate Workshop build and validation. Standard STREAM
array size and `NTIMES` are build-time dimensions; the adapter selects exact
installed manifest variants and cannot compile or resize a native executable
at run time. Prepared sizes are 256, 512, 1024, and 2048
MiB per array, with fixed `NTIMES=100`. Workshop uses the reviewed flags
`-O3 -fopenmp -mcmodel=medium -m64`, without `-march=native`; it
records compiler version, all flags, source digest, array size, pass count,
architecture, and binary digest. The large memory model is required for the
largest static arrays. Pass counts are not a runtime parameter, which avoids
overflow and validity risks from unbounded native repetitions. Runtime
defaults are `test-type=triad`, `threads=1`, `array-mib=512`, both
placement parameters `none`, and a bounded `deadline=1800` seconds. Array
memory is three times `array-mib`; the client requires it to fit within 90% of
the conservative host/cgroup available estimate. An explicit memory-node also
requires it to fit within 90% of the node's observed MemFree. With
`memory-node=none`, a restricted `Mems_allowed_list` is intersected with the
discovered memory-bearing NUMA nodes; the three arrays must fit within 90% of
their combined observed `MemFree`. Memoryless allowed nodes do not contribute
to that sum. These are instantaneous preflight snapshots, not reservations or
proof of later page placement; inherited memory policies may be more
restrictive. CPU count is
checked against the process's effective affinity, not the host's online CPU
count.

The same values must appear in the Multiplex `defaults` preset and in the
adapter's own CLI fallbacks. The installed Multiplex implementation applies
`defaults` only when a whole parameter set is empty; a partially specified
set receives no per-field defaults. Its `essentials` preset always overrides
matching explicit non-repeatable values. Therefore runtime code must fill
omitted parameters on partial requests, and no `essentials` preset will own a
user-selectable benchmark parameter. This matches the observed fio convention
where the default preset supplies empty-run parameters and the native launch
path supplies its own behavior for partial requests.

Rickshaw's installed runtime estimate uses client 1's parameters. Concurrent
clients therefore need the same `deadline` value; a longer deadline on another
client could exceed the global estimate.

`threads` is an OpenMP runtime value. `array-mib` must match a prepared
variant exactly. Optimization and `NTIMES` are fixed Workshop inputs;
`deadline` is only a watchdog and does not alter native work. No outer `iterations`
parameter is included because Crucible samples already repeat the workload,
and it could be confused with native `NTIMES` or wrapper-level repetitions.

All results are labeled `STREAM-derived` until every applicable STREAM
Run Rule has been established. The manifest preserves the native timing
calibration and advisory cache/topology observations. Unknown cache topology
remains unknown, and passing these partial checks does not assert full
Run Rule conformance. Small local mechanics smokes are not performance
results.

## CPU and NUMA placement

`cpu-node` and `memory-node` are separate optional parameters, each defaulting
to `none`. CPU placement selects the allowed CPUs associated with the chosen
NUMA node and checks the requested thread count against the intersection with
the process's effective affinity. Memory placement uses a separate explicit
memory-node request and checks `/proc/self/status` `Mems_allowed_list` before
launch. No NUMA memory binding is implied by CPU placement. With both options
at `none`, the process inherits its current CPU mask and current memory
policy. The standard source initializes arrays in OpenMP loops, so the
inherited memory policy governs its parallel first touch;
the manifest records effective CPUs, allowed memory nodes, requested CPU and
memory nodes, and the actual launch policy.

The runtime uses the installed toolbox CPU topology helper for live
CPU-to-node discovery. Its host-wide `get_online_cpus()` result is not an
effective-process CPU mask, so the adapter will intersect topology with
`os.sched_getaffinity(0)` and validate requested counts against that result.
Memory nodes are likewise intersected with `Mems_allowed_list`. These options
do not partition CPUs, change TuneD, or imply uperf's `cpu-pin=numa` behavior;
uperf's mode follows NIC locality to select CPUs and does not bind memory.
The initial implementation rejects `cpu_partitioning=1`; it does not attempt
to infer a workload mask from housekeeping-only engine affinity.

## Timing and result interval

The native program reports each kernel's minimum, average, and maximum time,
then reports the best MB/s rate. The client checks that the sum of the kernel
average times over 99 timed passes fits the monotonic process duration, and
the postprocessor checks the epoch timestamps against that duration. The
source does not provide absolute wall-clock
endpoints for the individual timed kernel iterations. The adapter retains
the child process launch and completion timestamps and wall-clock duration.
The CDM interval is described as a **score-support envelope**, not a
native kernel measurement interval: it includes allocation/initialization,
all four sequential kernels, native validation, and output. The selected
kernel's best native rate remains the score; other kernels are still run and
validated even when not selected. Neither min/average/max data nor the native
total duration is used to fabricate per-kernel wall-clock endpoints. The CDM
endpoints round outwards to whole milliseconds, with at most 1 ms endpoint
quantization.

## AMD STREAM Dynamic source gate

The NUMA STREAM wrapper uses a materially different native workload: the AMD
STREAM Dynamic C++ source acquired through Phoronix Test Suite, with runtime
array allocation and a `--stream_array_mb` value meaning MiB per array. The
original wrapper calls that source with `--threads`, `--stream_array_mb`, and
`--ntimes 100`; it does not call the build helper's jemalloc option. Its
parallel initialization is first-touch; it does not request memory binding.

The pinned Phoronix archive is
`amd-stream-dynamic-1.tar.xz`, SHA-256
`206d9c315d1d220e7ed56ee332f1bbb4454cd18a390fa810b94c0d3ea4c2d20e`.
The archive's `stream.cpp` SHA-256 is
`1508d85aca88fac7672dbe4af26b2848c56d3e15b64da8845226f29fe1d84c55`.
The archive contains a clickwrap license with internal-use and conditional
distribution restrictions, along with helper scripts, shared libraries,
and an object binary. The extracted `stream.cpp` retains McCalpin's STREAM
license header, but it is a converted and modified C++ derivative distributed
inside the AMD-licensed archive. The scope of the AMD terms over this source
alone is unresolved. No clickwrap terms will be accepted automatically. The
optional source-only build proposal is to acquire only the reviewed
`stream.cpp`, verify its exact digest, compile it without AMD scripts,
prebuilt objects, packaged libraries, or jemalloc, and label its runtime as
`amd-stream-dynamic`. Before that path is enabled in Workshop or published,
the source-only acquisition and redistribution rights must be confirmed. Until
then, users must not see it as an enabled source-selection runtime value. The
current adapter will identify its supported native source as standard STREAM
in its README and manifest; it will not advertise a selectable AMD workload.

## Evidence and review notes

- Ordinary wrapper HEAD is `8932e7bfa6cf1c99360c10549887eeb1d457d27e`;
  NUMA wrapper HEAD is `5ed897be86ba76ac6b6f89f235f3a178065313ae`.
- The ordinary wrapper builds optimization/array-size variants and passes
  `--ntimes 100` by default; its outer run repetition is a separate parameter.
- A review-only `gcc -O3 -fopenmp -DSTREAM_ARRAY_SIZE=1048576` versus
  `67108864` compile showed both executables are 22,160 bytes; large arrays
  occupy NOBITS BSS, so prepared compile-time sizes do not proportionally
  inflate image files.
- The official Phoronix archive was inspected under `/tmp`; no AMD native
  workload was executed. Build, memory, and first-touch claims above come from
  source inspection only.
- The installed Multiplex code and fio configuration were inspected. Empty,
  partial, explicit, sweep, and invalid-value cases were exercised against the
  installed Multiplex CLI with this subproject's final `multiplex.json`.
