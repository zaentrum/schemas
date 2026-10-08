#!/usr/bin/env python3
"""Write v2 library records for a catalog's items, from the catalog export, the package store and
the original files.

Usage:
  library-v2-from-catalog.py --export CATALOG.json --packages DIR --media DIR --out LIBRARY
                             [--items id,id,...] [--media-mode copy|move|none]
                             [--as-of TIMESTAMP] [--text-language LANG] [--dry-run]
  library-v2-from-catalog.py --export CATALOG.json --out LIBRARY --people-only [--items …] [--dry-run]
  library-v2-from-catalog.py --export CATALOG.json --out LIBRARY --projections-only [--items …] [--dry-run]
  library-v2-from-catalog.py --platform --export CATALOG.json --root SHARE --run RUN [--shard aa]
                             [--media DIR] [--packages DIR] [--extras DIR] [--items …] [--as-of …] [--dry-run]

The catalog is the working copy and this writes the record beside the bytes: one item folder per
row, holding the identity the row was created with, the texts and images the database held, the
original each row points at as the file itself reports it, and one version folder per packaged
asset with the package moved or copied in — and one folder per person the rows credit. Nothing is
named as it arrived: an original moved or copied in is original.<ext> beside its package, the name
its source record gives it too, and a downloaded trailer the same in its extra.

  <out>/movies/<aa>/<itemId>/          item.json  checksums.sha256  metadata.json  metadata/<sha256>.jpg
                                       sources/<sourceId>/source.json  ffprobe.json  checksums.sha256
                                       versions/<versionId>/version.json  original.<ext>  hls/ subs/ trickplay/
                                                             checksums.sha256  package.json  .complete
                                       extras/<extraId>/extra.json  original.<ext>  checksums.sha256
  <out>/series/<aa>/<seriesId>/        item.json  checksums.sha256  metadata.json  metadata/  extras/
                                       episodes/<episodeId>/ (as a movie, without extras/)
  <out>/people/<aa>/<personId>/        person.json  <sha256>.jpg

Every folder written once gets the checksums.sha256 that covers it in the same step, and a version
closes its chain last: checksums over version.json and the package, package.json with their hash,
.complete with package.json's.

Credits: each of an item's people entries is a credit of its metadata.json — personId, name, role
and, each null where the export says nothing, as an export from before credits were general does,
the job in the source's own words, the character, the billing order within the role and a series'
episode count. They are listed by role — actor, creator, director, writer, producer, composer,
cinematographer, editor, in that order, and any other role after them, alphabetically — then by
billing order, a credit without one last, then by name and personId: the export lists credits in no
particular order, and the same credits must make the same file. A credit whose role is not a token
is left out with a note, and so is an order or an episode count that is not a whole number, or a
count below nothing.

People: a catalog without person records knows only what its credits say, a personId and a name, so
that is what person.json holds and every other field stays empty. When the export carries a
top-level people list, each entry's fields are used under the names person.json gives them — id,
name, sortName, alsoKnownAs, birthDate, deathDate, birthPlace, knownForDepartment, biography (a
string in --text-language, or {language: text}), externalIds ({tmdbPerson, imdb, tvdb, wikidata},
or the [{source, externalId}] list items use), metadataLocked and lockedFields (its curation),
fieldOrigins (the database's own record of where each field came from, used instead of the
catalog's), and artwork ([{kind: "profile", base64, isPrimary, sourcePath, fetchedAt, …}]) — and
every person in it is written, credited or not, unless --items narrows the run to the people its
items credit. person.json is a projection: it is replaced whole, and an image it no longer names is
removed with it. --people-only writes people/ and touches no item folder, so a tree written earlier
gains its people without rewriting a record; run again after the database changed, it is how a
person record that went stale is projected again.

--projections-only is how a tree is projected again after the database changed — a stale projection
--compare reports is gone after one run. It rewrites the projections of what the tree already holds,
and nothing else: every item's metadata.json and the images it lists in metadata/, every person's
person.json and their portraits, each replaced whole from the export through a temporary file and a
rename. It never writes, rewrites or touches a record written once — item.json, a source, a version,
a package, an extra, an event, a checksums.sha256 — and an item or a person the export holds and the
tree does not is skipped with a note: creating them is the full build's job, or --people-only's. An
image is written once, by the name of its content, so one already there is left as it is, and one
the new projection no longer lists is left for library-v2-sweep.py, past its grace — where the full
build and --people-only remove it at once. Which version plays when the viewer does not choose is a
decision the export does not carry: it stays the one the projection on storage names while that
version is there, and is otherwise derived as the full build derives it.

Every projection says how fresh it is. A row's modifiedAt — an item's or a person's — is the
databaseUpdatedAt of its projection, the state of the row it reflects, and a row that carries
tmdbFetchedAt (with tmdbChangedAt, the day TMDB last reported a change) gives it sources.tmdb; a row
without them gets neither. An image is named by its own content, primary where the row's isPrimary
says so — at most one of a kind, the first the export lists — and its origin is TMDB with the
sourcePath it was fetched from when the row has one, and the catalog when it does not. Every artwork
row is an image of its kind, so rows of two kinds with the same bytes — a backdrop that is the
poster — are two entries sharing one file; metadata.json lists them by kind, then file.

The export is produced on the client side (see the query beside this tool in the runbook); this
tool needs no database driver and no network, only the standard library and libv2_records.py — the
record logic the packager vendors, so the two write the same records — and so it can be piped into
the pod that mounts the share behind that module:

  cat libv2_records.py library-v2-from-catalog.py | oc -n <ns> exec -i deploy/packager -- python3 - --export …

What it can fill in, and what it cannot:
  * the identity, texts, images, people, chapters, segments and trailers come from the export;
  * a trailer the catalog downloaded — one whose localPath is a file on the share — also becomes an
    extra of kind trailer beside its movie or series, its file copied or moved in as --media-mode
    says, titled by its link (Trailer when the link has none), its link kept in metadata.json's
    videos and named by the extra's origin, so a rebuild gives the link its localPath back. An
    episode's stays a link, because an episode has no extras, and so does every one with
    --media-mode none, because an extra holds its own file;
  * the container, streams, fidelity and essence of an original come from `ffprobe`, which is used
    when it is on PATH — without it a source record still carries the file's size, mtime and qh1
    fingerprint, and says in `probe.note` that nothing was probed;
  * everything the export does not hold stays empty rather than guessed: no content rating, no
    localized titles beyond the one text language, no collection, no season texts, no edition
    beyond what a file name says, and no measured colour.

An item whose original file cannot be read gets its item.json and metadata.json and nothing else:
a version must name a source record, and a source record must carry the file's fixity, which
cannot be invented from a path. Those items are listed at the end.

Every generated id is a UUIDv5 of the item id and a stable name, and every "written at" stamp is
--as-of (the export's own exportedAt by default), so running this twice writes the same tree.

--platform stages the library of the platform from the store it kept before — originals under
<root>/media, packages under <root>/packages, extras taken in under <root>/extras — for
katalog-manager to adopt. It reads that store and writes nothing outside
<root>/.work/migration/<run>/: the package files are hashed where they are, the originals probed
where they are, and the only files written are small — the records, the images the database holds
and copies of the subtitle files that came with the originals:

  staged/<itemId>/item/          the item folder as the library will hold it — item.json, metadata.json
                                 and its images, sources/<sourceId>/ with the probe and a copy of each
                                 subtitle file, versions/<versionId>/ with version.json, checksums.sha256,
                                 package.json and .complete but none of the package files, which the
                                 checksums list where the adopt puts them, nor the original, which the
                                 adopt renames in, extras/<extraId>/ the same — a title nothing packaged
                                 yet with version.json alone
  staged-people/<personId>/      person.json and the portraits of everyone a staged item credits,
                                 unless the library holds them already; the adopt puts them at
                                 people/<aa>/<personId>/ once the items are in
  units/<itemId>.json            the plan of one item (zaentrum.migration.unit/1), below
  report.json                    what is ready, every problem by its class, and what deleting every
                                 original would cost (report-<shard>.json for a shard)

The records are the platform's, and name no file as it arrived. Every original goes into its title's
version folder at once, under the name the library gives it, original.<ext>, and is deleted from
there once its package is recorded. A packaged title's version keeps it beside the package
(originalFiles ["original.<ext>"], the package derived); a title nothing packaged yet is taken in:
its version holds version.json and the original alone — no checksums, no package.json, no .complete
— until its first package is added to it. For an item whose original was already gone the source
record has no fixity, its probe.note says why, the version keeps none (originalFiles [], the package
canonical), and the adopt writes the original-deleted event (reason "gone before the library was
recorded", accepted [] — the gate was never measured). A subtitle file the scanner paired with the
original is copied into the source folder under the name the library gives it,
subtitle-<n>.<lang>[.forced][.sdh].<ext>, and the package's subtitle made from it — its manifest
marks it external; they are paired by the order katalog-manager handed the packager the files, by
path, and by language — names the copy as fromSidecar; the file itself goes to the arrivals,
<root>/.work/incoming/, at the path it had under media/. Nothing else beside the original is copied
or moved: a .nfo, an image or a text file is no part of the record, and stays for the cleanup. An
extra keeps no original: it goes to the arrivals, <root>/.work/extras/ (or .work/incoming/ from
media/), at the path it had, until its package is recorded. Extras are the catalog's own rows (the
export's extras), each named by its row's id and staged when its package finished; one without a
finished package stays the catalog's, its original among the arrivals. An episode under a season is
in its series' folder; music and anything else the library does not record is skipped. Ids are
UUIDv5 of the item and a stable name — a source's is the database's own when it already has one — so
staging again writes the same records; an item the library holds already, or whose package the
database holds as complete, is not staged again.

A unit plan says, for katalog-manager's adopt (POST /api/library/migrations/<run>/adopt), what to
move and what to change, all paths absolute as the run saw them — run it with --root at the services'
own path of the share:

  {"schema": "zaentrum.migration.unit/1", "run", "itemId", "type", "itemDir", "stagedDir",
   "moves": [{"kind", "from", "to"}, …]       renames, in this order, each undone in reverse:
       package  an old package folder's hls/ subs/ trickplay/, and an extra's, into the staged records
       publish  the staged item folder to itemDir (an episode's into its series' folder, which the
                series' unit put in place first: series before episodes)
       original the original into its version folder, itemDir/versions/<versionId>/original.<ext>, once
                the item is in place; and an extra's original to the arrivals
       sidecar  a subtitle file the scanner paired with the original, of which the source keeps a
                copy, to the arrivals, at the path it had under media/
       legacy   what is left of an old package folder (manifest.json, .complete) to the run's legacy/
   "guards": {"manifestSha256", "completeMtime", "listingSha256"}   checked again before the first
       move — when one differs the plan is stale and the item is staged again:
       manifestSha256  sha256:<hex> of the old package folder's manifest.json; null without one
       completeMtime   the modification time of its .complete, RFC 3339 in UTC with nine digits of
                       its fraction; null without a package
       listingSha256   sha256:<hex> of one "<size> <path>\n" line per file under each package move's
                       from — every regular file, the folders in the moves' order, one folder's files
                       by their path within it; null without a package move
   "db": {"recordedAt", "projectedDatabaseUpdatedAt",   when the item was recorded; the export's
                                                          modifiedAt of the row, to the second, which
                                                          its staged metadata.json reflects
          "sources": [{"sourceId", "filename", "arrivalPath", "libraryPath", "sizeBytes", "qh1",
                       "recordDir", "sidecars": [{"subtitleAssetId", "rendition", "path"}]}],
                       filename the name the library gives the original, arrivalPath where it lies once
                       adopted, its version folder, and libraryPath null: the catalog keeps library paths
                       only once a version is established; arrivalPath and qh1 null for an original gone
                       before the library was recorded
          "versions": [{"versionId", "packageId", "dir", "completedAt", "sourceIds", "verifiedAt",
                        "verifiedLevel", "takenIn"}]   verifiedLevel full, every byte was hashed by the
                       stage, and takenIn false; a version taken in — recorded with no package — has
                       packageId, completedAt, verifiedAt and verifiedLevel null, and takenIn true
          "assets": [{"id", "path", "sourceId"} | {"id", "path", "versionId"}]   the original's row at
                       its version folder — or, gone before, at its source folder, a retired one — and the
                       packaged row at <dir>/package.json; any other packaged row of the item goes
          "subtitles": [{"id", "path"}]   the package's rows in the version folder, the sidecars' at the
                       arrivals — or, the original gone before, where a retire points them
          "extras": [{"id", "dir", "packageId", "sourcePath"}]   dir and packageId null for an extra
                       that is not packaged; sourcePath its original among the arrivals, or null},
   "problems": [{"class", "detail"}]}

The problem classes: missing original, missing .complete, several packages, episode without series,
episode without numbers, season parent, music, not a library item, no title, unmappable sidecar,
subtitle row without its file, original outside the media root, dropped external-id source, artwork
not an image, extra not packaged, extra of an episode, extra without an id, extra original gone.
--dry-run writes report.json and nothing else, and hashes no package file. --shard narrows the run to
the items whose folder is in that shard — left(coalesce(seriesId, id), 2), as the export narrows by
it: run each shard of one run with the same --as-of, so the people two shards credit are staged alike.
"""
import argparse, base64, datetime, hashlib, json, os, re, shutil, sys

