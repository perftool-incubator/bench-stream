#!/usr/bin/env python3
# -*- mode: python; indent-tabs-mode: nil; python-indent: 4 -*-
# vim: autoindent tabstop=4 shiftwidth=4 expandtab softtabstop=4 filetype=python

"""Build reviewed, compile-time STREAM array-size variants in Workshop."""

import argparse
import datetime
import json
import platform
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from stream_common import (
    ARRAY_MIB_VARIANTS,
    COMPILER_FLAGS,
    INSTALL_DIR,
    NTIMES,
    SOURCE_RELATIVE_PATH,
    SOURCE_SHA256,
    UPSTREAM_COMMIT,
    UPSTREAM_REPOSITORY,
    sha256_file,
)


COMPILER = "gcc"
def run(command):
    print("$ " + shlex.join([str(part) for part in command]), flush=True)
    subprocess.run(command, check=True)


def variant_name(array_mib):
    return "stream-array-%dm-ntimes-%d-o3" % (array_mib, NTIMES)


def parse_args(argv):
    parser = argparse.ArgumentParser(description="Build prepared STREAM variants")
    parser.add_argument("--source-file", default=str(Path(SOURCE_RELATIVE_PATH)))
    parser.add_argument("--install-dir", default=str(INSTALL_DIR))
    parser.add_argument("--license-file")
    parser.add_argument("--notice-file")
    parser.add_argument("--adapter-license-file")
    parser.add_argument(
        "--array-mib",
        type=int,
        action="append",
        help="prepared MiB per array (repeat for variants; defaults to the reviewed Workshop set)",
    )
    args = parser.parse_args(argv)
    args.array_mibs = tuple(args.array_mib or ARRAY_MIB_VARIANTS)
    if not args.array_mibs or len(set(args.array_mibs)) != len(args.array_mibs):
        raise ValueError("array-mib variants must be a nonempty unique list")
    if any(mib <= 0 or mib > 8192 for mib in args.array_mibs):
        raise ValueError("array-mib variants must be between 1 and 8192 MiB")
    return args


