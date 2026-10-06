#!/usr/bin/env python3
"""Write v2 library records for a catalog's items, from the catalog export, the package store and
the original files.

Usage:
  library-v2-from-catalog.py --export CATALOG.json --packages DIR --media DIR --out LIBRARY
                             [--items id,id,...] [--media-mode copy|move|none]
                             [--as-of TIMESTAMP] [--text-language LANG] [--dry-run]
  library-v2-from-catalog.py --export CATALOG.json --out LIBRARY --people-only [--items …] [--dry-run]
  library-v2-from-catalog.py --export CATALOG.json --out LIBRARY --projections-only [--items …] [--dry-run]

The catalog is the working copy and this writes the record beside the bytes: one item folder per
row, holding the identity the row was created with, the texts and images the database held, the
original each row points at as the file itself reports it, and one version folder per packaged
asset with the package moved or copied in — and one folder per person the rows credit.

  <out>/movies/<aa>/<itemId>/          item.json  checksums.sha256  metadata.json  metadata/<sha256>.jpg
                                       sources/<sourceId>/source.json  ffprobe.json  checksums.sha256
                                       versions/<versionId>/version.json  hls/ subs/ trickplay/
                                                             checksums.sha256  package.json  .complete
                                       extras/<extraId>/extra.json  <the trailer>  checksums.sha256
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
sourcePath it was fetched from when the row has one, and the catalog when it does not.

The export is produced on the client side (see the query beside this tool in the runbook); this
tool needs no database driver and no network, only the standard library and libv2_records.py — the
record logic the packager vendors, so the two write the same records — and so it can be piped into
the pod that mounts the share behind that module:

  cat libv2_records.py library-v2-from-catalog.py | oc -n <ns> exec -i deploy/packager -- python3 - --export …

What it can fill in, and what it cannot:
  * the identity, texts, images, people, chapters, segments and trailers come from the export;
  * a trailer the catalog downloaded — one whose localPath is a file on the share — also becomes an
    extra of kind trailer beside its movie or series, its file copied or moved in as --media-mode
    says, its link kept in metadata.json's videos and named by the extra's origin, so a rebuild gives
    the link its localPath back. An episode's stays a link, because an episode has no extras, and so
    does every one with --media-mode none, because an extra holds its own file;
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
"""
import argparse, base64, datetime, hashlib, json, os, re, shutil, sys