try:  # the record logic the packager writes the same records with: beside this tool, or piped in front of it
    from libv2_records import (
        EXTRA_DIRS, LANGUAGE_RE, PACKAGE_DIRS, UUID_RE, chapter_marks, checksums, complete, deletion_gate, did,
        external_ids, extra_record, ffprobe, ffprobe_version, have_ffprobe, is_moment, json_bytes,
        library_original_name, listdir, num, package_files, package_record, peak_bandwidth, primary_language,
        probe_chapters, qh1, record_entry, segments, sha_file, sidecar_entry, source_record, text, ts, ts_of_mtime,
        version_record)
except ImportError:
    if "LIBV2_RECORDS" not in globals():
        sys.exit("library-v2-from-catalog.py needs libv2_records.py: run it from tools/, or pipe the two together, "
                 "cat tools/libv2_records.py tools/library-v2-from-catalog.py | python3 - …")

IMAGE_KINDS = {"poster", "backdrop", "logo", "still", "banner", "thumb"}
PERSON_IMAGE_KINDS = {"profile"}
# A person's reference ids: the source an export names, the field person.json keys it under, and the
# form the value must have.
PERSON_IDS = {"tmdb": ("tmdbPerson", r"[0-9]+"), "themoviedb": ("tmdbPerson", r"[0-9]+"),
              "tmdb-person": ("tmdbPerson", r"[0-9]+"), "tmdbperson": ("tmdbPerson", r"[0-9]+"),
              "imdb": ("imdb", r"nm[0-9]+"), "tvdb": ("tvdb", r"[0-9]+"), "wikidata": ("wikidata", r"Q[0-9]+")}
# A credit's role is a token of an open vocabulary. These are the roles a reader knows, in the order
# it lists them; any other token is a role too, listed after them.
ROLES = ("actor", "creator", "director", "writer", "producer", "composer", "cinematographer", "editor")
ROLE_RE = re.compile(r"[a-z][a-z0-9-]{0,39}")
DATE_RE = re.compile(r"^([0-9]{4})(-[0-9]{2}(-[0-9]{2})?)?")
# Where a projection's field came from, as fieldOrigins in defs.schema.json names it.
FIELD_ORIGINS = {"tmdb", "legacy-catalog", "filename", "folder-name", "file-tags", "manual"}
EXT_OF = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


# ---------------------------------------------------------------- small helpers
def link_of(t):
    """The site and the key a trailer link is listed under in videos[]: its site, or failing that the
    source it was found in, and the site's id for it, or failing that the last part of its url. An
    extra downloaded from the link names it the same way, so a rebuild finds one from the other."""
    site = text(t.get("site")) or text(t.get("source"))
    key = text(t.get("externalId"))
    if not key and t.get("url"):
        key = text(str(t["url"]).rsplit("/", 1)[-1])
    return site, key


def credit_order(c):
    """Where a credit stands in a projection: by role — the ones a reader knows in the order it lists
    them, any other after them, alphabetically — then by billing order, a credit without one last,
    then by name and personId. The export lists credits in no particular order; sorting them is what
    makes the same credits the same file."""
    rank = ROLES.index(c["role"]) if c["role"] in ROLES else len(ROLES)
    return (rank, c["role"], c["order"] is None, c["order"] or 0, c["name"], c["personId"])


def image_order(img):
    """Where an image stands in metadata.json: by kind, alphabetically, then by season — the series'
    own images first — then by file name, the hash of its bytes. The export lists an item's artwork in
    no particular order; sorting it is what makes the same images the same file."""
    return (img["kind"], img.get("season") is not None, img.get("season") or 0, img["file"])


# ---------------------------------------------------------------- images
def sniff_image(b):
    """(content type, width, height) from the header alone; (None, None, None) when unrecognised."""
    if b[:8] == b"\x89PNG\r\n\x1a\n" and len(b) >= 24:
        return "image/png", int.from_bytes(b[16:20], "big"), int.from_bytes(b[20:24], "big")
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        chunk = b[12:16]
        if chunk == b"VP8X" and len(b) >= 30:
            return "image/webp", 1 + int.from_bytes(b[24:27], "little"), 1 + int.from_bytes(b[27:30], "little")
        if chunk == b"VP8 " and len(b) >= 30:
            return "image/webp", int.from_bytes(b[26:28], "little") & 0x3FFF, int.from_bytes(b[28:30], "little") & 0x3FFF
        if chunk == b"VP8L" and len(b) >= 25:
            v = int.from_bytes(b[21:25], "little")
            return "image/webp", (v & 0x3FFF) + 1, ((v >> 14) & 0x3FFF) + 1
        return "image/webp", None, None
    if b[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(b):
            if b[i] != 0xFF:
                i += 1
                continue
            m = b[i + 1]
            if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                return "image/jpeg", int.from_bytes(b[i + 7:i + 9], "big"), int.from_bytes(b[i + 5:i + 7], "big")
            if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
                i += 2
                continue
            i += 2 + int.from_bytes(b[i + 2:i + 4], "big")
        return "image/jpeg", None, None
    return None, None, None


# ---------------------------------------------------------------- writing
class Writer:
    """Every record is written to a temporary file in its own folder and renamed into place, so a
    reader never sees half a file. --dry-run reports the same work and touches nothing."""

    def __init__(self, dry_run):
        self.dry_run = dry_run
        self.written = self.placed = self.bytes_placed = 0

    def mkdir(self, d):
        if not self.dry_run:
            os.makedirs(d, exist_ok=True)

    def write(self, path, data):
        self.written += 1
        if self.dry_run:
            return
        self.mkdir(os.path.dirname(path))
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)

    def write_json(self, path, doc):
        self.write(path, json_bytes(doc))

    def covered(self, folder, files):
        """Write-once files and, with them, the checksums.sha256 that lists exactly those files: the
        hashes come from the bytes being written, so a dry run computes the same ones."""
        for name in sorted(files):
            self.write(os.path.join(folder, name), files[name])
        self.write(os.path.join(folder, "checksums.sha256"),
                   checksums([record_entry(n, files[n]) for n in files])[0])

    def place(self, src, dst, mode):
        """Move or copy one file into the library. Re-running is safe: a file already in place with
        the same size is left alone."""
        if os.path.isfile(dst) and (not os.path.isfile(src) or os.path.getsize(dst) == os.path.getsize(src)):
            return True
        if not os.path.isfile(src):
            return False
        self.placed += 1
        self.bytes_placed += os.path.getsize(src)
        if self.dry_run:
            return True
        self.mkdir(os.path.dirname(dst))
        if mode == "move":
            shutil.move(src, dst)
        else:
            shutil.copy2(src, dst)
        return True

    def prune(self, d, keep):
        """Drop files in a folder the record no longer names — a projection replaces the whole set."""
        if self.dry_run or not os.path.isdir(d):
            return
        for name in listdir(d):
            if name not in keep and os.path.isfile(os.path.join(d, name)):
                os.unlink(os.path.join(d, name))


