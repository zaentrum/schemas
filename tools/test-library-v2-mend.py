#!/usr/bin/env python3
"""Prove library-v2-mend.py does what it says, on copies of library/v2/examples.

  python tools/test-library-v2-mend.py

The tool uses nothing but the standard library; the cases that run validate-library-v2.py need an
interpreter that can import jsonschema, and say so when it is missing. Each case puts made-up
boxes into one version of a copy of the examples — init segments and a first media segment per
rendition, no media — and a source probe with stream starts, the shape of a package whose picture
plays early (its video edit list cuts what the source's video has before its sound):

  decides     the affected version, every video rendition of it whose boxes say the same, and the
              versions in sync, shifted for another reason, undecidable (no stored probe, the probe
              not its record's) and in conflict (a package.json the library did not write as it
              writes its records) are told apart; a dry run changes no byte
  applies     --apply changes the media_time field of each affected init segment and nothing else of
              it, writes its line in checksums.sha256 again, package.json with the checksums' new hash
              and the entries in its mends, and .complete; a plain sha256sum -c holds, the record
              logic's full chain check holds, and the tree validates with --check-checksums
  again       a second --apply changes no byte, its journal included
  undoes      --undo leaves the version byte for byte as it was before it was mended (diff -r against
              a pristine copy), and the tree validates
  resumes     a run killed after the first file it replaced is finished by the next --apply, to
              the same bytes, and one killed in an undo by the next --undo
"""
import glob, hashlib, json, os, shutil, struct, subprocess, sys, tempfile

TOOLS = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(TOOLS, "..", "library", "v2", "examples")
SCHEMAS = os.path.join(TOOLS, "..", "library", "v2")
TOOL = os.path.join(TOOLS, "library-v2-mend.py")
VALIDATOR = os.path.join(TOOLS, "validate-library-v2.py")


class Tally:
    def __init__(self):
        self.failed, self.passed = 0, 0

    def ok(self, name, condition, detail=""):
        print(f"{'pass' if condition else 'FAIL'}  {name}")
        if not condition:
            self.failed += 1
            if detail:
                print("      " + "\n      ".join(str(detail).strip().splitlines()[-8:]))
        else:
            self.passed += 1

    def eq(self, name, got, want):
        self.ok(name, got == want, f"got {got!r}\nwant {want!r}")


# ---------------------------------------------------------------- made-up boxes
def box(kind, payload):
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def full(kind, payload, version=0, flags=0):
    return box(kind, bytes([version]) + flags.to_bytes(3, "big") + payload)


def init_segment(timescale, media_time=None, handler=b"vide"):
    """An init segment of one track, with one media edit at media_time (None: no edit list)."""
    edts = b"" if media_time is None else box(b"edts", full(
        b"elst", struct.pack(">IIi", 1, 0, media_time) + struct.pack(">hh", 1, 0)))
    mdia = box(b"mdia", full(b"mdhd", bytes(8) + struct.pack(">I", timescale) + bytes(8))
               + full(b"hdlr", bytes(4) + handler + bytes(13)) + box(b"minf", box(b"stbl", b"")))
    trak = box(b"trak", full(b"tkhd", bytes(80), flags=3) + edts + mdia)
    return box(b"ftyp", b"iso6" + bytes(4) + b"iso6cmfc") + box(b"moov", full(b"mvhd", bytes(96)) + trak)


def media_segment(tfdt, cto, samples=3, duration=667):
    entries = b"".join(struct.pack(">IIi", duration, 10, cto) for _ in range(samples))
    trun = full(b"trun", struct.pack(">Ii", samples, 0) + entries, version=1, flags=0x000B01)
    traf = box(b"traf", full(b"tfhd", struct.pack(">I", 1), flags=0x020000)
               + full(b"tfdt", struct.pack(">Q", tfdt), version=1) + trun)
    return box(b"moof", full(b"mfhd", struct.pack(">I", 1)) + traf) + box(b"mdat", bytes(10 * samples))


