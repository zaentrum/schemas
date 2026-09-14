#!/usr/bin/env python3
"""Validate a zaentrum library tree against the JSON Schemas and its own cross-file rules.

Usage:
  validate-library.py [--check-media | --check-checksums] [--schemas URL-or-dir] ROOT [ROOT ...]

ROOT is a folder holding movies/ and/or series/. Every item folder must contain manifest.json and
metadata/metadata.json. Beyond JSON Schema, this checks what spans files or needs arithmetic:

  folders      itemId equals the folder name and the folder sits in shard <first two chars>; an item
               folder holds only known entries; source/ and versions/ hold only what the manifest names
  metadata     same item and type as the manifest; every image exists with the recorded sha256, size,
               content type and dimensions, is listed once, and nothing unlisted sits in metadata/
  series       season numbers are unique; the series lists exactly the folders under episodes/; each
               episode names the series, carries the series' TV id, and agrees with the listing and with
               its version 2 numbering fields
  versions     unique ids, exactly one primary, '.' or versions/<own id>/; the top-level playback fields
               exist exactly when the version stored in the item folder has a package; one default audio
               rendition per playback set; every track says what it is for, and the version 2 default and forced
               hints do not contradict it (a forced track is never flagged default); lossless means no losses;
               truth, role and source state agree; probe files and sidecars match their hashes
  timeline     chapters and detected ranges on each version are well formed, and chapter marks of the original
               are kept on the version
  checksums    a package's checksums file matches its recorded hash and file count
  --check-media no package or original file is a hard link shared with another path; every original file a
               manifest places in a version folder exists with its size and qh1; every
               playback path exists, a complete or stale package has a video rendition (and audio when
               its original has audio), a .complete marker sits exactly where such a package is, every trickplay
               sprite sheet the VTT names exists and the cues cover the duration, and the checksums file lists
               exactly the package's files with the recorded total size
  --check-checksums  also hash every package file (implies --check-media)

The schemas are loaded from the library/v1 folder next to this tool by default; pass
--schemas https://zaentrum.github.io/schemas/library/v1 to use the published copies.
Needs: pip install "jsonschema[format-nongpl]>=4.23" referencing
"""
import argparse, hashlib, json, os, re, sys, urllib.request
from jsonschema import Draft202012Validator, FormatChecker, validators
from jsonschema.exceptions import best_match
from referencing import Registry, Resource

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "library", "v1")
NAMES = ["defs", "manifest", "metadata"]
BASE = "https://zaentrum.github.io/schemas/library/v1/"
ITEM_ENTRIES = {"manifest.json", "metadata", "source", "versions", "hls", "subs", "trickplay", "trailers", ".complete", ".failed", ".packaging", "checksums.sha256"}
PACKAGE_DIRS = ("hls", "subs", "trickplay", "trailers")
PACKAGE_MARKERS = (".complete",)
VTT_CUE = re.compile(r"^(\d+):(\d\d):(\d\d)\.(\d{3}) --> (\d+):(\d\d):(\d\d)\.(\d{3})")


def package_files(vp):
    """Every file of the package stored in a version folder, relative to it: rendition, subtitle, trickplay and
    trailer folders and the completion marker. Documents, metadata and other versions are not part of it."""
    out = []
    for d in PACKAGE_DIRS:
        base = os.path.join(vp, d)
        for root, dirs, files in os.walk(base):
            dirs[:] = sorted(x for x in dirs if not OS_ARTEFACTS.match(x))
            for f in sorted(files):
                if not OS_ARTEFACTS.match(f):
                    out.append(os.path.relpath(os.path.join(root, f), vp))
    out += [m for m in PACKAGE_MARKERS if os.path.isfile(os.path.join(vp, m))]
    return sorted(out)


def read_checksums(path):
    entries = {}
    for line in open(path, encoding="utf-8"):
        line = line.rstrip("\n")
        if not line:
            continue
        digest, _, rel = line.partition("  ")
        entries[rel] = digest
    return entries
