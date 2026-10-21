# Crucible STREAM benchmark

This subproject integrates the unmodified McCalpin STREAM 5.10 OpenMP C
program. Workshop fetches a pinned source archive, checks the native C file's
SHA-256, and builds fixed-size executables. Its requirements are declared under
the `default` userenv entry, which Workshop uses as a fallback across
x86_64 userenvs without a more specific entry.

Initial support is ordinary STREAM with separate optional NUMA CPU and memory
placement controls. The separate AMD STREAM Dynamic workload remains deferred
until its source-only licensing scope is resolved.

The recovered `alma9` baseline passed 16 native samples across all four kernels
and one/two threads; a separate four-sample run observed explicit memory binding
to NUMA node 0 and node 1. The two-client run also passed native validation,
stock indexing, and ordinary summary checks. A separate deployed run also
passed native validation, indexing, and one-sample summary checks at 1024 and
2048 MiB per array. Its standard deviation is null because each size had one
sample. That fresh Workshop run acquired source and compiled all four prepared
variants at stage 7, while stages 1–6 were cached. The later userenv smoke
matrix also completed fully uncached seven-stage image builds for `alma10`,
`fedora43`, `rhubi10`, and `stream9`. The concurrent summary is period-weighted
and does not establish instantaneous combined bandwidth. A later one-sample
`default`-Workshop smoke matrix completed on 8 of 10 selected x86_64 userenvs;
`alma8` and `rhubi8` failed during image building because their Python 3.6
runtime rejects the STREAM build script's `subprocess.check_output(...,
text=True)` call.
This matrix checks basic build and execution compatibility, not performance
qualification. Remaining untested userenvs, other architectures, and other
topologies remain unqualified; these results do not establish STREAM Run Rule
conformance or support comparative performance claims. See
[VALIDATION.md](VALIDATION.md) for run identities and evidence limits.

## Workload and test types

Each native invocation runs Copy, Scale, Add, and Triad sequentially and
validates the complete result. `test-type` chooses which native best rate is
reported as the sample's primary metric; it does not skip the other kernels.
The supported values are `copy`, `scale`, `add`, and `triad`, with `triad` as
the default. Multiplex can expand multiple selected types into separate tests.
Each kernel has a distinct CDM metric type so samples selecting different
kernels cannot be combined as if they measured the same operation.

The indexed value is the selected native best rate in decimal MB/s. It already
reflects the native OpenMP team and is not multiplied by `threads`. The CDM
metric uses `sum` to combine client values for the same selected kernel.
Rickshaw builds the shared `score-support` period from same-named client
intervals using their common overlap (`begin=max(client begins)`,
`end=min(client ends)`). CDM queries use that stored period range. Native best
rates have no absolute per-kernel timestamps, so an arithmetic sum of client
peak scores does not establish simultaneous aggregate bandwidth; inspect the
generated period bounds and query result before making that comparison.

## Build inputs and run-time options

Workshop uses fixed `-O3 -fopenmp -mcmodel=medium -m64` flags and
`NTIMES=100`. It prepares per-array sizes of 256, 512, 1024, and 2048 MiB;
each run allocates three arrays, so total array storage is three times the
selected size. `-mcmodel=medium` is needed for the largest static arrays.
Compiler and binary provenance are recorded with each prepared variant.
There is no run-time source checkout or compile.

The runtime defaults are `test-type=triad`, `threads=1`, `array-mib=512`,
`cpu-node=none`, `memory-node=none`, and `deadline=1800` seconds. Multiplex's
defaults preset applies to an empty parameter set. The client also fills in
these defaults on partial parameter sets, because Multiplex does not merge
defaults field by field. No `essentials` preset replaces user selections.

`deadline` limits the one native process; Rickshaw applies its normal runtime
padding. Use one shared `deadline` value across concurrent clients in a run:
Rickshaw derives the global runtime estimate from client 1's parameters. The
process runs all four kernels even when one is selected for reporting. Before
launch, the client checks the requested threads against the effective CPU
affinity and requires the three arrays to fit within 90% of observed available
host/cgroup memory, leaving a 10% reserve. If cgroup v2 memory files are
visible, it includes the process cgroup and visible ancestors; otherwise the
manifest says the estimate used host `MemAvailable` only. For an explicit
memory-node binding, it checks `/proc/self/status` `Mems_allowed_list` and node
memory before launch. An explicit memory-node request requires sysfs `MemFree`
and `MemTotal` data and requires the three arrays to fit within 90% of observed
node MemFree, leaving a 10% reserve. With `memory-node=none`, if
`Mems_allowed_list` excludes any discovered memory-bearing NUMA node, the
client checks the sum of `MemFree` across the permitted memory-bearing nodes
and applies the same 90% ceiling. Permitted memoryless nodes do not count
toward that sum. These checks use instantaneous observations; they do not
reserve memory or verify page placement, and an inherited memory policy may
impose tighter limits. A failed preflight produces no score.

## CPU and memory placement

`cpu-node` and `memory-node` are separate controls. CPU placement selects
allowed CPUs on the requested NUMA node using the installed toolbox topology
helper, then applies that CPU list to the native process. `memory-node` uses
`numactl --membind` only when explicitly requested. CPU binding alone does not
bind memory. With both values set to `none`, the process inherits its effective
CPU mask and current memory policy. The native program initializes arrays in
parallel OpenMP loops, so that inherited policy governs first touch.

The adapter sets `OMP_NUM_THREADS`, disables dynamic teams, and clears inherited
OpenMP affinity controls before every run. With `cpu-node=none`, GNU OpenMP
distributes threads over the inherited process CPU mask. With explicit node
placement, the selected CPU list is applied to the native process and
`GOMP_CPU_AFFINITY`/`OMP_PROC_BIND` keep the team on that list. The manifest
records the effective process CPU mask, `Cpus_allowed_list`,
`Mems_allowed_list`, selected CPU list, requested memory policy, cgroup memory
observations, and OpenMP settings. `cpu-partitioning=1`
is rejected in the initial integration; the adapter does not change TuneD or
host-wide CPU partitioning.

## Result labels and timing

All values are labeled `STREAM-derived`. The pinned C source is unmodified,
but this integration does not claim that every published STREAM Run Rule is
met on every host. The manifest includes the native clock-calibration output
and advisory cache observations. Unknown cache topology remains unknown, and
these observations do not establish Run Rule conformance. Small local
mechanics runs are not performance results.

The native output provides relative per-kernel minimum, average, and maximum
times and best rates, but no absolute wall-clock endpoints for the timed
iterations. The client checks that the sum of the kernel average times over
the 99 timed passes fits inside the monotonic child-process duration. The
postprocessor checks epoch timestamps against that duration within 10 ms or
0.1%, whichever is greater. The CDM period is therefore named
`score-support`: its begin/end
bound the native child process, including array initialization, all four
kernels, validation, and output. Epoch timestamps are rounded outward to
milliseconds, adding at most 1 ms of endpoint quantization. This envelope is
not a native per-kernel measurement interval. The selected best native rate
remains the indexed value.

## AMD STREAM Dynamic

The separate NUMA STREAM wrapper uses AMD STREAM Dynamic, a C++ source
distributed inside a Phoronix archive with additional clickwrap terms. That
source allocates arrays at run time and receives `--stream_array_mb` in MiB
per array; it is not standard STREAM plus `numactl`. Its source-only licensing
scope remains unresolved, so AMD STREAM Dynamic is not an enabled workload in
this subproject. The proposed source-only build must remain gated until its
source acquisition and redistribution rights are confirmed. No packaged AMD
objects, libraries, scripts, or clickwrap acceptance automation are included.

## Example and local checks

[`examples/stream.remotehosts.json`](examples/stream.remotehosts.json) is a
client-only `alma9` example with CPU partitioning disabled and no tool
collection. It expands Copy, Scale, Add, and Triad across one and two threads
using the 256 MiB-per-array variant. Replace its reserved `.invalid` hostname,
example controller address, and engine-user placeholder before running it.

Run the focused regression suite with:

```bash
python3 -m unittest discover -s tests -v
```

The suite validates a retained native-output fixture and adapter behavior; it
does not execute a native STREAM workload or download source.

## Files

The benchmark adapter and Crucible integration files use Apache License 2.0
terms in `LICENSE`. The separately licensed native source has its exact
license header in `third-party/STREAM-5.10-LICENSE.txt`; both license texts and
the third-party notice are copied beside the built executables in Workshop.

- `rickshaw.json`: engine files, runtime estimate, and controller postprocessor.
- `multiplex.json`: test-type, thread, prepared array, NUMA, and deadline values.
- `workshop.json`: pinned source and Workshop build tools for `alma9`.
- `stream-build.py`: source-verifying fixed-variant builder.
- `stream-client.py` and `stream_common.py`: runtime launcher and native output validator.
- `stream-post-process.py`: raw-output/manifest revalidation and selected CDM metric.
- `DESIGN.md`: reviewed design, source provenance, and AMD license gate.
- `VALIDATION.md`: executed build, native smoke, schema, Multiplex, and review checks.
- `tests/`: focused adapter regressions and sanitized native output fixture.
- `third-party/STREAM-5.10-LICENSE.txt`: native STREAM source license terms.
- `third-party/NOTICE.txt`: build notice copied alongside the executables.
  Keep the `.txt` extension because the installed Rickshaw source-images
  collector omits Markdown files.
