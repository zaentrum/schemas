#!/usr/bin/env python3
"""Mend the packages of a v2 library whose picture plays early against its sound: their video edit list.

Usage:
  library-v2-mend.py LIBRARY (--items ID[,ID...] | --csv FILE) [--run RUN] [--json]
  library-v2-mend.py LIBRARY (--items ... | --csv ...) --apply --by WHO [--run RUN]
  library-v2-mend.py LIBRARY (--items ... | --csv ...) --undo --by WHO [--run RUN]

LIBRARY holds movies/ and series/. The items are named by id (--items), or by a CSV with an itemId
(or item_id) column and, optionally, an itemDir (item_dir) column, relative to LIBRARY or absolute,
whose item.json must name the item -- an episode's folder is found under its series otherwise.
Nothing else of the tree is read. Versions alone are looked at: an extra's package (extras/<id>/)
is never mended by this tool.

The fault. A packager that reads a track's edit list as shaka-packager does (3.x,
TrackRunIterator::GetTimestampAdjustment) adds the empty edits and subtracts the media edit's
media_time; when that comes to 0 it takes the edit list for a missing one and, with
--mp4_reset_initial_composition_offset_to_zero (its default), moves the track by minus its first
composition offset. A video track gets such an edit list where its first frame comes after the start
of the file by exactly its first composition offset, both in whole milliseconds -- reordered frames,
the first decode at the file's start -- and the package's video then starts that much early against
its sound. Its init segment carries the evidence: an edit whose media_time is that offset, a first
decode time of 0.

The rule, per video rendition, as players that apply the edit lists present it (Chrome's and
WebKit's MSE do):
  S  the source: its first video stream's start minus the start of the audio stream the stereo
     rendition was made from (its sourceStreamIndex; where the package does not say, the start all
     of the source's audio streams share), from the probe its source record keeps
     (sources/<id>/ffprobe.json, held to the record's hash, else to its folder's checksums)
  P  the package: the rendition's first frame (first decode time + first composition offset - edit
     media_time) minus the stereo rendition's first input sample (first decode time + an AAC
     encoder's priming of 1024 samples - edit media_time: the priming that edit cuts)
  shift = P - S, in ms, negative: the picture early
Within 2 ms it is in sync. A shift that the video edit's media_time set to 0 brings within 2 ms is
this fault, and that rendition is mended. Every video rendition is held to it -- each rung of a
ladder has an init segment of its own -- and the version is affected when any of them is; one
shifted for another reason is reported (shifted-other) and never touched. A version with no stored
probe, one not held to a hash, more than one source, a stereo rendition that is not AAC, audio
streams that start at different times where the package does not say which it was made from, or
an init segment that is not one track's as a packager writes it -- an ftyp and a moov, a handler
of the rendition's kind, at most 4 MiB, every entry inside its box -- is undecidable and never
touched; one an event removed or superseded is skipped; one whose chain does not hold, whose
checksums are pending or whose package.json is not written as the records write it is a conflict.

The mend (package.schema.json, mends; kind avsync-editlist). In the init segment of each affected
rendition the media_time of its edit list's first media edit becomes 0: the same bytes but that
field, so its first frame is presented at its composition time, where the source has it. Then the
chain is closed again, as library-v2-fill-checksums.py closes one. The files are written in the
order no reader can take a half-written mend for a whole package: checksums.sha256 first (the
mended files' lines; package.json no longer names it, so the chain fails), then the init segments,
then package.json with the checksums file's new hash and an entry in its mends,
  {kind, file, box, old, new, sha256Before, sha256After, at, by}
(nothing else of it changes), and .complete last with package.json's new hash; `sha256sum -c
checksums.sha256` holds after it as before. The entry keeps the hash the file had before, and its
at the moment it was made (entries are ordered by their moments, whatever their zone or precision).
Each file is written to a temporary file of the process's own beside it (<name>.<pid>-<random>
.mending) and renamed into place, the temporary file taken away when the write fails; the bytes it
replaces are kept until the mend is recorded, and the version is reported mended only once its
record holds.

A dry run (the default) changes nothing and lists, per affected rendition, the file, the byte offset
of the field, the old and the new media_time and the file's old and new sha256, and the old and new
sha256 of checksums.sha256, package.json and .complete, and names a mend's files not yet put in
place (*.mending) it finds in a version. Runs come from one host: --apply and --undo hold the
library's mend lock (LIBRARY/.work/migration/mends.lock, flock: one run at a time, whatever its name,
and none left behind by a run that died; a file system that gives no lock is refused), and claim
each version before they write it (.work/migration/claims/<versionId>.claim, made only where there is
none, so that two runs never write one version even where the lock does not hold across hosts; a
claim a run of the same name left on the same host by a process that is gone is taken over, any
other refused). They journal under LIBRARY/.work/migration/<run>/ (the run is mend-avsync-editlist
unless --run names another), every path relative to LIBRARY:
  mends.jsonl       {"op": "mend", "dir", "itemId", "versionId", "packageId", "files": [{file, before,
                    after}], "mends": [the entries], "by"} before a version's files are replaced, and
                    {"op": "mended", ..., "complete": "sha256:<hex>"} once they are, the .complete
                    then: the catalog notes and verifies each version from these lines; "undo" and
                    "undone" lines alike for --undo; ASCII, one JSON object a line (a line a crash
                    cut short is skipped, with a warning)
  before/<dir>/     the files a step replaced, until its last line is written
--apply and --undo first finish what an earlier run of the same name left between a step's first
line and its last, for the items they are given; a version another run left so is refused, naming
that run. Running either again changes nothing. --undo puts back each avsync-editlist entry of a
version, newest first -- the field's old value, held to the entry's sha256Before -- removes the
entries, writes the file's line in checksums.sha256, the file, package.json and .complete again,
and leaves the version byte for byte as it was before it was mended. A run exits 1 when a version
is in conflict, was left mid-way, or what an earlier run left could not be finished, and when an
item asked for is not in the library.

It reads only the item folders it is given, never an original or the database, and writes nothing
but the files above. Python 3.11 or later, nothing to install.
"""
import argparse
import csv
import datetime
import fcntl
import glob
import hashlib
import json
import os
import re
import secrets
import socket
import struct
import sys