PLAYLIST = "#EXTM3U\n#EXT-X-VERSION:6\n#EXT-X-TARGETDURATION:6\n#EXT-X-MAP:URI=\"init.mp4\"\n#EXTINF:6.0,\nseg-00001.m4s\n#EXT-X-ENDLIST\n"


# ---------------------------------------------------------------- the fixture
def json_bytes(doc):
    return (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def write(p, data):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode())


def sums_over(folder, names):
    write(os.path.join(folder, "checksums.sha256"), "".join(f"{sha(os.path.join(folder, n))}  {n}\n" for n in sorted(names)))


def the_version(root):
    """The examples' movie version made from one source: the one these cases mend."""
    for vp in sorted(glob.glob(os.path.join(root, "movies", "*", "*", "versions", "*"))):
        v = json.load(open(os.path.join(vp, "version.json")))
        if len(v["sourceIds"]) == 1:
            return vp, v["sourceIds"][0]
    raise AssertionError("no version of one source in the examples")


def shape(root, video_start="0.083000", audio_start="0.062000", file_start="0.000000",
          v_elst=1328, v_tfdt=0, v_cto=1328, a_elst=None, a_tfdt=1920):
    """The version the_version names, its renditions given made-up boxes and its source a probe with
    these starts; every record and checksum written again so the tree is a valid one. The defaults
    are a package whose picture is 82.3 ms early: video 83 ms after the file in the source, its
    package's video edit list cutting that, the stereo rendition starting where its source does."""
    vp, sid = the_version(root)
    item = os.path.dirname(os.path.dirname(vp))
    pkg = json.load(open(os.path.join(vp, "package.json")))
    for r in pkg["renditions"]["video"]:
        d = os.path.join(vp, r["dir"])
        write(os.path.join(d, "init.mp4"), init_segment(16000, v_elst))
        write(os.path.join(d, "seg-00001.m4s"), media_segment(v_tfdt, v_cto))
        write(os.path.join(d, "playlist.m3u8"), PLAYLIST)
    for r in pkg["renditions"]["audio"]:
        d = os.path.join(vp, r["dir"])
        write(os.path.join(d, "init.mp4"), init_segment(48000, a_elst, b"soun"))
        write(os.path.join(d, "seg-00001.m4s"), media_segment(a_tfdt, 0, duration=1024))
        write(os.path.join(d, "playlist.m3u8"), PLAYLIST)
    files = ["version.json"]
    for sub in ("hls", "subs", "trickplay", "trailers"):
        for base, dirs, names in os.walk(os.path.join(vp, sub)):
            dirs.sort()
            files += [os.path.relpath(os.path.join(base, n), vp) for n in sorted(names)]
    sums_over(vp, files)
    pkg["checksums"].update(sha256="sha256:" + sha(os.path.join(vp, "checksums.sha256")), files=len(files),
                            bytes=sum(os.path.getsize(os.path.join(vp, f)) for f in files))
    write(os.path.join(vp, "package.json"), json_bytes(pkg))
    write(os.path.join(vp, ".complete"), "sha256:" + sha(os.path.join(vp, "package.json")) + "\n")
    # the source's probe, with the stream starts the shape needs
    sd = os.path.join(item, "sources", sid)
    probe = json.load(open(os.path.join(sd, "ffprobe.json")))
    for s in probe["streams"]:
        if s.get("codec_type") == "video":
            s["start_time"] = video_start
        elif s.get("codec_type") == "audio":
            s["start_time"] = audio_start
    probe["format"]["start_time"] = file_start
    write(os.path.join(sd, "ffprobe.json"), json.dumps(probe, indent=2))
    src = json.load(open(os.path.join(sd, "source.json")))
    src["probe"]["sha256"] = "sha256:" + sha(os.path.join(sd, "ffprobe.json"))
    write(os.path.join(sd, "source.json"), json_bytes(src))
    listed = [line.split("  ", 1)[1].rstrip("\n") for line in open(os.path.join(sd, "checksums.sha256")) if "  " in line]
    sums_over(sd, listed)
    return vp, os.path.basename(item)