def main(argv):
    args = parse_args(argv)
    if platform.machine() != "x86_64":
        raise RuntimeError("the initial Workshop build supports x86_64 only")

    source = Path(args.source_file)
    if not source.is_file():
        raise RuntimeError("pinned STREAM source file is missing: %s" % source)
    actual_source_sha = sha256_file(source)
    if actual_source_sha != SOURCE_SHA256:
        raise RuntimeError(
            "pinned STREAM source SHA-256 mismatch: expected %s, found %s"
            % (SOURCE_SHA256, actual_source_sha)
        )
    source_header = source.read_text(encoding="utf-8").split("# include", 1)[0]
    project_root = Path(__file__).resolve().parent
    license_file = Path(
        args.license_file or project_root / "third-party" / "STREAM-5.10-LICENSE.txt"
    )
    notice_file = Path(args.notice_file or project_root / "third-party" / "NOTICE.txt")
    adapter_license_file = Path(args.adapter_license_file or project_root / "LICENSE")
    if not license_file.is_file() or not notice_file.is_file() or not adapter_license_file.is_file():
        raise RuntimeError("the adapter license, STREAM native license, and third-party notice are required build inputs")
    if not license_file.read_text(encoding="utf-8").startswith(source_header):
        raise RuntimeError("the retained STREAM license text does not match the pinned source header")
    if shutil.which(COMPILER) is None:
        raise RuntimeError("Workshop build requires %s" % COMPILER)

    compiler_version = subprocess.check_output([COMPILER, "--version"], text=True).splitlines()[0]
    compiler_release = subprocess.check_output(
        [COMPILER, "-dumpfullversion", "-dumpversion"], text=True
    ).strip()
    install_dir = Path(args.install_dir)
    install_dir.mkdir(parents=True, exist_ok=True)
    installed_license = install_dir / "STREAM-5.10-LICENSE.txt"
    installed_notice = install_dir / "NOTICE.txt"
    installed_adapter_license = install_dir / "LICENSE"
    shutil.copyfile(license_file, installed_license)
    shutil.copyfile(notice_file, installed_notice)
    shutil.copyfile(adapter_license_file, installed_adapter_license)
    built_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    variant_manifests = []

    for array_mib in args.array_mibs:
        if array_mib * 1024 * 1024 % 8:
            raise ValueError("array-mib must map to a whole number of double-precision elements")
        array_elements = array_mib * 1024 * 1024 // 8
        binary = install_dir / variant_name(array_mib)
        command = [
            COMPILER,
            *COMPILER_FLAGS,
            "-DSTREAM_ARRAY_SIZE=%d" % array_elements,
            "-DNTIMES=%d" % NTIMES,
            str(source),
            "-o",
            str(binary),
        ]
        run(command)
        binary.chmod(0o755)
        provenance = {
            "benchmark": "STREAM 5.10",
            "native_source": "unmodified McCalpin STREAM 5.10 OpenMP C source",
            "upstream_repository": UPSTREAM_REPOSITORY,
            "upstream_commit": UPSTREAM_COMMIT,
            "source_relative_path": SOURCE_RELATIVE_PATH,
            "source_sha256": SOURCE_SHA256,
            "license_file": installed_license.name,
            "license_sha256": sha256_file(installed_license),
            "notice_file": installed_notice.name,
            "notice_sha256": sha256_file(installed_notice),
            "adapter_license_file": installed_adapter_license.name,
            "adapter_license_sha256": sha256_file(installed_adapter_license),
            "source_modified": False,
            "result_label": "STREAM-derived",
            "architecture": platform.machine(),
            "array_mib_per_array": array_mib,
            "array_elements_per_array": array_elements,
            "total_array_bytes": 3 * array_mib * 1024 * 1024,
            "ntimes": NTIMES,
            "compiler": COMPILER,
            "compiler_version": compiler_version,
            "compiler_release": compiler_release,
            "compiler_flags": list(COMPILER_FLAGS)
            + [
                "-DSTREAM_ARRAY_SIZE=%d" % array_elements,
                "-DNTIMES=%d" % NTIMES,
            ],
            "link_flags": ["-fopenmp", "-mcmodel=medium", "-m64"],
            "built_at_utc": built_at,
            "binary_name": binary.name,
            "binary_sha256": sha256_file(binary),
            "run_rules_conformance": "not asserted; results are labeled STREAM-derived",
        }
        provenance_path = install_dir / (binary.name + ".build.json")
        provenance_path.write_text(
            json.dumps(provenance, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        variant_manifests.append(provenance)
        print(json.dumps(provenance, sort_keys=True), flush=True)

    build_manifest = {
        "upstream_repository": UPSTREAM_REPOSITORY,
        "upstream_commit": UPSTREAM_COMMIT,
        "source_relative_path": SOURCE_RELATIVE_PATH,
        "source_sha256": SOURCE_SHA256,
        "license_file": installed_license.name,
        "license_sha256": sha256_file(installed_license),
        "notice_file": installed_notice.name,
        "notice_sha256": sha256_file(installed_notice),
        "adapter_license_file": installed_adapter_license.name,
        "adapter_license_sha256": sha256_file(installed_adapter_license),
        "source_modified": False,
        "architecture": platform.machine(),
        "compiler": COMPILER,
        "compiler_version": compiler_version,
        "compiler_release": compiler_release,
        "compiler_flags": list(COMPILER_FLAGS),
        "array_mib_variants_per_array": list(args.array_mibs),
        "ntimes": NTIMES,
        "result_label": "STREAM-derived",
        "built_at_utc": built_at,
        "variants": variant_manifests,
    }
    (install_dir / "build-manifest.json").write_text(
        json.dumps(build_manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except (OSError, subprocess.CalledProcessError, RuntimeError, ValueError) as exc:
        print("STREAM Workshop build failed: %s" % exc, file=sys.stderr)
        sys.exit(1)