SERIES_ENTRIES = {"manifest.json", "metadata", "episodes"}
ID_KEYS = {"schema", "itemId", "id", "seriesId", "personId", "path", "dir", "file", "vttPath", "manifestPath", "language", "type", "kind",
           "tmdbMovie", "tmdbTv", "tmdbSeason", "tmdbEpisode", "tmdbCollection", "imdb", "tvdb", "tmdbPerson", "sha256", "qh1"}
SEASON_EPISODE = re.compile(r"^S(\d+)E(\d+)$")
FREE_TEXT = {"overview", "tagline", "notes", "note", "review", "detail", "deletionReason"}
OS_ARTEFACTS = re.compile(r"^(\.DS_Store|\._.*|Thumbs\.db|desktop\.ini|@eaDir|\.@__thumb|#recycle|\.AppleDouble)$")
INT64 = (-(1 << 63), (1 << 63) - 1)
UNDETERMINED = {"und", "mul", "zxx", ""}
ISO639_2 = {"eng": "en", "ger": "de", "deu": "de", "fre": "fr", "fra": "fr", "ita": "it", "spa": "es", "jpn": "ja", "chi": "zh",
            "zho": "zh", "dut": "nl", "nld": "nl", "por": "pt", "rus": "ru", "pol": "pl", "cze": "cs", "ces": "cs", "swe": "sv",
            "nor": "no", "nob": "no", "nno": "no", "nb": "no", "nn": "no", "dan": "da", "fin": "fi", "kor": "ko", "tur": "tr",
            "ara": "ar", "heb": "he", "hun": "hu", "gre": "el", "ell": "el", "rum": "ro", "ron": "ro", "tha": "th", "vie": "vi",
            "ind": "id", "may": "ms", "msa": "ms", "hin": "hi", "ukr": "uk", "bul": "bg", "hrv": "hr", "srp": "sr", "slo": "sk",
            "slk": "sk", "slv": "sl", "est": "et", "lav": "lv", "lit": "lt", "ice": "is", "isl": "is", "cat": "ca", "baq": "eu",
            "eus": "eu", "glg": "gl", "per": "fa", "fas": "fa", "wel": "cy", "cym": "cy", "tam": "ta", "tel": "te"}


def primary_language(raw):
    code = (raw or "").lower().split("-")[0]
    return ISO639_2.get(code, code)


def listdir(d):
    """Directory entries without the files operating systems and NAS software drop into shared folders."""
    return sorted(n for n in os.listdir(d) if not OS_ARTEFACTS.match(n))


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
    # JSON Schema counts 7.0 as an integer; the Go reader of the playback fields does not.
    types = Draft202012Validator.TYPE_CHECKER.redefine(
        "integer", lambda checker, x: isinstance(x, int) and not isinstance(x, bool) and INT64[0] <= x <= INT64[1])
    Strict = validators.extend(Draft202012Validator, type_checker=types)
    for n, d in docs.items():
        Draft202012Validator.check_schema(d)
        if d["$id"] != BASE + f"{n}.schema.json":
            raise SystemExit(f"{n}.schema.json: $id {d['$id']} is not {BASE}{n}.schema.json")
    return {n: Strict(docs[n], registry=registry, format_checker=fc) for n in ("manifest", "metadata")}


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


# ---------------------------------------------------------------- files
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


EXT_TYPE = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


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