def tree():
    tmp = tempfile.mkdtemp(prefix="mend-")
    root = os.path.join(tmp, "library")
    shutil.copytree(EXAMPLES, root)
    return tmp, root


def snapshot(root, outside_work=True):
    out = {}
    for base, dirs, names in os.walk(root):
        rel = os.path.relpath(base, root)
        if outside_work and (rel == ".work" or rel.startswith(".work" + os.sep)):
            continue
        for n in names:
            p = os.path.join(base, n)
            out[os.path.relpath(p, root)] = sha(p)
    return out


def run(*args, **kw):
    return subprocess.run([sys.executable, TOOL, *args], capture_output=True, text=True, **kw)


def statuses(out):
    return {d["itemId"]: d["status"] for d in map(json.loads, out.splitlines()) if d.get("itemId")}


def can_validate():
    return subprocess.run([sys.executable, "-c", "import jsonschema, referencing"], capture_output=True).returncode == 0


def validates(t, name, root):
    if not can_validate():
        print(f"skip  {name} (no jsonschema in this Python)")
        return
    r = subprocess.run([sys.executable, VALIDATOR, "--check-checksums", "--schemas", SCHEMAS, root],
                       capture_output=True, text=True)
    t.ok(name, r.returncode == 0 and r.stdout.rstrip().endswith("OK"), r.stdout + r.stderr)


# ---------------------------------------------------------------- the cases
def decides(t):
    shapes = {
        "affected": {},
        "in sync": dict(video_start="0.000000", audio_start="-0.021000", file_start="-0.021000",
                        v_tfdt=336, a_elst=1024, a_tfdt=0),
        "shifted for another reason": dict(video_start="0.000000", audio_start="-0.021000", file_start="-0.021000",
                                           v_tfdt=672, a_elst=1024, a_tfdt=0),
    }
    want = {"affected": "affected", "in sync": "in-sync", "shifted for another reason": "shifted-other"}
    for name, kw in shapes.items():
        tmp, root = tree()
        try:
            vp, item = shape(root, **kw)
            before = snapshot(root, outside_work=False)
            r = run(root, "--items", item, "--json")
            got = statuses(r.stdout)
            t.eq(f"decides: {name}", got.get(item), want[name])
            if name == "affected":
                count = os.path.join(tmp, "count.csv")
                write(count, f"item_id,state\n{item},present\n")
                t.eq("decides: the items of a CSV with an item_id column", statuses(run(root, "--csv", count, "--json").stdout),
                     {item: "affected"})
            t.eq(f"decides: a dry run of the {name} package changes no byte", snapshot(root, outside_work=False), before)
            if name == "affected":
                d = next(json.loads(x) for x in r.stdout.splitlines() if json.loads(x).get("status") == "affected")
                t.eq("decides: every video rendition whose boxes say the same is mended",
                     [x["file"] for x in d["renditions"]],
                     [r["dir"] + "/init.mp4" for r in json.load(open(os.path.join(vp, "package.json")))["renditions"]["video"]])
                t.eq("decides: the shift, and what the mend leaves", (d["shiftMs"], d["renditions"][0]["afterMs"]), (-82.3, 0.7))
        finally:
            shutil.rmtree(tmp)
    # undecidable and conflict
    tmp, root = tree()
    try:
        vp, item = shape(root)
        probe = os.path.join(os.path.dirname(os.path.dirname(vp)), "sources", the_version(root)[1], "ffprobe.json")
        keep = open(probe, "rb").read()
        write(probe, keep + b" ")
        t.eq("decides: a probe that is not its record's is undecidable", statuses(run(root, "--items", item, "--json").stdout).get(item), "undecidable")
        os.remove(probe)
        t.eq("decides: no stored probe is undecidable", statuses(run(root, "--items", item, "--json").stdout).get(item), "undecidable")
        write(probe, keep)
        pj = os.path.join(vp, "package.json")
        write(pj, json.dumps(json.load(open(pj))) + "\n")
        write(os.path.join(vp, ".complete"), "sha256:" + sha(pj) + "\n")
        before = snapshot(root)
        r = run(root, "--items", item, "--apply", "--by", "a test", "--json")
        t.eq("decides: a package.json not written as the library writes it is a conflict, and left",
             (statuses(r.stdout).get(item), snapshot(root) == before), ("conflict", True))
        t.eq("decides: an item that is not there is undecidable",
             statuses(run(root, "--items", "00000000-0000-4000-8000-000000000000", "--json").stdout),
             {"00000000-0000-4000-8000-000000000000": "undecidable"})
    finally:
        shutil.rmtree(tmp)


