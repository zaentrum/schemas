#!/usr/bin/env python3
"""Validate a zaentrum library v2 tree against the JSON Schemas and the rules that span files.

Usage:
  validate-library-v2.py [--check-media | --check-checksums] [--schemas URL-or-dir] ROOT [ROOT ...]

ROOT is a folder holding movies/, series/ and people/. In v2 the database is the working copy and
this tree is the record that can rebuild it: every file is either written once (item, source,
version, package, event, extra) or replaced whole by the one service that owns it (metadata,
person). Nothing here is merged, so the rules below are about records agreeing with each other and
with the bytes beside them, never about a document being up to date.

  layout       only movies/, series/ and people/ at the root; shard folders of two characters; itemId
               and personId equal the folder name and its shard; an item folder holds only item.json,
               checksums.sha256, metadata.json, metadata/, sources/, versions/, events/, extras/ (a
               series: episodes/ instead of sources/ and versions/; an episode: no extras/) — so no
               media can sit in the item folder; every source and every event is a folder of its own
  covered      every write-once folder proves its records: an item folder's checksums.sha256 lists
               exactly item.json, a source folder's lists exactly source.json, its probe and its
               sidecars, an event folder's exactly event.json, and each digest matches the file; a
               version is one chain — .complete holds sha256:<hex> of package.json, package.json the
               hash of checksums.sha256, and checksums.sha256 lists version.json beside the package
               files and never .complete, package.json or itself. Projections are not covered: they
               are replaced whole, and an image's name is its own hash
  people       a person folder holds person.json and the images it lists; a death is not before the
               birth; a credit whose person has no folder is a note, not an error, because the
               person may not have been projected yet
  versions     every version is a folder under versions/ whose name is its versionId; its sourceIds
               name source records that exist and its originalFiles are those sources' file names;
               package.json exists exactly when .complete does; a package is canonical exactly when
               the version keeps no original; lossless means no losses; chapters are ordered and
               kept when the original carried them; every track says what it is for and the playlist
               flags do not contradict it
  metadata     same item and type as item.json; every image exists with the recorded hash, size,
               content type and dimensions, is named by its own content hash, is listed once, and
               nothing unlisted sits in metadata/ (the same for a person's images beside person.json);
               library.primaryVersionId and library.versionLabels name versions that are really there
  series       an episode names its enclosing series, agrees with it on the reference id, numbers
               itself consistently, does not collide with another episode, and sits in a season the
               series' metadata lists; its own numbering does not contradict the numbers item.json
               was created with, and no two episodes claim one place in one ordering
  events       the folder name is the event's own moment, eventId and kind; every source, version and package
               it names exists (a version-removed event is the exception — its folder may be gone,
               and a folder it names is ignored altogether); a deletion names a version that had an
               original and one of that version's own sources, and accepts no more than the
               deletion gate — the essence of the version's sources minus that of its package,
               computed here because no record holds it; a package-superseded event names a
               successor that exists in another version folder
  extras       bonus material sits in extras/<extraId>/ inside its movie or series, never an
               episode; each folder is named by its extraId and holds extra.json, the originals it
               names and/or a package (hls/ subs/ trickplay/); its checksums.sha256 lists extra.json
               and every file beside it, the originals included, and never itself, package.json or
               .complete; a packaged extra closes the chain a version does and carries no trailers,
               one that keeps only its original is finished by its checksums file, and one that never
               finished is a note; only a series' extra names a season, and only one the series'
               metadata lists; library.extras names extras that are there
  checksums    the checksums file matches its recorded hash and file count
  --check-media  no file under an item folder is a hard link shared with another path; every
               original a version names is there with its size and qh1, unless an original-deleted
               event covers it — in which case it must NOT be there; every rendition folder,
               subtitle and trickplay sheet a package names exists and the cues cover the duration;
               checksums.sha256 lists exactly version.json and the package's files, with their total
               size, and an extra's lists files of that total
  --check-checksums  also hash every package file and every file an extra's checksums list
               (implies --check-media)

The schemas are loaded from the library/v2 folder next to this tool by default; pass
--schemas https://zaentrum.github.io/schemas/library/v2 to use the published copies.
Needs: pip install "jsonschema[format-nongpl]>=4.23" referencing
"""
import argparse, datetime, hashlib, json, os, re, sys, urllib.request
from jsonschema import Draft202012Validator, FormatChecker, validators
from jsonschema.exceptions import best_match
from referencing import Registry, Resource

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "library", "v2")
NAMES = ["defs", "item", "metadata", "person", "source", "version", "package", "event", "extra"]
DOCUMENTS = ["item", "metadata", "person", "source", "version", "package", "event", "extra"]
BASE = "https://zaentrum.github.io/schemas/library/v2/"

CATEGORIES = (("movies", "movie"), ("series", "series"))
ROOT_ENTRIES = {"movies", "series", "people"}
SUMS = "checksums.sha256"
ITEM_ENTRIES = {"item.json", SUMS, "metadata.json", "metadata", "sources", "versions", "events", "extras"}
SERIES_ENTRIES = {"item.json", SUMS, "metadata.json", "metadata", "episodes", "events", "extras"}
VERSION_ENTRIES = {"version.json", "package.json", SUMS, ".complete", "hls", "subs", "trickplay", "trailers"}
PACKAGE_DIRS = ("hls", "subs", "trickplay", "trailers")
# An extra's package is the folders of a version's, without trailers: a trailer of its own is an extra.
EXTRA_DIRS = ("hls", "subs", "trickplay")
EXTRA_ENTRIES = {"extra.json", "package.json", SUMS, ".complete", *EXTRA_DIRS}
# Each link of a version's chain holds the hash of the next one down, so none of them can be listed by
# the checksums file it sits above.
CHAIN = (".complete", "package.json", SUMS)
EVENT_KINDS = ("original-deleted", "version-removed", "package-superseded", "source-removed", "note")
EVENT_FOLDER = re.compile(r"^(\d{8}T\d{6}Z)-([0-9a-f]{8})-(" + "|".join(EVENT_KINDS) + r")$")
OLD_EVENT_FILE = re.compile(r"^\d{8}T\d{6}Z(?:-[0-9a-f]{8})?-(" + "|".join(EVENT_KINDS) + r")\.json$")
SUM_LINE = re.compile(r"^([0-9a-f]{64})  (.+)$")
COMPLETE = re.compile(r"^sha256:[0-9a-f]{64}$")
UPGRADE = "library-v2-upgrade.py upgrades a tree from before 2026-10-02 (b) in place"
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
VTT_CUE = re.compile(r"^(\d+):(\d\d):(\d\d)\.(\d{3}) --> (\d+):(\d\d):(\d\d)\.(\d{3})")
OS_ARTEFACTS = re.compile(r"^(\.DS_Store|\._.*|Thumbs\.db|desktop\.ini|@eaDir|\.@__thumb|#recycle|\.AppleDouble)$")
EXT_TYPE = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
IMAGE_NAME = re.compile(r"^([0-9a-f]{64})\.(jpg|png|webp)$")
SWEEP = "library-v2-sweep.py collects it once it is older than the grace period"
ID_KEYS = {"schema", "itemId", "seriesId", "sourceId", "versionId", "packageId", "eventId", "personId", "file",
           "path", "dir", "vttPath", "manifestPath", "originalFile", "name", "language", "type", "kind",
           "tmdbMovie", "tmdbTv", "tmdbSeason", "tmdbEpisode", "tmdbCollection", "imdb", "tvdb", "tmdbPerson",
           "sha256", "qh1"}