KIND = "avsync-editlist"
BOX = "moov/trak/edts/elst"
SUMS = "checksums.sha256"
PRIMING = 1024  # an AAC encoder's priming, in samples
TOLERANCE_MS = 2.0
RUN = "mend-avsync-editlist"
MAX_INIT = 1 << 22  # what the validator reads of an init segment
# an entry's fields, in the order the record keeps them
ENTRY = ("kind", "file", "box", "old", "new", "sha256Before", "sha256After", "at", "by")
COMPLETE = re.compile(r"^sha256:[0-9a-f]{64}$")
MENDING = ".mending"  # the end of the name of a file written beside the one it replaces
HOST = socket.gethostname()
UNREADABLE = (struct.error, ValueError, ZeroDivisionError, IndexError, KeyError, TypeError, OSError, OverflowError)


# ------------------------------------------------------------------------------------------- util
def now(precise=False):
    t = datetime.datetime.now(datetime.timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%S.%fZ") if precise else t.strftime("%Y-%m-%dT%H:%M:%SZ")


def moment(value):
    """A record's date-time as an aware datetime, None when it is none: entries made in other time
    zones or at other precisions are ordered by when they were made, not by their text."""
    try:
        t = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo is not None else None


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
    """data at path in one step: a temporary file of this process's own in its folder (<name>.<pid>-
    <random>.mending), written through, renamed. A write that fails (no space, no permission, an
    interrupt) takes its temporary file away."""
    tmp = f"{path}.{os.getpid()}-{secrets.token_hex(4)}{MENDING}"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
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
            if off + 16 > end:
                raise ValueError(f"box {kind!r} at {off} runs past its parent")
            size, hdr = struct.unpack(">Q", buf[off + 8:off + 16])[0], 16
        elif size == 0:
            size = end - off
        if size < hdr or off + size > end:
            raise ValueError(f"box {kind!r} at {off} runs past its parent")
        yield kind, off, hdr, size
        off += size
    if off != end:
        raise ValueError(f"{end - off} bytes at {off} are no box")


def find(buf, path, off=0, end=None):
    """(payload start, end) of every box at path."""
    out = []
    for kind, o, hdr, size in boxes(buf, off, end):
        if kind == path[0]:
            out += [(o + hdr, o + size)] if len(path) == 1 else find(buf, path[1:], o + hdr, o + size)
    return out


def need(cond, why):
    if not cond:
        raise ValueError(why)


def init_timing(data, handler=None):
    """An init segment of one track as a packager writes it: {timescale, handler, samplerate, edits:
    [(offset of media_time, its width, media_time, segment_duration, rate)]}. Raises ValueError for
    one that is none, or whose handler is not `handler`."""
    need(len(data) <= MAX_INIT, f"{len(data)} bytes, more than an init segment's {MAX_INIT}")
    top = [kind for kind, *_ in boxes(data)]
    need(top[:1] == [b"ftyp"] and top.count(b"moov") == 1 and set(top) <= {b"ftyp", b"moov", b"free", b"skip"},
         f"its top-level boxes are {b' '.join(top).decode('latin-1')}, not an ftyp and a moov")
    traks = find(data, [b"moov", b"trak"])
    need(len(traks) == 1, f"{len(traks)} tracks, one expected")
    mdhd = find(data, [b"moov", b"trak", b"mdia", b"mdhd"])
    need(len(mdhd) == 1, "no media header")
    ms, me = mdhd[0]
    v = data[ms]
    need(ms + (24 if v else 16) <= me, "a media header too short")
    ts = struct.unpack(">I", data[ms + (20 if v else 12):ms + (24 if v else 16)])[0]
    need(ts > 0, "a timescale of 0")
    hdlr = find(data, [b"moov", b"trak", b"mdia", b"hdlr"])
    need(len(hdlr) == 1 and hdlr[0][0] + 12 <= hdlr[0][1], "no handler")
    kind = data[hdlr[0][0] + 8:hdlr[0][0] + 12]
    if handler is not None:
        need(kind == handler, f"its handler is {kind.decode('latin-1')!r}, not {handler.decode()!r}")
    rate = None
    for es, ee in find(data, [b"moov", b"trak", b"mdia", b"minf", b"stbl", b"stsd"]):
        if es + 8 + 8 <= ee:
            entry = es + 8  # past version, flags and the entry count
            size, fmt = struct.unpack(">I4s", data[entry:entry + 8])
            if fmt == b"mp4a" and entry + 36 <= min(ee, entry + size):
                rate = struct.unpack(">I", data[entry + 32:entry + 36])[0] >> 16
    edits = []
    for es, ee in find(data, [b"moov", b"trak", b"edts", b"elst"]):
        need(es + 8 <= ee, "an edit list too short")
        v = data[es]
        n = struct.unpack(">I", data[es + 4:es + 8])[0]
        width = 20 if v == 1 else 12
        need(es + 8 + n * width <= ee, f"an edit list of {n} entries runs past its box")
        p = es + 8
        for _ in range(n):
            if v == 1:
                dur, mt = struct.unpack(">Qq", data[p:p + 16])
                edits.append((p + 8, 8, mt, dur, struct.unpack(">h", data[p + 16:p + 18])[0]))
            else:
                dur, mt = struct.unpack(">Ii", data[p:p + 8])
                edits.append((p + 4, 4, mt, dur, struct.unpack(">h", data[p + 8:p + 10])[0]))
            p += width
    return {"timescale": ts, "handler": kind, "samplerate": rate, "edits": edits}


def media_edit(edits):
    """The first media edit (media_time not -1), or None."""
    return next((e for e in edits if e[2] != -1), None)


def first_fragment(data):
    """(baseMediaDecodeTime, first sample's composition offset) of a media segment's first traf."""
    tfdt = find(data, [b"moof", b"traf", b"tfdt"])
    trun = find(data, [b"moof", b"traf", b"trun"])
    need(tfdt and trun, "no track fragment")
    ts, te = tfdt[0]
    v = data[ts]
    need(ts + (12 if v else 8) <= te, "a decode time too short")
    base = struct.unpack(">Q" if v else ">I", data[ts + 4:ts + (12 if v else 8)])[0]
    rs, re_ = trun[0]
    need(rs + 8 <= re_, "a track run too short")
    tv, flags = data[rs], int.from_bytes(data[rs + 1:rs + 4], "big")
    p = rs + 8
    if flags & 0x1:
        p += 4
    if flags & 0x4:
        p += 4
    if not flags & 0x800:
        return base, 0
    p += 4 * sum(1 for bit in (0x100, 0x200, 0x400) if flags & bit)
    need(p + 4 <= re_, "a track run too short")
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


def rendition_timing(rendition_dir, handler):
    t = init_timing(read(os.path.join(rendition_dir, "init.mp4")), handler)
    seg = first_segment(rendition_dir)
    need(seg is not None and os.path.isfile(seg), "no media segment")
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
    head = read(mark).decode("utf-8", "replace").strip()
    if not COMPLETE.match(head) or head != sha_bytes(pkg_bytes):
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


def entries_problem(d, pkg, listed):
    """What is wrong with the mends of the version folder d, "" when nothing is: each mended file is
    the bytes its line lists and its last entry left, and its entries, newest first, give back the
    bytes each was made to with its field's old value written back -- so nothing else of the file
    changed."""
    by_file = {}
    for m in pkg.get("mends") or []:
        by_file.setdefault(m.get("file"), []).append(m)
    for rel, ms in by_file.items():
        p = os.path.join(d, rel or "")
        if rel not in listed or not os.path.isfile(p):
            return f"its mends name {rel}, which {SUMS} does not list"
        data = read(p)
        if sha_bytes(data) != "sha256:" + listed[rel] or sha_bytes(data) != ms[-1].get("sha256After"):
            return f"{rel} is not the bytes its last mend left and {SUMS} lists"
        for m in reversed(ms):
            if sha_bytes(data) != m.get("sha256After"):
                return f"{rel}: its mends do not lead back to the bytes it has"
            edit = media_edit(init_timing(data, b"vide" if m.get("kind") == KIND else None)["edits"])
            if m.get("kind") != KIND or edit is None or edit[2] != m.get("new"):
                return f"{rel}: its edit list's media_time is not the one its mend wrote"
            data = with_media_time(data, edit, m["old"])
            if sha_bytes(data) != m.get("sha256Before"):
                return f"{rel}: putting back {m['old']} does not give the bytes it had before its mend"
    return ""


def last_after(pkg, rel):
    """The hash the last mend of the file rel left, or None when nothing mended it."""
    ms = [m for m in pkg.get("mends") or [] if m.get("file") == rel]
    return ms[-1]["sha256After"] if ms else None


def close_chain(pkg, sums, changes, mends):
    """The chain over a version whose files changes names changed ({file: (hash before, hash after)}):
    checksums.sha256 with those files' lines written again, package.json with the checksums file's
    new hash and mends (none: the key gone), and .complete -- as library-v2-fill-checksums.py closes
    one. Raises ValueError when a line is not there exactly once."""
    lines = sums.decode("utf-8").splitlines(keepends=True)
    for rel, (before, after) in sorted(changes.items()):
        at = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == f"{before.split(':', 1)[1]}  {rel}"]
        if len(at) != 1 or sum(1 for line in lines if line.rstrip("\r\n").endswith(f"  {rel}")) != 1:
            raise ValueError(f"{SUMS} does not list {rel} as {before}, once")
        old = lines[at[0]]
        lines[at[0]] = f"{after.split(':', 1)[1]}  {rel}" + old[len(old.rstrip(chr(13) + chr(10))):]
    new_sums = "".join(lines).encode("utf-8")
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
        self.plan = {}        # file -> new bytes (checksums.sha256, the inits, package.json, .complete)
        self.files = []       # [{file, before, after}] in the order they are written
        self.entries = []     # the mends entries an apply records

    def to_json(self):
        return {"itemId": self.item_id, "versionId": self.version_id, "dir": self.rel,
                "packageId": self.package_id, "status": self.status, "reason": self.reason,
                "shiftMs": self.shift_ms, "renditions": self.renditions, "files": self.files,
                "renditionsLeft": [{"dir": d, "why": w} for d, w in self.left]}