try:  # the record logic the packager writes the same records with: beside this tool, or piped in front of it
    from libv2_records import (
        LANGUAGE_RE, PACKAGE_DIRS, UUID_RE, chapter_marks, checksums, complete, did, external_ids, extra_record,
        ffprobe, ffprobe_version, have_ffprobe, is_moment, json_bytes, listdir, num, package_files, package_record,
        peak_bandwidth, probe_chapters, qh1, record_entry, segments, sha_file, source_record, text, ts, ts_of_mtime,
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
# The names an extra's folder keeps for itself, which an original beside them cannot have.
EXTRA_RECORDS = {"extra.json", "package.json", "checksums.sha256", ".complete", "hls", "subs", "trickplay", ".", ".."}


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
    def metadata_json(self, row, d, version_ids, episodes):
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
        doc = {"schema": "zaentrum.library.metadata/2", "itemId": row["id"], "type": row["type"],
               "asOf": self.as_of, "projectedBy": "library-v2-from-catalog", **self.freshness(row["id"], row),
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
        library = {"match": {"status": "matched" if any(k.startswith("tmdb") for k in
                                                        (self.item_json(row)["externalIds"])) else "unmatched",
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
        return out

    def artwork(self, owner, entries, folder, kinds, label, holder, series=False):
        """The images a projection lists, from the export's artwork: each written into folder, named by
        the hash of its own bytes, which decide its type and size whatever the row says. An image is
        primary where the row's isPrimary says so — at most one of a kind, the first the export lists —
        and its origin is TMDB with the sourcePath it was fetched from when the row has one, the
        catalog when it does not. Byte-identical entries are one image, listed once, primary when
        either is. Returns the entries and the names of the files they list."""
        out, by_name = [], {}
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
            if name in by_name:
                self.note(owner, f"{kind} artwork is byte-identical to the {by_name[name]['kind']}, listed once")
                if primary and by_name[name]["kind"] == kind:
                    by_name[name]["primary"] = True
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
            if not (self.projecting and os.path.isfile(os.path.join(folder, name))):
                # an image is written once: a projection run leaves one already there as it is
                self.w.write(os.path.join(folder, name), raw)
                self.counts["images"] += 1
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
            by_name[name] = entry
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
        return out, set(by_name)

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
                                 mtime=ts_of_mtime(path),
                                 origin_taken_by={"copy": "copy", "move": "move"}.get(self.a.media_mode),
                                 probe=probe, probe_version=self.probe_version)
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
        an original, and the checksums over both, written last because they say the extra is finished.
        The link stays in metadata.json's videos, where it is still published. Returns how many."""
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
            if name in EXTRA_RECORDS or name in names or name != text(name):
                self.note(row["id"], f"trailer {name!r} cannot be an original's name in an extra's folder, so it stays a link")
                continue
            names.add(name)
            xid = did(row["id"], "extra", name)
            xp = os.path.join(d, "extras", xid)
            digest, size = sha_file(path), os.path.getsize(path)
            site, key = link_of(t)
            probe = ffprobe(path) if self.probe_version else None
            if probe:
                self.counts["probed"] += 1
            else:
                self.note(row["id"], f"trailer {name} was not probed: its streams, fidelity and essence stay empty")
            doc = extra_record(xid, created_at=self.as_of, created_by="library-v2-from-catalog", kind="trailer",
                               title=text(t.get("title")) or os.path.splitext(name)[0],
                               origin={"kind": "link", **{k: v for k, v in (
                                   ("site", site), ("externalId", key), ("url", text(t.get("url"))),
                                   ("fetchedAt", ts(t.get("fetchedAt")))) if v}},
                               probe=probe, probe_version=self.probe_version, original_files=[name],
                               originals=[{"name": name, "sizeBytes": size,
                                           "fixity": {"qh1": qh1(path), "sha256": digest, "sha256At": self.as_of}}])
            record = json_bytes(doc)
            self.w.write(os.path.join(xp, "extra.json"), record)
            if not self.w.place(path, os.path.join(xp, name), self.a.media_mode):
                self.note(row["id"], f"trailer {name} could not be placed; its extra never finished")
                continue
            self.w.write(os.path.join(xp, "checksums.sha256"),
                         checksums([record_entry("extra.json", record), (name, digest.split(":", 1)[1], size)])[0])
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
            if self.projecting and not os.path.isfile(os.path.join(self.a.out, "people", pid[:2], pid, "person.json")):
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
        d = os.path.join(self.a.out, "people", pid[:2], pid)
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


def main():
    ap = argparse.ArgumentParser(prog="library-v2-from-catalog.py",
                                 description="Write v2 library records from a catalog export.")
    ap.add_argument("--export", required=True, help="the catalog export produced on the client side")
    ap.add_argument("--packages", help="the package store the catalog's packaged assets point into")
    ap.add_argument("--media", help="the folder the catalog's primary assets point into")
    ap.add_argument("--out", required=True, help="the library root to write: movies/, series/ and people/ go here")
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
    if args.people_only and args.projections_only:
        ap.error("--people-only and --projections-only exclude each other: --projections-only rewrites people too")
    if not (args.people_only or args.projections_only) and not (args.packages and args.media):
        ap.error("--packages and --media are needed, unless --people-only or --projections-only")
    args.packages_given = bool(args.packages)
    args.out = os.path.abspath(args.out)
    args.media = os.path.abspath(args.media or ".")
    args.packages = os.path.abspath(args.packages or ".")

    with open(args.export, encoding="utf-8") as f:
        export = json.load(f)
    rows = export.get("items") or []
    by_id = {r["id"]: r for r in rows}
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
        rows = [r for r in rows if r["id"] in chosen]

    b = Build(args, export)
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
    return 1 if b.skipped else 0


if __name__ == "__main__":
    sys.exit(main())