FREE_TEXT = {"overview", "tagline", "notes", "note", "detail", "reason"}
INT64 = (-(1 << 63), (1 << 63) - 1)
# What an original carries that a package can fail to carry, in the terms an original-deleted event
# accepts. 'chapters' is not here (the version keeps the marks, not the file) and neither is
# 'interlaced' (a deinterlaced picture is not a poorer one).
LOSS_FLAGS = ("surround", "losslessAudio", "objectAudio", "hdr10Metadata", "dolbyVision", "stereo3d",
              "imageSubtitles", "styledSubtitles", "fonts", "closedCaptions")
LOSS_COUNTS = ("maxAudioChannels", "maxVideoHeight", "videoBitDepth", "subtitleTracks",
               "commentaryTracks", "commentarySubtitles", "audioDescriptionTracks")
LOSS_LANGUAGES = ("audioLanguages", "subtitleLanguages", "sdhSubtitleLanguages", "forcedSubtitleLanguages")


def deletion_gate(sources, package):
    """What deleting a version's originals would cost: the essence of its sources minus the essence
    of its package. Nothing records this — it is computed here and by every reader, so it stays right
    however often the version is re-packaged."""
    lost = set()
    for key in LOSS_FLAGS:
        if any(s.get(key) for s in sources) and not package.get(key):
            lost.add(key)
    for key in LOSS_COUNTS:
        had = [s[key] for s in sources if s.get(key) is not None]
        kept = package.get(key)
        if had and (kept is None or kept < max(had)):
            lost.add(key)
    for key in LOSS_LANGUAGES:
        kept = set(package.get(key) or [])
        lost |= {f"{key}:{lang}" for s in sources for lang in s.get(key) or [] if lang not in kept}
    return lost


def listdir(d):
    """Directory entries without the files operating systems and NAS software drop into shared folders."""
    return sorted(n for n in os.listdir(d) if not OS_ARTEFACTS.match(n))


def sha_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def qh1(p):
    """sha256(first 64 KiB || last 64 KiB || uint64be(size)): cheap proof a large file is the one recorded."""
    size = os.path.getsize(p)
    h = hashlib.sha256()
    with open(p, "rb") as f:
        h.update(f.read(65536))
        if size > 65536:
            f.seek(max(size - 65536, 0))
            h.update(f.read(65536))
    h.update(size.to_bytes(8, "big"))
    return "sha256:" + h.hexdigest()


def stamp_of(timestamp):
    """The <YYYYMMDDTHHMMSSZ> an event file name carries, from the event's own 'at'."""
    t = datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return t.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sniff_image(p):
    """(content type, width, height) from the file header; None where unknown."""
    with open(p, "rb") as f:
        b = f.read(1 << 24)
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


def read_checksums(path):
    entries = {}
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line:
            continue
        digest, _, rel = line.partition("  ")
        entries[rel] = digest
    return entries


def read_sums(path):
    """A sha256sum file as ({name: hex digest}, [lines that are not '<64 hex>  <name>'])."""
    entries, bad = {}, []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            m = SUM_LINE.match(line)
            if m:
                entries[m.group(2)] = m.group(1)
            elif line:
                bad.append(line)
    return entries, bad


def package_files(vp, folders=PACKAGE_DIRS):
    """Every file of the package in a version folder, relative to it: the rendition, subtitle,
    trickplay and trailer folders. The records, the chain above them and the original beside them are
    not part of the package."""
    out = []
    for d in folders:
        for root, dirs, files in os.walk(os.path.join(vp, d)):
            dirs[:] = sorted(x for x in dirs if not OS_ARTEFACTS.match(x))
            out += [os.path.relpath(os.path.join(root, f), vp) for f in sorted(files) if not OS_ARTEFACTS.match(f)]
    return sorted(out)


def extra_files(xp, originals):
    """Every file an extra's checksums must list, relative to its folder: extra.json, the originals
    that are there and every file of its package. Only the chain above them is left out."""
    named = [n for n in ["extra.json", *originals] if os.path.isfile(os.path.join(xp, n))]
    return sorted(named + package_files(xp, EXTRA_DIRS))


