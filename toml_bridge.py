#!/usr/bin/env python3
"""TOML to JSON bridge for the base container toolchain.

Reads TOML from stdin, writes JSON to stdout. Uses tomllib (stdlib 3.11+)
with vendored tomli as fallback (Python 3.6+). ADR-37 sec. Containerised
parsing.
"""
import json
import re
import sys

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


_ARRAY_SPEC = {
    "rules": ("rule", lambda e: e.get("rule", "")),
    "args": ("arg", lambda e: "%s=%s" % (e["key"], e["value"]) if "key" in e else ""),
    "ports": ("port", lambda e: "%s:%s" % (e["host"], e["container"]) if "host" in e else ""),
    "cap_add": ("cap_add", lambda e: e.get("name", "")),
    "security_opt": ("security_opt", lambda e: e.get("name", "")),
    "volumes": ("mount", lambda e: ":".join(v for v in [e.get("source", ""), e.get("target", ""), e.get("mode", "")] if v)),
    "tmpfs": ("tmpfs", lambda e: e.get("path", "")),
    "devices": ("device", lambda e: e.get("path", "")),
    "additional_contexts": ("context", lambda e: "%s=%s" % (e["name"], e["source"]) if "name" in e else ""),
}


def _format_value(v):
    """Format a scalar TOML value for KV output."""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _emit_array(section, key, items):
    """Emit array of tables as numbered-key KV lines."""
    spec = _ARRAY_SPEC.get(key)
    if not spec:
        return
    prefix, serializer = spec
    for i, elem in enumerate(items, 1):
        print(f"{section}\t{prefix}_{i}\t{serializer(elem)}")


def _emit_kv(data):
    """Emit section/key/value tab-separated lines for bash consumption."""
    for section, entries in data.items():
        if isinstance(entries, list):
            _emit_array(section, section, entries)
        elif isinstance(entries, dict):
            for key, value in entries.items():
                if isinstance(value, list):
                    _emit_array(section, key, value)
                else:
                    print(f"{section}\t{key}\t{_format_value(value)}")


def _merge_layers(paths):
    """Type-aware merge of TOML layers (lowest precedence first).

    Tables: key-level merge (upper overrides only keys it defines).
    Arrays: replace (upper replaces entire array).
    """
    merged = {}
    for path in paths:
        try:
            with open(path, "rb") as fh:
                layer = tomllib.loads(fh.read().decode())
        except FileNotFoundError:
            continue
        except Exception as exc:
            print(f"toml-bridge: {path}: {exc}", file=sys.stderr)
            raise SystemExit(1)
        for section, entries in layer.items():
            if isinstance(entries, dict):
                if section not in merged or not isinstance(merged[section], dict):
                    merged[section] = {}
                merged[section].update(entries)
            else:
                merged[section] = entries
    return merged


# Every numbered family a table HOSTS as table scalars, and the [[...]]
# path that family is written under. Only --entries reads it (base#4).
_SECTION_FAMILIES = {
    "image": (("rule", "image.rules"),),
    "build": (("arg", "build.args"),),
    "network": (("port", "network.ports"),),
    "volumes": (("mount", "volumes"),),
    "tmpfs": (("tmpfs", "tmpfs"),),
    "devices": (("device", "devices.bindings"),
                ("cgroup_rule", "devices.cgroup_rules")),
    "additional_contexts": (("context", "additional_contexts"),),
    "security": (("cap_add", "security.cap_add"),
                 ("cap_drop", "security.cap_drop"),
                 ("security_opt", "security.security_opt")),
}


def _entry_fields(path, value):
    """The fields of the [[<path>]] entry a numbered scalar stands for."""
    def split(sep, names):
        return list(zip(names, value.split(sep, len(names) - 1)))

    if path == "build.args":
        return split("=", ("key", "value"))
    if path == "additional_contexts":
        return split("=", ("name", "source"))
    if path == "network.ports":
        return split(":", ("host", "container"))
    if path == "volumes":
        return split(":", ("source", "target", "mode"))
    if path in ("security.cap_add", "security.cap_drop"):
        return [("cap", value)]
    if path == "security.security_opt":
        return [("opt", value)]
    if path in ("tmpfs", "devices.bindings"):
        return [("path", value)]
    return [("rule", value)]


def _header_path(opener, key):
    """The dotted path a table header names, decoded as TOML decodes it."""
    closer = "]]" if opener == "[[" else "]"
    try:
        node = tomllib.loads(f"{opener}{key}{closer}\n")
    except tomllib.TOMLDecodeError:
        return None
    parts = []
    while isinstance(node, dict) and len(node) == 1:
        name, node = next(iter(node.items()))
        parts.append(name)
    return ".".join(parts) if parts else None


def _header_positions(raw):
    """Where each table header sits in the source text.

    Returns ({path: [line, ...]} for `[[path]]` headers,
    {path: line} for the first `[path]` header).
    """
    aot, table = {}, {}
    header = re.compile(r"^\s*(\[\[?)\s*([^\[\]]+?)\s*\]\]?\s*(#.*)?$")
    for number, line in enumerate(raw.splitlines()):
        m = header.match(line)
        if not m:
            continue
        path = _header_path(m.group(1), m.group(2))
        if path is None:
            continue
        if m.group(1) == "[[":
            aot.setdefault(path, []).append(number)
        else:
            table.setdefault(path, number)
    return aot, table


