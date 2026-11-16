#!/usr/bin/env python3
"""Mend the packages of a v2 library whose picture plays early against its sound: their video edit list.

Usage:
  library-v2-mend.py LIBRARY (--items ID[,ID...] | --csv FILE) [--run RUN] [--json]
  library-v2-mend.py LIBRARY (--items ... | --csv ...) --apply --by WHO [--run RUN]
  library-v2-mend.py LIBRARY (--items ... | --csv ...) --undo --by WHO [--run RUN]

LIBRARY holds movies/ and series/. The items are named by id (--items), or by a CSV with an itemId
(or item_id) column and, optionally, an itemDir (item_dir) column, relative to LIBRARY or absolute —
an episode's folder is found under its series otherwise. Nothing else of the tree is read.

The fault. A packager that reads a track's edit list as shaka-packager does (3.x,
TrackRunIterator::GetTimestampAdjustment) adds the empty edits and subtracts the media edit's
media_time; when that comes to 0 it takes the edit list for a missing one and, with
--mp4_reset_initial_composition_offset_to_zero (its default), moves the track by minus its first
composition offset. A video track gets such an edit list where its first frame comes after the start
of the file by exactly its first composition offset, both in whole milliseconds -- reordered frames,
the first decode at the file's start -- and the package's video then starts that much early against
its sound. Its init segment carries the evidence: an edit whose media_time is that offset, a first
decode time of 0.

The rule, per version, as players that apply the edit lists present it (Chrome's and WebKit's MSE do):
  S  the source: its first video stream's start minus the start of the audio stream the stereo
     rendition was made from (its sourceStreamIndex), from the probe its source record keeps
     (sources/<id>/ffprobe.json, held to the record's hash)
  P  the package: its top video rendition's first frame (first decode time + first composition
     offset - edit media_time) minus its stereo rendition's first input sample (first decode time
     + 1024 - edit media_time: an AAC encoder's priming, which that edit cuts)
  shift = P - S, in ms, negative: the picture early
Within 2 ms it is in sync. A shift that the video edit's media_time set to 0 brings within 2 ms is
this fault: the package is affected, and every video rendition whose own boxes say the same is
mended. Any other shift is reported (shifted-other) and never touched. A version with no stored
probe, more than one source or a stereo rendition that is not AAC is undecidable and never touched;
one an event removed or superseded is skipped; one whose chain does not hold, whose checksums are
pending or whose package.json is not written as the records write it is a conflict.

The mend (package.schema.json, mends; kind avsync-editlist). In the init segment of each affected
rendition the media_time of its edit list's first media edit becomes 0: the same bytes but that
field, so its first frame is presented at its composition time, where the source has it. Then the
chain is closed again, as library-v2-fill-checksums.py closes one: the file's line in
checksums.sha256 with its new digest, package.json with the checksums file's new hash and an entry
in its mends,
  {kind, file, box, old, new, sha256Before, sha256After, at, by}
(nothing else of it changes), and .complete with package.json's new hash; `sha256sum -c
checksums.sha256` holds after it as before. The entry keeps the hash the file had before. Each
file is written to a temporary file in its folder and renamed into place; the bytes it replaces are
kept until the mend is recorded.

A dry run (the default) changes nothing and lists, per affected rendition, the file, the byte offset
of the field, the old and the new media_time and the file's old and new sha256, and the old and new
sha256 of checksums.sha256, package.json and .complete. --apply and --undo journal under LIBRARY/.work/migration/<run>/
(the run is mend-avsync-editlist unless --run names another), every path relative to LIBRARY:
  mends.jsonl       {"op": "mend", "dir", "itemId", "versionId", "packageId", "files": [{file, before,
                    after}], "mends": [the entries], "by"} before a version's files are replaced, and
                    {"op": "mended", ..., "complete": "sha256:<hex>"} once they are, the .complete
                    then: the catalog notes and verifies each version from these lines; "undo" and
                    "undone" lines alike for --undo
  before/<dir>/     the files a step replaced, until its last line is written
--apply first finishes what an earlier run left between a "mend" and its "mended" line; running it
again changes nothing. --undo puts back each avsync-editlist entry of a version, newest first --
the field's old value, held to the entry's sha256Before -- removes the entries, writes the file's
line in checksums.sha256, package.json and .complete again, and leaves the version byte for byte
as it was before it was mended.

It reads only the item folders it is given, never an original or the database, and writes nothing
but the files above. Python 3.11 or later, nothing to install.
"""
import argparse
import csv
import datetime
import glob
import hashlib
import json
import os
import struct
import sys