def walk_keys(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield k
            yield from walk_keys(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk_keys(v)


def walk_strings(o, key=None):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from walk_strings(v, k)
    elif isinstance(o, list):
        for v in o:
            yield from walk_strings(v, key)
    elif isinstance(o, str):
        yield key, o


# ---------------------------------------------------------------- schemas
def load_schemas(where):
    fc = FormatChecker()
    if "date-time" not in fc.checkers:
        raise SystemExit('date-time values cannot be checked in this environment: pip install "jsonschema[format-nongpl]>=4.23"')
    docs = {}
    for n in NAMES:
        if where.startswith("http"):
            with urllib.request.urlopen(f"{where.rstrip('/')}/{n}.schema.json", timeout=30) as r:
                docs[n] = json.load(r)
        else:
            docs[n] = json.load(open(os.path.join(where, f"{n}.schema.json")))
    registry = Registry().with_resources((BASE + f"{n}.schema.json", Resource.from_contents(d)) for n, d in docs.items())
    # JSON Schema counts 7.0 as an integer; the readers of the playback fields do not.
    types = Draft202012Validator.TYPE_CHECKER.redefine(
        "integer", lambda checker, x: isinstance(x, int) and not isinstance(x, bool) and INT64[0] <= x <= INT64[1])
    Strict = validators.extend(Draft202012Validator, type_checker=types)
    for n, d in docs.items():
        Draft202012Validator.check_schema(d)
        if d["$id"] != BASE + f"{n}.schema.json":
            raise SystemExit(f"{n}.schema.json: $id {d['$id']} is not {BASE}{n}.schema.json")
    return {n: Strict(docs[n], registry=registry, format_checker=fc) for n in DOCUMENTS}


def describe(e):
    """A short, specific reason: the deepest relevant error, never a dump of the whole document."""
    if e.context:
        ctx = list(e.context)
        if e.instance is not None:
            # an object that fails a "value or null" choice: the null branch's complaint is noise
            ctx = [c for c in ctx if not (c.validator == "type" and c.validator_value == "null")] or ctx
        deepest = max(len(c.absolute_path) for c in ctx)
        return describe(best_match([c for c in ctx if len(c.absolute_path) == deepest]))
    where = e.json_path
    if e.validator == "not" and isinstance(e.instance, dict):
        forbidden = [r for sub in (e.validator_value.get("anyOf") or [e.validator_value]) for r in (sub.get("required") or [])]
        present = [f for f in forbidden if f in e.instance]
        return f"{where}: must not have {', '.join(present) or 'this combination'} here"
    msg = e.message
    r = repr(e.instance)
    if len(r) > 80 and r in msg:
        msg = msg.replace(r, "<value>")
    return f"{where}: {msg[:240]}"


# ---------------------------------------------------------------- checker
class Checker:
    def __init__(self, validators, check_media, check_checksums=False):
        self.v = validators
        self.check_media = check_media or check_checksums
        self.check_checksums = check_checksums
        self.errors = []
        self.notes = []
        self.root_dir = None
        self.counts = {"movie": 0, "series": 0, "episode": 0, "people": 0, "versions": 0, "packages": 0,
                       "sources": 0, "images": 0, "events": 0, "extras": 0}

    def err(self, where, msg):
        self.errors.append(f"{where}: {msg}")

    def note(self, where, msg):
        """Something worth knowing that is not wrong: the tree is still a valid record."""
        self.notes.append(f"{where}: {msg}")

    def load(self, p):
        try:
            with open(p) as f:
                return json.load(f)
        except FileNotFoundError:
            self.err(p, "missing")
        except json.JSONDecodeError as e:
            self.err(p, f"not JSON: {e}")
        return None

    def document(self, kind, p, required=True):
        """Load one record and check it against its schema. Returns (document, whether it is valid);
        a document that fails its schema is still returned, so the checks that only need a name or an
        id can still run and report what is really wrong."""
        if not required and not os.path.isfile(p):
            return None, True
        doc = self.load(p)
        if doc is None:
            return None, False
        if not isinstance(doc, dict):
            self.err(p, "not a JSON object")
            return None, False
        return doc, self.schema_ok(kind, doc, p)

    def covered(self, folder, names, what):
        """folder/checksums.sha256 lists exactly names — no more, no fewer — and each digest is that of
        the file now there: the proof a write-once file is still the bytes that were written with it."""
        sums = os.path.join(folder, SUMS)
        if not os.path.isfile(sums):
            self.err(sums, f"missing: {what} is covered by a checksums file written with it "
                           f"({UPGRADE})")
            return
        entries, bad = read_sums(sums)
        for line in bad[:3]:
            self.err(sums, f"not a sha256sum line: {line[:80]!r}")
        for name in sorted(set(names) - set(entries)):
            self.err(sums, f"does not list {name}")
        for name in sorted(set(entries) - set(names)):
            self.err(sums, f"lists {name}, which is not {what}")
        for name in sorted(set(names) & set(entries)):
            f = os.path.join(folder, name)
            if os.path.isfile(f) and sha_file(f).split(":", 1)[1] != entries[name]:
                self.err(f, f"does not match the checksum {SUMS} recorded for it: changed after it was written")

    def schema_ok(self, kind, doc, where):
        errs = list(self.v[kind].iter_errors(doc))
        for e in sorted(errs, key=lambda e: list(map(str, e.absolute_path))):
            self.err(where, describe(e))
        for k, s in walk_strings(doc):
            if (k in ID_KEYS and ("\n" in s or "\r" in s)) or (k not in FREE_TEXT and s != s.rstrip("\r\n")):
                self.err(where, f"{k} contains a line break: {s!r}")
                errs.append(k)
        for k in walk_keys(doc):
            if k != k.strip():
                self.err(where, f"key {k!r} has surrounding whitespace")
                errs.append(k)
        return not errs

    # ------------------------------------------------------------ one item folder
    def item(self, d, expect_type, series=None):
        try:
            self._item(d, expect_type, series)
        except Exception as e:  # a malformed record must not stop the run
            self.err(d, f"cannot check: {type(e).__name__}: {e}")

    def _item(self, d, expect_type, series):
        ip = os.path.join(d, "item.json")
        item, valid = self.document("item", ip)
        if item is None:
            return
        folder = os.path.basename(d.rstrip("/"))
        if item.get("itemId") != folder:
            self.err(ip, f"itemId {item.get('itemId')} does not match folder {folder}")
        if item.get("type") != expect_type:
            self.err(ip, f"type {item.get('type')} where {expect_type} was expected")
        self.counts[expect_type] += 1
        self.covered(d, {"item.json"}, "item.json")

        allowed = SERIES_ENTRIES if expect_type == "series" else ITEM_ENTRIES
        for name in listdir(d):
            if name == "extras" and expect_type == "episode":
                self.err(os.path.join(d, name), "an episode has no extras: bonus material belongs to its series, "
                                                "in the series' own extras/ folder, where it may name its season")
            elif name not in allowed:
                self.err(os.path.join(d, name), f"unexpected entry in a {expect_type} folder"
                                                f"{'; media belongs in versions/<versionId>/' if expect_type != 'series' else ''}")
        events = self.events(d)
        removed = {e["versionId"] for e in events if e["kind"] == "version-removed" and e.get("versionId")}
        sources, versions, packages = {}, {}, {}
        if expect_type != "series":
            sources = self.sources(d)
            versions, packages = self.versions(d, sources, events, removed)
        extras = self.extras(d) if expect_type != "episode" else {}
        meta = self.metadata(d, item, versions, removed, extras)
        if not valid:
            return  # the rules that span files assume the documented shape
        self.extra_seasons(d, expect_type, extras, meta)

        ids = item.get("externalIds") or {}
        if expect_type == "movie" and set(ids) & {"tmdbTv", "tmdbSeason", "tmdbEpisode"}:
            self.err(ip, "a movie carries series or episode reference ids")
        if expect_type == "series" and set(ids) & {"tmdbMovie", "tmdbSeason", "tmdbEpisode", "tmdbCollection"}:
            self.err(ip, "a series carries movie, season or episode reference ids")
        if expect_type == "episode":
            if set(ids) & {"tmdbMovie", "tmdbCollection"}:
                self.err(ip, "an episode carries movie reference ids")
            self.episode(ip, item, meta, series)

        self.event_subjects(events, sources, versions, packages, removed)
        if expect_type == "series":
            self.series(d, item, meta)
        if self.check_media:
            self.hard_links(d)

    # ------------------------------------------------------------ metadata.json + metadata/
    def metadata(self, d, item, versions=(), removed=(), extras=()):
        where = os.path.join(d, "metadata.json")
        meta, valid = self.document("metadata", where)
        if meta is None:
            return None
        if meta.get("itemId") != item.get("itemId"):
            self.err(where, "itemId differs from item.json")
        if meta.get("type") != item.get("type"):
            self.err(where, "type differs from item.json")
        if not valid:
            return None
        md = os.path.join(d, "metadata")
        seasons = {s["number"] for s in (meta.get("series") or {}).get("seasons") or []}
        numbers = [s["number"] for s in (meta.get("series") or {}).get("seasons") or []]
        if len(numbers) != len(set(numbers)):
            self.err(where, "series.seasons lists a season number twice")
        listed = self.images(md, meta["images"], "metadata.json")
        for img in meta["images"]:
            f = os.path.join(md, img["file"])
            if item.get("type") != "series" and img.get("season") is not None:
                self.err(f, "season-specific image on a non-series item")
            if img.get("season") is not None and img["season"] not in seasons:
                self.err(f, f"names season {img['season']}, which metadata.json does not describe")
        if os.path.isdir(md):
            for name in listdir(md):
                if name not in listed:
                    self.unlisted(os.path.join(md, name), "metadata.json")
        self.projected_decisions(where, meta, versions, removed, extras)
        self.credits(where, meta)
        return meta

    def images(self, folder, entries, owner):
        """The images a projection lists, each against the file it names in folder: there, listed
        once, named by the hash of its own content, of the recorded size, type and dimensions.
        Returns the names listed."""
        listed = set()
        for img in entries:
            name = img["file"]
            f = os.path.join(folder, name)
            if name in listed:
                self.err(f, f"listed twice in {owner}")
            listed.add(name)
            if not os.path.isfile(f):
                self.err(f, f"listed in {owner} but does not exist")
                continue
            self.counts["images"] += 1
            digest = sha_file(f)
            if digest != img["sha256"]:
                self.err(f, f"sha256 does not match {owner}")
            if name.rsplit(".", 1)[0] != digest.split(":", 1)[1]:
                self.err(f, "file name is not the hash of its own content")
            if os.path.getsize(f) != img["sizeBytes"]:
                self.err(f, f"size {os.path.getsize(f)} != recorded {img['sizeBytes']}")
            ctype, w, h = sniff_image(f)
            if ctype != img["contentType"]:
                self.err(f, f"content is {ctype or 'not a known image'} but recorded as {img['contentType']}")
            if EXT_TYPE.get(name.rsplit(".", 1)[-1]) != img["contentType"]:
                self.err(f, f"extension does not match {img['contentType']}")
            for label, actual, recorded in (("width", w, img.get("width")), ("height", h, img.get("height"))):
                if recorded is not None and actual is None:
                    self.err(f, f"{label} is recorded as {recorded} but cannot be read from the file")
                elif recorded is not None and actual != recorded:
                    self.err(f, f"{label} is {actual} but recorded as {recorded}")
        return listed

    def unlisted(self, f, owner):
        """A file beside a projection that the projection does not list. An image named by the hash of
        its own bytes is one an earlier projection listed and a later one dropped: garbage the sweep
        collects, not a broken record, so it is a note. Anything else is an error."""
        m = IMAGE_NAME.match(os.path.basename(f))
        if m and os.path.isfile(f) and sha_file(f).split(":", 1)[1] == m.group(1):
            self.note(f, f"an image {owner} no longer lists; {SWEEP}")
        elif m and os.path.isfile(f):
            self.err(f, f"not listed in {owner}, and not named by the hash of its own content")
        else:
            self.err(f, f"not listed in {owner}" + ("" if owner == "metadata.json" else
                                                     f": not person.json and not an image {owner} lists"))

    def credits(self, where, meta):
        """A credit keys a person the database holds. Without a record in people/ the tree cannot
        restore that person's texts and images, which is worth saying — but the person may simply
        not have been projected yet, so it is a note."""
        for c in meta.get("credits") or []:
            pid = c["personId"]
            if not os.path.isfile(os.path.join(self.root_dir or "", "people", pid[:2], pid, "person.json")):
                self.note(where, f"credit {c['name']!r} ({c['role']}) names person {pid}, who has no "
                                 f"people/{pid[:2]}/{pid}/person.json yet")

    def projected_decisions(self, where, meta, versions, removed, extras=()):
        """metadata.library holds what the database decided about this item's storage, so every
        version and every extra it names has to be one that is really there."""
        lib = meta.get("library") or {}
        primary = lib.get("primaryVersionId")
        if primary and primary not in versions:
            self.err(where, f"library.primaryVersionId {primary} names "
                            + ("a version that was removed" if primary in removed else "no version folder"))
        for vid in sorted(lib.get("versionLabels") or {}):
            if vid not in versions:
                self.err(where, f"library.versionLabels names {vid}, which is "
                                + ("a removed version" if vid in removed else "no version folder"))
        for xid in sorted(lib.get("extras") or {}):
            if xid not in extras:
                self.err(where, f"library.extras names {xid}, which is no extras/<extraId>/ folder of this item")

    # ------------------------------------------------------------ events/
    def events(self, d):
        """events/<YYYYMMDDTHHMMSSZ>-<eventId8>-<kind>/, each holding event.json and the checksums
        written with it."""
        base = os.path.join(d, "events")
        out = []
        if not os.path.isdir(base):
            return out
        for name in listdir(base):
            p = os.path.join(base, name)
            m = EVENT_FOLDER.match(name)
            if not m or not os.path.isdir(p):
                self.err(p, f"an event record in the layout before 2026-10-02 (b): each event is a folder "
                            f"events/<YYYYMMDDTHHMMSSZ>-<eventId8>-<kind>/ holding event.json ({UPGRADE})"
                         if OLD_EVENT_FILE.match(name) and os.path.isfile(p) else
                         "not an events/<YYYYMMDDTHHMMSSZ>-<8 hex of the eventId>-<kind>/ folder")
                continue
            ep = os.path.join(p, "event.json")
            for entry in listdir(p):
                if entry not in ("event.json", SUMS):
                    self.err(os.path.join(p, entry), "not event.json or the checksums written with it")
            ev, valid = self.document("event", ep)
            if ev is None or not valid:
                continue
            self.counts["events"] += 1
            self.covered(p, {"event.json"}, "event.json")
            if m.group(3) != ev["kind"]:
                self.err(ep, f"folder name says {m.group(3)} but the record's kind is {ev['kind']}")
            if m.group(1) != stamp_of(ev["at"]):
                self.err(ep, f"folder name says {m.group(1)} but the record happened at {stamp_of(ev['at'])}")
            if not ev["eventId"].startswith(m.group(2)):
                self.err(ep, f"folder name says {m.group(2)}, which does not start the eventId {ev['eventId']}")
            out.append({"where": ep, **ev})
        return out

    # ------------------------------------------------------------ sources/
    def sources(self, d):
        """sources/<sourceId>/, each holding source.json, the probe and sidecars it names, and the
        checksums written with all of them."""
        base = os.path.join(d, "sources")
        records = {}
        if not os.path.isdir(base):
            return records
        for name in listdir(base):
            p = os.path.join(base, name)
            if not os.path.isdir(p):
                self.err(p, f"a source record in the layout before 2026-10-02 (b): each source is a folder "
                            f"sources/<sourceId>/ holding source.json ({UPGRADE})"
                         if name.endswith(".json") and UUID.match(name[:-5]) else "not a sources/<sourceId>/ folder")
                continue
            if not UUID.match(name):
                self.err(p, "a source folder is named by its sourceId")
                continue
            sp = os.path.join(p, "source.json")
            if not os.path.isfile(sp):
                self.err(p, "a source folder without its source.json record")
                continue
            src, valid = self.document("source", sp)
            if src is None or not valid:
                continue
            self.counts["sources"] += 1
            if src["sourceId"] != name:
                self.err(sp, f"sourceId {src['sourceId']} does not match the folder name")
                continue
            records[name] = src
            self.source_files(d, sp, src)
            named = {"source.json"} | {os.path.basename(x["file"]) for x in src.get("sidecars") or []} | \
                ({os.path.basename(src["probe"]["file"])} if src["probe"].get("file") else set())
            for entry in listdir(p):
                if entry not in named and entry != SUMS:
                    self.err(os.path.join(p, entry), "not the record, the probe, a sidecar or the checksums of this source")
            self.covered(p, named, "source.json, its probe or one of its sidecars")
        return records

    def source_files(self, d, where, src):
        pf, psha = src["probe"].get("file"), src["probe"].get("sha256")
        if pf:
            if pf != f"sources/{src['sourceId']}/ffprobe.json":
                self.err(where, f"probe file {pf} is not sources/{src['sourceId']}/ffprobe.json")
            f = os.path.join(d, pf)
            if not os.path.isfile(f):
                self.err(f, "probe file missing")
            elif sha_file(f) != psha:
                self.err(f, "probe sha256 does not match the source record")
        elif psha:
            self.err(where, "records a probe sha256 but no probe file")
        for sc in src.get("sidecars") or []:
            if not sc["file"].startswith(f"sources/{src['sourceId']}/"):
                self.err(where, f"sidecar {sc['file']} is not in sources/{src['sourceId']}/")
            f = os.path.join(d, sc["file"])
            if not os.path.isfile(f):
                self.err(f, "sidecar missing")
                continue
            if "sizeBytes" in sc and os.path.getsize(f) != sc["sizeBytes"]:
                self.err(f, "sidecar size does not match the source record")
            if "sha256" in sc and sha_file(f) != sc["sha256"]:
                self.err(f, "sidecar sha256 does not match the source record")

    # ------------------------------------------------------------ versions/
    def versions(self, d, sources, events, removed):
        base = os.path.join(d, "versions")
        versions, packages = {}, {}
        if os.path.isdir(base):
            for name in listdir(base):
                p = os.path.join(base, name)
                if not os.path.isdir(p):
                    self.err(p, "every version is a folder under versions/")
                    continue
                if not UUID.match(name):
                    self.err(p, "a version folder is named by its versionId")
                    continue
                if name in removed:
                    continue  # a version-removed event says to ignore this folder, so nothing in it counts
                got = self.version(d, p, name, sources, events)
                if got:
                    versions[name] = got[0]
                    if got[1]:
                        packages[got[1]["packageId"]] = got[1]
        return versions, packages

    def version(self, d, vp, vid, sources, events):
        where = os.path.join(vp, "version.json")
        v, valid = self.document("version", where)
        if v is None or not valid:
            return None
        self.counts["versions"] += 1
        if v["versionId"] != vid:
            self.err(where, f"versionId {v['versionId']} does not match folder {vid}")
        for name in listdir(vp):
            if name not in VERSION_ENTRIES and name not in v["originalFiles"]:
                self.err(os.path.join(vp, name), "not a record, the package or an original this version names")
        for sid in v["sourceIds"]:
            if sid not in sources:
                self.err(where, f"names source {sid}, which has no record under sources/")
        named = {sources[s]["file"]["name"] for s in v["sourceIds"] if s in sources}
        for name in v["originalFiles"]:
            if named and name not in named:
                self.err(where, f"originalFiles names {name}, which is not the file name of any source this version names")
        for kind in ("chapters", "segments"):
            marks = v.get(kind) or []
            if any(m["endMs"] < m["startMs"] for m in marks):
                self.err(where, f"a {kind[:-1]} ends before it starts")
        chapters = v.get("chapters") or []
        if any(chapters[i]["startMs"] > chapters[i + 1]["startMs"] for i in range(len(chapters) - 1)):
            self.err(where, "chapters are not in timeline order")
        if bool(chapters) != (v.get("chaptersFrom") is not None):
            self.err(where, "chaptersFrom must be set exactly when there are chapters")
        if any((sources[s]["essence"].get("chapters") for s in v["sourceIds"] if s in sources)) and not chapters:
            self.err(where, "the original carries chapter marks but the version keeps none")

        marker = os.path.isfile(os.path.join(vp, ".complete"))
        pkg, pkg_valid = self.document("package", os.path.join(vp, "package.json"), required=False)
        pkg = pkg if pkg_valid else None
        has_package = os.path.isfile(os.path.join(vp, "package.json"))
        if has_package != marker:
            self.err(vp, "package.json exists exactly when .complete does, and here only "
                         + ("package.json" if has_package else ".complete") + " is present")
        elif not marker and any(os.path.exists(os.path.join(vp, n)) for n in PACKAGE_DIRS + (SUMS,)):
            self.note(vp, f"a package that never finished: no .complete beside it; {SWEEP}")
        if pkg is not None:
            self.counts["packages"] += 1
            self.package(vp, v, pkg)
            if marker:
                self.chain(vp)
        gate = deletion_gate([sources[s]["essence"] for s in v["sourceIds"] if s in sources],
                             (pkg or {}).get("essence") or {})
        gone, whole = set(), False
        for e in [x for x in events if x["kind"] == "original-deleted" and x.get("versionId") == vid]:
            if not v["originalFiles"]:
                self.err(e["where"], f"version {vid} never named an original, so none can have been deleted")
            over = sorted(set(e.get("accepted") or []) - gate)
            if over:
                self.err(e["where"], f"accepted names {', '.join(over)}, which the records do not say the "
                                     f"package failed to carry; a person may accept less than the gate, never more")
            if e.get("sourceId"):
                if e["sourceId"] not in v["sourceIds"]:
                    self.err(e["where"], f"names source {e['sourceId']}, which version {vid} was not made from")
                gone.add(e["sourceId"])
            else:
                whole = True
        if self.check_media:
            self.media(vp, v, pkg, sources, gone, whole)
        return v, pkg

    def package(self, vp, v, pkg, what="version"):
        """A package and the record it was made for, v: a version's or an extra's."""
        where = os.path.join(vp, "package.json")
        if pkg["fidelity"]["lossless"] != (not pkg["fidelity"]["losses"]):
            self.err(where, "fidelity.lossless must be true exactly when losses is empty")
        if (pkg["role"] == "canonical") != (not v["originalFiles"]):
            self.err(where, f"role must be 'canonical' exactly when the {what} keeps no original "
                            f"({what}.json originalFiles is empty)"
                            + ("; a deletion afterwards is an event, not a rewrite" if what == "version" else ""))
        audio = pkg["renditions"]["audio"]
        video = pkg["renditions"]["video"]
        subs = pkg.get("subtitles") or []
        for kind, items in (("video", video), ("audio", audio), ("subtitle", subs)):
            ids = [x["id"] for x in items]
            if len(ids) != len(set(ids)):
                self.err(where, f"{kind} rendition ids are not unique")
        if audio and sum(1 for a in audio if a.get("default")) != 1:
            self.err(where, f"exactly one audio rendition must be default, found {sum(1 for a in audio if a.get('default'))}")
        if sum(1 for s in subs if s.get("default") and not s.get("forced")) > 1:
            self.err(where, "more than one non-forced subtitle is default")
        for x in audio + subs:
            kind = "audio" if x in audio else "subtitle"
            if x.get("purposeFrom") == "assumed" and x.get("purpose") not in ("dialogue", "main"):
                self.err(where, f"{kind} {x['id']} purpose {x.get('purpose')} cannot be assumed; only dialogue or main can")
            if (x.get("purposeFrom") is None) != (x.get("purpose") in (None, "unknown")):
                self.err(where, f"{kind} {x['id']} needs purposeFrom exactly when its purpose is known")
        for x in subs:
            if x.get("forced") and x.get("purpose") not in (None, "forced", "signs-songs", "unknown"):
                self.err(where, f"subtitle {x['id']} is flagged forced but its purpose is {x['purpose']}")
            if x.get("default") and (x.get("forced") or x.get("purpose") in ("forced", "signs-songs")):
                self.err(where, f"subtitle {x['id']} is a forced track flagged default: a reader would open with it instead of no subtitles")
        cs = pkg["checksums"]
        f = os.path.join(vp, cs["file"])
        if not os.path.isfile(f):
            self.err(where, f"checksums file {cs['file']} missing")
        elif sha_file(f) != cs["sha256"]:
            self.err(where, f"checksums file {cs['file']} does not match its recorded sha256")
        elif len(read_checksums(f)) != cs["files"]:
            self.err(where, f"checksums file lists {len(read_checksums(f))} files, the record says {cs['files']}")

    def head(self, vp, what="version"):
        """.complete, the head of the chain: sha256:<hex> of the package.json beside it, and nothing else."""
        mark = os.path.join(vp, ".complete")
        with open(mark, "rb") as f:
            body = f.read().decode("utf-8", "replace").strip()
        if not COMPLETE.match(body):
            self.err(mark, f"holds {body[:40]!r}, not sha256:<hex> of package.json, the head of the chain "
                           f"that covers this {what}" + (f" ({UPGRADE})" if what == "version" else ""))
        elif body != sha_file(os.path.join(vp, "package.json")):
            self.err(mark, "does not name this package.json: package.json changed after the package completed")

    def chain_links(self, sums):
        """The sha256sum file sums as {name: digest}, with what is wrong with it said: a line that is
        not one, and a link of the chain listed below itself."""
        listed, bad = read_sums(sums)
        for line in bad[:3]:
            self.err(sums, f"not a sha256sum line: {line[:80]!r}")
        why = {".complete": "which is written after it and holds the hash of package.json",
               "package.json": "which holds the hash of this file", SUMS: "itself"}
        for name in CHAIN:
            if name in listed:
                self.err(sums, f"lists {name}, {why[name]}: a link of the chain cannot be listed below itself")
        return listed

    def chain(self, vp):
        """.complete -> package.json -> checksums.sha256 -> version.json and every package file. The
        records are checked always; the package files, which can be many and large, with
        --check-media and --check-checksums."""
        self.head(vp)
        sums = os.path.join(vp, SUMS)
        if not os.path.isfile(sums):
            return  # package() says so
        listed = self.chain_links(sums)
        vj = os.path.join(vp, "version.json")
        if "version.json" not in listed:
            self.err(sums, f"does not list version.json, the record its package was made for ({UPGRADE})")
        elif sha_file(vj).split(":", 1)[1] != listed["version.json"]:
            self.err(vj, f"does not match the checksum {SUMS} recorded for it: changed after the package completed")

    # ------------------------------------------------------------ the bytes
    def media(self, vp, v, pkg, sources, gone, whole):
        """gone: sources an original-deleted event named one by one; whole: an event that named none,
        so every original of the version is gone."""
        for name in v["originalFiles"]:
            f = os.path.join(vp, name)
            sid = next((s for s in v["sourceIds"] if s in sources and sources[s]["file"]["name"] == name), None)
            src = sources.get(sid)
            if whole or sid in gone:
                if os.path.isfile(f):
                    self.err(f, "an original-deleted event names this original, but it is still here")
            elif not os.path.isfile(f):
                self.err(f, "original file missing, and no original-deleted event says it was removed")
            elif src and os.path.getsize(f) != src["file"]["sizeBytes"]:
                self.err(f, f"original is {os.path.getsize(f)} bytes, its source record says {src['file']['sizeBytes']}")
            elif src and qh1(f) != src["file"]["fixity"]["qh1"]:
                self.err(f, "original does not match its qh1 fixity")
        if pkg is None:
            return
        where = os.path.join(vp, "package.json")
        self.playable(vp, pkg)
        cs = pkg["checksums"]
        if os.path.isfile(os.path.join(vp, cs["file"])):
            listed = read_checksums(os.path.join(vp, cs["file"]))
            on_disk = package_files(vp) + (["version.json"] if os.path.isfile(os.path.join(vp, "version.json")) else [])
            for rel in sorted(set(on_disk) - set(listed)):
                self.err(where, f"package file {rel} is not in {cs['file']}")
            for rel in sorted(set(listed) - set(on_disk) - set(CHAIN)):
                self.err(where, f"{cs['file']} lists {rel}, which is not a file of this package")
            present = [rel for rel in listed if rel in set(on_disk)]
            total = sum(os.path.getsize(os.path.join(vp, rel)) for rel in present)
            if len(present) == len(listed) and total != cs["bytes"]:
                self.err(where, f"the files {cs['file']} lists total {total} bytes, the record says {cs['bytes']}")
            if self.check_checksums:
                for rel in present:
                    if sha_file(os.path.join(vp, rel)).split(":", 1)[1] != listed[rel]:
                        self.err(where, f"{rel} does not match its checksum")

    def playable(self, vp, pkg):
        """Every rendition folder, subtitle and trickplay sheet the package in vp names is there, and
        the trickplay cues cover its duration."""
        where = os.path.join(vp, "package.json")
        ren = pkg["renditions"]
        if not ren["video"]:
            self.err(where, "a package needs at least one video rendition")
        for r in ren["video"] + ren["audio"]:
            if not os.path.isdir(os.path.join(vp, r["dir"])):
                self.err(where, f"rendition dir {r['dir']} missing")
        for sub in pkg.get("subtitles") or []:
            if not os.path.isfile(os.path.join(vp, sub["path"])):
                self.err(where, f"subtitle {sub['path']} missing")
        tp = pkg.get("trickplay")
        if tp:
            vtt = os.path.join(vp, tp["vttPath"])
            if not os.path.isfile(vtt):
                self.err(where, f"trickplay {tp['vttPath']} missing")
            else:
                self.trickplay(where, tp, vtt, pkg["durationMs"])

    def trickplay(self, where, tp, vtt, duration_ms):
        """Every sprite sheet the VTT names exists, and the cues cover the whole duration."""
        base = os.path.dirname(vtt)
        cues, sheets = 0, set()
        for line in open(vtt, encoding="utf-8", errors="replace"):
            line = line.strip()
            if VTT_CUE.match(line):
                cues += 1
            elif line and not line.startswith("WEBVTT") and "#xywh=" in line:
                sheets.add(line.split("#", 1)[0])
        for sheet in sorted(sheets):
            if not os.path.isfile(os.path.join(base, sheet)):
                self.err(where, f"trickplay sprite sheet {sheet} named by the VTT is missing")
        expected = duration_ms // (tp["intervalSec"] * 1000)
        if cues < expected:
            self.err(where, f"trickplay covers {cues} of {expected} thumbnails")

    def hard_links(self, d):
        """A library is portable only if its files are its own: a hard link ties a file to another path."""
        linked = []
        for root, dirs, files in os.walk(d):
            dirs[:] = sorted(x for x in dirs if not OS_ARTEFACTS.match(x) and not (root == d and x == "episodes"))
            for f in sorted(files):
                p = os.path.join(root, f)
                if not OS_ARTEFACTS.match(f) and not os.path.islink(p) and os.stat(p).st_nlink > 1:
                    linked.append(os.path.relpath(p, d))
        if linked:
            self.err(d, f"{len(linked)} file(s) are hard links shared with another path, e.g. {linked[0]}; "
                        f"the library must hold independent files")

    # ------------------------------------------------------------ extras/
    def extras(self, d):
        """extras/<extraId>/, each a write-once folder: extra.json, the originals it keeps and/or a
        package, and the checksums written with them. Returns {extraId: the record, or None when it
        does not hold to its schema} for every extra folder that holds an extra.json."""
        base = os.path.join(d, "extras")
        found = {}
        if not os.path.isdir(base):
            return found
        for name in listdir(base):
            p = os.path.join(base, name)
            if not os.path.isdir(p):
                self.err(p, "every extra is a folder under extras/: extras/<extraId>/ holding extra.json")
                continue
            if not UUID.match(name):
                self.err(p, "an extra folder is named by its extraId")
                continue
            xp = os.path.join(p, "extra.json")
            if not os.path.isfile(xp):
                self.err(p, "an extra folder without its extra.json record")
                continue
            x, valid = self.document("extra", xp)
            found[name] = x if valid else None
            if x is None or not valid:
                continue
            self.counts["extras"] += 1
            if x["extraId"] != name:
                self.err(xp, f"extraId {x['extraId']} does not match folder {name}")
            self.extra(p, x)
        return found

    def extra(self, xp, x):
        """What one extra folder holds, whether it finished, and the checksums that cover it. A
        packaged extra is finished by its .complete, one that keeps only its original by its checksums
        file, written last; anything else never finished, and is a note, as a package that never
        finished is."""
        for name in listdir(xp):
            if name not in EXTRA_ENTRIES and name not in x["originalFiles"]:
                self.err(os.path.join(xp, name), "not a record, the package or an original this extra names")
        marker = os.path.isfile(os.path.join(xp, ".complete"))
        has_package = os.path.isfile(os.path.join(xp, "package.json"))
        if has_package != marker:
            self.err(xp, "package.json exists exactly when .complete does, and here only "
                         + ("package.json" if has_package else ".complete") + " is present")
            return
        pkg = None
        if not marker:
            if any(os.path.exists(os.path.join(xp, n)) for n in EXTRA_DIRS):
                self.note(xp, f"an extra whose package never finished: no .complete beside it; {SWEEP}")
                return
            if not os.path.isfile(os.path.join(xp, SUMS)):
                self.note(xp, "an extra that never finished: no checksums.sha256 beside it, so neither the "
                              "database nor a rebuild uses it until the writer that took it in finishes it")
                return
            if not x["originalFiles"]:
                self.err(os.path.join(xp, "extra.json"), "an extra that holds neither an original nor a package")
        else:
            pkg, ok = self.document("package", os.path.join(xp, "package.json"))
            pkg = pkg if ok else None
            if pkg is not None:
                self.package(xp, x, pkg, what="extra")
                if pkg.get("trailers"):
                    self.err(os.path.join(xp, "package.json"), "an extra's package lists no trailers: a trailer "
                                                               "that is a file of its own is an extra of kind trailer")
            self.head(xp, "extra")
        self.extra_covered(xp, x)
        if self.check_media and pkg is not None:
            self.playable(xp, pkg)
            self.extra_total(xp, pkg)

    def extra_covered(self, xp, x):
        """checksums.sha256 lists extra.json and every file beside it — the originals and every file of
        the package — and nothing else: never itself, package.json or .complete, the links above it.
        Listing is a walk of the folder; of the digests, extra.json's is checked always and the rest,
        which can be large, with --check-checksums."""
        sums = os.path.join(xp, SUMS)
        if not os.path.isfile(sums):
            return  # package() says so; an extra without a package and without checksums never finished
        listed = self.chain_links(sums)
        for name in x["originalFiles"]:
            if not os.path.isfile(os.path.join(xp, name)):
                self.err(os.path.join(xp, name), "an original extra.json names is not here: an extra keeps its "
                                                 "originals for as long as it exists")
        present = extra_files(xp, x["originalFiles"])
        for rel in sorted(set(present) - set(listed)):
            self.err(sums, f"does not list {rel}: an extra's checksums cover extra.json and every file beside it")
        for rel in sorted(set(listed) - set(present) - set(CHAIN) - set(x["originalFiles"])):
            self.err(sums, f"lists {rel}, which is not a file of this extra")
        xj = os.path.join(xp, "extra.json")
        if "extra.json" in listed and sha_file(xj).split(":", 1)[1] != listed["extra.json"]:
            self.err(xj, f"does not match the checksum {SUMS} recorded for it: changed after it was written")
        if self.check_checksums:
            for rel in sorted(set(present) & set(listed) - {"extra.json"}):
                if sha_file(os.path.join(xp, rel)).split(":", 1)[1] != listed[rel]:
                    self.err(os.path.join(xp, rel), "does not match its checksum")

    def extra_total(self, xp, pkg):
        """The files an extra's checksums list add up to the size its package record says."""
        cs = pkg["checksums"]
        sums = os.path.join(xp, cs["file"])
        if not os.path.isfile(sums):
            return
        listed = read_checksums(sums)
        present = [rel for rel in listed if os.path.isfile(os.path.join(xp, rel))]
        total = sum(os.path.getsize(os.path.join(xp, rel)) for rel in present)
        if len(present) == len(listed) and total != cs["bytes"]:
            self.err(os.path.join(xp, "package.json"),
                     f"the files {cs['file']} lists total {total} bytes, the record says {cs['bytes']}")

    def extra_seasons(self, d, expect_type, extras, meta):
        """A season is something only a series has, and an extra can only belong to one its projection
        knows."""
        seasons = {s["number"] for s in ((meta or {}).get("series") or {}).get("seasons") or []}
        for xid, x in sorted(extras.items()):
            if not x or "seasonNumber" not in x:
                continue
            where = os.path.join(d, "extras", xid, "extra.json")
            if expect_type != "series":
                self.err(where, f"names season {x['seasonNumber']}, but only a series' extra belongs to a season")
            elif meta is not None and x["seasonNumber"] not in seasons:
                self.err(where, f"season {x['seasonNumber']}, which the series' metadata.json does not list")

    # ------------------------------------------------------------ events against the records
    def event_subjects(self, events, sources, versions, packages, removed):
        for e in events:
            subjects = [("sourceId", e.get("sourceId"), sources, "source record under sources/"),
                        ("packageId", e.get("packageId"), packages, "package of any version")]
            if e["kind"] != "version-removed":
                # the one event whose subject is allowed to be gone: that is what it records
                subjects.append(("versionId", e.get("versionId"), versions, "version folder under versions/"))
            by = e.get("supersededBy") or {}
            subjects += [("supersededBy.versionId", by.get("versionId"), versions, "version folder under versions/"),
                         ("supersededBy.packageId", by.get("packageId"), packages, "package of any version")]
            for field, value, known, what in subjects:
                if value and value not in known:
                    self.err(e["where"], f"{field} {value} names no {what}"
                                         + (", and a removed version is not one" if value in removed else ""))
            if by.get("versionId") and by["versionId"] == e.get("versionId"):
                self.err(e["where"], "supersededBy names the version it supersedes; a re-package is a new folder")

    # ------------------------------------------------------------ series and episodes
    def series(self, d, item, meta):
        ed = os.path.join(d, "episodes")
        folders = set()
        if os.path.isdir(ed):
            for name in listdir(ed):
                if os.path.isdir(os.path.join(ed, name)):
                    folders.add(name)
                else:
                    self.err(os.path.join(ed, name), "unexpected file among the episode folders")
        context = {"itemId": item["itemId"], "tmdbTv": (item.get("externalIds") or {}).get("tmdbTv"),
                   "seasons": {s["number"] for s in ((meta or {}).get("series") or {}).get("seasons") or []},
                   "listed": {}, "places": {}, "where": os.path.join(d, "metadata.json")}
        for name in sorted(folders):
            self.item(os.path.join(ed, name), "episode", series=context)

    def episode(self, where, item, meta, series):
        if series is None:
            self.err(where, "episode outside a series folder")
            return
        if item["seriesId"] != series["itemId"]:
            self.err(where, f"seriesId {item['seriesId']} is not the enclosing series {series['itemId']}")
        tv = (item.get("externalIds") or {}).get("tmdbTv")
        if tv and series["tmdbTv"] and tv != series["tmdbTv"]:
            self.err(where, f"externalIds.tmdbTv {tv} differs from the series' {series['tmdbTv']}")
        season, number = item["seasonNumber"], item["episodeNumber"]
        code = item.get("episodeCode")
        if code and (int(code[1:code.index("E")]), int(code[code.index("E") + 1:])) != (season, number):
            self.err(where, f"episodeCode {code} differs from seasonNumber {season} and episodeNumber {number}")
        if series["seasons"] and season not in series["seasons"]:
            self.err(where, f"season {season}, which the series' metadata.json does not list")
        other = series["listed"].get((season, number))
        if other:
            self.err(where, f"S{season:02d}E{number:02d} is already the numbering of episode {other}")
        series["listed"][(season, number)] = item["itemId"]
        self.numbering(os.path.join(os.path.dirname(where), "metadata.json"), item, meta, series)

    def numbering(self, where, item, meta, series):
        """The episode's place in each ordering. Nothing on the series repeats it, so the only things
        to check are that it does not contradict the numbers item.json was created with, and that no
        two episodes of the series claim one place in one ordering."""
        mine = ((meta or {}).get("library") or {}).get("numbering") or {}
        aired = mine.get("aired")
        if aired and (aired.get("season"), aired["episode"]) != (item["seasonNumber"], item["episodeNumber"]):
            self.err(where, f"library.numbering.aired is S{aired.get('season')}E{aired['episode']}, but item.json was "
                            f"created with S{item['seasonNumber']}E{item['episodeNumber']}")
        for name, place in sorted(mine.items()):
            at = (name, place.get("season"), place["episode"])
            other = series["places"].get(at)
            if other:
                self.err(where, f"library.numbering.{name} puts this episode where {other} already is")
            series["places"][at] = item["itemId"]

    # ------------------------------------------------------------ people/
    def person(self, d):
        try:
            self._person(d)
        except Exception as e:  # a malformed record must not stop the run
            self.err(d, f"cannot check: {type(e).__name__}: {e}")

    def _person(self, d):
        where = os.path.join(d, "person.json")
        doc, valid = self.document("person", where)
        if doc is None:
            return
        self.counts["people"] += 1
        folder = os.path.basename(d.rstrip("/"))
        if doc.get("personId") != folder:
            self.err(where, f"personId {doc.get('personId')} does not match folder {folder}")
        if not valid:
            return
        listed = self.images(d, doc["images"], "person.json")
        for name in listdir(d):
            if name != "person.json" and name not in listed:
                self.unlisted(os.path.join(d, name), "person.json")
        born, died = doc.get("birthDate"), doc.get("deathDate")
        if born and died and died[:min(len(born), len(died))] < born[:min(len(born), len(died))]:
            self.err(where, f"deathDate {died} is before birthDate {born}")
        if self.check_media:
            self.hard_links(d)

    def shards(self, base, what):
        """The <aa>/<id>/ folders of one category, with the rules every category shares."""
        for shard in listdir(base):
            sd = os.path.join(base, shard)
            if not os.path.isdir(sd):
                self.err(sd, "unexpected file among the shard folders")
                continue
            if not re.fullmatch(r"[0-9a-f]{2}", shard):
                self.err(sd, f"shard folders are the first two characters of {what} id")
            for name in listdir(sd):
                p = os.path.join(sd, name)
                if name[:2] != shard:
                    self.err(p, f"{what.split()[-1]} folder is not in shard {name[:2]}")
                if not os.path.isdir(p):
                    self.err(p, "unexpected file in a shard folder")
                    continue
                yield p

    # ------------------------------------------------------------ roots
    def root(self, r):
        self.root_dir = r
        found = 0
        for name in listdir(r):
            if name == "_swept":
                self.err(os.path.join(r, name), "the quarantine of a library-v2-sweep.py --apply that did not "
                                                "finish; the next --apply finishes it")
            elif name not in ROOT_ENTRIES:
                self.err(os.path.join(r, name), "a library root holds only movies/, series/ and people/")
        for category, kind in CATEGORIES:
            base = os.path.join(r, category)
            if not os.path.isdir(base):
                continue
            for p in self.shards(base, "an item"):
                self.item(p, kind)
                found += 1
        if os.path.isdir(os.path.join(r, "people")):
            for p in self.shards(os.path.join(r, "people"), "a person"):
                self.person(p)
        if not found:
            self.err(r, "no items found under movies/ or series/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="+")
    ap.add_argument("--schemas", default=HERE)
    ap.add_argument("--check-media", action="store_true")
    ap.add_argument("--check-checksums", action="store_true",
                    help="also hash every package file against its checksums file (implies --check-media)")
    args = ap.parse_args()
    c = Checker(load_schemas(args.schemas), args.check_media, args.check_checksums)
    for r in args.roots:
        c.root(r)
    print("checked:", c.counts)
    for n in c.notes[:50]:
        print("  note " + n)
    if len(c.notes) > 50:
        print(f"  ... and {len(c.notes) - 50} more notes")
    if c.errors:
        for e in c.errors[:200]:
            print("  " + e)
        if len(c.errors) > 200:
            print(f"  ... and {len(c.errors) - 200} more")
        print(f"FAILED: {len(c.errors)} problem(s)")
        sys.exit(1)
    print("OK")


if __name__ == "__main__":
    main()