def probe_starts(probe, audio_index=None):
    """(video start, audio start) from a probe, or (None, why): the audio stream audio_index names,
    else the start every audio stream shares."""
    streams = probe.get("streams") or []
    video = [s for s in streams if s.get("codec_type") == "video"
             and not (s.get("disposition") or {}).get("attached_pic")]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if not video or not audio:
        return None, "its probe has no video or no audio stream"
    try:
        vs = float(video[0]["start_time"])
        if audio_index is not None:
            named = [s for s in audio if s.get("index") == audio_index]
            if not named:
                return None, f"its probe has no audio stream {audio_index}, which its stereo rendition was made from"
            return (vs, float(named[0]["start_time"])), None
        starts = {round(float(s["start_time"]), 6) for s in audio}
    except (KeyError, TypeError, ValueError):
        return None, "its probe has no stream start times"
    if len(starts) != 1:
        return None, ("its package does not say which audio stream its stereo rendition was made from "
                      "(sourceStreamIndex), and the source's audio streams start at different times")
    return (vs, starts.pop()), None


def probe_held(item_dir, source_id, probe_rec):
    """The stored probe's path, held to the hash its source record keeps; (None, why) otherwise. A
    record that keeps no hash holds nothing: the folder's own checksums are written with it."""
    rel = probe_rec.get("file") or ""
    p = os.path.join(item_dir, rel)
    if not rel.startswith(f"sources/{source_id}/") or ".." in rel.split("/") or not os.path.isfile(p):
        return None, "no stored probe"
    if not probe_rec.get("sha256"):
        return None, "its source record keeps no hash of the stored probe: nothing holds the probe to the source"
    if sha_bytes(read(p)) != probe_rec["sha256"]:
        return None, "the stored probe does not match its source record's hash"
    return p, None


