# Native output fixture

`stream-output-success.txt` is retained from a successful run of the pinned,
unmodified STREAM 5.10 OpenMP C source. It contains only native program output;
host names, run paths, process metadata, and environment details are not part
of the fixture. Its 256 MiB-per-array size and one-thread summary exercise the
real parser and validation rules. The bandwidth values are local smoke data,
not portable performance expectations.

The fixture is used by `python3 -m unittest discover -s tests -v`. The tests do
not execute a native STREAM binary or download source.
