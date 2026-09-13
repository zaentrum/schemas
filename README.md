# schemas

Avro schemas for the zaentrum event-driven platform. This repo is the
single source of truth for Kafka event payload shapes across the platform,
published to a shared Apicurio schema registry.

## Status

First wave covers the five core domains: library item lifecycle, transcode
processing, playback sessions, download-gateway adapters, and push-notification
fan-out.

## Layout

```
stube/
  library/    item lifecycle events (created/updated/deleted)
  processing/ transcoder task + result events
  playback/   session lifecycle from chino-stream / tv-stream / musig-stream
  download/   download-gateway adapter completion events
  notify/     fan-out push notification requests
tools/
  publish-to-apicurio.sh   helper for publishing schemas to a registry
```

The top-level `stube/` directory mirrors the Avro namespace and Kafka topic
prefix (`stube.<domain>`); it is an operational name and is kept as-is.

## Naming convention

- Topic: `stube.<domain>.<event>` (e.g. `stube.library.item.created`).
- Avro namespace: `stube.<domain>` (mirrors directory).
- Avro record name: `PascalCase` (`ItemCreated`, `TaskTranscode`).
- Artifact id in the registry: dot-joined path without extension
  (e.g. `stube.library.item-created`).

## Local development

Validate every schema parses as JSON:

```bash
find stube -name '*.avsc' -print0 | xargs -0 -I{} python -c "import json,sys; json.load(open('{}'))"
```

Or, if you have `avro-tools`:

```bash
find stube -name '*.avsc' -exec avro-tools compile schema {} /tmp/avro-gen \;
```

## Publishing

`tools/publish-to-apicurio.sh` walks every `.avsc` and publishes it to an
Apicurio registry. Point it at your own registry via environment variables:

```bash
APICURIO_URL=https://apicurio.example/ GROUP_ID=zaentrum ./tools/publish-to-apicurio.sh
```

## License

[MPL-2.0](LICENSE).

## Library storage schemas

`library/` holds JSON Schemas (draft 2020-12) for the platform's on-storage
library, where storage is the source of truth and databases are caches built
from it.

| Document | What it describes |
|---|---|
| `work.json` | What a thing *is*: a movie, series, episode or track. Titles in every language, external ids, credits, artwork, series seasons, episode numbering under every ordering scheme, curation. The entry point. |
| `version.json` | One cut or presentation of a work: theatrical or director's cut, colour or black-and-white, SDR or HDR, 2D or 3D. Carries the evidence behind each conclusion and who decided it. |
| `source.json` | The master file of a version: fixity, what its name and folder claimed, whether it is an original or already a re-encode, a full stream inventory, chapters, sidecars, and the verbatim probe output. |
| `package.json` | A derived, disposable rendition, and above all what it *lost* relative to its source. |

Layout on storage:

```
works/<first 2 hex of id>/<workId>/
  work.json
  art/<kind>.<sha256 prefix>.<ext>
  versions/<versionId>/
    version.json
    source.json
    source/<original filename>
    packages/<packageId>/package.json   (+ the playback manifest and segments)
  episodes/<episodeId>/...              (series only, same shape)
```

Directories are keyed only by stable ids, never by titles or numbers, so a
rename, renumbering or alternate episode order never moves a file. Numbers
and titles live inside the documents.

Validate documents:

```sh
pip install "jsonschema>=4.23" referencing
python tools/validate-library.py <directory-or-files>
```

`tools/library-migrate.py` is a reference migrator that builds these documents
from a legacy catalog export plus probe output. It never invents a value: a
field the source data does not hold is left empty and reported.