def decide(v, removed, superseded, by="", at=None):
    """v.status: affected, mended, in-sync, shifted-other, undecidable, skipped or conflict; for an
    affected version the plan an apply writes. Input that cannot be read fails this version alone."""
    try:
        _decide(v, removed, superseded, by, at)
    except UNREADABLE as e:
        v.status, v.reason = "undecidable", f"its records or boxes cannot be read: {e.__class__.__name__}: {e}"
        v.renditions, v.files, v.plan, v.entries = [], [], {}, []


def _decide(v, removed, superseded, by, at):
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
    probe_path, why = probe_held(v.item_dir, sources[0], source.get("probe") or {})
    if probe_path is None:
        v.status, v.reason = "undecidable", why
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
        a = rendition_timing(os.path.join(d, audios[0]["dir"]), b"soun")
    except UNREADABLE as e:
        v.status, v.reason = "undecidable", f"its stereo rendition's boxes cannot be read: {e}"
        return
    a_edit = media_edit(a["edits"])
    priming = PRIMING * a["timescale"] / (a["samplerate"] or a["timescale"])
    a_first = (a["tfdt"] + priming - (a_edit[2] if a_edit else 0)) / a["timescale"]
    mended_files = {m.get("file") for m in package.get("mends") or [] if m.get("kind") == KIND}
    in_sync, mended, other = [], [], []
    dirs = [str(r.get("dir") or "") for r in videos]
    bad = [x for x in dirs if not re.fullmatch(r"hls/[^/]+", x) or x == "hls/.." or dirs.count(x) > 1]
    if bad:
        v.status, v.reason = "undecidable", f"its video renditions' folders are not one hls/<name> each: {bad[0]!r}"
        return
    for i, rrel in enumerate(dirs):
        rdir = os.path.join(d, rrel)
        try:
            t = rendition_timing(rdir, b"vide")
        except UNREADABLE as e:
            v.status, v.reason = "undecidable", f"the boxes of its video rendition {rrel} cannot be read: {e}"
            v.renditions = []
            return
        e, ts = media_edit(t["edits"]), t["timescale"]
        mt = e[2] if e else 0
        P = (t["tfdt"] + t["cto"] - mt) / ts - a_first
        shift = round((P - S) * 1000, 1)
        after = round((P + mt / ts - S) * 1000, 1)
        if i == 0:
            v.shift_ms = shift
        if abs(shift) <= TOLERANCE_MS:
            why = (f"its video starts {P * 1000:+.1f} ms after its sound, as in the source ({S * 1000:+.1f} ms)"
                   + (f"; {rrel}/init.mp4 has an {KIND} entry" if f"{rrel}/init.mp4" in mended_files else ""))
            (mended if f"{rrel}/init.mp4" in mended_files else in_sync).append((rrel, why))
        elif len(t["edits"]) != 1 or e is None or e[4] != 1:
            other.append((rrel, f"shifted {shift:+.1f} ms, but its edit list is not one media edit at rate 1 "
                                f"({len(t['edits'])} edits): another fault, not this mend"))
        elif mt <= 0:
            other.append((rrel, f"shifted {shift:+.1f} ms, and its edit list cuts nothing (media_time {mt}): "
                                f"another fault, not this mend"))
        elif abs(after) > TOLERANCE_MS:
            other.append((rrel, f"shifted {shift:+.1f} ms, and its edit list ({mt}/{ts}) would leave it "
                                f"{after:+.1f} ms: another fault, not this mend"))
        else:
            v.renditions.append({"file": f"{rrel}/init.mp4", "offset": e[0], "width": e[1], "old": mt, "new": 0,
                                 "timescale": ts, "shiftMs": shift, "afterMs": after})
    v.left = [(rrel, "shifted-other: " + why) for rrel, why in other]  # never touched, always reported
    if not v.renditions:
        if other:
            v.status, v.reason = "shifted-other", f"{len(other)} of its {len(dirs)} video renditions shifted for another reason"
        elif mended:
            v.status, v.reason = "mended", mended[0][1]
        else:
            v.status, v.reason = "in-sync", (in_sync[0][1] if len(in_sync) == 1 else
                                             f"all {len(in_sync)} video renditions in sync with the source")
        return
    problem = plan(v, at or now(), by)
    if problem:
        v.status, v.reason = "conflict", problem
        return
    v.status = "affected"
    r0 = v.renditions[0]
    v.reason = (f"{', '.join(r['file'].rsplit('/', 1)[0] for r in v.renditions)}: picture {-r0['shiftMs']:.1f} ms "
                f"early against its sound: its video edit list ({r0['old']}/{r0['timescale']}) cuts the delay "
                f"the source has; with media_time 0 it is {r0['afterMs']:+.1f} ms")


