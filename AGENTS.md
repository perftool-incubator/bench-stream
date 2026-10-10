# STREAM benchmark contribution guide

## Required reading and review

Before changing this subproject, read the current Crucible documentation from
the enclosing Crucible checkout:

- `docs/benchmark-integration-lessons.md`
- `docs/implementing-a-new-benchmark.md`

If those files are unavailable locally, use the current integration branch
until the documentation is merged:

- <https://github.com/perftool-incubator/crucible/blob/feat/coremark-benchmark/docs/benchmark-integration-lessons.md>
- <https://github.com/perftool-incubator/crucible/blob/feat/coremark-benchmark/docs/implementing-a-new-benchmark.md>

Implementation work uses `gpt-6-luna` with extra-high reasoning; independent
review uses `gpt-6.1-sol`. Send an evidence-based design for review before
substantial implementation, then send the exact staged tree, commands, raw
evidence, and unrun cases for review. Iterate on findings before handoff.
Contribute verified integration lessons through the coordinating parent so
the shared lessons document does not receive conflicting edits.

## Workload contract

This repository is the Crucible subproject `stream`. Preserve the material
difference between the two upstream workloads:

- The currently supported native workload is the unmodified McCalpin STREAM
  5.10 OpenMP C source at
  `redhat-performance/streams-wrapper` commit
  `8932e7bfa6cf1c99360c10549887eeb1d457d27e`, file
  `streams/stream_omp_5_10.c`, SHA-256
  `8daae19bde612b31a556004d180c2a7baeb77a96015e08717cc1995b4f05c334`.
- The NUMA STREAM wrapper uses AMD STREAM Dynamic, a separate C++ workload
  distributed in a Phoronix archive. It is not standard STREAM plus `numactl`.
  Its source-only licensing scope is unresolved; keep it disabled unless that
  gate is cleared. Do not accept clickwrap terms, publish archive contents, or
  claim AMD workload support without explicit review.

The user-facing `test-type` runtime parameter accepts `copy`, `scale`, `add`,
and `triad`, with `triad` as the default. The unmodified standard native
program runs and validates all four kernels sequentially on every invocation;
`test-type` selects the reported primary metric and does not suppress other
kernels. Preserve all raw native output and validation results.

Keep Workshop build inputs separate from runtime parameters. The current
Workshop target is Crucible `alma9` on x86_64. It builds the four prepared
standard variants at 256, 512, 1024, and 2048 MiB per array with fixed
`NTIMES=100` and `-O3 -fopenmp -mcmodel=medium -m64`. Record source, compiler,
flags, binary and license provenance. Build and install dependencies and
executables during Workshop preparation; never download or compile at
benchmark runtime. Compile and validate every declared prepared size on its
target build environment.

Keep CPU and memory placement as separate optional runtime controls. CPU-node
selection must use live topology intersected with the process's effective CPU
affinity. Memory-node placement must check `Mems_allowed_list` and apply an
explicit memory policy; CPU placement alone does not bind memory. Toolbox
topology describes host hardware, not the process's allowed mask. Uperf's
`cpu-pin=numa` follows NIC locality to select CPUs and does not bind memory.
Do not change TuneD, reboot hosts, force CPU partitioning, or infer that a
NUMA node is available without checking effective permissions.

## Interfaces and validation gates

Inspect the current schemas, role defaults, and actual interfaces before
changing code. Use a client-only role where the native workload requires no
server; verify that from the launch path instead of adding placeholder roles.
Keep user parameters out of `essentials` if that preset would override explicit
selections. Multiplex `defaults` applies only to a wholly empty parameter set,
not field by field, so adapter fallbacks must cover partial requests.

Before handoff, complete and report the relevant focused validation:

1. Run `python3 -m unittest discover -s tests -v`; keep the suite focused on
   parser, manifest, topology, memory-preflight, process-cleanup, and direct
   hook regressions. Do not launch full-size native workloads from the unit
   suite.
2. Validate the example run file and Rickshaw, Workshop, Multiplex, and
   benchmark metadata against the installed schemas. Compile Python and shell
   entry points, check executable modes, and run `git diff --check`.
3. Exercise the installed Multiplex CLI with empty, partial, explicit,
   multi-value sweep, and invalid requests. Verify expanded values and that
   explicit selections survive.
4. Build every prepared native variant from the pinned source in Workshop or
   its target-equivalent environment. Preserve compiler and binary
   provenance, licenses, and source hashes.
5. For native validation, retain output, effective parameters, process status,
   timing, and build provenance together. Reject nonzero exits, failure or
   missing validation markers, wrong dimensions/thread counts, non-finite
   values, and manifest/output disagreement. A valid performance metric must
   not survive a failed or interrupted run.
6. Exercise the declared Rickshaw runtime and postprocess entry points
   directly. Verify default and partial arguments, selected-kernel metric
   type, timing bounds, stale-result removal on failure, timeout cleanup, and
   failure handling. Test CPU and memory-node placement independently.
7. Check the native score units and CDM contract. The selected score is
   decimal MB/s and already includes its OpenMP team; never multiply it by
   thread count. Use distinct metric types for Copy, Scale, Add, and Triad.
   The child-process `score-support` period bounds the run but is not an
   absolute native kernel measurement interval. When converting nanosecond
   endpoints to milliseconds, round the begin down and end up. Default
   aggregation sums same-kernel client values. Rickshaw forms shared
   same-name periods from the clients' common interval (`begin=max(begins)`,
   `end=min(ends)`). Inspect the generated period range and stock CDM query
   before interpreting a multi-client total; native best scores have no
   absolute per-kernel timestamps and do not establish simultaneous
   aggregate bandwidth.
8. State which userenvs, native workloads, variants, samples, and CDM queries
   were actually exercised. A local build does not establish a Workshop build
   or deployed Crucible run; one sample does not establish repeated-sample
   aggregation behavior. Keep incomplete support limits visible.

The user has authorized focused local build, native, and adapter verification
for this benchmark. Coordinate remote workloads, root installation changes,
shared framework changes, commits, pushes, publication, and catalog edits with
the parent. Do not modify source snapshots or unrelated benchmark projects.
Keep private infrastructure details, credentials, and host-specific values
out of publishable files. For SSH work, including QUADS, reuse the user's existing identity and
public key at `~/.ssh/id_ed25519` and `~/.ssh/id_ed25519.pub`. If either file
is missing, report that; do not generate a replacement or service-specific
identity.
