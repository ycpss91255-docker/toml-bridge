# toml-bridge

A containerised TOML parser. You pipe TOML in, you get JSON — or
tab-separated `section<TAB>key<TAB>value` lines — back out.

It exists so that a repo whose host contract is "Docker + Git + `just`"
can read TOML configuration without also demanding a host Python, a
`pip`, or a virtualenv. Several projects in the org need exactly that,
and until now the image was built ad hoc inside
[`ycpss91255-docker/base`](https://github.com/ycpss91255-docker/base).
This repo is that image, extracted, so the projects that need it pull it
instead of each rebuilding it.

The whole parser is one small Python file (`toml_bridge.py`) on a pinned
`python:*-alpine` base, plus [`tomli`](https://github.com/hukkin/tomli)
as the pre-3.11 fallback for `tomllib`.

## Pull

```sh
docker pull ghcr.io/ycpss91255-docker/toml-bridge:v0.1.0
```

Pin to a tag — or, better, to a digest — and never to `:latest`. The
publish workflow moves `:latest` on every finished (non-prerelease)
`vX.Y.Z` tag, which is precisely the thing a consumer does not want
moving under it.

The image is multi-architecture: `linux/amd64` and `linux/arm64`, each
built on a native runner. `:main` also exists as the rolling build of
this branch; it is fine for trying the image out and is not something
to depend on.

## The CLI contract

The image's `ENTRYPOINT` is the parser itself:

```dockerfile
ENTRYPOINT ["python3", "/usr/local/bin/toml-bridge"]
```

so arguments passed after the image name are the parser's arguments, and
anything you want to run *instead* of the parser needs
`--entrypoint`. There are three modes.

### 1. Default — TOML on stdin, JSON on stdout

```console
$ printf '[gui]\nmode = "wayland"\n' | docker run --rm -i ghcr.io/ycpss91255-docker/toml-bridge:v0.1.0
{"gui": {"mode": "wayland"}}
```

- **In:** the whole of stdin, decoded as UTF-8, parsed as one TOML
  document. `-i` is required; without it stdin is empty, which parses
  cleanly as the empty document.
- **Out:** one JSON object, `json.dump` defaults — `", "` and `": "`
  separators, keys in document order, **no trailing newline**.
  `ensure_ascii=False`, so non-ASCII is emitted as raw UTF-8 rather than
  `\uXXXX` escapes.
- Empty input produces `{}` and exit 0.

### 2. `--kv` — TOML in, tab-separated lines out

The mode that exists for `bash`, which cannot read JSON. Each line is
`section`, `key`, `value`, separated by single tab characters and
terminated by a newline:

```console
$ printf '[gui]\nmode = "wayland"\ndebug = true\n' | docker run --rm -i <image> --kv
gui	mode	wayland
gui	debug	true
```

The consuming idiom is `while IFS=$'\t' read -r section key value`.

Value formatting:

| TOML type | Emitted as |
|---|---|
| string | the string, unquoted, unescaped |
| boolean | `true` / `false` |
| integer, float | Python `str()` — `3`, `1.5` |
| offset date-time | Python `str(datetime)` — `1979-05-27 07:32:00+00:00`, a **space** separator, not TOML's `T` |
| array of tables | see below |
| sub-table | Python `repr` of the dict — see *Under-specified behaviour* |

An array of tables is flattened into numbered keys, 1-based, under its
parent section. Which arrays are recognised, and how each is serialised,
is a fixed table in `toml_bridge.py`:

| Array key | Emitted key prefix | Element serialised as |
|---|---|---|
| `rules` | `rule_N` | `rule` |
| `args` | `arg_N` | `key=value` |
| `ports` | `port_N` | `host:container` |
| `cap_add` | `cap_add_N` | `name` |
| `security_opt` | `security_opt_N` | `name` |
| `volumes` | `mount_N` | `source:target:mode`, empty parts dropped |
| `tmpfs` | `tmpfs_N` | `path` |
| `devices` | `device_N` | `path` |
| `additional_contexts` | `context_N` | `name=source` |

```console
$ printf '[[svc.ports]]\nhost = 8080\ncontainer = 80\n' | docker run --rm -i <image> --kv
svc	port_1	8080:80
```

An array whose key is not in that table is **silently dropped** — no
output line, no warning, exit 0.

### 3. `--merge` — layer several TOML files

```console
$ docker run --rm -v /etc/app:/etc/app:ro <image> --merge /etc/app/base.toml /etc/app/local.toml
```

- Files are read **inside the container**, so every path must be mounted
  in and named by its container path. stdin is not read in this mode.
- Paths are listed **lowest precedence first**; later files win.
- Merge is one level deep and type-aware: for a top-level key whose
  value is a table, the layers are merged key by key, so an upper layer
  overrides only the keys it actually defines. For any other top-level
  value — an array, an array of tables, a bare scalar — the upper layer
  **replaces** it wholesale.
- A path that does not exist is skipped silently. Merging zero existing
  files yields `{}` and exit 0.
- `--merge` composes with `--kv`; without it the output is JSON, as in
  mode 1.

```console
$ cat base.toml;  cat local.toml
[a]
x = 1
y = 2
[a]
y = 9

$ docker run --rm -v "$PWD:$PWD:ro" <image> --merge "$PWD/base.toml" "$PWD/local.toml"
{"a": {"x": 1, "y": 9}}
```

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Parsed. Output is on stdout, which may legitimately be `{}` or empty. |
| `1` | The parser refused: a TOML syntax error, a non-UTF-8 byte, an unreadable (but existing) path in `--merge`, or an internal error. A diagnostic is on stderr. |

Anything else came from Docker, not from the parser: `125` for a bad
`docker run`, `126`/`127` for an entrypoint that could not be executed,
`137` for a kill. The parser itself never produces those, but a caller
that only tests `if ! docker run ...` cannot tell them apart from a
syntax error. See below.

### How a syntax error is reported

One line on **stderr**, prefixed `toml-bridge: `, carrying the
underlying parser's message. Nothing is written to stdout. Exit `1`.

```console
$ printf '[gui\n' | docker run --rm -i <image>; echo "exit=$?"
toml-bridge: Expected ']' at the end of a table declaration (at line 1, column 5)
exit=1
```

In `--merge` mode the offending path is inserted, so a caller can tell
which layer failed:

```console
$ docker run --rm -v "$PWD:$PWD:ro" <image> --merge "$PWD/bad.toml"; echo "exit=$?"
toml-bridge: /work/bad.toml: Expected ']' at the end of a table declaration (at line 1, column 5)
exit=1
```

The line/column text comes verbatim from `tomllib`. It is a CPython
implementation detail, not a specified format — see below.

## Under-specified behaviour

Everything above is what the parser *does today*, read off the
implementation. The list below is where the contract is genuinely
ambiguous or surprising. It is written down rather than resolved,
because the extraction of this image from `base` is not the moment to
change what `base` already depends on. **A caller should not rely on any
of these staying as they are.**

1. **Argument parsing is positional-by-accident.** The parser keeps as
   file paths every argument that does not start with `-`, and detects
   modes by testing whether the exact strings `--kv` and `--merge`
   appear anywhere in `argv`. Consequences:
   - An unrecognised flag is **silently ignored**. `--help` and
     `--version` are not implemented, so they are ignored too: the
     parser waits on stdin and prints `{}`.
   - A file path beginning with `-` cannot be passed, and `--` is not
     honoured as an end-of-options marker.
   - In the default (stdin) mode, extra positional arguments are
     accepted and ignored rather than refused.
2. **Error message text is not a contract.** The message after
   `toml-bridge: ` is `str()` of whatever exception the parser raised.
   The shipped image is Python 3.13, so it is always stdlib `tomllib` —
   but `tomli` is installed as the documented fallback, and its wording
   for the same input is not guaranteed to match. Nothing parses these
   messages today; nothing should start.
3. **`--kv` cannot represent nested tables.** `_emit_kv` walks exactly
   two levels. A sub-table is neither recursed into nor rejected; it is
   printed through `str()`, so `[gui.sub] x = 1` emits the literal line
   `gui<TAB>sub<TAB>{'x': 1}` — a Python `repr` on a bash-facing wire
   format. Whether the right answer is a dotted key, a refusal, or an
   error is an open question.
4. **`--kv` drops top-level scalars.** A key defined before any `[table]`
   header has no section to be filed under, and is discarded without
   comment.
5. **`--kv` values are not escaped.** A tab or a newline inside a TOML
   string is emitted raw and corrupts the record: `k = "a\tb"` becomes
   `s<TAB>k<TAB>a<TAB>b`, which `read -r` splits into a fourth field
   that the caller never sees. There is no escaping or quoting scheme,
   and no rejection of values that contain the delimiters.
6. **A recognised array key holding the wrong shape crashes.** The
   serialisers assume arrays of tables. `rules = ["a", "b"]` — an array
   of strings under a key the table recognises — raises an uncaught
   `AttributeError` and exits 1 with a Python traceback on stderr,
   rather than a `toml-bridge: ` diagnostic. Only the `--merge` and
   stdin-parse paths have exception handlers; `--kv` emission has none.
7. **`--kv` output is not atomic.** Lines are printed as they are
   produced, so a document that crashes the emitter part-way through
   has already written valid lines to stdout *and* exits 1. A caller
   that pipes `--kv` into a loop and does not check the exit status will
   silently consume a truncated document — which is exactly what
   `base`'s `_toml_tokenize` does today, since it reads from a process
   substitution and discards the parser's status.
8. **"Absent" and "empty" are the same answer in `--merge`.** Missing
   paths are skipped silently, so merging five paths that all fail to
   exist is indistinguishable, on both stdout and the exit code, from
   merging five genuinely empty files. A typo in a path is not an error.
9. **The recognised-array table is `base`'s vocabulary, not TOML's.**
   `ports`, `cap_add`, `security_opt`, `volumes`, `devices`,
   `additional_contexts` are container-compose concepts that reached a
   general-purpose TOML parser through its first consumer. A second
   consumer with different arrays gets silent drops (see mode 2) until
   the table is edited here. Whether the table should be data the caller
   supplies, or should not exist at all, is the substantive open design
   question in this repo.
10. **Exit codes do not separate the parser from Docker.** `1` means the
    parser refused; `125`–`127` mean `docker run` did. A shim that
    forwards `docker run`'s status — the usual shape — hands its caller
    a single "it failed" with no way to distinguish invalid TOML from a
    missing image.

## Calling it from bash

There is no shell library in this repo, deliberately: the invocation is
two `docker run` lines, and the consumer owns where the image reference
comes from. The shape both current consumers use:

```bash
# Parse one file. JSON on stdout; add --kv for tab-separated lines.
toml_parse() {
  docker run --rm -i "${TOML_BRIDGE_IMAGE}" "${@:2}" < "$1"
}

# Merge layers, lowest precedence first. Each file is bind-mounted at
# its own absolute path so the container and the host agree on names.
toml_merge() {
  local -a mounts=() paths=() abs
  for f in "$@"; do
    [[ -f $f ]] || continue
    abs="$(cd -- "$(dirname -- "$f")" && pwd -P)/$(basename -- "$f")"
    mounts+=(-v "${abs}:${abs}:ro"); paths+=("${abs}")
  done
  (( ${#paths[@]} )) || return 0
  docker run --rm "${mounts[@]}" "${TOML_BRIDGE_IMAGE}" --merge "${paths[@]}"
}
```

Check the exit status. Point 7 above is what happens if you do not.

## Consumers

| Repo | What it reads |
|---|---|
| [`ycpss91255-docker/base`](https://github.com/ycpss91255-docker/base) | its own configuration — `dist/script/docker/lib/toml_bridge.sh` shims the image, `conf.sh` tokenises `--kv` output. `base` also lifts the parser into its `test-tools` image with `COPY --from`, so repos downstream of `base` inherit it without pulling this image directly. |
| [`ycpss91255-research/vendor_kit`](https://github.com/ycpss91255-research/vendor_kit) | a consumer repo's manifest — the file that says which parts of a vendored tree get installed. |

## Building and testing locally

```sh
docker build -t toml-bridge:local .
printf '[gui]\nmode = "wayland"\n' | docker run --rm -i toml-bridge:local
```

The `# tool-pin:` comments in the `Dockerfile` are the org's pin-bot
annotations: each names the upstream its `ARG` tracks, so bumping the
Python base or `tomli` is mechanical rather than archaeological.

## Releasing

`.github/workflows/release-toml-bridge.yaml` publishes to GHCR. Each
architecture builds on its own native runner — `ubuntu-latest` for
amd64, `ubuntu-24.04-arm` for arm64, no QEMU — pushes a single-arch
image by digest, and a merge job assembles the multi-arch manifest.

| Trigger | Tags published |
|---|---|
| push of `vX.Y.Z` | `:vX.Y.Z`, plus `:latest` when the tag is not a prerelease |
| push of `vX.Y.Z-rc1` | `:vX.Y.Z-rc1` only; `:latest` stays where it is |
| push to `main` | `:main` |
| `workflow_dispatch` | resolved from the ref it was dispatched from; any ref that is neither `main` nor a `v*` tag is refused |

`script/ci/release-ref.sh` is the single classifier of "is this tag a
prerelease" — a copy of the script of the same name in `base`, which
refuses a ref it cannot read as a semver tag rather than guessing
`false`, since `false` is the branch that moves `:latest`.

## Licence

Apache-2.0. See [`LICENSE`](LICENSE).