def plan(v, at, by):
    """The bytes an apply writes for v, in the order it writes them: checksums.sha256 with the
    mended files' lines, each affected init, package.json with the checksums' new hash and the
    entries, .complete."""
    pkg, pkg_bytes, listed = chain(v.dir)
    if pkg is None:
        return listed  # why the chain does not hold
    if json_bytes(pkg) != pkg_bytes:
        return "package.json is not written as the library writes its records: it could not be put back byte for byte"
    if read(os.path.join(v.dir, ".complete")) != complete_bytes(pkg_bytes):
        return ".complete is not written as the library writes it (sha256:<hex> and a line break): it could not be put back byte for byte"
    why = entries_problem(v.dir, pkg, listed)
    if why:
        return why
    sums = read(os.path.join(v.dir, SUMS))
    entries = list(pkg.get("mends") or [])
    last = max((m.get("at") for m in entries if moment(m.get("at"))), key=moment, default=None)
    if last is not None and moment(last) > moment(at):
        at = last  # this host's clock is behind the last entry's: the entries stay in the order they were made
    changes = {}
    for r in v.renditions:
        data = read(os.path.join(v.dir, r["file"]))
        if r["file"] not in listed:
            return f"{SUMS} does not list {r['file']}"
        if sha_bytes(data) != "sha256:" + listed[r["file"]]:
            return f"{r['file']} does not match its line in {SUMS}"
        new = with_media_time(data, (r["offset"], r["width"]), 0)
        r["before"], r["after"] = sha_bytes(data), sha_bytes(new)
        v.plan[r["file"]] = new
        changes[r["file"]] = (r["before"], r["after"])
        entry = {"kind": KIND, "file": r["file"], "box": BOX, "old": r["old"], "new": 0,
                 "sha256Before": r["before"], "sha256After": r["after"], "at": at, "by": by or "-"}
        entries.append(entry)
        v.entries.append(entry)
    try:
        new_sums, new_pkg, new_complete = close_chain(pkg, sums, changes, entries)
    except ValueError as e:
        return str(e)
    v.plan[SUMS], v.plan["package.json"], v.plan[".complete"] = new_sums, new_pkg, new_complete
    v.files = [{"file": SUMS, "before": sha_bytes(sums), "after": sha_bytes(new_sums)}] + [
        {"file": r["file"], "before": r["before"], "after": r["after"]} for r in v.renditions] + [
        {"file": "package.json", "before": sha_bytes(pkg_bytes), "after": sha_bytes(new_pkg)},
        {"file": ".complete", "before": sha_bytes(read(os.path.join(v.dir, ".complete"))),
         "after": sha_bytes(new_complete)}]
    return None


def recorded(d):
    """Why the version's record does not hold as a mend leaves it, "" when it does: its chain holds,
    and its mends lead to the bytes its files have and its checksums list."""
    pkg, _, listed = chain(d)
    if pkg is None:
        return listed
    return entries_problem(d, pkg, listed)