def _emit_entries(data, raw):
    """Emit the file's values in setup.toml's own vocabulary, for display.

    One row per field, `<section>\t<n>\t<header>\t<field>\t<value>`;
    <header> is the table as the file spells it and <n> numbers the
    blocks in header order. An entry with no fields is one header-only
    row. An unknown array is refused by name. Rows are collected before
    any is printed, so a refusal leaves stdout empty (base#4).
    """
    aot_pos, table_pos = _header_positions(raw)
    blocks = []

    def place(section):
        if section in table_pos:
            return table_pos[section]
        inner = [n for path, n in table_pos.items()
                 if path.startswith(section + ".")]
        inner += [n for path, ns in aot_pos.items()
                  if path.startswith(section + ".") for n in ns]
        return min(inner) if inner else -1

    def block(pos, section, header, fields):
        blocks.append((pos, len(blocks), section, header, fields))

    def entries(section, path, key, items):
        if key not in _ARRAY_SPEC:
            print(f"toml-bridge: {path} is an array, and no numbered family "
                  f"of the shell view carries an array by that name, so "
                  f"nothing would read it: check the spelling of the array "
                  f"name, or remove it", file=sys.stderr)
            raise SystemExit(1)
        found = aot_pos.get(path, [])
        exact = len(found) == len(items)
        for index, elem in enumerate(items):
            if not isinstance(elem, dict):
                continue
            fields = [(field, _format_value(value))
                      for field, value in elem.items()
                      if not isinstance(value, (dict, list))]
            pos = found[index] if exact else place(section)
            block(pos, section, f"[[{path}]]", fields)

    for section, node in data.items():
        if isinstance(node, list):
            entries(section, section, section, node)
            continue
        if not isinstance(node, dict):
            continue
        scalars, arrays, numbered = [], [], []
        families = _SECTION_FAMILIES.get(section, ())

        def walk(prefix, table):
            for key, value in table.items():
                if isinstance(value, list):
                    arrays.append((f"{section}.{prefix}{key}", key, value))
                elif isinstance(value, dict):
                    walk(f"{prefix}{key}.", value)
                else:
                    value = _format_value(value)
                    if value == "":
                        continue
                    for family, path in families if not prefix else ():
                        m = re.fullmatch(rf"{family}_([0-9]+)", key)
                        if m:
                            numbered.append((path, int(m.group(1)), value))
                            break
                    else:
                        scalars.append((f"{prefix}{key}", value))

        walk("", node)
        pos = place(section)
        if scalars:
            block(pos, section, f"[{section}]", scalars)
        for path, _n, value in sorted(numbered, key=lambda r: r[:2]):
            block(pos, section, f"[[{path}]]", _entry_fields(path, value))
        for path, key, items in arrays:
            entries(section, path, key, items)

    for n, (_pos, _seq, section, header, fields) in enumerate(
            sorted(blocks, key=lambda b: b[:2]), 1):
        for field, value in fields or [(None, None)]:
            if field is None:
                print(f"{section}\t{n}\t{header}")
            else:
                print(f"{section}\t{n}\t{header}\t{field}\t{value}")


def _emit_empty_arrays(data):
    """Emit `<section>\t<path>` for every array declared with NO entries.

    <section> is the path's first component (base#4).
    """
    def walk(prefix, node):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, list):
                if not value:
                    print(f"{path.split('.')[0]}\t{path}")
            elif isinstance(value, dict):
                walk(path, value)

    walk("", data)


def _emit_tables(data):
    """Emit the dotted path of every TABLE the file declares, one per line.

    A root-level array is listed by its name too; the walk does not
    descend into an array's elements (base#4).
    """
    def walk(prefix, node):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                print(path)
                walk(path, value)
            elif isinstance(value, list) and not prefix:
                print(path)

    walk("", data)


def _emit_empty_families(data):
    """Emit `<section>\t<key>\t<path>` for every array declared EMPTY.

    <section> and <key> are the KV row the array's first entry would take
    (`<prefix>_1` from _ARRAY_SPEC); an array _ARRAY_SPEC does not know is
    left out (base#4).
    """
    def walk(section, node):
        for key, value in node.items():
            if isinstance(value, dict):
                walk(f"{section}.{key}" if section else key, value)
            elif isinstance(value, list) and not value:
                spec = _ARRAY_SPEC.get(key)
                if not spec:
                    continue
                home = section or key
                path = f"{section}.{key}" if section else key
                print(f"{home}\t{spec[0]}_1\t{path}")

    walk("", data)


def main():
    argv = [a for a in sys.argv[1:] if not a.startswith("-")]
    kv_mode = "--kv" in sys.argv
    merge_mode = "--merge" in sys.argv
    entries_mode = "--entries" in sys.argv

    if entries_mode and merge_mode:
        print("toml-bridge: --entries reads one file from stdin, "
              "not --merge", file=sys.stderr)
        raise SystemExit(1)

    if merge_mode:
        data = _merge_layers(argv)
    else:
        raw = sys.stdin.buffer.read()
        try:
            data = tomllib.loads(raw.decode())
        except Exception as exc:
            print(f"toml-bridge: {exc}", file=sys.stderr)
            raise SystemExit(1)

    if entries_mode:
        _emit_entries(data, raw.decode())
    elif "--empty-families" in sys.argv:
        _emit_empty_families(data)
    elif "--tables" in sys.argv:
        _emit_tables(data)
    elif "--empty-arrays" in sys.argv:
        _emit_empty_arrays(data)
    elif kv_mode:
        _emit_kv(data)
    else:
        json.dump(data, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()
