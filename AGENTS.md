# schemas

The schemas repository holds the platform's published contracts. `stube/` has the Avro
schemas of Kafka event payloads, a folder a domain (`stube.<domain>` is their namespace and
topic prefix); the services' item events are JSON today, and the records under
`stube/library/` describe an earlier design that no service emits
([events](https://github.com/zaentrum/zaentrum/blob/main/docs/extending/events.md)).
`library/v1/` and `library/v2/` have the JSON Schemas (draft 2020-12) of the library on
storage, each with an example tree, served by GitHub Pages at the URL each `$id` names.
`tools/` has the Python tools that validate, write, migrate and rebuild library trees, and
`tools/libv2_records.py`, the record logic the packager vendors; the [README](README.md)
describes them all.

How a change is proposed and what a pull request has to pass is in the
[guide to contributing](https://github.com/zaentrum/.github/blob/main/CONTRIBUTING.md);
what may be built is in [building from the specification](https://github.com/zaentrum/zaentrum/blob/main/AGENTS.md).

## Build

Nothing is compiled and there is no image. `build.yml`'s `validate` job parses every Avro
schema with fastavro, and its library jobs byte-compile the tools:

```sh
python -m pip install fastavro "jsonschema[format-nongpl]>=4.23" referencing
python -c 'import glob; from fastavro.schema import load_schema; [load_schema(p) for p in sorted(glob.glob("stube/**/*.avsc", recursive=True))]'
python -m py_compile tools/*.py
```

The tools are Python 3.11 or later, standard library alone, but for the two validators.
`build.yml` uses 3.12, and 3.11 for its pod job.

## Test

`build.yml` runs five jobs on a pull request and on `main`: `ids`, which checks that every
schema's `$id` is the URL it is served at; `validate`, the parse above; `library`, which
holds the v1 example tree to its schemas, the rules across files and its checksums, proves
the validator rejects broken trees, and that the examples are what their generator writes:

```sh
python tools/validate-library.py --check-checksums library/v1/examples
python tools/test-validate-library.py
python tools/make-library-examples.py && git diff --exit-code -- library/v1/examples
```

`library-v2`, the same for v2, which also proves the v2 tools on trees they write and the
record logic against `tools/testdata/libv2_records/`:

```sh
python tools/validate-library-v2.py --check-checksums library/v2/examples
python tools/test-validate-library-v2.py
python tools/test-library-v2-tools.py
python tools/make-library-v2-examples.py && git diff --exit-code -- library/v2/examples
```

and `library-v2-pod`, `python tools/test-library-v2-tools.py` on Python 3.11 with nothing
installed, as a pod runs the tools: the cases that need jsonschema say `skip`. `pages.yml`,
on `main` or started by hand, makes the same `$id` check before it publishes `index.html`
and `library/` to GitHub Pages.

## Run it

The example trees are the recorded answers. The tools run on them, or on a library of your
own. The first command needs `jsonschema[format-nongpl]` and `referencing`, as installed above;
the second, which writes the catalog rows a tree implies, needs Python alone:

```sh
python tools/validate-library-v2.py --check-checksums /path/to/library
python tools/library-v2-rebuild.py library/v2/examples --out rows.json
```

On a platform's library the tools that need Python alone run where its share is mounted,
piped into a pod's Python (the README's "Writing and reading a tree"); the validator runs
where its two packages are installed. `tools/publish-to-apicurio.sh` lists the Avro
schemas it would publish to `APICURIO_URL` and sends nothing.

## What the tests leave unchecked

- The Avro schemas are only parsed: no test holds them to what a service sends, and nothing
  publishes them to a registry. No service runs here; the packager validates its trees with
  `validate-library-v2.py` in its own tests, where a checkout of this repository is beside it.
- `tools/library-migrate.py`, the v1 reference migrator, is only byte-compiled. The v2
  migration and sweep tools are proven on trees the tests write; `--apply` on a platform's
  library happens only there.

## Before a pull request

- The example trees are generated: after a change to a schema, a tool or a generator, run
  `python tools/make-library-examples.py` and `python tools/make-library-v2-examples.py`
  and commit what they change. `build.yml` fails on any difference.
- A new schema's `$id` is `https://zaentrum.github.io/schemas/` and its path; `build.yml`'s
  `ids` job checks it on every pull request, and `pages.yml` again before it publishes.
- The packager vendors `tools/libv2_records.py` byte for byte, pinned with the golden records
  of `tools/testdata/libv2_records/`: a change to either goes there from the same commit.
