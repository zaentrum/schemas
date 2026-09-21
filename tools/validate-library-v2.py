#!/usr/bin/env python3
"""Validate a zaentrum library v2 tree against the JSON Schemas and the rules that span files.

Usage:
  validate-library-v2.py [--check-media | --check-checksums] [--schemas URL-or-dir] ROOT [ROOT ...]

ROOT is a folder holding movies/ and/or series/. In v2 the database is the working copy and this
tree is the record that can rebuild it: every file is either written once (item, source, version,
package, event) or replaced whole by the one service that owns it (metadata). Nothing here is
merged, so the rules below are about records agreeing with each other and with the bytes beside
them, never about a document being up to date.

  layout       only movies/ and series/ at the root; shard folders of two characters; itemId equals
               the folder name and its shard; an item folder holds only item.json, metadata.json,
               metadata/, sources/, versions/, events/ (a series: episodes/ instead of sources/ and
               versions/) — so no media can sit in the item folder
  versions     every version is a folder under versions/ whose name is its versionId; its sourceIds
               name source records that exist and its originalFiles are those sources' file names;
               package.json exists exactly when .complete does; a package is canonical exactly when
               the version keeps no original; lossless means no losses; chapters are ordered and
               kept when the original carried them; every track says what it is for and the playlist
               flags do not contradict it
  metadata     same item and type as item.json; every image exists with the recorded hash, size,
               content type and dimensions, is named by its own content hash, is listed once, and
               nothing unlisted sits in metadata/; library.primaryVersionId and library.versionLabels
               name versions that are really there
  series       an episode names its enclosing series, agrees with it on the reference id, numbers
               itself consistently, does not collide with another episode, and sits in a season the
               series' metadata lists; the default ordering lists exactly the episode folders, every
               ordering lists only episodes that exist, and each episode's own numbering agrees with
               the series' ordering of the same name and with the aired numbers in its item.json
  events       the file name is the event's own moment and kind; every source, version and package
               it names exists (a version-removed event is the exception — its folder may be gone,
               and a folder it names is ignored altogether); a deletion names a version that had an
               original, one of that version's own sources, and accepts exactly what the version
               says deleting it costs; a package-superseded event names a successor that exists in
               another version folder
  checksums    the checksums file matches its recorded hash and file count
  --check-media  no file under an item folder is a hard link shared with another path; every
               original a version names is there with its size and qh1, unless an original-deleted
               event covers it — in which case it must NOT be there; every rendition folder,
               subtitle and trickplay sheet the package names exists and the cues cover the
               duration; checksums.sha256 lists exactly the package's files with their total size
  --check-checksums  also hash every package file (implies --check-media)

The schemas are loaded from the library/v2 folder next to this tool by default; pass
--schemas https://zaentrum.github.io/schemas/library/v2 to use the published copies.
Needs: pip install "jsonschema[format-nongpl]>=4.23" referencing
"""
import argparse, datetime, hashlib, json, os, re, sys, urllib.request
from jsonschema import Draft202012Validator, FormatChecker, validators
from jsonschema.exceptions import best_match
from referencing import Registry, Resource

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "library", "v2")
NAMES = ["defs", "item", "metadata", "source", "version", "package", "event"]
DOCUMENTS = ["item", "metadata", "source", "version", "package", "event"]
BASE = "https://zaentrum.github.io/schemas/library/v2/"