KIND = "avsync-editlist"
BOX = "moov/trak/edts/elst"
SUMS = "checksums.sha256"
PRIMING = 1024  # an AAC encoder's priming, in samples at 48 kHz
TOLERANCE_MS = 2.0
RUN = "mend-avsync-editlist"
# an entry's fields, in the order the record keeps them
ENTRY = ("kind", "file", "box", "old", "new", "sha256Before", "sha256After", "at", "by")


# ------------------------------------------------------------------------------------------- util
def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha_bytes(b):
    return "sha256:" + hashlib.sha256(b).hexdigest()


def read(p):
    with open(p, "rb") as f:
        return f.read()


def json_bytes(doc):
    """A record as the library writes it: two-space indent, UTF-8, one trailing line break."""
    return (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def complete_bytes(package_bytes):
    return (sha_bytes(package_bytes) + "\n").encode("utf-8")


def replace(path, data):
    """data at path in one step: a temporary file in its folder, written through, renamed."""
    tmp = path + ".mending"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    fd = os.open(os.path.dirname(path) or ".", os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


# ------------------------------------------------------------------------------------- MP4 boxes
def boxes(buf, off=0, end=None):
    end = len(buf) if end is None else end
    while off + 8 <= end:
        size, kind = struct.unpack(">I4s", buf[off:off + 8])
        hdr = 8
        if size == 1:
            size, hdr = struct.unpack(">Q", buf[off + 8:off + 16])[0], 16
        elif size == 0:
            size = end - off
        if size < hdr or off + size > end:
            raise ValueError(f"box {kind!r} at {off} runs past its parent")
        yield kind, off, hdr, size
        off += size


def find(buf, path, off=0, end=None):
    """(payload start, end) of every box at path."""
    out = []
    for kind, o, hdr, size in boxes(buf, off, end):
        if kind == path[0]:
            out += [(o + hdr, o + size)] if len(path) == 1 else find(buf, path[1:], o + hdr, o + size)
    return out


def init_timing(data):
    """An init segment of one track: {timescale, edits: [(offset of media_time, its width,
    media_time, segment_duration, rate)]}."""
    traks = find(data, [b"moov", b"trak"])
    if len(traks) != 1:
        raise ValueError(f"{len(traks)} tracks, one expected")
    mdhd = find(data, [b"moov", b"trak", b"mdia", b"mdhd"])
    if len(mdhd) != 1:
        raise ValueError("no media header")
    ms = mdhd[0][0]
    v = data[ms]
    ts = struct.unpack(">I", data[ms + (20 if v else 12):ms + (24 if v else 16)])[0]
    edits = []
    for es, _ in find(data, [b"moov", b"trak", b"edts", b"elst"]):
        v = data[es]
        n = struct.unpack(">I", data[es + 4:es + 8])[0]
        p = es + 8
        for _ in range(n):
            if v == 1:
                dur, mt = struct.unpack(">Qq", data[p:p + 16])
                edits.append((p + 8, 8, mt, dur, struct.unpack(">h", data[p + 16:p + 18])[0]))
                p += 20
            else:
                dur, mt = struct.unpack(">Ii", data[p:p + 8])
                edits.append((p + 4, 4, mt, dur, struct.unpack(">h", data[p + 8:p + 10])[0]))
                p += 12
    return {"timescale": ts, "edits": edits}


def media_edit(edits):
    """The first media edit (media_time not -1), or None."""
    return next((e for e in edits if e[2] != -1), None)


def first_fragment(data):
    """(baseMediaDecodeTime, first sample's composition offset) of a media segment's first traf."""
    tfdt = find(data, [b"moof", b"traf", b"tfdt"])
    trun = find(data, [b"moof", b"traf", b"trun"])
    if not tfdt or not trun:
        raise ValueError("no track fragment")
    ts = tfdt[0][0]
    v = data[ts]
    base = struct.unpack(">Q" if v else ">I", data[ts + 4:ts + (12 if v else 8)])[0]
    rs = trun[0][0]
    tv, flags = data[rs], int.from_bytes(data[rs + 1:rs + 4], "big")
    p = rs + 8
    if flags & 0x1:
        p += 4
    if flags & 0x4:
        p += 4
    if not flags & 0x800:
        return base, 0
    p += 4 * sum(1 for bit in (0x100, 0x200, 0x400) if flags & bit)
    return base, struct.unpack(">i" if tv else ">I", data[p:p + 4])[0]


def first_segment(rendition_dir):
    """The first media segment the rendition's playlist names, else the first seg-*.m4s."""
    pl = os.path.join(rendition_dir, "playlist.m3u8")
    if os.path.isfile(pl):
        with open(pl, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    return os.path.join(rendition_dir, line.split("?")[0])
    segs = sorted(glob.glob(os.path.join(rendition_dir, "seg-*.m4s")))
    return segs[0] if segs else None


def rendition_timing(rendition_dir):
    t = init_timing(read(os.path.join(rendition_dir, "init.mp4")))
    seg = first_segment(rendition_dir)
    if seg is None or not os.path.isfile(seg):
        raise ValueError("no media segment")
    t["tfdt"], t["cto"] = first_fragment(read(seg))
    return t


def with_media_time(data, edit, value):
    off, width = edit[0], edit[1]
    return data[:off] + struct.pack(">q" if width == 8 else ">i", value) + data[off + width:]


# ------------------------------------------------------------------------------------------ tree
def item_dir_of(root, item_id, hint=None):
    if hint:
        d = hint if os.path.isabs(hint) else os.path.join(root, hint)
        return d if os.path.isdir(d) else None
    d = os.path.join(root, "movies", item_id[:2], item_id)
    if os.path.isdir(d):
        return d
    hits = glob.glob(os.path.join(root, "series", "*", "*", "episodes", item_id))
    return hits[0] if len(hits) == 1 else None


def events_of(item_dir):
    out = []
    for p in sorted(glob.glob(os.path.join(item_dir, "events", "*", "event.json"))):
        try:
            out.append(json.loads(read(p)))
        except (OSError, ValueError):
            pass
    return out


def read_sums(path):
    out = {}
    for line in read(path).decode("utf-8").splitlines():
        digest, _, rel = line.partition("  ")
        if rel:
            out[rel] = digest
    return out


def chain(d):
    """(package, package.json bytes, listed digests) of a version folder whose chain holds, or
    (None, None, why)."""
    pj, mark, sums = (os.path.join(d, n) for n in ("package.json", ".complete", SUMS))
    pkg_bytes = read(pj)
    if read(mark).decode("utf-8", "replace").strip() != sha_bytes(pkg_bytes):
        return None, None, ".complete does not name package.json"
    try:
        pkg = json.loads(pkg_bytes)
    except ValueError as e:
        return None, None, f"package.json cannot be read: {e}"
    cs = pkg.get("checksums") or {}
    if cs.get("pending"):
        return None, None, "its checksums are pending: fill them first (library-v2-fill-checksums.py)"
    if not os.path.isfile(sums) or sha_bytes(read(sums)) != cs.get("sha256"):
        return None, None, f"package.json does not name {SUMS}"
    return pkg, pkg_bytes, read_sums(sums)


def last_after(pkg, rel):
    """The hash the last mend of the file rel left, or None when nothing mended it."""
    ms = [m for m in pkg.get("mends") or [] if m.get("file") == rel]
    return ms[-1]["sha256After"] if ms else None


def close_chain(pkg, sums, changes, mends):
    """The chain over a version whose files changes names changed ({file: (hash before, hash after)}):
    checksums.sha256 with those files' lines written again, package.json with the checksums file's
    new hash and mends (none: the key gone), and .complete -- as library-v2-fill-checksums.py closes
    one. Raises ValueError when a line is not there exactly once."""
    text = sums.decode("utf-8")
    for rel, (before, after) in sorted(changes.items()):
        line = f"{before.split(':', 1)[1]}  {rel}\n"
        if text.count(line) != 1:
            raise ValueError(f"{SUMS} does not list {rel} as {before}")
        text = text.replace(line, f"{after.split(':', 1)[1]}  {rel}\n")
    new_sums = text.encode("utf-8")
    doc = {k: v for k, v in pkg.items() if k != "mends"}
    doc["checksums"] = {**pkg["checksums"], "sha256": sha_bytes(new_sums)}
    if mends:
        doc["mends"] = mends
    new_pkg = json_bytes(doc)
    return new_sums, new_pkg, complete_bytes(new_pkg)


# ------------------------------------------------------------------------------------- deciding
class Version:
    def __init__(self, root, item_id, item_dir, vdir):
        self.root, self.item_id, self.item_dir, self.dir = root, item_id, item_dir, vdir
        self.version_id = os.path.basename(vdir)
        self.rel = os.path.relpath(vdir, root).replace(os.sep, "/")
        self.status, self.reason, self.shift_ms, self.package_id = None, "", None, None
        self.renditions = []  # [{file, offset, width, old, new, timescale, shiftMs, afterMs, before, after}]
        self.left = []        # [(rendition dir, why)]
        self.plan = {}        # file -> new bytes (the inits, package.json, .complete)
        self.files = []       # [{file, before, after}] in the order they are written
        self.entries = []     # the mends entries an apply records

    def to_json(self):
        return {"itemId": self.item_id, "versionId": self.version_id, "dir": self.rel,
                "packageId": self.package_id, "status": self.status, "reason": self.reason,
                "shiftMs": self.shift_ms, "renditions": self.renditions, "files": self.files,
                "renditionsLeft": [{"dir": d, "why": w} for d, w in self.left]}


def probe_starts(probe, audio_index=None):
    streams = probe.get("streams") or []
    video = [s for s in streams if s.get("codec_type") == "video"
             and not (s.get("disposition") or {}).get("attached_pic")]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if audio_index is not None:
        audio = [s for s in audio if s.get("index") == audio_index] or audio
    if not video or not audio:
        return None, "its probe has no video or no audio stream"
    try:
        return (float(video[0]["start_time"]), float(audio[0]["start_time"])), None
    except (KeyError, TypeError, ValueError):
        return None, "its probe has no stream start times"


def decide(v, removed, superseded, by="", at=None):
    """v.status: affected, mended, in-sync, shifted-other, undecidable, skipped or conflict; for an
    affected version the plan an apply writes."""
    d = v.dir
    if v.version_id in removed:
        v.status, v.reason = "skipped", "a version-removed event names it"
        return
    if not (os.path.isfile(os.path.join(d, ".complete")) and os.path.isfile(os.path.join(d, "package.json"))):
        v.status, v.reason = "skipped", "no package.json and .complete: no finished package"
        return
    try:
        package = json.loads(read(os.path.join(d, "package.json")))
        version = json.loads(read(os.path.join(d, "version.json")))
    except (OSError, ValueError) as e:
        v.status, v.reason = "conflict", f"its records cannot be read: {e}"
        return
    v.package_id = package.get("packageId")
    if v.package_id in superseded:
        v.status, v.reason = "skipped", "a package-superseded event names its package"
        return
    sources = version.get("sourceIds") or []
    if len(sources) != 1:
        v.status, v.reason = "undecidable", f"the version names {len(sources)} sources, one expected"
        return
    try:
        source = json.loads(read(os.path.join(v.item_dir, "sources", sources[0], "source.json")))
    except (OSError, ValueError):
        v.status, v.reason = "undecidable", "no source record"
        return
    probe_rec = source.get("probe") or {}
    probe_path = os.path.join(v.item_dir, probe_rec.get("file") or "")
    if not probe_rec.get("file") or not os.path.isfile(probe_path):
        v.status, v.reason = "undecidable", "no stored probe"
        return
    if probe_rec.get("sha256") and sha_bytes(read(probe_path)) != probe_rec["sha256"]:
        v.status, v.reason = "undecidable", "the stored probe does not match its source record's hash"
        return
    videos = (package.get("renditions") or {}).get("video") or []
    audios = (package.get("renditions") or {}).get("audio") or []
    if not videos or not audios:
        v.status, v.reason = "undecidable", "no video or no stereo audio rendition"
        return
    if not str(audios[0].get("codec") or "").startswith("mp4a.40"):
        v.status, v.reason = "undecidable", f"its stereo rendition is {audios[0].get('codec')!r}, not AAC"
        return
    starts, why = probe_starts(json.loads(read(probe_path)), audios[0].get("sourceStreamIndex"))
    if starts is None:
        v.status, v.reason = "undecidable", why
        return
    S = starts[0] - starts[1]
    try:
        a = rendition_timing(os.path.join(d, audios[0]["dir"]))
    except (OSError, ValueError, KeyError) as e:
        v.status, v.reason = "undecidable", f"its stereo rendition's boxes cannot be read: {e}"
        return
    a_edit = media_edit(a["edits"])
    a_first = (a["tfdt"] + PRIMING * a["timescale"] / 48000 - (a_edit[2] if a_edit else 0)) / a["timescale"]
    mended_files = {m["file"] for m in package.get("mends") or [] if m.get("kind") == KIND}
    for i, r in enumerate(videos):
        rdir = os.path.join(d, r.get("dir") or "")
        rrel = os.path.relpath(rdir, d).replace(os.sep, "/")
        try:
            t = rendition_timing(rdir)
        except (OSError, ValueError) as e:
            if i == 0:
                v.status, v.reason = "undecidable", f"its top video rendition's boxes cannot be read: {e}"
                return
            v.left.append((rrel, f"its boxes cannot be read: {e}"))
            continue
        e, ts = media_edit(t["edits"]), t["timescale"]
        mt = e[2] if e else 0
        P = (t["tfdt"] + t["cto"] - mt) / ts - a_first
        shift = round((P - S) * 1000, 1)
        after = round((P + mt / ts - S) * 1000, 1)
        if i == 0:
            v.shift_ms = shift
        if abs(shift) <= TOLERANCE_MS:
            why = (f"mended: {f'{rrel}/init.mp4'} has an {KIND} entry; its video starts {P * 1000:+.1f} ms "
                   f"after its sound, as in the source ({S * 1000:+.1f} ms)" if f"{rrel}/init.mp4" in mended_files
                   else f"in sync: its video starts {P * 1000:+.1f} ms after its sound, as in the source "
                        f"({S * 1000:+.1f} ms)")
        elif len(t["edits"]) != 1 or e is None or e[4] != 1 or mt <= 0:
            why = (f"shifted {shift:+.1f} ms, but it has no single media edit to mend "
                   f"({len(t['edits'])} edits): another fault, not this mend")
        elif abs(after) > TOLERANCE_MS:
            why = (f"shifted {shift:+.1f} ms, and its edit list ({mt}/{ts}) would leave it {after:+.1f} ms: "
                   f"another fault, not this mend")
        else:
            v.renditions.append({"file": f"{rrel}/init.mp4", "offset": e[0], "width": e[1], "old": mt, "new": 0,
                                 "timescale": ts, "shiftMs": shift, "afterMs": after})
            continue
        if i == 0:
            v.status = ("mended" if why.startswith("mended") else
                        "in-sync" if why.startswith("in sync") else "shifted-other")
            v.reason = why
            return
        v.left.append((rrel, why))
    if not v.renditions:
        v.status, v.reason = "in-sync", "no rendition is shifted by its edit list"
        return
    problem = plan(v, at or now(), by)
    if problem:
        v.status, v.reason = "conflict", problem
        return
    v.status = "affected"
    v.reason = (f"picture {-v.shift_ms:.1f} ms early against its sound: its video edit list "
                f"({v.renditions[0]['old']}/{v.renditions[0]['timescale']}) cuts the delay the source has; "
                f"with media_time 0 it is {v.renditions[0]['afterMs']:+.1f} ms")


def plan(v, at, by):
    """The bytes an apply writes for v: each affected init, checksums.sha256 with their lines,
    package.json with the checksums' new hash and their entries, .complete."""
    pkg, pkg_bytes, listed = chain(v.dir)
    if pkg is None:
        return listed  # why the chain does not hold
    if json_bytes(pkg) != pkg_bytes:
        return "package.json is not written as the library writes its records: it could not be put back byte for byte"
    sums = read(os.path.join(v.dir, SUMS))
    entries = list(pkg.get("mends") or [])
    changes = {}
    for r in v.renditions:
        data = read(os.path.join(v.dir, r["file"]))
        if r["file"] not in listed:
            return f"{SUMS} does not list {r['file']}"
        if sha_bytes(data) != "sha256:" + listed[r["file"]]:
            return f"{r['file']} does not match its line in {SUMS}"
        if last_after(pkg, r["file"]) not in (None, "sha256:" + listed[r["file"]]):
            return f"{r['file']}: its last mend did not leave the digest {SUMS} lists"
        new = with_media_time(data, (r["offset"], r["width"]), 0)
        r["before"], r["after"] = sha_bytes(data), sha_bytes(new)
        v.plan[r["file"]] = new
        changes[r["file"]] = (r["before"], r["after"])
        entry = {"kind": KIND, "file": r["file"], "box": BOX, "old": r["old"], "new": 0,
                 "sha256Before": r["before"], "sha256After": r["after"], "at": at, "by": by or "-"}
        entries.append(entry)
        v.entries.append(entry)
    new_sums, new_pkg, new_complete = close_chain(pkg, sums, changes, entries)
    v.plan[SUMS], v.plan["package.json"], v.plan[".complete"] = new_sums, new_pkg, new_complete
    v.files = [{"file": r["file"], "before": r["before"], "after": r["after"]} for r in v.renditions] + [
        {"file": SUMS, "before": sha_bytes(sums), "after": sha_bytes(new_sums)},
        {"file": "package.json", "before": sha_bytes(pkg_bytes), "after": sha_bytes(new_pkg)},
        {"file": ".complete", "before": sha_bytes(read(os.path.join(v.dir, ".complete"))),
         "after": sha_bytes(new_complete)}]
    return None


def recorded(d):
    """Whether the version's chain holds, every mended file matches its line in the checksums, and
    that line is the hash its last mend left."""
    pkg, _, listed = chain(d)
    if pkg is None:
        return listed
    for m in pkg.get("mends") or []:
        now = sha_bytes(read(os.path.join(d, m["file"])))
        if now != "sha256:" + listed.get(m["file"], "") or now != last_after(pkg, m["file"]):
            return f"{m['file']} is not the bytes its last mend left and {SUMS} lists"
    return None


# ------------------------------------------------------------------------------------------ runs
class Run:
    def __init__(self, root, name):
        self.root = root
        self.dir = os.path.join(root, ".work", "migration", name)
        self.journal = os.path.join(self.dir, "mends.jsonl")

    def lock(self):
        os.makedirs(self.dir, exist_ok=True)
        p = os.path.join(self.dir, "mends.lock")
        try:
            fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            sys.exit(f"{p} exists: another run is going, or one died (remove it once you know which)")
        os.write(fd, f"{os.getpid()} {now()}\n".encode())
        os.close(fd)
        return p

    def lines(self):
        if not os.path.isfile(self.journal):
            return []
        out = []
        with open(self.journal, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass  # a line a crash cut short
        return out

    def add(self, **entry):
        os.makedirs(self.dir, exist_ok=True)
        with open(self.journal, "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": now(), **entry}, ensure_ascii=False, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def kept(self, vrel, name):
        return os.path.join(self.dir, "before", vrel, name)

    def keep(self, vrel, name, data):
        p = self.kept(vrel, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        replace(p, data)

    def drop(self, vrel, names):
        top = os.path.join(self.dir, "before")
        for n in names:
            p = self.kept(vrel, n)
            if os.path.isfile(p):
                os.remove(p)
            d = os.path.dirname(p)
            while d.startswith(top + os.sep) and os.path.isdir(d) and not os.listdir(d):
                os.rmdir(d)
                d = os.path.dirname(d)

    def open_step(self, vrel):
        """The last mend or undo line of vrel that no mended or undone line closed, else None."""
        last = None
        for e in self.lines():
            if e.get("dir") == vrel and e.get("op") in ("mend", "mended", "undo", "undone"):
                last = e
        return last if last and last["op"] in ("mend", "undo") else None


def write_planned(d, files, new):
    """Each file in order: left where it is the new one already, replaced where it is the old one,
    else a conflict."""
    for f in files:
        p = os.path.join(d, f["file"])
        cur = sha_bytes(read(p))
        if cur == f["after"]:
            continue
        if cur != f["before"]:
            raise RuntimeError(f"{f['file']} is neither the file the run found nor the one it writes")
        if sha_bytes(new[f["file"]]) != f["after"]:
            raise RuntimeError(f"{f['file']}: the bytes to write are not the planned ones")
        replace(p, new[f["file"]])


def step(run, v, op, files, new, extra):
    """Journal op, keep the old bytes, write files in order, check, journal its end, drop them."""
    names = [f["file"] for f in files]
    for n in names:
        run.keep(v.rel, n, read(os.path.join(v.dir, n)))
    run.add(op=op, dir=v.rel, itemId=v.item_id, versionId=v.version_id, packageId=v.package_id,
            files=files, **extra)
    write_planned(v.dir, files, new)
    finish(run, v.rel, v.item_id, v.version_id, v.package_id, op, files, names)


def finish(run, vrel, item_id, version_id, package_id, op, files, names):
    d = os.path.join(run.root, vrel)
    for f in files:
        if sha_bytes(read(os.path.join(d, f["file"]))) != f["after"]:
            raise RuntimeError(f"{f['file']} is not the planned file")
    why = recorded(d)
    if why:
        raise RuntimeError(f"after the {op}: {why}")
    run.add(op=op + ("ed" if op == "mend" else "ne"), dir=vrel, itemId=item_id, versionId=version_id,
            packageId=package_id, complete=read(os.path.join(d, ".complete")).decode().strip())
    run.drop(vrel, names)


def resume(run):
    """Finish each step an earlier run left between its first line and its last, from the bytes it
    kept and the entries it journaled."""
    out = []
    seen = set()
    for e in [x for x in run.lines() if x.get("op") in ("mend", "undo")]:
        if e["dir"] in seen or run.open_step(e["dir"]) != e:
            continue
        seen.add(e["dir"])
        vrel, d = e["dir"], os.path.join(run.root, e["dir"])
        try:
            kept = {f["file"]: read(run.kept(vrel, f["file"])) for f in e["files"]}
            new = rebuilt(kept, e)
            write_planned(d, e["files"], new)
            finish(run, vrel, e["itemId"], e["versionId"], e.get("packageId"), e["op"], e["files"],
                   [f["file"] for f in e["files"]])
            out.append((vrel, f"finished the {e['op']} an earlier run left"))
        except (OSError, RuntimeError, ValueError, KeyError) as x:
            out.append((vrel, f"an earlier run left a {e['op']} unfinished, and it cannot be finished: {x}"))
    return out


def rebuilt(kept, e):
    """The bytes a journaled step writes, from the files it kept and the entries it names: for a
    mend, each init with its new media_time; for an undo, each init put back through its entries,
    newest first. Then the chain over them (close_chain)."""
    pkg = json.loads(kept["package.json"])
    new, changes = {}, {}
    if e["op"] == "mend":
        for m in e["mends"]:
            t = init_timing(kept[m["file"]])
            new[m["file"]] = with_media_time(kept[m["file"]], media_edit(t["edits"]), m["new"])
        added = [{k: m[k] for k in ENTRY} for m in e["mends"]]  # the journal sorts their keys
        mends = list(pkg.get("mends") or []) + added
    else:
        gone = e["mends"]
        state = {m["file"]: kept[m["file"]] for m in gone}
        for m in reversed(gone):
            t = init_timing(state[m["file"]])
            state[m["file"]] = with_media_time(state[m["file"]], media_edit(t["edits"]), m["old"])
        new.update(state)
        mends = [m for m in pkg.get("mends") or [] if m not in gone]
    for f in new:
        changes[f] = (sha_bytes(kept[f]), sha_bytes(new[f]))
    new[SUMS], new["package.json"], new[".complete"] = close_chain(pkg, kept[SUMS], changes, mends)
    return new


def apply_version(run, v):
    step(run, v, "mend", v.files, v.plan, {"mends": v.entries, "by": v.entries[0]["by"]})


def undo_version(run, v, by):
    """Put back every avsync-editlist entry of v, newest first: (whether anything was, why not)."""
    if not (os.path.isfile(os.path.join(v.dir, "package.json")) and os.path.isfile(os.path.join(v.dir, ".complete"))):
        return False, None
    pkg, pkg_bytes, listed = chain(v.dir)
    if pkg is None:
        return False, f"its chain does not hold: {listed}"
    gone = [m for m in pkg.get("mends") or [] if m.get("kind") == KIND]
    if not gone:
        return False, None
    if json_bytes(pkg) != pkg_bytes:
        return False, "package.json is not written as the library writes its records"
    v.package_id = pkg.get("packageId")
    kept = {m["file"]: read(os.path.join(v.dir, m["file"])) for m in gone}
    kept[SUMS] = read(os.path.join(v.dir, SUMS))
    kept["package.json"], kept[".complete"] = pkg_bytes, read(os.path.join(v.dir, ".complete"))
    # each file back through its entries, newest first, held to each entry's hashes and its line
    for f in {m["file"] for m in gone}:
        if sha_bytes(kept[f]) != "sha256:" + listed.get(f, "") or sha_bytes(kept[f]) != last_after(pkg, f):
            return False, f"{f} is not the bytes its last mend left and {SUMS} lists"
    state = {f: kept[f] for f in {m["file"] for m in gone}}
    for m in reversed(gone):
        data = state[m["file"]]
        if sha_bytes(data) != m["sha256After"]:
            return False, f"{m['file']}: its mends do not lead back to the bytes it has"
        edit = media_edit(init_timing(data)["edits"])
        if edit is None or edit[2] != m["new"]:
            return False, f"{m['file']}: its edit list's media_time is not the one its mend wrote"
        state[m["file"]] = with_media_time(data, edit, m["old"])
        if sha_bytes(state[m["file"]]) != m["sha256Before"]:
            return False, f"{m['file']}: putting back {m['old']} does not give the bytes it had"
    e = {"op": "undo", "mends": gone}
    new = rebuilt(kept, e)
    files = [{"file": f, "before": sha_bytes(kept[f]), "after": sha_bytes(new[f])}
             for f in sorted(state)] + [
        {"file": n, "before": sha_bytes(kept[n]), "after": sha_bytes(new[n])} for n in (SUMS, "package.json", ".complete")]
    step(run, v, "undo", files, new, {"mends": gone, "by": by})
    return True, None


# ------------------------------------------------------------------------------------------ main
def targets(a):
    items = [(i.strip(), None) for i in a.items.split(",") if i.strip()]
    if a.csv:
        with open(a.csv, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                item = (row.get("itemId") or row.get("item_id") or "").strip()
                if item:
                    items.append((item, (row.get("itemDir") or row.get("item_dir") or "").strip() or None))
    return items


def versions_of(root, item_id, hint, by):
    idir = item_dir_of(root, item_id, hint)
    if idir is None:
        return None, []
    evs = events_of(idir)
    removed = {e.get("versionId") for e in evs if e.get("kind") == "version-removed"}
    superseded = {e.get("packageId") for e in evs if e.get("kind") == "package-superseded"}
    out = []
    for vdir in sorted(glob.glob(os.path.join(idir, "versions", "*"))):
        if os.path.isdir(vdir):
            v = Version(root, item_id, idir, vdir)
            decide(v, removed, superseded, by)
            out.append(v)
    return idir, out


def show(v):
    print(f"{v.status:13} {v.item_id} {v.version_id}  {v.reason}")
    for r in v.renditions:
        if "before" in r:
            print(f"{'':13}   {v.rel}/{r['file']} @{r['offset']}: media_time {r['old']} -> {r['new']} "
                  f"(timescale {r['timescale']})  {r['before']} -> {r['after']}")
    for f in v.files[len(v.renditions):]:
        print(f"{'':13}   {v.rel}/{f['file']}: {f['before']} -> {f['after']}")
    for d, why in v.left:
        print(f"{'':13}   {v.rel}/{d} left: {why}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("root")
    ap.add_argument("--items", default="", help="comma-separated item ids")
    ap.add_argument("--csv", default="", help="a CSV with an itemId column (and optionally itemDir)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="mend the affected versions")
    mode.add_argument("--undo", action="store_true", help="put back what was mended")
    ap.add_argument("--by", default="", help="who gave the word; recorded in each entry (needed to apply or undo)")
    ap.add_argument("--run", default=RUN, help=f"the run's folder under LIBRARY/.work/migration/ ({RUN})")
    ap.add_argument("--json", action="store_true", help="one JSON object per version")
    a = ap.parse_args()
    root = os.path.abspath(a.root)
    if not (os.path.isdir(os.path.join(root, "movies")) or os.path.isdir(os.path.join(root, "series"))):
        sys.exit(f"{root} holds no movies/ or series/: not a library v2 root")
    if not a.items and not a.csv:
        sys.exit("name the items: --items ID[,ID...] or --csv FILE")
    if (a.apply or a.undo) and not a.by:
        sys.exit("--apply and --undo need --by: who gave the word")
    run = Run(root, a.run)
    lock = run.lock() if (a.apply or a.undo) else None
    counts, failed = {}, False
    try:
        if a.apply or a.undo:
            for vrel, what in resume(run):
                print(f"{'resumed':13} {vrel}  {what}")
        for item_id, hint in targets(a):
            idir, vs = versions_of(root, item_id, hint, a.by)
            if idir is None or not vs:
                why = "no item folder of a library v2 tree" if idir is None else "no version folder"
                if idir is not None and os.path.isfile(os.path.join(idir, "manifest.json")):
                    why = "a package-store folder (manifest.json), not a library v2 item: no stored probe"
                counts["undecidable"] = counts.get("undecidable", 0) + 1
                print(json.dumps({"itemId": item_id, "status": "undecidable", "reason": why}) if a.json
                      else f"{'undecidable':13} {item_id}  {why}")
                continue
            for v in vs:
                if run.open_step(v.rel):
                    v.status = "interrupted"
                    v.reason = f"an earlier run of {a.run} stopped mid-way: --apply or --undo finishes it"
                    v.renditions, v.files = [], []
                elif a.undo and v.status != "skipped":
                    try:
                        done, why = undo_version(run, v, a.by)
                    except (OSError, RuntimeError, ValueError) as x:
                        done, why = False, str(x)
                    if why:
                        v.status, v.reason, failed = "conflict", why, True
                    elif done:
                        v.status, v.reason = "undone", "put back as it was before it was mended"
                    else:
                        v.status, v.reason = "unchanged", "nothing of it is mended"
                    v.renditions, v.files = [], []
                elif a.apply and v.status == "affected":
                    try:
                        apply_version(run, v)
                        v.status = "mended"
                    except (OSError, RuntimeError) as x:
                        v.status, v.reason, failed = "conflict", str(x), True
                counts[v.status] = counts.get(v.status, 0) + 1
                if a.json:
                    print(json.dumps(v.to_json()))
                else:
                    show(v)
    finally:
        if lock:
            os.remove(lock)
    if not a.json:
        print("versions: " + ", ".join(f"{k} {n}" for k, n in sorted(counts.items())))
        if not a.apply and not a.undo:
            print("a dry run: nothing was changed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
