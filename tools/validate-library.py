#!/usr/bin/env python3
"""Validate library schemas and, optionally, library documents against them.

  validate-library.py                    check every schema is valid JSON Schema 2020-12
  validate-library.py <dir-or-files...>  also validate work/version/source/package documents

Documents are matched to a schema by their "schema" field, so any file tree can be checked.
"""
import json, sys, pathlib
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

HERE = pathlib.Path(__file__).resolve().parent.parent / "library"
SCHEMAS = {p.name: json.loads(p.read_text()) for p in sorted(HERE.glob("*.schema.json"))}
registry = Registry()
for name, s in SCHEMAS.items():
    r = Resource.from_contents(s)
    registry = registry.with_resource(s["$id"], r).with_resource(name, r)

BY_KIND = {
    "zaentrum.library.work/1": "work.schema.json",
    "zaentrum.library.version/1": "version.schema.json",
    "zaentrum.library.source/1": "source.schema.json",
    "zaentrum.library.package/1": "package.schema.json",
}

def check_schemas():
    bad = 0
    for name, s in SCHEMAS.items():
        try:
            Draft202012Validator.check_schema(s)
            v = Draft202012Validator(s, registry=registry)
            # force resolution of every $ref by validating an empty object
            list(v.iter_errors({}))
            print(f"schema ok   {name}")
        except Exception as e:
            bad += 1
            print(f"schema FAIL {name}: {e}")
    return bad

def check_docs(paths):
    files = []
    for p in map(pathlib.Path, paths):
        files += sorted(p.rglob("*.json")) if p.is_dir() else [p]
    counts, bad = {}, 0
    for f in files:
        try:
            doc = json.loads(f.read_text())
        except Exception:
            continue
        kind = doc.get("schema") if isinstance(doc, dict) else None
        if kind not in BY_KIND:
            continue
        v = Draft202012Validator(SCHEMAS[BY_KIND[kind]], registry=registry, format_checker=FormatChecker())
        errs = sorted(v.iter_errors(doc), key=lambda e: list(e.absolute_path))
        counts[kind] = counts.get(kind, 0) + 1
        if errs:
            bad += 1
            print(f"doc FAIL {f}")
            for e in errs[:6]:
                print(f"    at /{'/'.join(map(str, e.absolute_path))}: {e.message[:160]}")
    for k, n in sorted(counts.items()):
        print(f"validated {n:4} {k}")
    return bad

if __name__ == "__main__":
    failures = check_schemas()
    if len(sys.argv) > 1:
        failures += check_docs(sys.argv[1:])
    print("RESULT:", "OK" if failures == 0 else f"{failures} failure(s)")
    sys.exit(1 if failures else 0)