# ------------------------------------------------------------------------------------------ runs
def lock_library(root, exclusive):
    """The library's mend lock, held until the process ends: exclusive for --apply and --undo,
    shared for a dry run where a run made it, so that no run reads while another one writes. The
    lock is the kernel's on the open file: it goes with the process, however that ends, and the
    empty file it is held on stays."""
    d = os.path.join(root, ".work", "migration")
    p = os.path.join(d, "mends.lock")
    if not exclusive and not os.path.isfile(p):
        return None  # no run ever wrote: a dry run creates nothing
    try:
        os.makedirs(d, exist_ok=True)
        fd = os.open(p, os.O_CREAT | os.O_RDWR if exclusive else os.O_RDONLY, 0o644)
    except OSError as e:
        sys.exit(f"cannot open the library's mend lock ({e.strerror}: {p}): a run writes under .work/migration/, "
                 f"which must be writable" if exclusive else f"cannot read the library's mend lock ({e.strerror}: {p})")
    try:
        fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        sys.exit("another mend run holds the library's mend lock (.work/migration/mends.lock): wait for it to end")
    except OSError as e:
        os.close(fd)
        sys.exit(f"the library's file system gives no lock ({e.strerror}): runs come from one host, on a file "
                 f"system that locks (.work/migration/mends.lock)")
    return fd


def open_steps(root):
    """Every step a run of the library left between its first line and its last: {version dir:
    (run name, the step's first line)}."""
    out = {}
    for journal in sorted(glob.glob(os.path.join(root, ".work", "migration", "*", "mends.jsonl"))):
        name = os.path.basename(os.path.dirname(journal))
        last = {}
        for e in Run(root, name).lines():
            if e.get("op") in ("mend", "mended", "undo", "undone") and e.get("dir"):
                last[e["dir"]] = e
        for vrel, e in last.items():
            if e["op"] in ("mend", "undo"):
                out[vrel] = (name, e)
    return out