# ---------------------------------------------------------------- checker
class Checker:
    def __init__(self, validators, check_media, check_checksums=False):
        self.v = validators
        self.check_media = check_media or check_checksums
        self.check_checksums = check_checksums
        self.errors = []
        self.counts = {"movie": 0, "series": 0, "episode": 0, "images": 0, "probes": 0}

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
        except Exception as e:  # a malformed document must not stop the run
            self.err(d, f"cannot check: {type(e).__name__}: {e}")

    def _item(self, d, expect_type, series):
        mp = os.path.join(d, "manifest.json")
        man = self.load(mp)
        if man is None:
            return
        if not isinstance(man, dict):
            self.err(mp, "not a JSON object")
            return
        valid = self.schema_ok("manifest", man, mp)
        iid, t = man.get("itemId"), man.get("type")
        if iid != os.path.basename(d.rstrip("/")):
            self.err(mp, f"itemId {iid} does not match folder {os.path.basename(d)}")
        if t != expect_type:
            self.err(mp, f"type {t} where {expect_type} was expected")
        if t in self.counts:
            self.counts[t] += 1
        allowed = set(SERIES_ENTRIES if expect_type == "series" else ITEM_ENTRIES)
        for v in man.get("versions") or [] if isinstance(man.get("versions"), list) else []:
            for src in (v.get("sources") or []) if isinstance(v, dict) and v.get("path") == "." else []:
                if isinstance(src, dict) and isinstance(src.get("file"), dict) and src["file"].get("path"):
                    allowed.add(src["file"]["path"])
        for name in listdir(d):
            if name not in allowed:
                self.err(os.path.join(d, name), f"unexpected entry in a {expect_type} folder")
        self.metadata(d, man)
        if not valid:
            return  # cross-file rules assume the documented shape
        ids = man.get("externalIds") or {}
        if t == "movie" and set(ids) & {"tmdbTv", "tmdbSeason", "tmdbEpisode"}:
            self.err(mp, "a movie carries series or episode reference ids")
        if t == "series" and set(ids) & {"tmdbMovie", "tmdbSeason", "tmdbEpisode", "tmdbCollection"}:
            self.err(mp, "a series carries movie, season or episode reference ids")
        if t == "episode":
            if "tmdbMovie" in ids or "tmdbCollection" in ids:
                self.err(mp, "an episode carries movie reference ids")
            self.episode(mp, man, series)
        if t == "series":
            self.series(d, mp, man)
        else:
            self.versions(d, mp, man)

    # ------------------------------------------------------------ metadata/
    def metadata(self, d, man):
        md = os.path.join(d, "metadata")
        where = os.path.join(md, "metadata.json")
        meta = self.load(where)
        if meta is None or not isinstance(meta, dict):
            return
        valid = self.schema_ok("metadata", meta, where)
        if meta.get("itemId") != man.get("itemId"):
            self.err(where, "itemId differs from the manifest")
        if meta.get("type") != man.get("type"):
            self.err(where, "type differs from the manifest")
        if not valid:
            return
        listed = set()
        seasons = {s["number"] for s in (man.get("series") or {}).get("seasons") or []} | \
                  {s["number"] for s in (meta.get("series") or {}).get("seasons") or []}
        numbers = [s["number"] for s in (meta.get("series") or {}).get("seasons") or []]
        if len(numbers) != len(set(numbers)):
            self.err(where, "series.seasons lists a season number twice")
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
            if os.path.getsize(f) != img["sizeBytes"]:
                self.err(f, f"size {os.path.getsize(f)} != recorded {img['sizeBytes']}")
            if sha_file(f) != img["sha256"]:
                self.err(f, "sha256 does not match metadata.json")
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
            if man.get("type") != "series" and img.get("season") is not None:
                self.err(f, "season-specific image on a non-series item")
            if img.get("season") is not None and img["season"] not in seasons:
                self.err(f, f"names season {img['season']}, which neither the manifest nor metadata describes")
        if os.path.isdir(md):
            for name in listdir(md):
                if name != "metadata.json" and name not in listed:
                    self.err(os.path.join(md, name), "not listed in metadata.json")

    # ------------------------------------------------------------ series and episodes
    def series(self, d, mp, man):
        s = man["series"]
        numbers = [x["number"] for x in s["seasons"]]
        if len(numbers) != len(set(numbers)):
            self.err(mp, "series.seasons lists a season number twice")
        listed = {}
        for season in s["seasons"]:
            for e in season["episodes"]:
                eid = e["itemId"]
                if eid in listed:
                    self.err(mp, f"episode {eid} listed twice")
                listed[eid] = {"season": season["number"], "episode": e.get("episode"), "episodeEnd": e.get("episodeEnd")}
                if e["path"] != f"episodes/{eid}/":
                    self.err(mp, f"episode path {e['path']} is not episodes/{eid}/")
        for m in s.get("masters") or []:
            for eid in m["episodes"]:
                if eid not in listed:
                    self.err(mp, f"masters names {eid}, which is not a listed episode")
        ed = os.path.join(d, "episodes")
        present = set(listdir(ed)) if os.path.isdir(ed) else set()
        for missing in sorted(set(listed) - present):
            self.err(mp, f"lists episode {missing} but episodes/{missing}/ does not exist")
        for orphan in sorted(present - set(listed)):
            self.err(os.path.join(ed, orphan), "episode folder not listed in the series manifest")
        context = {"itemId": man["itemId"], "tmdbTv": (man.get("externalIds") or {}).get("tmdbTv"),
                   "ordering": s["defaultOrdering"], "listed": listed}
        for eid in sorted(present & set(listed)):
            self.item(os.path.join(ed, eid), "episode", series=context)

    def episode(self, mp, man, series):
        e = man["episode"]
        if series is None:
            self.err(mp, "episode outside a series folder")
            return
        if e["seriesId"] != series["itemId"]:
            self.err(mp, f"episode.seriesId {e['seriesId']} is not the enclosing series {series['itemId']}")
        tv = (man.get("externalIds") or {}).get("tmdbTv")
        if tv and series["tmdbTv"] and tv != series["tmdbTv"]:
            self.err(mp, f"externalIds.tmdbTv {tv} differs from the series' {series['tmdbTv']}")
        listing = series["listed"].get(man["itemId"])
        coords = [c for c in e["coordinates"] if c["scheme"] == series["ordering"]]
        if listing and coords:
            c = coords[0]
            same = c["episode"] == listing["episode"] if c.get("season") is None else \
                (c["season"], c["episode"]) == (listing["season"], listing["episode"])
            if not same:
                shown = f"S{c['season']}E{c['episode']}" if c.get("season") is not None else f"episode {c['episode']}"
                self.err(mp, f"{series['ordering']} coordinates {shown} differ from the series listing "
                             f"S{listing['season']}E{listing['episode']}")
        aired = next((c for c in e["coordinates"] if c["scheme"] == "aired"), None)
        if aired:
            for field, value in (("seasonNumber", aired.get("season")), ("episodeNumber", aired["episode"])):
                if field in man and man[field] is not None and man[field] != value:
                    self.err(mp, f"{field} {man[field]} differs from the aired coordinates ({value})")
            m = SEASON_EPISODE.match(man.get("episodeCode") or "")
            if m and (int(m.group(1)), int(m.group(2))) != (aired.get("season"), aired["episode"]):
                self.err(mp, f"episodeCode {man['episodeCode']} differs from the aired coordinates")

    # ------------------------------------------------------------ versions, sources, packages
    def playback_set(self, where, pb):
        """Descriptions must be complete and the version 2 playback hints must not contradict them."""
        audio = (pb.get("renditions") or {}).get("audio") or []
        video = (pb.get("renditions") or {}).get("video") or []
        subs = pb.get("subtitles") or []
        for kind, items in (("video", video), ("audio", audio), ("subtitle", subs)):
            ids = [x["id"] for x in items]
            if len(ids) != len(set(ids)):
                self.err(where, f"{kind} rendition ids are not unique")
        if audio and sum(1 for a in audio if a.get("default")) != 1:
            self.err(where, f"exactly one audio rendition must be default, found {sum(1 for a in audio if a.get('default'))}")
        if sum(1 for s in subs if s.get("default") and not s.get("forced")) > 1:
            self.err(where, "more than one non-forced subtitle is default")
        def is_forced(x):
            return bool(x.get("forced")) or x.get("purpose") in ("forced", "signs-songs")

        for x in audio + subs:
            kind = "audio" if x in audio else "subtitle"
            if x.get("purposeFrom") == "assumed" and x.get("purpose") not in ("dialogue", "main"):
                self.err(where, f"{kind} {x['id']} purpose {x.get('purpose')} cannot be assumed; only dialogue or main can")
            if (x.get("purposeFrom") is None) != (x.get("purpose") in (None, "unknown")):
                self.err(where, f"{kind} {x['id']} needs purposeFrom exactly when its purpose is known")
        for x in subs:
            if x.get("forced") and x.get("purpose") not in (None, "forced", "signs-songs", "unknown"):
                self.err(where, f"subtitle {x['id']} is flagged forced but its purpose is {x['purpose']}")
            if x.get("default") and is_forced(x):
                self.err(where, f"subtitle {x['id']} is a forced track flagged default: a version 2 reader would open with it instead of no subtitles")

    def versions(self, d, mp, man):
        versions = man["versions"]
        ids = [v["id"] for v in versions]
        if len(ids) != len(set(ids)):
            self.err(mp, "version ids are not unique")
        if sum(1 for v in versions if v["primary"]) != 1:
            self.err(mp, "exactly one version must be primary")
        here = [v for v in versions if v["path"] == "."]
        if len(here) > 1:
            self.err(mp, "more than one version is stored in the item folder (path '.')")
        root_pkg = here[0]["package"] if here else None
        if root_pkg is not None and "renditions" not in man and root_pkg["state"] in ("complete", "stale"):
            self.err(mp, "the version stored in the item folder has a complete package but the manifest has no playback fields")
        if root_pkg is None and "renditions" in man:
            self.err(mp, "playback fields present but no version in the item folder has a package")

        if self.check_media and not here and os.path.isfile(os.path.join(d, ".complete")):
            self.err(mp, ".complete marker in the item folder but no version is stored there")
        referenced_versions, referenced_sources = set(), set()
        for v in versions:
            vw = f"{mp} version {v['id']}"
            if v["path"] != "." and v["path"] != f"versions/{v['id']}/":
                self.err(vw, f"path {v['path']} is not versions/{v['id']}/")
            vp = os.path.normpath(os.path.join(d, v["path"]))
            if v["path"] != ".":
                referenced_versions.add(v["id"])
            if not os.path.isdir(vp):
                self.err(vw, f"folder {v['path']} does not exist")
            pkg = v["package"]
            sources = v["sources"]
            for kind in ("chapters", "segments"):
                marks = v.get(kind) or []
                if any(m["endMs"] < m["startMs"] for m in marks):
                    self.err(vw, f"a {kind[:-1]} ends before it starts")
                if kind == "chapters" and any(marks[i]["startMs"] > marks[i + 1]["startMs"] for i in range(len(marks) - 1)):
                    self.err(vw, "chapters are not in timeline order")
            if bool(v.get("chapters")) != (v.get("chaptersFrom") is not None):
                self.err(vw, "chaptersFrom must be set exactly when there are chapters")
            if any(s["essence"].get("chapters") for s in sources) and not v.get("chapters"):
                self.err(vw, "the original carries chapter marks but the version keeps none")
            all_deleted = all(s["state"] == "deleted" for s in sources)
            if (v["truth"]["kind"] == "package") != all_deleted:
                self.err(vw, "truth.kind must be 'package' exactly when every source is deleted")
            if pkg is not None and (pkg["role"] == "canonical") != (v["truth"]["kind"] == "package"):
                self.err(vw, "package.role must be 'canonical' exactly when truth.kind is 'package'")
            if all_deleted and pkg is None:
                self.err(vw, "every source is deleted and there is no package: nothing of this version remains")
            for s in sources:
                referenced_sources.add(s["id"])
                orig = s["file"].get("path")
                if orig and s["state"] != "present":
                    self.err(vw, f"source {s['id']} names a file but is not present")
                if orig and self.check_media:
                    f = os.path.join(vp, orig)
                    if not os.path.isfile(f):
                        self.err(f, "original file missing")
                    elif os.path.getsize(f) != s["file"]["sizeBytes"]:
                        self.err(f, f"original is {os.path.getsize(f)} bytes, the manifest says {s['file']['sizeBytes']}")
                    elif qh1(f) != s["file"]["fixity"]["qh1"]:
                        self.err(f, "original does not match its qh1 fixity")
                if s["state"] == "deleted" and not s["file"].get("deletedAt"):
                    self.err(vw, f"source {s['id']} is deleted without file.deletedAt")
                pf, psha = s["probe"].get("file"), s["probe"].get("sha256")
                if pf:
                    f = os.path.join(d, pf)
                    if not os.path.isfile(f):
                        self.err(f, "probe file missing")
                    else:
                        self.counts["probes"] += 1
                        if sha_file(f) != psha:
                            self.err(f, "probe sha256 does not match the manifest")
                elif psha:
                    self.err(vw, f"source {s['id']} records a probe sha256 but no probe file")
                for sc in s.get("sidecars") or []:
                    f = os.path.join(d, sc["file"])
                    if not os.path.isfile(f):
                        self.err(f, "sidecar missing")
                        continue
                    if "sizeBytes" in sc and os.path.getsize(f) != sc["sizeBytes"]:
                        self.err(f, "sidecar size does not match the manifest")
                    if "sha256" in sc and sha_file(f) != sc["sha256"]:
                        self.err(f, "sidecar sha256 does not match the manifest")
            if pkg is not None:
                if pkg["fidelity"]["lossless"] != (not pkg["fidelity"]["losses"]):
                    self.err(vw, "fidelity.lossless must be true exactly when losses is empty")
                cs = pkg.get("checksums")
                if cs:
                    f = os.path.join(vp, cs["file"])
                    if not os.path.isfile(f):
                        self.err(vw, f"checksums file {cs['file']} missing")
                    elif sha_file(f) != cs["sha256"]:
                        self.err(vw, f"checksums file {cs['file']} does not match its recorded sha256")
                    elif len(read_checksums(f)) != cs["files"]:
                        self.err(vw, f"checksums file lists {len(read_checksums(f))} files, the manifest says {cs['files']}")
                pb = man if v["path"] == "." else pkg.get("playback") or {}
                self.playback_set(vw, pb)
            if self.check_media:
                self.media(vw, vp, v, man)
        # nothing unreferenced under source/ or versions/
        for sub, refs in (("source", referenced_sources), ("versions", referenced_versions)):
            base = os.path.join(d, sub)
            if os.path.isdir(base):
                for name in listdir(base):
                    if name not in refs:
                        self.err(os.path.join(base, name), f"not referenced by the manifest")
        src_dir = os.path.join(d, "source")
        for v in versions:
            for s in v["sources"]:
                sd = os.path.join(src_dir, s["id"])
                if os.path.isdir(sd):
                    known = {os.path.basename(s["probe"].get("file") or "")} | {os.path.basename(x["file"]) for x in s.get("sidecars") or []}
                    for name in listdir(sd):
                        if name not in known:
                            self.err(os.path.join(sd, name), "not the probe or a sidecar of this source")

    def media(self, vw, vp, v, man):
        # A library is portable only if its files are its own: a hard link ties a file to another path.
        linked = [rel for rel in package_files(vp) if os.stat(os.path.join(vp, rel)).st_nlink > 1]
        orig = [s["file"]["path"] for s in v["sources"] if s["file"].get("path")]
        linked += [o for o in orig if os.path.isfile(os.path.join(vp, o)) and os.stat(os.path.join(vp, o)).st_nlink > 1]
        if linked:
            self.err(vw, f"{len(linked)} file(s) are hard links shared with another path, e.g. {linked[0]}; the library must hold independent files")
        pkg = v["package"]
        marker = os.path.isfile(os.path.join(vp, ".complete"))
        playable = pkg is not None and pkg["state"] in ("complete", "stale")
        if marker and not playable:
            self.err(vw, ".complete marker present but the package is neither complete nor stale")
        if not playable:
            return
        if not marker:
            self.err(vw, ".complete marker missing")
        pb = man if v["path"] == "." else pkg.get("playback") or {}
        ren = pb.get("renditions") or {}
        if not ren.get("video"):
            self.err(vw, "a playable package needs at least one video rendition")
        has_audio = any(st["type"] == "audio" for src in v["sources"] for st in src["streams"])
        if has_audio and not ren.get("audio"):
            self.err(vw, "the original has audio but the package has no audio rendition")
        for r in (ren.get("video") or []) + (ren.get("audio") or []):
            if not os.path.isdir(os.path.join(vp, r["dir"])):
                self.err(vw, f"rendition dir {r['dir']} missing")
        for sub in pb.get("subtitles") or []:
            if not os.path.isfile(os.path.join(vp, sub["path"])):
                self.err(vw, f"subtitle {sub['path']} missing")
        tp = pb.get("trickplay")
        if tp:
            vtt = os.path.join(vp, tp["vttPath"])
            if not os.path.isfile(vtt):
                self.err(vw, f"trickplay {tp['vttPath']} missing")
            else:
                self.trickplay(vw, vp, tp, vtt, pb.get("durationMs") or 0)
        cs = pkg.get("checksums")
        if cs and os.path.isfile(os.path.join(vp, cs["file"])):
            listed = read_checksums(os.path.join(vp, cs["file"]))
            on_disk = package_files(vp)
            for rel in sorted(set(on_disk) - set(listed)):
                self.err(vw, f"package file {rel} is not in {cs['file']}")
            for rel in sorted(set(listed) - set(on_disk)):
                self.err(vw, f"{cs['file']} lists {rel}, which does not exist")
            present = [rel for rel in listed if rel in set(on_disk)]
            total = sum(os.path.getsize(os.path.join(vp, rel)) for rel in present)
            if len(present) == len(listed) and total != cs["bytes"]:
                self.err(vw, f"package files total {total} bytes, the manifest says {cs['bytes']}")
            if self.check_checksums:
                for rel in present:
                    if sha_file(os.path.join(vp, rel)).split(":", 1)[1] != listed[rel]:
                        self.err(vw, f"{rel} does not match its checksum")

    def trickplay(self, vw, vp, tp, vtt, duration_ms):
        """Every sprite sheet the VTT names exists, and the cues cover the whole duration."""
        base = os.path.dirname(vtt)
        cues, sheets, last_end = 0, set(), 0
        for line in open(vtt, encoding="utf-8", errors="replace"):
            line = line.strip()
            m = VTT_CUE.match(line)
            if m:
                cues += 1
                h, mi, se, ms = (int(x) for x in m.groups()[4:])
                last_end = max(last_end, ((h * 60 + mi) * 60 + se) * 1000 + ms)
            elif line and not line.startswith("WEBVTT") and "#xywh=" in line:
                sheets.add(line.split("#", 1)[0])
        for sheet in sorted(sheets):
            if not os.path.isfile(os.path.join(base, sheet)):
                self.err(vw, f"trickplay sprite sheet {sheet} named by the VTT is missing")
        expected = duration_ms // (tp["intervalSec"] * 1000) if tp["intervalSec"] else 0
        if cues < expected:
            self.err(vw, f"trickplay covers {cues} of {expected} thumbnails")

    # ------------------------------------------------------------ roots
    def root(self, r):
        found = 0
        for category, kind in (("movies", "movie"), ("series", "series")):
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
    ap.add_argument("--check-checksums", action="store_true", help="also hash every package file against its checksums file (implies --check-media)")
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