CATEGORIES = (("movies", "movie"), ("series", "series"))
ITEM_ENTRIES = {"item.json", "metadata.json", "metadata", "sources", "versions", "events"}
SERIES_ENTRIES = {"item.json", "metadata.json", "metadata", "episodes", "events"}
VERSION_ENTRIES = {"version.json", "package.json", "checksums.sha256", ".complete", "hls", "subs", "trickplay", "trailers"}
PACKAGE_DIRS = ("hls", "subs", "trickplay", "trailers")
PACKAGE_MARKERS = (".complete",)
EVENT_KINDS = ("original-deleted", "version-removed", "package-superseded", "source-removed", "note")
EVENT_NAME = re.compile(r"^(\d{8}T\d{6}Z)(?:-([0-9a-f]{8}))?-(" + "|".join(EVENT_KINDS) + r")\.json$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
VTT_CUE = re.compile(r"^(\d+):(\d\d):(\d\d)\.(\d{3}) --> (\d+):(\d\d):(\d\d)\.(\d{3})")
OS_ARTEFACTS = re.compile(r"^(\.DS_Store|\._.*|Thumbs\.db|desktop\.ini|@eaDir|\.@__thumb|#recycle|\.AppleDouble)$")
EXT_TYPE = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
ID_KEYS = {"schema", "itemId", "seriesId", "sourceId", "versionId", "packageId", "eventId", "personId", "file",
           "path", "dir", "vttPath", "manifestPath", "originalFile", "name", "language", "type", "kind",
           "tmdbMovie", "tmdbTv", "tmdbSeason", "tmdbEpisode", "tmdbCollection", "imdb", "tvdb", "tmdbPerson",
           "sha256", "qh1"}
FREE_TEXT = {"overview", "tagline", "notes", "note", "detail", "reason"}
INT64 = (-(1 << 63), (1 << 63) - 1)


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


def package_files(vp):
    """Every file of the package in a version folder, relative to it: the rendition, subtitle,
    trickplay and trailer folders and the completion marker. The records, the checksums file and the
    original next to them are not part of the package."""
    out = []
    for d in PACKAGE_DIRS:
        for root, dirs, files in os.walk(os.path.join(vp, d)):
            dirs[:] = sorted(x for x in dirs if not OS_ARTEFACTS.match(x))
            out += [os.path.relpath(os.path.join(root, f), vp) for f in sorted(files) if not OS_ARTEFACTS.match(f)]
    out += [m for m in PACKAGE_MARKERS if os.path.isfile(os.path.join(vp, m))]
    return sorted(out)


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
        self.counts = {"movie": 0, "series": 0, "episode": 0, "versions": 0, "packages": 0,
                       "sources": 0, "images": 0, "events": 0}

    def err(self, where, msg):
        self.errors.append(f"{where}: {msg}")

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

        allowed = set(SERIES_ENTRIES if expect_type == "series" else ITEM_ENTRIES)
        for name in listdir(d):
            if name not in allowed:
                self.err(os.path.join(d, name), f"unexpected entry in a {expect_type} folder"
                                                f"{'; media belongs in versions/<versionId>/' if expect_type != 'series' else ''}")
        events = self.events(d)
        removed = {e["versionId"] for e in events if e["kind"] == "version-removed" and e.get("versionId")}
        sources, versions, packages = {}, {}, {}
        if expect_type != "series":
            sources = self.sources(d)
            versions, packages = self.versions(d, sources, events, removed)
        meta = self.metadata(d, item, versions, removed)
        if not valid:
            return  # the rules that span files assume the documented shape

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
    def metadata(self, d, item, versions=(), removed=()):
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
        listed = set()
        for img in meta["images"]:
            name = img["file"]
            f = os.path.join(md, name)
            if name in listed:
                self.err(f, "listed twice in metadata.json")
            listed.add(name)
            if not os.path.isfile(f):
                self.err(f, "listed in metadata.json but does not exist")
                continue
            self.counts["images"] += 1
            digest = sha_file(f)
            if digest != img["sha256"]:
                self.err(f, "sha256 does not match metadata.json")
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
            if item.get("type") != "series" and img.get("season") is not None:
                self.err(f, "season-specific image on a non-series item")
            if img.get("season") is not None and img["season"] not in seasons:
                self.err(f, f"names season {img['season']}, which metadata.json does not describe")
        if os.path.isdir(md):
            for name in listdir(md):
                if name not in listed:
                    self.err(os.path.join(md, name), "not listed in metadata.json")
        self.projected_decisions(where, meta, versions, removed)
        return meta

    def projected_decisions(self, where, meta, versions, removed):
        """metadata.library holds what the database decided about this item's storage, so every
        version it names has to be one that is really there."""
        lib = meta.get("library") or {}
        primary = lib.get("primaryVersionId")
        if primary and primary not in versions:
            self.err(where, f"library.primaryVersionId {primary} names "
                            + ("a version that was removed" if primary in removed else "no version folder"))
        for vid in sorted(lib.get("versionLabels") or {}):
            if vid not in versions:
                self.err(where, f"library.versionLabels names {vid}, which is "
                                + ("a removed version" if vid in removed else "no version folder"))

    # ------------------------------------------------------------ events/
    def events(self, d):
        base = os.path.join(d, "events")
        out = []
        if not os.path.isdir(base):
            return out
        for name in listdir(base):
            p = os.path.join(base, name)
            m = EVENT_NAME.match(name)
            if not m:
                self.err(p, "not an events/<YYYYMMDDTHHMMSSZ>[-<8 hex of the eventId>]-<kind>.json record")
                continue
            ev, valid = self.document("event", p)
            if ev is None or not valid:
                continue
            self.counts["events"] += 1
            if m.group(3) != ev["kind"]:
                self.err(p, f"file name says {m.group(3)} but the record's kind is {ev['kind']}")
            if m.group(1) != stamp_of(ev["at"]):
                self.err(p, f"file name says {m.group(1)} but the record happened at {stamp_of(ev['at'])}")
            if m.group(2) and not ev["eventId"].startswith(m.group(2)):
                self.err(p, f"file name says {m.group(2)}, which does not start the eventId {ev['eventId']}")
            out.append({"where": p, **ev})
        return out

    # ------------------------------------------------------------ sources/
    def sources(self, d):
        base = os.path.join(d, "sources")
        records, folders = {}, set()
        if not os.path.isdir(base):
            return records
        for name in listdir(base):
            p = os.path.join(base, name)
            if os.path.isdir(p):
                folders.add(name)
                continue
            if not (name.endswith(".json") and UUID.match(name[:-5])):
                self.err(p, "not a sources/<sourceId>.json record")
                continue
            src, valid = self.document("source", p)
            if src is None or not valid:
                continue
            self.counts["sources"] += 1
            if src["sourceId"] != name[:-5]:
                self.err(p, f"sourceId {src['sourceId']} does not match the file name")
                continue
            records[src["sourceId"]] = src
            self.source_files(d, p, src)
        for name in sorted(folders):
            if name not in records:
                self.err(os.path.join(base, name), "no sources/<sourceId>.json record names this folder")
                continue
            known = {os.path.basename(records[name]["probe"].get("file") or "")} | \
                    {os.path.basename(x["file"]) for x in records[name].get("sidecars") or []}
            for entry in listdir(os.path.join(base, name)):
                if entry not in known:
                    self.err(os.path.join(base, name, entry), "not the probe or a sidecar of this source")
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
        if pkg is not None:
            self.counts["packages"] += 1
            self.package(vp, v, pkg)
        gone, whole = set(), False
        for e in [x for x in events if x["kind"] == "original-deleted" and x.get("versionId") == vid]:
            if not v["originalFiles"]:
                self.err(e["where"], f"version {vid} never named an original, so none can have been deleted")
            if e.get("accepted") is not None and e["accepted"] != v["lostIfOriginalDeleted"]:
                self.err(e["where"], "accepted is not what the version says deleting its originals costs "
                                     f"({v['lostIfOriginalDeleted']})")
            if e.get("sourceId"):
                if e["sourceId"] not in v["sourceIds"]:
                    self.err(e["where"], f"names source {e['sourceId']}, which version {vid} was not made from")
                gone.add(e["sourceId"])
            else:
                whole = True
        if self.check_media:
            self.media(vp, v, pkg, sources, gone, whole)
        return v, pkg

    def package(self, vp, v, pkg):
        where = os.path.join(vp, "package.json")
        if pkg["fidelity"]["lossless"] != (not pkg["fidelity"]["losses"]):
            self.err(where, "fidelity.lossless must be true exactly when losses is empty")
        if (pkg["role"] == "canonical") != (not v["originalFiles"]):
            self.err(where, "role must be 'canonical' exactly when the version keeps no original "
                            "(version.json originalFiles is empty); a deletion afterwards is an event, not a rewrite")
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
        cs = pkg["checksums"]
        if os.path.isfile(os.path.join(vp, cs["file"])):
            listed = read_checksums(os.path.join(vp, cs["file"]))
            on_disk = package_files(vp)
            for rel in sorted(set(on_disk) - set(listed)):
                self.err(where, f"package file {rel} is not in {cs['file']}")
            for rel in sorted(set(listed) - set(on_disk)):
                self.err(where, f"{cs['file']} lists {rel}, which is not a file of this package")
            present = [rel for rel in listed if rel in set(on_disk)]
            total = sum(os.path.getsize(os.path.join(vp, rel)) for rel in present)
            if len(present) == len(listed) and total != cs["bytes"]:
                self.err(where, f"package files total {total} bytes, the record says {cs['bytes']}")
            if self.check_checksums:
                for rel in present:
                    if sha_file(os.path.join(vp, rel)).split(":", 1)[1] != listed[rel]:
                        self.err(where, f"{rel} does not match its checksum")

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
        lib = (meta or {}).get("library") or {}
        orderings = lib.get("orderings") or {}
        where = os.path.join(d, "metadata.json")
        ed = os.path.join(d, "episodes")
        folders = set()
        if os.path.isdir(ed):
            for name in listdir(ed):
                if os.path.isdir(os.path.join(ed, name)):
                    folders.add(name)
                else:
                    self.err(os.path.join(ed, name), "unexpected file among the episode folders")
        default = lib.get("defaultOrdering")
        if orderings and default and default not in orderings:
            self.err(where, f"library.defaultOrdering is {default}, which library.orderings does not describe")
        for name, entries in sorted(orderings.items()):
            listed = [e["itemId"] for e in entries]
            if len(listed) != len(set(listed)):
                self.err(where, f"library.orderings.{name} lists an episode twice")
            for missing in sorted(set(listed) - folders):
                self.err(where, f"library.orderings.{name} lists {missing}, which is no episode folder of this series")
            if name == default:
                for absent in sorted(folders - set(listed)):
                    self.err(where, f"library.orderings.{name} is the default ordering but leaves out episode {absent}")
        context = {"itemId": item["itemId"], "tmdbTv": (item.get("externalIds") or {}).get("tmdbTv"),
                   "seasons": {s["number"] for s in ((meta or {}).get("series") or {}).get("seasons") or []},
                   "orderings": orderings, "listed": {}, "where": where}
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
        """The episode's place in each ordering, against the series' projection of the same ordering
        and against the aired numbering item.json was created with."""
        mine = ((meta or {}).get("library") or {}).get("numbering") or {}
        aired = mine.get("aired")
        if aired and (aired.get("season"), aired["episode"]) != (item["seasonNumber"], item["episodeNumber"]):
            self.err(where, f"library.numbering.aired is S{aired.get('season')}E{aired['episode']}, but item.json was "
                            f"created with S{item['seasonNumber']}E{item['episodeNumber']}")
        for name, place in sorted(mine.items()):
            entries = series["orderings"].get(name)
            if entries is None:
                if series["orderings"]:
                    self.err(where, f"library.numbering.{name} is an ordering the series does not describe")
                continue
            theirs = next((e for e in entries if e["itemId"] == item["itemId"]), None)
            if theirs is None:
                self.err(where, f"library.numbering.{name} places this episode in an ordering that leaves it out")
            elif (theirs.get("season"), theirs["episode"], theirs.get("episodeEnd")) != \
                    (place.get("season"), place["episode"], place.get("episodeEnd")):
                self.err(where, f"library.numbering.{name} disagrees with the series' ordering of the same name")
        for name, entries in sorted(series["orderings"].items()):
            if any(e["itemId"] == item["itemId"] for e in entries) and name not in mine:
                self.err(where, f"the series' {name} ordering places this episode, but it records no numbering for it")

    # ------------------------------------------------------------ roots
    def root(self, r):
        found = 0
        known = {c for c, _ in CATEGORIES}
        for name in listdir(r):
            if name not in known:
                self.err(os.path.join(r, name), "a library root holds only movies/ and series/")
        for category, kind in CATEGORIES:
            base = os.path.join(r, category)
            if not os.path.isdir(base):
                continue
            for shard in listdir(base):
                sd = os.path.join(base, shard)
                if not os.path.isdir(sd):
                    self.err(sd, "unexpected file among the shard folders")
                    continue
                if not re.fullmatch(r"[0-9a-f]{2}", shard):
                    self.err(sd, "shard folders are the first two characters of an item id")
                for iid in listdir(sd):
                    p = os.path.join(sd, iid)
                    if iid[:2] != shard:
                        self.err(p, f"item folder is not in shard {iid[:2]}")
                    if not os.path.isdir(p):
                        self.err(p, "unexpected file in a shard folder")
                        continue
                    self.item(p, kind)
                    found += 1
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