class Run:
    def __init__(self, root, name):
        self.root = root
        self.name = name
        self.dir = os.path.join(root, ".work", "migration", name)
        self.journal = os.path.join(self.dir, "mends.jsonl")

    def lines(self):
        """The journal's lines; one that cannot be read -- cut short by a crash or a full disk, in the
        middle of a character too -- is skipped, with a warning."""
        if not os.path.isfile(self.journal):
            return []
        out = []
        with open(self.journal, "rb") as f:
            for n, line in enumerate(f, 1):
                if not line.strip():
                    continue
                try:
                    e = json.loads(line.decode("utf-8"))
                    if not isinstance(e, dict):
                        raise ValueError("no object")
                    out.append(e)
                except ValueError:  # UnicodeDecodeError is one
                    print(f"warning: {os.path.relpath(self.journal, self.root)} line {n} cannot be read "
                          f"(cut short?): skipped", file=sys.stderr)
        return out

    def add(self, **entry):
        """One line, written through; a line before it cut short ends first, and one this write
        could not finish is taken back off."""
        os.makedirs(self.dir, exist_ok=True)
        line = (json.dumps({"at": now(precise=True), **entry}, ensure_ascii=True, sort_keys=True) + "\n").encode("ascii")
        with open(self.journal, "ab+") as f:
            size = f.seek(0, os.SEEK_END)
            if size:
                f.seek(size - 1)
                if f.read(1) != b"\n":
                    line = b"\n" + line
            try:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
            except BaseException:
                f.truncate(size)
                raise

    def claim(self, version_id, vrel):
        """Claim the version for this run: .work/migration/claims/<versionId>.claim, made only where
        there is none (O_EXCL), so that two runs never write one version, whichever host they run on.
        A claim this run's name left on this host, by a process that is gone, is taken over (it is
        what a run that died leaves); any other is refused with what it says (ClaimHeld)."""
        d = os.path.join(self.root, ".work", "migration", "claims")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, f"{version_id}.claim")
        mine = json.dumps({"run": self.name, "host": HOST, "pid": os.getpid(), "at": now(precise=True), "dir": vrel},
                          ensure_ascii=True, sort_keys=True).encode("ascii") + b"\n"
        for _ in range(2):
            try:
                fd = os.open(p, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                try:
                    held = json.loads(read(p))
                except (OSError, ValueError):
                    held = {}
                if held.get("run") == self.name and held.get("host") == HOST and held.get("pid") == os.getpid():
                    return p
                if held.get("run") == self.name and held.get("host") == HOST and not alive(held.get("pid")):
                    os.remove(p)  # left by this run's name on this host by a process that died
                    continue
                raise ClaimHeld(held, p)
            try:
                os.write(fd, mine)
                os.fsync(fd)
            finally:
                os.close(fd)
            return p
        raise ClaimHeld({}, p)

    def unclaim(self, version_id):
        p = os.path.join(self.root, ".work", "migration", "claims", f"{version_id}.claim")
        try:
            if json.loads(read(p)).get("pid") == os.getpid():
                os.remove(p)
        except (OSError, ValueError):
            pass

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


class ClaimHeld(RuntimeError):
    def __init__(self, held, path):
        self.held = held
        who = (f"run {held.get('run')} on {held.get('host')} (process {held.get('pid')}) since {held.get('at')}"
               if held else "a claim that cannot be read")
        super().__init__(f"claimed by {who}: another run is at it, or one that died on that host left it "
                         f"({os.path.basename(path)}; once no run is going on, that run's name on that host "
                         f"finishes it)")


def alive(pid):
    try:
        os.kill(int(pid), 0)
    except (TypeError, ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True
    return True


def strays(vdir):
    """The files a mend writes beside the ones it replaces before it renames them (*.mending), in
    the folders of the version folder vdir it writes in -- the folder itself and its renditions'
    (hls/<rendition>/): relative paths."""
    out = []
    for d in [vdir] + sorted(glob.glob(os.path.join(glob.escape(vdir), "hls", "*", ""))):
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        out += [os.path.relpath(os.path.join(d, n), vdir).replace(os.sep, "/") for n in names if n.endswith(MENDING)]
    return out


def clear_strays(d, files):
    """Take away what an earlier process of this step left beside the files it writes (their
    *.mending), once the version is claimed for this run."""
    for f in files:
        for p in glob.glob(glob.escape(os.path.join(d, f["file"])) + ".*" + MENDING) + [os.path.join(d, f["file"]) + MENDING]:
            if os.path.isfile(p):
                os.remove(p)


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
    run.claim(v.version_id, v.rel)
    try:
        for n in names:
            run.keep(v.rel, n, read(os.path.join(v.dir, n)))
        run.add(op=op, dir=v.rel, itemId=v.item_id, versionId=v.version_id, packageId=v.package_id,
                files=files, **extra)
    except BaseException:
        run.unclaim(v.version_id)  # nothing of the version was written yet
        raise
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
    run.unclaim(version_id)


def resume(run, steps, named):
    """Finish each step this run left between its first line and its last, of the version folders
    named (relative to the library), from the bytes it kept and the entries it journaled:
    [(dir, what, finished)]."""
    out = []
    for vrel, (name, e) in sorted(steps.items()):
        if name != run.name or vrel not in named:
            continue
        d = os.path.join(run.root, vrel)
        try:
            run.claim(e["versionId"], vrel)
            clear_strays(d, e["files"])
            kept = {f["file"]: read(run.kept(vrel, f["file"])) for f in e["files"]}
            new = rebuilt(kept, e)
            write_planned(d, e["files"], new)
            finish(run, vrel, e["itemId"], e["versionId"], e.get("packageId"), e["op"], e["files"],
                   [f["file"] for f in e["files"]])
            out.append((vrel, f"finished the {e['op']} an earlier run left", True))
        except UNREADABLE + (RuntimeError,) as x:
            out.append((vrel, f"an earlier run left a {e['op']} unfinished, and it cannot be finished: {x}", False))
    return out


def rebuilt(kept, e):
    """The bytes a journaled step writes, from the files it kept and the entries it names: for a
    mend, each init with its new media_time; for an undo, each init put back through its entries,
    newest first. Then the chain over them (close_chain)."""
    pkg = json.loads(kept["package.json"])
    new, changes = {}, {}
    if e["op"] == "mend":
        for m in e["mends"]:
            t = init_timing(kept[m["file"]], b"vide")
            new[m["file"]] = with_media_time(kept[m["file"]], media_edit(t["edits"]), m["new"])
        added = [{k: m[k] for k in ENTRY} for m in e["mends"]]  # the journal sorts their keys
        mends = list(pkg.get("mends") or []) + added
    else:
        gone = e["mends"]
        state = {m["file"]: kept[m["file"]] for m in gone}
        for m in reversed(gone):
            t = init_timing(state[m["file"]], b"vide")
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
    why = entries_problem(v.dir, pkg, listed)
    if why:
        return False, why
    v.package_id = pkg.get("packageId")
    kept = {m["file"]: read(os.path.join(v.dir, m["file"])) for m in gone}
    kept[SUMS] = read(os.path.join(v.dir, SUMS))
    kept["package.json"], kept[".complete"] = pkg_bytes, read(os.path.join(v.dir, ".complete"))
    e = {"op": "undo", "mends": gone}
    new = rebuilt(kept, e)
    inits = sorted({m["file"] for m in gone})
    files = [{"file": SUMS, "before": sha_bytes(kept[SUMS]), "after": sha_bytes(new[SUMS])}] + [
        {"file": f, "before": sha_bytes(kept[f]), "after": sha_bytes(new[f])} for f in inits] + [
        {"file": n, "before": sha_bytes(kept[n]), "after": sha_bytes(new[n])} for n in ("package.json", ".complete")]
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


def item_folder(root, item_id, hint):
    """The item's folder, (None, why) when there is none: one a CSV names must hold the item's
    item.json."""
    idir = item_dir_of(root, item_id, hint)
    if idir is None:
        return None, "no item folder of a library v2 tree"
    if hint:
        try:
            named = json.loads(read(os.path.join(idir, "item.json"))).get("itemId")
        except (OSError, ValueError):
            named = None
        if named != item_id:
            return None, f"the folder {hint} is not the item's: its item.json names {named}"
    return idir, None


def versions_of(root, item_id, hint, by):
    """The item's folder and its versions, decided; (None, why) when there is no such folder."""
    idir, why = item_folder(root, item_id, hint)
    if idir is None:
        return None, why
    evs = events_of(idir)
    removed = {e.get("versionId") for e in evs if e.get("kind") == "version-removed"}
    superseded = {e.get("packageId") for e in evs if e.get("kind") == "package-superseded"}
    out = []
    for vdir in sorted(glob.glob(os.path.join(idir, "versions", "*"))):
        if os.path.isdir(vdir):
            v = Version(root, item_id, idir, vdir)
            decide(v, removed, superseded, by)
            out.append(v)
    if not out:
        why = "no version folder"
        if os.path.isfile(os.path.join(idir, "manifest.json")):
            why = "a package-store folder (manifest.json), not a library v2 item: no stored probe"
        return None, why
    return idir, out


def show(v):
    print(f"{v.status:13} {v.item_id} {v.version_id}  {v.reason}")
    for f in v.files:
        r = next((x for x in v.renditions if x["file"] == f["file"]), None)
        if r is not None:
            print(f"{'':13}   {v.rel}/{r['file']} @{r['offset']}: media_time {r['old']} -> {r['new']} "
                  f"(timescale {r['timescale']})  {f['before']} -> {f['after']}")
        else:
            print(f"{'':13}   {v.rel}/{f['file']}: {f['before']} -> {f['after']}")
    for d, why in v.left:
        print(f"{'':13}   {v.rel}/{d}: {why}")


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
    if not re.fullmatch(r"[A-Za-z0-9._-]+", a.run):
        sys.exit(f"{a.run!r} is no run's name: letters, digits, dots, dashes and underscores")
    lock_library(root, a.apply or a.undo)
    run = Run(root, a.run)
    counts, failed = {}, False
    wanted = targets(a)
    folders = {}
    for item_id, hint in wanted:
        idir, _ = item_folder(root, item_id, hint)  # only an item's own folder is finished for it
        if idir:
            for vdir in glob.glob(os.path.join(idir, "versions", "*")):
                folders[os.path.relpath(vdir, root).replace(os.sep, "/")] = item_id
    steps = open_steps(root)
    if a.apply or a.undo:
        for vrel, what, done in resume(run, steps, set(folders)):
            print(f"{'resumed' if done else 'conflict':13} {vrel}  {what}")
            failed |= not done
        steps = open_steps(root)
    for item_id, hint in wanted:
        idir, vs = versions_of(root, item_id, hint, a.by)
        if idir is None:
            failed = True  # an item asked for that this library does not hold is no success
            counts["undecidable"] = counts.get("undecidable", 0) + 1
            print(json.dumps({"itemId": item_id, "status": "undecidable", "reason": vs}) if a.json
                  else f"{'undecidable':13} {item_id}  {vs}")
            continue
        for v in vs:
            left = strays(v.dir)
            if v.rel in steps:
                name = steps[v.rel][0]
                v.status, failed = "interrupted", True
                v.reason = (f"run {name} stopped mid-way through a {steps[v.rel][1]['op']} of it: "
                            f"--run {name} --apply or --undo finishes it"
                            + (f"; its files not yet put in place: {', '.join(left)}" if left else ""))
                v.renditions, v.files, v.left = [], [], []
            elif left:
                v.status, failed = "conflict", True
                v.reason = (f"a mend's files not yet put in place are in it ({', '.join(left)}), and no run's journal "
                            f"has a step of it open: another run is writing it, or they were left; once no run is "
                            f"going on, take them away")
                v.renditions, v.files, v.left = [], [], []
            elif a.undo and v.status != "skipped":
                try:
                    done, why = undo_version(run, v, a.by)
                except UNREADABLE + (RuntimeError,) as x:
                    done, why = False, str(x)
                if why and v.rel in open_steps(root):
                    v.status, v.reason, failed = "interrupted", f"the undo stopped mid-way ({why}): --run {run.name} --undo finishes it", True
                elif why:
                    v.status, v.reason, failed = "conflict", why, True
                elif done:
                    v.status, v.reason = "undone", "put back as it was before it was mended"
                else:
                    v.status, v.reason = "unchanged", "nothing of it is mended"
                v.renditions, v.files, v.left = [], [], []
            elif a.apply and v.status == "affected":
                try:
                    apply_version(run, v)
                    why = recorded(v.dir)
                    if why:
                        raise RuntimeError(f"after the mend: {why}")
                    v.status = "mended"
                except UNREADABLE + (RuntimeError,) as x:
                    failed = True
                    if v.rel in open_steps(root):
                        v.status, v.reason = "interrupted", f"the mend stopped mid-way ({x}): --run {run.name} --apply or --undo finishes it"
                    else:
                        v.status, v.reason = "conflict", str(x)
            elif v.status == "conflict":
                failed = True
            counts[v.status] = counts.get(v.status, 0) + 1
            if a.json:
                print(json.dumps(v.to_json()))
            else:
                show(v)
    if not a.json:
        print("versions: " + ", ".join(f"{k} {n}" for k, n in sorted(counts.items())))
        if not a.apply and not a.undo:
            print("a dry run: nothing was changed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