def applies_again_undoes(t):
    tmp, root = tree()
    try:
        vp, item = shape(root)
        rel = os.path.relpath(vp, root)
        pristine = os.path.join(tmp, "pristine")
        shutil.copytree(root, pristine)
        before = snapshot(root)
        inits = {f: open(os.path.join(root, f), "rb").read() for f in before if f.startswith(rel) and f.endswith("init.mp4")}
        r = run(root, "--items", item, "--apply", "--by", "a test", "--json")
        t.eq("applies: the version is mended", statuses(r.stdout).get(item), "mended")
        after = snapshot(root)
        changed = sorted(f for f in after if before.get(f) != after[f])
        video = [x["dir"] + "/init.mp4" for x in json.load(open(os.path.join(vp, "package.json")))["renditions"]["video"]]
        t.eq("applies: only the video init segments, checksums.sha256, package.json and .complete change",
             changed, sorted([os.path.join(rel, f) for f in video]
                             + [os.path.join(rel, n) for n in ("checksums.sha256", "package.json", ".complete")]))
        listed = [line.rstrip("\n").split("  ", 1) for line in open(os.path.join(vp, "checksums.sha256"))]
        t.ok("applies: a plain sha256sum -c of the version holds", all(sha(os.path.join(vp, n)) == d for d, n in listed))
        old_lines = {n: d for d, n in (line.rstrip("\n").split("  ", 1) for line in open(os.path.join(pristine, rel, "checksums.sha256")))}
        t.eq("applies: checksums.sha256 changes in the mended files' lines alone",
             sorted(n for d, n in listed if old_lines.get(n) != d), sorted(video))
        sys.path.insert(0, TOOLS)
        import libv2_records
        t.eq("applies: the record logic's full chain check holds", libv2_records.chain_problems(vp, full=True), [])
        for f in video:
            old, new = inits[os.path.join(rel, f)], open(os.path.join(vp, f), "rb").read()
            diff = [i for i in range(len(old)) if old[i] != new[i]]
            at = old.index(b"elst") + 16
            t.ok(f"applies: {f} differs in its media_time field alone, now 0",
                 len(old) == len(new) and diff and min(diff) >= at and max(diff) < at + 4 and new[at:at + 4] == bytes(4), diff)
        pkg = json.load(open(os.path.join(vp, "package.json")))
        t.eq("applies: one entry per mended file, of the fields the format lists",
             [sorted(m) for m in pkg["mends"]], [sorted(["kind", "file", "box", "old", "new", "sha256Before", "sha256After", "at", "by"])] * len(video))
        t.eq("applies: the entries name the files, the old value and the hashes",
             [(m["file"], m["kind"], m["box"], m["old"], m["new"], m["sha256Before"], m["sha256After"], m["by"]) for m in pkg["mends"]],
             [(f, "avsync-editlist", "moov/trak/edts/elst", 1328, 0, "sha256:" + hashlib.sha256(inits[os.path.join(rel, f)]).hexdigest(),
               "sha256:" + sha(os.path.join(vp, f)), "a test") for f in video])
        t.eq(".complete names the new package.json", open(os.path.join(vp, ".complete")).read(), "sha256:" + sha(os.path.join(vp, "package.json")) + "\n")
        journal = os.path.join(root, ".work", "migration", "mend-avsync-editlist", "mends.jsonl")
        ops = [json.loads(x)["op"] for x in open(journal)]
        t.eq("applies: journaled before and after", ops, ["mend", "mended"])
        t.eq("applies: the bytes it replaced are not kept once it is recorded",
             glob.glob(os.path.join(root, ".work", "migration", "mend-avsync-editlist", "before", "**", "*.*"), recursive=True), [])
        t.eq("applies: the dry run now says mended", statuses(run(root, "--items", item, "--json").stdout).get(item), "mended")
        validates(t, "applies: the mended tree validates with --check-checksums", root)
        whole = snapshot(root, outside_work=False)
        run(root, "--items", item, "--apply", "--by", "a test", "--json")
        t.eq("again: a second --apply changes no byte, its journal included", snapshot(root, outside_work=False), whole)
        r = run(root, "--items", item, "--undo", "--by", "a test", "--json")
        t.eq("undoes: the version is put back", statuses(r.stdout).get(item), "undone")
        t.eq("undoes: byte for byte as it was before it was mended", snapshot(root), before)
        d = subprocess.run(["diff", "-r", "-x", ".work", pristine, root], capture_output=True, text=True)
        t.ok("undoes: diff -r against the pristine copy finds nothing", d.returncode == 0 and not d.stdout, d.stdout)
        validates(t, "undoes: the tree validates", root)
        r = run(root, "--items", item, "--undo", "--by", "a test", "--json")
        t.eq("undoes: a second --undo changes nothing", (statuses(r.stdout).get(item), snapshot(root) == before), ("unchanged", True))
    finally:
        shutil.rmtree(tmp)