# ---------------------------------------------------------------- the build
class Build:
    def __init__(self, args, export):
        self.a = args
        self.export = export
        self.w = Writer(args.dry_run)
        # --projections-only: write the projections of what the tree holds, and nothing that is written once
        self.projecting = bool(getattr(args, "projections_only", False))
        self.as_of = ts(args.as_of or export.get("exportedAt")) or ts(datetime.datetime.now(
            datetime.timezone.utc).replace(microsecond=0).isoformat())
        self.probe_version = ffprobe_version() if have_ffprobe() else None
        self.notes = []
        self.skipped = []
        self.counts = {"items": 0, "people": 0, "sources": 0, "versions": 0, "packages": 0, "extras": 0, "images": 0,
                       "probed": 0}

    def note(self, item_id, msg):
        self.notes.append(f"{item_id}: {msg}")

    # -------------------------------------------------- paths
    def under(self, base, path, marker):
        """The catalog holds the path as the services see it; the share may be mounted elsewhere."""
        if os.path.exists(path):
            return path
        p = str(path).replace("\\", "/")
        i = p.rfind("/" + marker + "/")
        if i >= 0:
            return os.path.join(base, p[i + len(marker) + 2:])
        return os.path.join(base, os.path.basename(p))

    def item_dir(self, row, by_id):
        iid = row["id"]
        if row["type"] == "movie":
            return os.path.join(self.a.out, "movies", iid[:2], iid)
        if row["type"] == "series":
            return os.path.join(self.a.out, "series", iid[:2], iid)
        parent = row.get("parentId")
        return os.path.join(self.a.out, "series", parent[:2], parent, "episodes", iid)

    # -------------------------------------------------- item.json
    def item_json(self, row):
        ids, notes = external_ids(row.get("externalIds"), row["type"])
        for n in notes:
            self.note(row["id"], n)
        doc = {"schema": "zaentrum.library.item/2", "itemId": row["id"], "type": row["type"],
               "title": text(row.get("title")) or "", "externalIds": ids,
               "createdAt": ts(row.get("createdAt")) or self.as_of}
        if text(row.get("createdBy")):
            doc["createdBy"] = text(row["createdBy"])
        doc["provenance"] = {"migratedFrom": "legacy-catalog", "legacyItemId": row["id"], "migratedAt": self.as_of,
                             "legacyCreatedBy": text(row.get("createdBy"))}
        if row["type"] == "episode":
            doc["seriesId"] = row["parentId"]
            doc["seasonNumber"] = int(row["seasonNumber"])
            doc["episodeNumber"] = int(row["episodeNumber"])
            doc["episodeCode"] = f"S{int(row['seasonNumber']):02d}E{int(row['episodeNumber']):02d}"
        return doc

    # -------------------------------------------------- metadata.json and the images
    def metadata_json(self, row, d, version_ids, episodes, extras=None):
        lg = self.a.text_language
        localized = {}
        body = {}
        if text(row.get("title")):
            body["title"] = text(row["title"])
        if text(row.get("sortTitle")):
            body["sortTitle"] = text(row["sortTitle"])
        if row.get("tagline"):
            body["tagline"] = str(row["tagline"]).strip()
        if row.get("description"):
            body["overview"] = str(row["description"]).strip()
        if body:
            localized[lg] = body
        ids = self.item_json(row)["externalIds"]
        doc = {"schema": "zaentrum.library.metadata/2", "itemId": row["id"], "type": row["type"],
               "asOf": self.as_of, "projectedBy": "library-v2-from-catalog", **self.freshness(row["id"], row),
               "externalIds": ids,
               "titles": {"primary": text(row.get("title")) or "", "original": None,
                          "sort": text(row.get("sortTitle")), "qualifier": None, "localized": localized},
               "releaseDate": str(row["year"]) if row.get("year") else None,
               "genres": sorted({g for g in (row.get("genres") or []) if g}),
               "tags": sorted({t for t in (row.get("tags") or []) if t}),
               "rating": row["rating"] if isinstance(row.get("rating"), (int, float)) and 0 <= row["rating"] <= 10 else None,
               "contentRating": None}
        credits = []
        for p in row.get("people") or []:
            pid = text(p.get("personId"))
            if not pid or not re.fullmatch(r"[0-9a-f-]{36}", pid):
                continue
            name, role = text(p.get("name")) or "", text(p.get("role")) or "actor"
            if not ROLE_RE.fullmatch(role):
                self.note(row["id"], f"{name or pid!r} is credited as {role!r}, which is not a role token "
                                     f"('actor', 'production-designer'), so the credit is dropped")
                continue
            # an export from before credits were general carries none of these: each stays null
            credits.append({"personId": pid, "name": name, "role": role, "job": text(p.get("job")),
                            "character": text(p.get("character")),
                            "order": self.whole(row["id"], f"the billing order of {name!r} as {role}", p.get("order")),
                            "episodeCount": self.whole(row["id"], f"the episode count of {name!r} as {role}",
                                                       p.get("episodeCount"), least=0),
                            "tmdbPerson": None})
        doc["credits"] = sorted(credits, key=credit_order)
        if row["type"] == "movie":
            doc["collection"] = None
        if row["type"] == "series":
            seasons = sorted({int(e["seasonNumber"]) for e in episodes if e.get("seasonNumber") is not None})
            doc["series"] = {"seasons": [{"number": n, "tmdbSeason": None, "name": None, "overview": None,
                                          "airDate": None, "episodeCountReference": None} for n in seasons]}
        library = {"match": {"status": "matched" if any(k.startswith("tmdb") for k in ids) else "unmatched",
                             "decidedBy": "legacy-catalog", "decidedAt": self.as_of}}
        if version_ids:
            library["primaryVersionId"] = version_ids[0]
        if row.get("durationMs"):
            library["reference"] = {"runtimeMs": int(row["durationMs"]), "runtimeSource": "legacy-catalog"}
        if row["type"] == "series":
            library["defaultOrdering"] = "aired"
        if row["type"] == "episode":
            library["numbering"] = {"aired": {"season": int(row["seasonNumber"]),
                                              "episode": int(row["episodeNumber"]), "episodeEnd": None}}
        if extras and row["type"] in ("movie", "series"):
            library["extras"] = extras  # how a viewer is shown the extras: decisions of the database
        doc["library"] = library
        doc["images"] = self.images(row, d)
        doc["videos"] = self.videos(row)
        doc["curation"] = {"metadataLocked": bool(row.get("metadataLocked")), "lockedFields": [], "notes": None}
        origins = {}
        for field, value in (("titles.primary", doc["titles"]["primary"]), ("titles.sort", doc["titles"]["sort"]),
                             ("releaseDate", doc["releaseDate"]), ("genres", doc["genres"]), ("tags", doc["tags"]),
                             ("rating", doc["rating"]), ("credits", doc["credits"]), ("images", doc["images"]),
                             ("videos", doc["videos"])):
            if value:
                origins[field] = "legacy-catalog"
        doc["fieldOrigins"] = origins
        return doc

    def images(self, row, d):
        md = os.path.join(d, "metadata")
        out, names = self.artwork(row["id"], row.get("artwork") or [], md, IMAGE_KINDS, "artwork", "a v2 record",
                                  series=row["type"] == "series")
        if not self.projecting:  # a projection run leaves an image it dropped to the sweep
            self.w.prune(md, names)
        return sorted(out, key=image_order)

    def artwork(self, owner, entries, folder, kinds, label, holder, series=False):
        """The images a projection lists, from the export's artwork: one entry for every row, of its
        kind, its file written into folder once and named by the hash of its own bytes, which decide
        its type and size whatever the row says. Byte-identical rows of two kinds — a backdrop that is
        the poster — are two entries sharing one file, so the record keeps that the title has both;
        byte-identical rows of one kind are one entry, primary when either is. An image is primary
        where the row's isPrimary says so — at most one of a kind, the first the export lists — and
        its origin is TMDB with the sourcePath it was fetched from when the row has one, the catalog
        when it does not. Returns the entries and the names of the files they list."""
        out, by_entry, files = [], {}, set()
        for art in entries:
            art = art if isinstance(art, dict) else {}
            kind = (art.get("kind") or "").lower()
            if kind not in kinds:
                self.note(owner, f"{label} kind {kind!r} is not one {holder} holds, dropped")
                continue
            try:
                raw = base64.b64decode(art.get("base64") or "", validate=False)
            except (ValueError, TypeError):
                raw = b""
            if not raw:
                self.note(owner, f"{kind} artwork has no bytes, dropped")
                continue
            ctype, w, h = sniff_image(raw)
            if ctype not in EXT_OF:
                self.note(owner, f"{kind} artwork is not a JPEG, PNG or WebP, dropped")
                continue
            digest = hashlib.sha256(raw).hexdigest()
            name = f"{digest}.{EXT_OF[ctype]}"
            primary = art.get("isPrimary") is True
            if (name, kind) in by_entry:
                self.note(owner, f"{kind} artwork is byte-identical to another {kind}, listed once")
                if primary:
                    by_entry[(name, kind)]["primary"] = True
                continue
            recorded = str(art.get("sha256") or "").strip().lower()
            if recorded and recorded.split(":", 1)[-1] != digest:
                self.note(owner, f"{kind} artwork's sha256 {recorded!r} is not the hash of its bytes; the record "
                                 f"names the bytes")
            said = [(k, art[k], got) for k, got in (("contentType", ctype), ("width", w), ("height", h))
                    if art.get(k) is not None and (art[k] if k == "contentType" else num(art[k])) != got]
            if said:
                self.note(owner, f"{kind} artwork says " + ", ".join(f"{k} {v!r}" for k, v, _ in said)
                                 + " where its bytes say " + ", ".join(repr(got) for _, _, got in said)
                                 + "; the record says what the bytes say")
            if name not in files and not (self.projecting and os.path.isfile(os.path.join(folder, name))):
                # an image is written once — the file another kind shares too — and a projection run
                # leaves one already there as it is
                self.w.write(os.path.join(folder, name), raw)
                self.counts["images"] += 1
            files.add(name)
            fetched = self.moment(owner, f"the {kind} artwork's fetchedAt", art.get("fetchedAt"))
            ref = text(art.get("sourcePath"))
            entry = {"kind": kind, **({"primary": True} if primary else {}), "file": name,
                     "sha256": "sha256:" + digest, "contentType": ctype, "sizeBytes": len(raw), "width": w,
                     "height": h, "language": None, "sourceUrl": None, "fetchedAt": fetched,
                     "origin": {"source": "tmdb" if ref else "legacy-catalog", **({"ref": ref} if ref else {}),
                                **({"fetchedAt": fetched} if fetched else {})}}
            if series:
                entry["season"] = None
            out.append(entry)
            by_entry[(name, kind)] = entry
        first = {}
        for e in out:
            if not e.get("primary"):
                continue
            if e["kind"] in first:
                del e["primary"]
                self.note(owner, f"a second primary {e['kind']}, {e['file'][:12]}…, is not primary: at most one of a "
                                 f"kind is, and {first[e['kind']][:12]}…, which the export lists first, stays it")
            else:
                first[e["kind"]] = e["file"]
        return out, files

    def moment(self, owner, field, v):
        """A timestamp as a record holds one, or None — with a note when the export holds something
        that is not a moment: a record never carries a guess."""
        s = ts(v)
        if s and not is_moment(s):
            self.note(owner, f"{field} {v!r} is not a moment, dropped")
            return None
        return s

    def whole(self, owner, what, v, least=None):
        """A whole number as a record holds one, or None when the export holds none — with a note
        when it holds something else, a fraction or a count below least: a record never carries a
        guess. 3.0 is 3."""
        if v is None:
            return None
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        if isinstance(v, int) and not isinstance(v, bool) and -(1 << 63) <= v < (1 << 63) \
                and (least is None or v >= least):
            return v
        self.note(owner, f"{what} {v!r} is not a whole number" + (f" of at least {least}" if least is not None else "")
                  + ", dropped")
        return None

    def freshness(self, owner, entry):
        """How fresh a projection is, from the row it projects: the row's modifiedAt is the
        databaseUpdatedAt, the state of the row the projection reflects, and tmdbFetchedAt with
        tmdbChangedAt the tmdb entry of sources — only with a tmdbFetchedAt, because a source entry
        says when the database fetched from it. What the export does not carry is not written."""
        out = {}
        updated = self.moment(owner, "modifiedAt", entry.get("modifiedAt"))
        if updated:
            out["databaseUpdatedAt"] = updated
        fetched = self.moment(owner, "tmdbFetchedAt", entry.get("tmdbFetchedAt"))
        changed = self.date(owner, "tmdbChangedAt", entry.get("tmdbChangedAt"))
        if fetched:
            out["sources"] = {"tmdb": {"fetchedAt": fetched, **({"changedAt": changed} if changed else {})}}
        elif changed:
            self.note(owner, f"tmdbChangedAt {changed} without a tmdbFetchedAt is not recorded: a source entry says "
                             f"when the database fetched from it")
        return out

    def videos(self, row):
        out = []
        for t in row.get("trailers") or []:
            site, key = link_of(t)
            if not site or not key:
                self.note(row["id"], "a trailer names neither a site nor an id, dropped")
                continue
            source = (text(t.get("source")) or "").lower()
            out.append({"site": site, "key": key, "url": text(t.get("url")), "name": text(t.get("title")),
                        "kind": "trailer", "language": None,
                        "durationMs": int(t["durationSec"]) * 1000 if num(t.get("durationSec")) else None,
                        "publishedAt": None,
                        "origin": source if source in ("tmdb", "manual") else "legacy-catalog"})
        return out

    # -------------------------------------------------- sources
    def source(self, row, d, asset):
        """One original as the file itself reports it. Returns ({record, probe, chapters, path}, None) —
        the source record, the bytes of the probe written beside it, the chapter marks the file itself
        carries and where it is on disk — or (None, why)."""
        path = self.under(self.a.media, asset["path"], "media")
        if not os.path.isfile(path):
            return None, f"original {asset['path']} is not on this share"
        name = os.path.basename(path)
        rel = os.path.relpath(path, self.a.media) if path.startswith(os.path.abspath(self.a.media) + os.sep) \
            else str(asset["path"]).lstrip("/")
        probe = ffprobe(path) if self.probe_version else None
        if probe:
            self.counts["probed"] += 1
        else:
            self.note(row["id"], f"{name} was not probed: streams, fidelity and essence stay empty")
        rec, raw = source_record(did(row["id"], "source", name), name, os.path.getsize(path), taken_at=self.as_of,
                                 taken_by="library-v2-from-catalog", library_path=rel, qh1=qh1(path),
                                 mtime=ts_of_mtime(path), probe=probe, probe_version=self.probe_version)
        return {"record": rec, "probe": raw, "chapters": probe_chapters(probe), "path": path}, None

    # -------------------------------------------------- versions and packages
    def version(self, row, d, asset, source, index):
        pkg_manifest_path = self.under(self.a.packages, asset["path"], "packages")
        pkg_dir = os.path.dirname(pkg_manifest_path)
        if not os.path.isdir(pkg_dir):
            return None, f"package folder {asset['path']} is not on this share"
        if not os.path.isfile(os.path.join(pkg_dir, ".complete")):
            return None, f"package {os.path.relpath(pkg_dir, self.a.packages)} has no .complete marker"
        man = {}
        if os.path.isfile(pkg_manifest_path):
            try:
                man = json.load(open(pkg_manifest_path, encoding="utf-8"))
            except (OSError, ValueError) as e:
                return None, f"package manifest could not be read: {e}"
        vid = did(row["id"], "version", os.path.relpath(pkg_dir, self.a.packages))
        pid = did(row["id"], "package", os.path.relpath(pkg_dir, self.a.packages))
        vp = os.path.join(d, "versions", vid)

        rec = source["record"]
        keep_original = self.a.media_mode in ("copy", "move")
        name = rec["file"]["name"]
        marks, marks_from = chapter_marks(row.get("chapters")), "legacy-catalog"
        if not marks:
            marks, marks_from = source["chapters"], "original-file"
        version = version_record(vid, rec, created_at=ts(man.get("packagedAt")) or self.as_of,
                                 created_by="library-v2-from-catalog", chapters=marks, chapters_from=marks_from,
                                 segments=segments(row.get("segments")),
                                 original_files=[name] if keep_original else [])

        # the package's own files, moved or copied in. The store's .complete only says the package
        # finished; the version's is written last and holds the hash of package.json.
        entries, missing = package_files(pkg_dir), []
        for entry in listdir(pkg_dir):
            if entry not in PACKAGE_DIRS and entry not in (".complete", "manifest.json", ".packaging"):
                self.note(row["id"], f"package holds {entry!r}, which is not part of a v2 version folder; left behind")
        self.w.mkdir(vp)
        for rel, _, _ in entries:
            if not self.w.place(os.path.join(pkg_dir, rel), os.path.join(vp, rel),
                                self.a.media_mode if self.a.media_mode != "none" else "copy"):
                missing.append(rel)
        if missing:
            return None, f"package files could not be placed: {', '.join(missing[:3])}"
        if keep_original:
            if not self.w.place(source["path"], os.path.join(vp, name), self.a.media_mode):
                return None, f"original {name} could not be placed"

        # the chain, from the bottom up: the version record, the checksums over it and every package
        # file, package.json with their hash, and .complete with package.json's.
        version_bytes = json_bytes(version)
        listed = entries + [record_entry("version.json", version_bytes)]
        package, notes = package_record(
            pid, man, listed, source=rec, role="derived" if keep_original else "canonical", created_at=self.as_of,
            duration_ms=asset.get("durationMs"),
            peak_bandwidth_bps=peak_bandwidth(pkg_dir) or (num(asset.get("bitrateKbps")) or 0) * 1000 or None)
        for n in notes:
            self.note(row["id"], n)
        package_bytes = json_bytes(package)
        self.w.write(os.path.join(vp, "version.json"), version_bytes)
        self.w.write(os.path.join(vp, "checksums.sha256"), checksums(listed)[0])
        self.w.write(os.path.join(vp, "package.json"), package_bytes)
        self.w.write(os.path.join(vp, ".complete"), complete(package_bytes))
        if self.a.media_mode == "move" and not self.a.dry_run and os.path.isfile(os.path.join(pkg_dir, ".complete")):
            os.unlink(os.path.join(pkg_dir, ".complete"))
        self.counts["versions"] += 1
        self.counts["packages"] += 1
        return vid, None

    # -------------------------------------------------- bonus material
    def extras(self, row, d):
        """A trailer the catalog downloaded — a link whose localPath is a file on this share — becomes an
        extra of kind trailer beside its movie or series: extra.json, the file copied or moved in like
        an original, under the name the library gives an original, and the checksums over both, written
        last because they say the extra is finished. The link stays in metadata.json's videos, where it
        is still published, and titles the extra; a link with no title titles it Trailer, because the
        record keeps nothing of the name the file came with. Returns how many."""
        written, names = 0, set()
        for t in row.get("trailers") or []:
            local = str(t.get("localPath") or "").strip()
            if not local:
                continue
            name = os.path.basename(local.replace("\\", "/"))
            path = self.under(self.a.media, local, "media")
            if row["type"] == "episode":
                self.note(row["id"], f"trailer {name} stays a link: an episode has no extras, they are its series'")
                continue
            if not os.path.isfile(path):
                self.note(row["id"], f"trailer {local} is not on this share, so it stays a link")
                continue
            if self.a.media_mode == "none":
                self.note(row["id"], f"trailer {name} stays a link: --media-mode none leaves its file where it is, "
                                     f"and an extra holds its own file")
                continue
            if name in names:
                self.note(row["id"], f"trailer {name!r} is another link's file too, so it stays a link")
                continue
            names.add(name)
            xid = did(row["id"], "extra", name)
            xp = os.path.join(d, "extras", xid)
            kept = library_original_name(name)
            digest, size = sha_file(path), os.path.getsize(path)
            site, key = link_of(t)
            probe = ffprobe(path) if self.probe_version else None
            if probe:
                self.counts["probed"] += 1
            else:
                self.note(row["id"], f"trailer {name} was not probed: its streams, fidelity and essence stay empty")
            doc = extra_record(xid, created_at=self.as_of, created_by="library-v2-from-catalog", kind="trailer",
                               title=text(t.get("title")) or "Trailer",
                               origin={"kind": "link", **{k: v for k, v in (
                                   ("site", site), ("externalId", key), ("url", text(t.get("url"))),
                                   ("fetchedAt", ts(t.get("fetchedAt")))) if v}},
                               probe=probe, probe_version=self.probe_version, original_files=[kept],
                               originals=[{"name": kept, "sizeBytes": size,
                                           "fixity": {"qh1": qh1(path), "sha256": digest, "sha256At": self.as_of}}])
            record = json_bytes(doc)
            self.w.write(os.path.join(xp, "extra.json"), record)
            if not self.w.place(path, os.path.join(xp, kept), self.a.media_mode):
                self.note(row["id"], f"trailer {name} could not be placed; its extra never finished")
                continue
            self.w.write(os.path.join(xp, "checksums.sha256"),
                         checksums([record_entry("extra.json", record), (kept, digest.split(":", 1)[1], size)])[0])
            written += 1
        self.counts["extras"] += written
        return written

    # -------------------------------------------------- one item
    def build(self, row, by_id, episodes):
        d = self.item_dir(row, by_id)
        item = self.item_json(row)
        if not item["title"]:
            self.skipped.append((row["id"], "the row has no title, and a record must carry the one it was created with"))
            return
        if row["type"] == "episode" and (row.get("seasonNumber") is None or row.get("episodeNumber") is None
                                         or not row.get("parentId")):
            self.skipped.append((row["id"], "an episode row without a series, a season or an episode number "
                                            "cannot be an episode record"))
            return
        self.w.mkdir(d)
        version_ids = []
        if row["type"] != "series":
            primary = next((a for a in row.get("playbackAssets") or [] if a.get("kind") == "primary"), None)
            packaged = [a for a in row.get("playbackAssets") or [] if a.get("kind") == "packaged"]
            source = None
            if primary:
                source, why = self.source(row, d, primary)
                if source is None:
                    self.note(row["id"], why)
            if source is None and packaged:
                self.note(row["id"], "no original could be read, so its versions and packages are not recorded: "
                                     "a version names a source record, and a source record carries the file's fixity")
            elif source is not None:
                files = {"source.json": json_bytes(source["record"])}
                if source["probe"] is not None:
                    files["ffprobe.json"] = source["probe"]
                self.w.covered(os.path.join(d, "sources", source["record"]["sourceId"]), files)
                self.counts["sources"] += 1
                for i, asset in enumerate(sorted(packaged, key=lambda a: str(a.get("path")))):
                    vid, why = self.version(row, d, asset, source, i)
                    if vid:
                        version_ids.append(vid)
                    else:
                        self.note(row["id"], why)
        self.extras(row, d)
        self.w.covered(d, {"item.json": json_bytes(item)})
        self.w.write_json(os.path.join(d, "metadata.json"),
                          self.metadata_json(row, d, version_ids, episodes))
        self.counts["items"] += 1

    # -------------------------------------------------- only the projection of an item the tree holds
    def project(self, row, by_id, episodes):
        """The projection of an item the tree already holds — metadata.json and the images it lists —
        replaced whole from the export, and nothing else: no record is written, rewritten or touched.
        An item the tree does not hold is skipped with a note: creating one is the full build's job."""
        if row.get("type") not in ("movie", "series", "episode") or (row["type"] == "episode" and not row.get("parentId")):
            self.note(row["id"], "is not an item a tree can hold, so it has no projection to write")
            return
        d = self.item_dir(row, by_id)
        record = os.path.join(d, "item.json")
        if not os.path.isfile(record):
            self.note(row["id"], "has no folder on storage, so no projection of it is written: creating an item is "
                                 "the full build's job")
            return
        try:
            held = json.load(open(record, encoding="utf-8")).get("itemId")
        except (OSError, ValueError, AttributeError) as e:
            self.note(row["id"], f"its item.json cannot be read ({e}), so no projection of it is written")
            return
        if held != row["id"]:
            self.note(row["id"], f"its folder's item.json names {held}, so no projection of it is written")
            return
        self.w.write_json(os.path.join(d, "metadata.json"),
                          self.metadata_json(row, d, self.stored_versions(row, d), episodes))
        self.counts["items"] += 1

    def stored_versions(self, row, d):
        """The versions of an item on storage, for its projection's primaryVersionId. Which version plays
        when the viewer does not choose is a decision the export does not carry, so it stays the one
        the projection on storage names while that version is there; otherwise it is the first the
        export's packaged assets name, as the full build named its versions — of those on storage."""
        live = self.live_versions(d)
        try:
            current = (json.load(open(os.path.join(d, "metadata.json"), encoding="utf-8")).get("library") or {}) \
                .get("primaryVersionId")
        except (OSError, ValueError, AttributeError):
            current = None
        if current in live:
            return [current]
        packaged = sorted((a for a in row.get("playbackAssets") or [] if a.get("kind") == "packaged"),
                          key=lambda a: str(a.get("path")))
        return [v for v in (did(row["id"], "version", self.store_path(a.get("path") or "")) for a in packaged)
                if v in live]

    def live_versions(self, d):
        """The version folders of an item on storage that hold their record and that no version-removed
        event retired: the versions a projection may name."""
        base, events = os.path.join(d, "versions"), os.path.join(d, "events")
        found = {n for n in (listdir(base) if os.path.isdir(base) else [])
                 if os.path.isfile(os.path.join(base, n, "version.json"))}
        for name in (listdir(events) if os.path.isdir(events) else []):
            try:
                ev = json.load(open(os.path.join(events, name, "event.json"), encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(ev, dict) and ev.get("kind") == "version-removed":
                found.discard(ev.get("versionId"))
        return found

    def store_path(self, path):
        """A packaged asset's package folder relative to the store root, the name the full build
        derives a version's id from. With --packages it is found as the build finds it; without, it is
        the part of the catalog's path after /packages/, which is what the build finds wherever the
        store is mounted."""
        if getattr(self.a, "packages_given", True):
            return os.path.relpath(os.path.dirname(self.under(self.a.packages, path, "packages")), self.a.packages)
        p = str(path).replace("\\", "/")
        i = p.rfind("/packages/")
        return os.path.dirname(p[i + len("/packages/"):] if i >= 0 else os.path.basename(p)) or "."

    # -------------------------------------------------- people
    def person_dir(self, pid):
        return os.path.join(self.a.out, "people", pid[:2], pid)

    def people(self, rows, everyone):
        """One person.json per person the rows credit — and, when the export lists its people and
        the run is not narrowed to some items, per person it lists. Returns how many were written."""
        listed = {str(p["id"]): p for p in self.export.get("people") or [] if isinstance(p, dict) and p.get("id")}
        names = {}
        for row in rows:
            for c in row.get("people") or []:
                pid = text(c.get("personId"))
                if pid and UUID_RE.match(pid):
                    names.setdefault(pid, [])
                    if text(c.get("name")) and text(c["name"]) not in names[pid]:
                        names[pid].append(text(c["name"]))
        wanted = sorted(set(names) | ({pid for pid in listed if UUID_RE.match(pid)} if everyone else set()))
        for pid in wanted:
            if self.projecting and not os.path.isfile(os.path.join(self.person_dir(pid), "person.json")):
                self.note(pid, "has no folder on storage, so no projection of them is written: --people-only writes "
                               "a person who is new")
                continue
            try:
                self.person(pid, listed.get(pid) or {}, names.get(pid) or [])
            except Exception as e:  # one unreadable person must not stop the run
                self.skipped.append((pid, f"{type(e).__name__}: {e}"))

    def person(self, pid, entry, credited_as):
        name = text(entry.get("name")) or (credited_as[0] if credited_as else None)
        if not name:
            self.skipped.append((pid, "a person record must carry a name, and nothing names this person"))
            return
        if len(credited_as) > 1:
            self.note(pid, f"credited under {len(credited_as)} names ({', '.join(map(repr, credited_as))}); "
                           f"person.json says {name!r}")
        d = self.person_dir(pid)
        locked = entry.get("lockedFields") if isinstance(entry.get("lockedFields"), list) else []
        doc = {"schema": "zaentrum.library.person/2", "personId": pid, "asOf": self.as_of,
               "projectedBy": "library-v2-from-catalog", **self.freshness(pid, entry),
               "name": name, "sortName": text(entry.get("sortName")),
               "alsoKnownAs": sorted({text(x) for x in entry.get("alsoKnownAs") or [] if text(x)} - {name}),
               "birthDate": self.date(pid, "birthDate", entry.get("birthDate")),
               "deathDate": self.date(pid, "deathDate", entry.get("deathDate")),
               "birthPlace": text(entry.get("birthPlace")), "knownForDepartment": text(entry.get("knownForDepartment")),
               "biography": self.biography(pid, entry.get("biography")),
               "externalIds": self.person_ids(pid, entry.get("externalIds")),
               "images": self.person_images(pid, d, entry.get("artwork") or []),
               "curation": {"metadataLocked": bool(entry.get("metadataLocked")),
                            "lockedFields": sorted({text(x) for x in locked if text(x)}), "notes": None}}
        if isinstance(entry.get("fieldOrigins"), dict):
            doc["fieldOrigins"] = self.field_origins(pid, entry["fieldOrigins"])
        else:
            doc["fieldOrigins"] = {k: "legacy-catalog" for k in ("name", "sortName", "alsoKnownAs", "birthDate",
                                                                 "deathDate", "birthPlace", "knownForDepartment",
                                                                 "biography", "externalIds", "images") if doc[k]}
        self.w.write_json(os.path.join(d, "person.json"), doc)
        self.counts["people"] += 1

    def field_origins(self, pid, v):
        """Where each field came from, as the database records it — in place of the catalog's own
        guess. An origin a record cannot name is dropped with a note, never renamed."""
        out = {}
        for field, origin in v.items():
            key = text(field)
            if not key:
                continue
            if origin in FIELD_ORIGINS:
                out[key] = origin
            else:
                self.note(pid, f"fieldOrigins says {key} came from {origin!r}, which a record cannot name, dropped")
        return out

    def date(self, pid, field, v):
        """A date as the format holds one — a year, a year and month, or a day — from a date or the
        date part of a timestamp; anything else is dropped rather than guessed."""
        if not v:
            return None
        m = DATE_RE.match(str(v).strip())
        if not m:
            self.note(pid, f"{field} {v!r} is not a date, dropped")
            return None
        return m.group(0)

    def biography(self, pid, v):
        if not v:
            return {}
        if isinstance(v, dict):
            out = {}
            for k, body in v.items():
                if not LANGUAGE_RE.match(str(k)):
                    self.note(pid, f"biography in {k!r}, which is not a language, dropped")
                elif str(body or "").strip():
                    out[str(k)] = str(body).strip()
            return out
        return {self.a.text_language: str(v).strip()} if str(v).strip() else {}

    def person_ids(self, pid, v):
        pairs = v.items() if isinstance(v, dict) else \
            [((e or {}).get("source"), (e or {}).get("externalId")) for e in v or []]
        by_field = {field: pattern for field, pattern in PERSON_IDS.values()}
        out = {}
        for source, value in pairs:
            source, value = str(source or ""), text(value)
            field, pattern = (source, by_field[source]) if source in by_field else \
                PERSON_IDS.get(source.lower(), (None, None))
            if not value:
                continue
            if field is None:
                self.note(pid, f"person id source {source!r} has no person.json field, dropped")
            elif not re.fullmatch(pattern, value):
                self.note(pid, f"{value!r} is not a valid {field} id, dropped")
            else:
                out[field] = value
        return out

    def person_images(self, pid, d, artwork):
        """The portraits beside person.json, named by their own content, with which one is primary and
        where each came from. person.json is replaced whole, so an image the new one does not name is
        removed with it."""
        out, names = self.artwork(pid, artwork, d, PERSON_IMAGE_KINDS, "person artwork", "a person record")
        if not self.projecting:  # a projection run leaves a portrait it dropped to the sweep
            self.w.prune(d, names | {"person.json"})
        return out


# ---------------------------------------------------------------- --platform: the library staged from the old store
# The types of rows the library does not record, and so the migration skips.
MUSIC_TYPES = ("music", "album", "track", "artist", "song", "audiobook", "podcast")
# The subtitle files katalog-manager hands the packager beside an original (subtitleFilesOf): what a
# package's external subtitles were made from, so what they are mapped back to.
PACKAGER_SIDECARS = (".srt", ".vtt", ".ass", ".ssa")
PACKAGER_SIDECAR_MAX_BYTES = 50 * 1024 * 1024
# The notes that are also a problem of the item they are about, by the class the report counts them in.
PROBLEM_NOTES = (("has no v2 field, dropped", "dropped external-id source"),
                 ("is not a JPEG, PNG or WebP, dropped", "artwork not an image"))
GONE = ("the original was gone before the library was recorded: nothing could probe it or take its fixity, and "
        "it is deleted outside the record")


def listing_sha256(folders):
    """The guard katalog-manager checks again before it moves a unit's package folders: sha256 over one
    '<size> <path>' line, with a line break, per file under each folder — the folders in the order the
    moves name them, the files of one by their path within it — so a package that changed after it was
    staged is staged again, not adopted. Every regular file counts."""
    h = hashlib.sha256()
    for folder in folders:
        rels = []
        for base, dirs, files in os.walk(folder):
            rels += [os.path.relpath(os.path.join(base, f), folder) for f in files]
        for rel in sorted(rels):
            p = os.path.join(folder, rel)
            h.update(f"{os.path.getsize(p)} {p}\n".encode("utf-8"))
    return "sha256:" + h.hexdigest()


def mtime_ns(p):
    """A file's modification time as RFC 3339 in UTC with nine digits of its fraction, the precision
    katalog-manager reads it back with."""
    ns = os.stat(p).st_mtime_ns
    t = datetime.datetime.fromtimestamp(ns // 10 ** 9, datetime.timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%S") + f".{ns % 10 ** 9:09d}Z"


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Platform(Build):
    """--platform: every record of the items an export holds, staged under <root>/.work/migration/<run>/
    for katalog-manager to adopt: one folder per item under staged/<itemId>/item/, written as the item
    folder will hold it, the records of its package with the checksums computed from the package files
    where they are, and units/<itemId>.json, the plan of what the adopt moves where and what it changes
    in the database. It reads the old store and writes nothing outside the run's folder: the package
    files are hashed in place, the originals probed in place, and only small files are written — the
    records, the images the database holds and copies of the subtitle files. people/ is staged under
    staged-people/<personId>/. --dry-run writes report.json and nothing else, and hashes no package."""

    def __init__(self, args, export):
        super().__init__(args, export)
        self.root = args.root
        self.work = os.path.join(self.root, ".work")
        self.run_dir = os.path.join(self.work, "migration", args.run)
        self.by_id = {r["id"]: r for r in export.get("items") or [] if isinstance(r, dict) and r.get("id")}
        self.extras_of, self.sources_of = {}, {}
        for x in export.get("extras") or []:
            if isinstance(x, dict):
                self.extras_of.setdefault(x.get("itemId"), []).append(x)
        for s in export.get("sources") or []:
            if isinstance(s, dict):
                self.sources_of.setdefault(s.get("itemId"), []).append(s)
        # an item the database holds a complete version of has its record already
        self.recorded = {v.get("itemId") for v in export.get("versions") or []
                         if isinstance(v, dict) and v.get("state") == "complete"}
        self.entries, self.staged_rows, self.current = [], [], None
        self.gate, self.measured, self.unmeasured = {}, 0, 0
        self.counts.update(units=0, recorded=0, takenIn=0)

    # -------------------------------------------------- where everything is
    def series_of(self, row):
        """The series an episode's folder is in: its parent, or the parent of the season it is under.
        Returns (seriesId or None, whether it is found through a season)."""
        parent = self.by_id.get(row.get("parentId"))
        if parent and parent.get("type") == "season":
            series = self.by_id.get(parent.get("parentId"))
            return (series["id"] if series and series.get("type") == "series" else None), True
        return (parent["id"] if parent and parent.get("type") == "series" else None), False

    def shard_of(self, row):
        """The shard an item's folder is in, as the export narrows by it: left(coalesce(seriesId, id), 2)."""
        parent = self.by_id.get(row.get("parentId")) or {}
        key = None
        if row.get("type") == "episode":
            key = parent.get("parentId") if parent.get("type") == "season" else row.get("parentId")
        elif row.get("type") == "season":
            key = row.get("parentId")
        return str(key or row["id"])[:2]

    def library_dir(self, row):
        iid = row["id"]
        if row["type"] == "episode":
            sid = self.series_of(row)[0]
            return os.path.join(self.root, "series", sid[:2], sid, "episodes", iid)
        return os.path.join(self.root, "movies" if row["type"] == "movie" else "series", iid[:2], iid)

    def item_dir(self, row, by_id=None):
        return os.path.join(self.run_dir, "staged", row["id"], "item")

    def person_dir(self, pid):
        return os.path.join(self.run_dir, "staged-people", pid)

    def arrival_of(self, local):
        """Where a file of the old media or extras folder goes: the same place under .work/incoming or
        .work/extras. Returns (the arrival path, the path relative to the arrivals), or (None, None)."""
        for base, arrivals in ((self.a.media, "incoming"), (self.a.extras, "extras")):
            if local.startswith(base + os.sep):
                rel = os.path.relpath(local, base)
                return os.path.join(self.work, arrivals, rel), rel.replace(os.sep, "/")
        return None, None

    def located(self, path, markers=("media",)):
        """A file the catalog names, where it is on this share, or None."""
        if not path:
            return None
        for marker in markers:
            p = self.under(self.a.media if marker == "media" else self.a.extras, path, marker)
            if os.path.isfile(p):
                return p
        return None

    # -------------------------------------------------- what the report says of an item
    def note(self, item_id, msg):
        super().note(item_id, msg)
        if self.current is not None and self.current["itemId"] == item_id:
            for phrase, cls in PROBLEM_NOTES:
                if phrase in msg:
                    self.problem(cls, msg)

    def problem(self, cls, detail):
        self.current["problems"].append({"class": cls, "detail": detail})

    def item_json(self, row):
        doc = super().item_json(row)
        if row["type"] == "episode":
            doc["seriesId"] = self.series_of(row)[0]
        return doc

    # -------------------------------------------------- one item
    def build(self, row, by_id, episodes):
        if row.get("type") == "season":
            return  # not a folder of its own: its episodes are in its series' folder
        self.current = {"itemId": row["id"], "type": row.get("type"), "title": text(row.get("title")),
                        "staged": False, "ready": False, "problems": []}
        self.entries.append(self.current)
        try:
            self.stage(row)
        finally:
            self.current["ready"] = self.current["staged"] and not self.current["problems"]
            self.current = None

    def stage(self, row):
        kind = row.get("type")
        if kind in MUSIC_TYPES:
            return self.problem("music", "music is not part of the library record: skipped")
        if kind not in ("movie", "series", "episode"):
            return self.problem("not a library item", f"a row of type {kind!r} has no folder in the library")
        if not text(row.get("title")):
            return self.problem("no title", "a record carries the title an item was created with, and the row has none")
        if kind == "episode":
            series, through_season = self.series_of(row)
            if through_season:
                self.problem("season parent", "its parent is a season: its folder is in the season's series")
            if not series:
                return self.problem("episode without series", "no series in the export holds it, so it has no folder")
            if not text(self.by_id[series].get("title")):
                return self.problem("episode without series", "its series cannot be recorded, so it has no folder")
            if row.get("seasonNumber") is None or row.get("episodeNumber") is None:
                return self.problem("episode without numbers", "an episode is recorded with its season and episode "
                                                               "numbers, and the row lacks them")
        target = self.library_dir(row)
        if os.path.isfile(os.path.join(target, "item.json")) or row["id"] in self.recorded:
            self.current["recorded"] = True
            self.counts["recorded"] += 1
            return  # adopted by an earlier run, or recorded by the platform since
        d = self.item_dir(row)
        if not self.a.dry_run:
            shutil.rmtree(os.path.dirname(d), ignore_errors=True)
        moves = {"package": [], "original": [], "legacy": []}
        db = {"recordedAt": now_utc(), "projectedDatabaseUpdatedAt": self.moment(row["id"], "modifiedAt",
                                                                                row.get("modifiedAt")),
              "sources": [], "versions": [], "assets": [], "subtitles": [], "extras": []}
        guards = {"manifestSha256": None, "completeMtime": None, "listingSha256": None}
        version_ids = []
        if kind != "series":
            vid = self.media(row, d, target, moves, db, guards)
            if vid:
                version_ids.append(vid)
        series_episodes = [r for r in self.by_id.values() if r.get("type") == "episode" and
                           self.series_of(r)[0] == row["id"]] if kind == "series" else []
        decisions = self.stage_extras(row, d, target, moves, db, series_episodes)
        self.w.covered(d, {"item.json": json_bytes(self.item_json(row))})
        self.w.write_json(os.path.join(d, "metadata.json"),
                          self.metadata_json(row, d, version_ids, series_episodes, decisions))
        if moves["package"]:
            guards["listingSha256"] = None if self.a.dry_run else listing_sha256([m["from"] for m in moves["package"]])
        unit = {"schema": "zaentrum.migration.unit/1", "run": self.a.run, "itemId": row["id"], "type": kind,
                "itemDir": target, "stagedDir": d,
                "moves": moves["package"] + [{"kind": "publish", "from": d, "to": target}] + moves["original"]
                + moves["legacy"],
                "guards": guards, "db": db, "problems": self.current["problems"]}
        self.w.write_json(os.path.join(self.run_dir, "units", row["id"] + ".json"), unit)
        self.current["staged"] = True
        self.counts["units"] += 1
        self.counts["items"] += 1
        self.staged_rows.append(row)

    # -------------------------------------------------- an item's original and its package
    def media(self, row, d, target, moves, db, guards):
        """The source record of the item's original and the version made of it, staged — with the records
        of its package, when it has one; the moves that put the package and the original where the record
        says, and the subtitle files that came with the original among the arrivals; and the rows that
        change with them. Returns the versionId, or None when there is nothing the library can record."""
        iid = row["id"]
        assets = row.get("playbackAssets") or []
        primary = next((a for a in assets if a.get("kind") == "primary" and a.get("isPrimary")), None) \
            or next((a for a in assets if a.get("kind") == "primary"), None)
        packaged = sorted((a for a in assets if a.get("kind") == "packaged"), key=lambda a: str(a.get("path")))
        original = self.located((primary or {}).get("path"))
        if not primary:
            self.problem("missing original", "the catalog names no original")
        elif original is None:
            self.problem("missing original", f"{primary['path']} is not on the share")
        if len(packaged) > 1:
            self.problem("several packages", f"{len(packaged)} packaged rows: {packaged[0]['path']} is the version, "
                                             f"and adopting it drops the others, as a new package does")
        pkg = None
        if packaged:
            a = packaged[0]
            folder = os.path.dirname(self.under(self.a.packages, a["path"], "packages"))
            if not os.path.isfile(os.path.join(folder, ".complete")):
                self.problem("missing .complete", f"the package {a['path']} never finished: it is not recorded, and "
                                                  f"the title is packaged again")
            else:
                mp = os.path.join(folder, "manifest.json")
                try:
                    pkg = (a, folder, json.load(open(mp, encoding="utf-8")) if os.path.isfile(mp) else {})
                except (OSError, ValueError) as e:
                    self.problem("missing .complete", f"the package manifest {mp} cannot be read ({e})")
        name = os.path.basename(original or str((primary or {}).get("path") or "").replace("\\", "/"))
        rel = self.arrival_of(original)[1] if original else None
        if original and rel is None:
            self.problem("original outside the media root", f"{original} is not under the media root, so the adopt "
                                                            f"refuses to move it into the library")
        library_path = rel or os.path.basename(name)
        if not original and primary:
            p = str(primary.get("path") or "").replace("\\", "/")
            i = p.rfind("/media/")
            library_path = p[i + len("/media/"):] if i >= 0 else name

        # the item's subtitle rows: its package's, and the files beside the original the scanner paired
        package_rows, sidecar_rows = [], []
        for s in row.get("subtitleAssets") or []:
            sp = str(s.get("path") or "")
            in_package = self.under(self.a.packages, sp, "packages") if pkg else ""
            if pkg and in_package.startswith(pkg[1] + os.sep):
                package_rows.append((s, os.path.relpath(in_package, pkg[1]).replace(os.sep, "/")))
                continue
            local = self.located(sp)
            if local:
                sidecar_rows.append((s, local))
            elif sp:
                self.problem("subtitle row without its file", f"subtitle {s.get('id')} names {sp}, which is no file "
                                                              f"of the share: it is left as it is")
        primary_folder = os.path.dirname(self.under(self.a.media, primary["path"], "media")) if primary else None

        # the sidecar rows to the package subtitles the packager made from them: katalog-manager handed it
        # the subtitle files in the original's folder, by path, and it made one subtitle of each it could
        # read, marked external, in that order — each in its row's language
        externals = [s for s in (pkg[2].get("subtitles") or []) if s.get("external")] if pkg else []
        took = sorted([s for s, p in sidecar_rows if os.path.splitext(p)[1].lower() in PACKAGER_SIDECARS
                       and os.path.getsize(p) <= PACKAGER_SIDECAR_MAX_BYTES
                       and (primary_folder is None or p.startswith(primary_folder + os.sep))],
                      key=lambda s: str(s.get("path")))
        mapped = {}
        for e in externals:
            want = primary_language(e.get("language") or "und")
            hit = next((s for s in took if primary_language(s.get("language") or "und") == want), None)
            if hit is None:
                self.problem("unmappable sidecar", f"subtitle {e.get('id')} of the package was made from a sidecar "
                                                   f"no subtitle row of the item names in its language")
                continue
            took.remove(hit)
            mapped[hit["id"]] = e
        for s in took if pkg else []:
            self.problem("unmappable sidecar", f"the sidecar {s.get('path')} is no subtitle of the package: its copy "
                                               f"is kept with the source, and its row points at it once the original "
                                               f"is gone")

        sid = next((s["id"] for s in self.sources_of.get(iid, []) if s.get("state") == "present" and s.get("id")
                    and (s.get("arrivalPath") == (primary or {}).get("path") or s.get("filename") == name)), None) \
            or did(iid, "source", name)
        source_dir = os.path.join(target, "sources", sid)
        size = os.path.getsize(original) if original else num((primary or {}).get("sizeBytes"))
        if pkg and size is None:
            self.problem("missing original", "and the catalog recorded no size for it: no source record can be "
                                             "written, so its package is not recorded")
            pkg = None
        if not pkg and not original:
            return None  # nothing the library can record: the catalog keeps the title as it is

        # sources/<sourceId>/: the probe, a copy of each subtitle file that came with the original — in the order
        # the catalog lists them, under the name the record logic gives it, and nothing else that sat beside it —
        # the record
        probe = ffprobe(original) if original and self.probe_version else None
        if probe:
            self.counts["probed"] += 1
        elif original:
            self.note(iid, f"{name} was not probed: streams, fidelity and essence stay empty")
        entries = [sidecar_entry(sid, n, local, os.path.basename(local), s.get("language"))
                   for n, (s, local) in enumerate(sidecar_rows, 1)]
        copy_of = {s.get("id"): e["file"] for (s, _), e in zip(sidecar_rows, entries)}
        files = {os.path.basename(e["file"]): open(local, "rb").read() for (_, local), e in zip(sidecar_rows, entries)}
        rec, raw = source_record(sid, name, size, taken_at=self.as_of, taken_by="library-v2-from-catalog",
                                 library_path=library_path, qh1=qh1(original) if original else None,
                                 mtime=ts_of_mtime(original) if original else None,
                                 probe=probe, probe_version=self.probe_version, sidecars=entries,
                                 note=None if original else GONE)
        files["source.json"] = json_bytes(rec)
        if raw is not None:
            files["ffprobe.json"] = raw
        self.w.covered(os.path.join(d, "sources", sid), files)
        self.counts["sources"] += 1

        # versions/<versionId>/: version.json, which names the original the adopt renames in beside it under the
        # name the library gives it — and the records over the package, when there is one, which the adopt moves
        # in too. A title nothing packaged yet is taken in: its version holds the original alone, no checksums,
        # no package.json, no .complete, until its first package is added
        kept = rec["file"]["name"] if original else None
        if pkg:
            a, folder, man = pkg
            store = os.path.relpath(folder, self.a.packages)
            vid, pid, created = did(iid, "version", store), did(iid, "package", store), ts(man.get("packagedAt"))
        else:
            vid, pid, created = did(iid, "version", name), None, None
        vp, vdir = os.path.join(d, "versions", vid), os.path.join(target, "versions", vid)
        marks, marks_from = chapter_marks(row.get("chapters")), "legacy-catalog"
        if not marks:
            marks, marks_from = probe_chapters(probe), "original-file"
        version = version_record(vid, rec, created_at=created or self.as_of, created_by="library-v2-from-catalog",
                                 chapters=marks, chapters_from=marks_from, segments=segments(row.get("segments")),
                                 original_files=[kept] if kept else [])
        version_bytes = json_bytes(version)
        self.w.write(os.path.join(vp, "version.json"), version_bytes)
        self.counts["versions"] += 1
        if pkg:
            listed = [] if self.a.dry_run else package_files(folder) + [record_entry("version.json", version_bytes)]
            package, notes = package_record(
                pid, man, listed, source=rec, role="derived" if kept else "canonical", created_at=self.as_of,
                duration_ms=a.get("durationMs"),
                peak_bandwidth_bps=peak_bandwidth(folder, (man.get("hls") or {}).get("master") or "hls/master.m3u8")
                or (num(a.get("bitrateKbps")) or 0) * 1000 or None,
                sidecars={e.get("id"): copy_of[row_id] for row_id, e in mapped.items()})
            for n in notes:
                self.note(iid, n)
            if rec["streams"]:
                self.measured += 1
                for key in deletion_gate([rec["essence"]], package["essence"]):
                    self.gate[key] = self.gate.get(key, 0) + 1
            else:
                self.unmeasured += 1
            package_bytes = json_bytes(package)
            self.w.write(os.path.join(vp, "checksums.sha256"), checksums(listed)[0])
            self.w.write(os.path.join(vp, "package.json"), package_bytes)
            self.w.write(os.path.join(vp, ".complete"), complete(package_bytes))
            self.counts["packages"] += 1
            for sub in PACKAGE_DIRS:
                if os.path.isdir(os.path.join(folder, sub)):
                    moves["package"].append({"kind": "package", "from": os.path.join(folder, sub),
                                             "to": os.path.join(vp, sub)})
            moves["legacy"].append({"kind": "legacy", "from": folder, "to": os.path.join(self.run_dir, "legacy", store)})
            mp = os.path.join(folder, "manifest.json")
            guards.update(manifestSha256=sha_file(mp) if os.path.isfile(mp) else None,
                          completeMtime=mtime_ns(os.path.join(folder, ".complete")))
        else:
            self.counts["takenIn"] += 1

        # the original into its version folder once the item is in place, and the subtitle files that came
        # with it — the source keeps a copy of each — to the arrivals beside where it was
        if original:
            moves["original"].append({"kind": "original", "from": original, "to": os.path.join(vdir, kept)})
            for _, local in sidecar_rows:
                to = self.arrival_of(local)[0]
                if to:
                    moves["original"].append({"kind": "sidecar", "from": local, "to": to})
            if primary:
                db["assets"].append({"id": primary.get("id"), "path": os.path.join(vdir, kept), "sourceId": sid})
            for s, local in sidecar_rows:
                to = self.arrival_of(local)[0]
                if to:
                    db["subtitles"].append({"id": s.get("id"), "path": to})
        db["sources"].append({"sourceId": sid, "filename": rec["file"]["name"],
                              "arrivalPath": os.path.join(vdir, kept) if original else None, "libraryPath": None,
                              "sizeBytes": size, "qh1": rec["file"].get("fixity", {}).get("qh1"),
                              "recordDir": source_dir,
                              "sidecars": [{"subtitleAssetId": row_id, "rendition": e.get("id"), "path": e.get("path")}
                                           for row_id, e in sorted(mapped.items())]})
        if not pkg:
            db["versions"].append({"versionId": vid, "packageId": None, "dir": vdir, "completedAt": None,
                                   "sourceIds": [sid], "verifiedAt": None, "verifiedLevel": None, "takenIn": True})
            return vid
        db["versions"].append({"versionId": vid, "packageId": pid, "dir": vdir, "completedAt": package["createdAt"],
                               "sourceIds": [sid], "verifiedAt": None if self.a.dry_run else now_utc(),
                               "verifiedLevel": "full", "takenIn": False})
        db["assets"].append({"id": a.get("id"), "path": os.path.join(vdir, "package.json"), "versionId": vid})
        if not original and primary:
            # gone before the library was recorded: the original's row is a retired one, as a retire leaves it
            db["assets"].append({"id": primary.get("id"), "path": source_dir, "sourceId": sid})
        rendered = {s.get("path") for s in package["subtitles"]}
        for s, rel_path in package_rows:
            if rel_path in rendered or os.path.isfile(os.path.join(folder, rel_path)):
                db["subtitles"].append({"id": s.get("id"), "path": os.path.join(vdir, rel_path)})
            else:
                self.problem("subtitle row without its file", f"subtitle {s.get('id')} names {s.get('path')}, which "
                                                              f"the package does not hold: it is left as it is")
        if not original:
            for s, _ in sidecar_rows:
                e = mapped.get(s.get("id"))
                db["subtitles"].append({"id": s.get("id"), "path": os.path.join(vdir, e["path"]) if e
                                        else os.path.join(target, copy_of[s.get("id")])})
        return vid

    # -------------------------------------------------- the title's extras, from the catalog's
    def stage_extras(self, row, d, target, moves, db, series_episodes):
        """Every extra of the title the catalog holds (039), staged under extras/<extraId>/ when its
        package finished: extra.json, the records over its package, which the adopt moves in, and what
        it was packaged from. Returns the decisions about how they are shown, for library.extras."""
        decisions = {}
        seasons = {int(e["seasonNumber"]) for e in series_episodes if e.get("seasonNumber") is not None}
        for x in sorted(self.extras_of.get(row["id"], []), key=lambda x: str(x.get("id"))):
            xid = str(x.get("id") or "")
            if row["type"] == "episode":
                self.problem("extra of an episode", f"extra {xid} belongs to an episode, which has no extras")
                continue
            if not UUID_RE.match(xid):
                self.problem("extra without an id", f"extra {xid!r} cannot name a folder")
                continue
            local = self.located(x.get("sourcePath"), ("media", "extras"))
            arrival = self.arrival_of(local)[0] if local else None
            if local and arrival:
                moves["original"].append({"kind": "original", "from": local, "to": arrival})
            folder = self.under(self.a.packages, x["packagePath"], "packages") if x.get("packagePath") else None
            if not (x.get("state") == "ready" and x.get("packagedAt") and folder
                    and os.path.isfile(os.path.join(folder, ".complete"))):
                self.problem("extra not packaged", f"extra {xid} ({x.get('state')}) has no finished package: it stays "
                                                   f"in the catalog, its original among the arrivals")
                db["extras"].append({"id": xid, "dir": None, "packageId": None, "sourcePath": arrival or local})
                continue
            mp = os.path.join(folder, "manifest.json")
            try:
                man = json.load(open(mp, encoding="utf-8")) if os.path.isfile(mp) else {}
            except (OSError, ValueError) as e:
                self.problem("extra not packaged", f"extra {xid}'s package manifest cannot be read ({e})")
                continue
            if local:
                made_from = [{"name": os.path.basename(local), "sizeBytes": os.path.getsize(local),
                              "fixity": {"qh1": qh1(local)}}]
            elif num(x.get("sourceSize")) is not None and re.fullmatch(r"sha256:[0-9a-f]{64}", str(x.get("sourceQh1"))):
                made_from = [{"name": os.path.basename(str(x.get("sourcePath") or "original")),
                              "sizeBytes": num(x["sourceSize"]), "fixity": {"qh1": x["sourceQh1"]}}]
            else:
                made_from = []
                self.problem("extra original gone", f"extra {xid}'s original is gone and the catalog kept no fixity "
                                                    f"of it: its record cannot say what it was packaged from")
            season = num(x.get("seasonNumber"))
            if season is not None and (row["type"] != "series" or season not in seasons):
                self.note(row["id"], f"extra {xid} names season {season}, which the series does not hold: it is "
                                     f"the series' as a whole")
                season = None
            origin = x.get("origin") if isinstance(x.get("origin"), dict) else None
            if origin and (origin.get("kind") != "link"
                           or set(origin) - {"kind", "site", "externalId", "url", "fetchedAt"}):
                self.note(row["id"], f"extra {xid}'s origin {origin!r} is not a link the record can name, dropped")
                origin = None
            titles = {k: text(v) for k, v in (x.get("localizedTitles") or {}).items()
                      if LANGUAGE_RE.match(str(k)) and text(v)}
            language = text(x.get("language"))
            probe = ffprobe(local) if local and self.probe_version else None
            if probe:
                self.counts["probed"] += 1
            doc = extra_record(xid, created_at=self.moment(xid, "createdAt", x.get("createdAt")) or self.as_of,
                               created_by=text(x.get("createdBy")) or "library-v2-from-catalog", kind=x.get("kind"),
                               title=text(x.get("title")), localized_titles=titles,
                               language=language if language and LANGUAGE_RE.match(language) else None,
                               season_number=season, origin=origin, probe=probe, probe_version=self.probe_version,
                               probed_at=self.as_of, packaged_from=made_from)
            record = json_bytes(doc)
            xp = os.path.join(d, "extras", xid)
            store = os.path.relpath(folder, self.a.packages)
            listed = [] if self.a.dry_run else package_files(folder, EXTRA_DIRS) + [record_entry("extra.json", record)]
            package, notes = package_record(did(xid, "package", store), man, listed, source=doc if probe else None,
                                            created_at=self.as_of, duration_ms=x.get("durationMs"),
                                            peak_bandwidth_bps=peak_bandwidth(folder) or num(x.get("peakBandwidthBps")))
            for n in notes:
                self.note(row["id"], f"extra {xid}: {n}")
            package_bytes = json_bytes(package)
            self.w.write(os.path.join(xp, "extra.json"), record)
            self.w.write(os.path.join(xp, "checksums.sha256"), checksums(listed)[0])
            self.w.write(os.path.join(xp, "package.json"), package_bytes)
            self.w.write(os.path.join(xp, ".complete"), complete(package_bytes))
            for sub in EXTRA_DIRS:
                if os.path.isdir(os.path.join(folder, sub)):
                    moves["package"].append({"kind": "package", "from": os.path.join(folder, sub),
                                             "to": os.path.join(xp, sub)})
            moves["legacy"].append({"kind": "legacy", "from": folder, "to": os.path.join(self.run_dir, "legacy", store)})
            db["extras"].append({"id": xid, "dir": os.path.join(target, "extras", xid),
                                 "packageId": package["packageId"], "sourcePath": arrival or local})
            decision = {k: v for k, v in (("order", num(x.get("sortOrder"))), ("hidden", x.get("hidden") is True or None),
                                          ("label", text(x.get("label")))) if v is not None}
            if decision:
                decisions[xid] = decision
            self.counts["extras"] += 1
        return decisions

    # -------------------------------------------------- people, and the report
    def people(self, rows, everyone):
        """The people the staged items credit, staged under staged-people/<personId>/ — but not one
        whose folder the library holds already: that is the projector's."""
        listed = {str(p["id"]): p for p in self.export.get("people") or [] if isinstance(p, dict) and p.get("id")}
        names = {}
        for row in self.staged_rows:
            for c in row.get("people") or []:
                pid = text(c.get("personId"))
                if pid and UUID_RE.match(pid):
                    names.setdefault(pid, [])
                    if text(c.get("name")) and text(c["name"]) not in names[pid]:
                        names[pid].append(text(c["name"]))
        for pid in sorted(names):
            if os.path.isfile(os.path.join(self.root, "people", pid[:2], pid, "person.json")):
                continue
            if not self.a.dry_run:
                shutil.rmtree(self.person_dir(pid), ignore_errors=True)
            try:
                self.person(pid, listed.get(pid) or {}, names[pid])
            except Exception as e:  # one unreadable person must not stop the run
                self.skipped.append((pid, f"{type(e).__name__}: {e}"))

    def finish(self):
        """report.json — report-<shard>.json for a shard — written whatever else the run wrote: what is
        ready, the problems of every item by class, and what deleting every original would cost."""
        problems = {}
        for e in self.entries:
            for p in e["problems"]:
                problems[p["class"]] = problems.get(p["class"], 0) + 1
        report = {"schema": "zaentrum.migration.report/1", "run": self.a.run, "asOf": self.as_of,
                  "dryRun": bool(self.a.dry_run), "shard": self.a.shard or None, "root": self.root,
                  "counts": {"items": len(self.entries), "staged": self.counts["units"],
                             "ready": sum(1 for e in self.entries if e["ready"]), "recorded": self.counts["recorded"],
                             "versions": self.counts["versions"], "takenIn": self.counts["takenIn"],
                             "extras": self.counts["extras"], "people": self.counts["people"]},
                  "problems": dict(sorted(problems.items())),
                  "loss": {"measured": self.measured, "unmeasured": self.unmeasured,
                           "gate": dict(sorted(self.gate.items(), key=lambda kv: (-kv[1], kv[0])))},
                  "items": self.entries}
        path = os.path.join(self.run_dir, f"report-{self.a.shard}.json" if self.a.shard else "report.json")
        os.makedirs(self.run_dir, exist_ok=True)
        with open(path + ".tmp", "wb") as f:
            f.write(json_bytes(report))
        os.replace(path + ".tmp", path)
        return path, report


def main():
    ap = argparse.ArgumentParser(prog="library-v2-from-catalog.py",
                                 description="Write v2 library records from a catalog export.")
    ap.add_argument("--export", required=True, help="the catalog export produced on the client side")
    ap.add_argument("--packages", help="the package store the catalog's packaged assets point into")
    ap.add_argument("--media", help="the folder the catalog's primary assets point into")
    ap.add_argument("--out", help="the library root to write: movies/, series/ and people/ go here")
    ap.add_argument("--platform", action="store_true",
                    help="stage every record the platform's library needs under <root>/.work/migration/<run>/ for "
                         "katalog-manager to adopt, with a plan per item of what moves where; writes nothing else")
    ap.add_argument("--root", help="--platform: the share's root as the services see it, the library root that holds "
                                   "movies/, series/, people/ and .work/")
    ap.add_argument("--run", help="--platform: the migration run, the folder .work/migration/<run>/")
    ap.add_argument("--extras", help="--platform: the folder extras were taken in from (default <root>/extras)")
    ap.add_argument("--shard", help="--platform: only the items whose folder is in this shard, the first two "
                                    "characters of the id of a movie or a series; with an export of that shard")
    ap.add_argument("--people-only", action="store_true",
                    help="write people/ and nothing else, so a tree written earlier gains its people "
                         "without a record being rewritten; needs neither --packages nor --media")
    ap.add_argument("--projections-only", action="store_true",
                    help="rewrite only the projections of what the tree already holds — every item's metadata.json "
                         "and its images, every person's person.json and portraits — and no record; an item or a "
                         "person the tree does not hold is skipped with a note; needs neither --packages nor --media")
    ap.add_argument("--items", default="", help="comma-separated item ids; a selected episode brings its series, "
                                                "a selected series brings its episodes")
    ap.add_argument("--media-mode", choices=("copy", "move", "none"), default="copy",
                    help="copy (default) or move the bytes into the version folder, or leave them where they are")
    ap.add_argument("--as-of", default="", help="the moment every record says it was written; the export's "
                                                "exportedAt by default, which is what makes a re-run identical")
    ap.add_argument("--text-language", default="und",
                    help="the language key the row's description and tagline are written under; 'und' by default, "
                         "because the catalog does not record what language its texts are in")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if args.platform:
        if args.out or args.people_only or args.projections_only:
            ap.error("--platform stages its records under <root>/.work/migration/<run>/: no --out, --people-only or "
                     "--projections-only with it")
        if not args.root or not args.run:
            ap.error("--platform needs --root and --run")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", args.run):
            ap.error(f"--run {args.run!r} is not a folder name: letters, digits, '.', '_' and '-'")
        if args.shard and not re.fullmatch(r"[0-9a-f]{2}", args.shard):
            ap.error(f"--shard {args.shard!r} is not a shard: two lower-case hex characters")
        args.root = os.path.abspath(args.root)
        args.media = os.path.abspath(args.media or os.path.join(args.root, "media"))
        args.packages = os.path.abspath(args.packages or os.path.join(args.root, "packages"))
        args.extras = os.path.abspath(args.extras or os.path.join(args.root, "extras"))
        args.out = args.root
        args.media_mode = "none"
    elif args.root or args.run or args.extras or args.shard:
        ap.error("--root, --run, --extras and --shard go with --platform")
    elif not args.out:
        ap.error("--out is needed, unless --platform")
    if args.people_only and args.projections_only:
        ap.error("--people-only and --projections-only exclude each other: --projections-only rewrites people too")
    if not (args.platform or args.people_only or args.projections_only) and not (args.packages and args.media):
        ap.error("--packages and --media are needed, unless --people-only or --projections-only")
    args.packages_given = bool(args.packages)
    args.out = os.path.abspath(args.out)
    args.media = os.path.abspath(args.media or ".")
    args.packages = os.path.abspath(args.packages or ".")

    with open(args.export, encoding="utf-8") as f:
        export = json.load(f)
    rows = export.get("items") or []
    by_id = {r["id"]: r for r in rows}
    b = (Platform if args.platform else Build)(args, export)
    wanted = [x.strip() for x in args.items.split(",") if x.strip()]
    if wanted:
        chosen = {x for x in wanted if x in by_id}
        for x in wanted:
            if x not in by_id:
                print(f"note: --items names {x}, which the export does not hold")
        for x in list(chosen):
            row = by_id[x]
            if row["type"] == "episode" and row.get("parentId") in by_id:
                chosen.add(row["parentId"])
            if row["type"] == "series":
                chosen |= {r["id"] for r in rows if r.get("parentId") == x}
        if args.platform:  # an episode under a season brings its series, and a series the episodes of its seasons
            chosen |= {b.series_of(by_id[x])[0] for x in list(chosen) if by_id[x].get("type") == "episode"} - {None}
            chosen |= {r["id"] for r in rows if r.get("type") == "episode" and b.series_of(r)[0] in chosen}
        rows = [r for r in rows if r["id"] in chosen]
    if args.platform and args.shard:
        rows = [r for r in rows if b.shard_of(r) == args.shard]
    if not b.probe_version and not (args.people_only or args.projections_only):
        print("note: ffprobe is not on PATH; source records will carry size, mtime and qh1 only")
    order = {"series": 0, "movie": 1, "episode": 2}
    for row in ([] if args.people_only else sorted(rows, key=lambda r: (order.get(r["type"], 3), r["id"]))):
        episodes = [r for r in (export.get("items") or []) if r.get("parentId") == row["id"]] \
            if row["type"] == "series" else []
        try:
            (b.project if args.projections_only else b.build)(row, by_id, episodes)
        except Exception as e:  # one unreadable item must not stop the run
            b.skipped.append((row["id"], f"{type(e).__name__}: {e}"))
    b.people(rows, everyone=not wanted)

    print(f"{'would write' if args.dry_run else 'wrote'}: {b.counts}")
    print(f"files placed: {b.w.placed} ({b.w.bytes_placed} bytes), records written: {b.w.written}")
    for n in b.notes:
        print("  note " + n)
    for iid, why in b.skipped:
        print(f"  skipped {iid}: {why}")
    if args.platform:
        path, report = b.finish()
        print(f"report: {path}")
        print(f"  {report['counts']}")
        for cls, n in report["problems"].items():
            print(f"  problem {cls}: {n}")
        if report["loss"]["gate"] or report["loss"]["unmeasured"]:
            print(f"  deleting every original would cost: {report['loss']['gate']} "
                  f"({report['loss']['unmeasured']} not measured)")
    return 1 if b.skipped else 0


if __name__ == "__main__":
    sys.exit(main())