KILL_AFTER_FIRST = """
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location("mend", sys.argv[1])
mend = importlib.util.module_from_spec(spec); spec.loader.exec_module(mend)
real, n = mend.replace, [0]
def replace(path, data):
    real(path, data)
    if "/versions/" in path and "/.work/" not in path:
        n[0] += 1
        if n[0] == 1:
            os._exit(9)
mend.replace = replace
sys.argv = sys.argv[1:]
mend.main()
"""


def resumes(t):
    tmp, root = tree()
    try:
        vp, item = shape(root)
        before = snapshot(root)
        killed = subprocess.run([sys.executable, "-c", KILL_AFTER_FIRST, TOOL, root, "--items", item, "--apply", "--by", "a test"],
                                capture_output=True, text=True)
        t.eq("resumes: the run was killed after the first file", killed.returncode, 9)
        os.remove(os.path.join(root, ".work", "migration", "mend-avsync-editlist", "mends.lock"))
        r = run(root, "--items", item, "--json")
        t.eq("resumes: a dry run says the version was left mid-way", statuses(r.stdout).get(item), "interrupted")
        r = run(root, "--items", item, "--apply", "--by", "a test")
        t.ok("resumes: --apply finishes it", "finished the mend an earlier run left" in r.stdout, r.stdout + r.stderr)
        entries = json.load(open(os.path.join(vp, "package.json")))["mends"]
        journaled = [json.loads(x) for x in open(os.path.join(root, ".work", "migration", "mend-avsync-editlist", "mends.jsonl"))]
        t.eq("resumes: to the entries the killed run journaled", entries, journaled[0]["mends"])
        validates(t, "resumes: the tree validates", root)
        killed = subprocess.run([sys.executable, "-c", KILL_AFTER_FIRST, TOOL, root, "--items", item, "--undo", "--by", "a test"],
                                capture_output=True, text=True)
        os.remove(os.path.join(root, ".work", "migration", "mend-avsync-editlist", "mends.lock"))
        r = run(root, "--items", item, "--undo", "--by", "a test")
        t.ok("resumes: an undo killed mid-way is finished by the next --undo",
             killed.returncode == 9 and "finished the undo an earlier run left" in r.stdout and snapshot(root) == before, r.stdout + r.stderr)
    finally:
        shutil.rmtree(tmp)


def main():
    t = Tally()
    decides(t)
    applies_again_undoes(t)
    resumes(t)
    print(f"{t.passed}/{t.passed + t.failed} checks pass")
    sys.exit(1 if t.failed else 0)


if __name__ == "__main__":
    main()
