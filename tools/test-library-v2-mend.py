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
          v_elst=1328, v_tfdt=0, v_cto=1328, a_elst=None, a_tfdt=1920, per=None, probe_sha=True):
    """The version the_version names, its renditions given made-up boxes and its source a probe with
    these starts; every record and checksum written again so the tree is a valid one. The defaults
    are a package whose picture is 82.3 ms early: video 83 ms after the file in the source, its
    package's video edit list cutting that, the stereo rendition starting where its source does.
    per: a dict of v_elst, v_tfdt, v_cto, init (the init's bytes) for each video rendition, by index,
    that differs from the rest. probe_sha False: the source record keeps no hash of its probe."""
    vp, sid = the_version(root)
    item = os.path.dirname(os.path.dirname(vp))
    pkg = json.load(open(os.path.join(vp, "package.json")))
    for i, r in enumerate(pkg["renditions"]["video"]):
        d = os.path.join(vp, r["dir"])
        o = {"v_elst": v_elst, "v_tfdt": v_tfdt, "v_cto": v_cto, **((per or {}).get(i) or {})}
        write(os.path.join(d, "init.mp4"), o.get("init") or init_segment(16000, o["v_elst"]))
        write(os.path.join(d, "seg-00001.m4s"), media_segment(o["v_tfdt"], o["v_cto"]))
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
    src["probe"]["sha256"] = "sha256:" + sha(os.path.join(sd, "ffprobe.json")) if probe_sha else None
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
        r = run(root, "--items", "00000000-0000-4000-8000-000000000000", "--json")
        t.eq("decides: an item that is not there is undecidable, and the run exits 1",
             (statuses(r.stdout), r.returncode), ({"00000000-0000-4000-8000-000000000000": "undecidable"}, 1))
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


STOPPED = """
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location("mend", sys.argv[1])
mend = importlib.util.module_from_spec(spec); spec.loader.exec_module(mend)
at, how = int(os.environ["STOP_AT"]), os.environ.get("STOP_HOW", "kill")
real, real_rename, n, m = mend.replace, os.replace, [0], [0]
def inside(path):
    return "/versions/" in path and "/.work/" not in path
def replace(path, data):
    real(path, data)
    if inside(path):
        n[0] += 1
        if n[0] == at and how == "kill":
            os._exit(9)
def rename(src, dst):
    if src.endswith(".mending") and inside(dst):
        m[0] += 1
        if m[0] == at and how == "refused":
            raise PermissionError(13, "Permission denied")
        if m[0] == at and how == "torn":
            os._exit(9)  # its temporary file written, not renamed
    real_rename(src, dst)
mend.replace, os.replace = replace, rename
sys.argv = sys.argv[1:]
mend.main()
"""


def stopped(root, item, op, at, how="kill", run_name=None):
    """A run stopped after its at-th file of the version (kill), or refused the at-th rename (refused)."""
    return subprocess.run([sys.executable, "-c", STOPPED, TOOL, root, "--items", item, f"--{op}", "--by", "a test"]
                          + (["--run", run_name] if run_name else []),
                          capture_output=True, text=True, env=dict(os.environ, STOP_AT=str(at), STOP_HOW=how))


def journal_of(root, run_name="mend-avsync-editlist"):
    return [json.loads(x) for x in open(os.path.join(root, ".work", "migration", run_name, "mends.jsonl"))]


def chain_broken(vp):
    sys.path.insert(0, TOOLS)
    import libv2_records
    return bool(libv2_records.chain_problems(vp, full=True))


def resumes(t):
    tmp, root = tree()
    try:
        vp, item = shape(root)
        before = snapshot(root)
        killed = stopped(root, item, "apply", 1)
        t.eq("resumes: the run was killed after the first file", killed.returncode, 9)
        r = run(root, "--items", item, "--json")
        t.eq("resumes: a dry run says the version was left mid-way, and exits 1",
             (statuses(r.stdout).get(item), r.returncode), ("interrupted", 1))
        r = run(root, "--items", item, "--apply", "--by", "a test")
        t.ok("resumes: --apply finishes it; the killed run left no lock", "finished the mend an earlier run left" in r.stdout, r.stdout + r.stderr)
        entries = json.load(open(os.path.join(vp, "package.json")))["mends"]
        t.eq("resumes: to the entries the killed run journaled", entries, journal_of(root)[0]["mends"])
        validates(t, "resumes: the tree validates", root)
        killed = stopped(root, item, "undo", 1)
        r = run(root, "--items", item, "--undo", "--by", "a test")
        t.ok("resumes: an undo killed mid-way is finished by the next --undo",
             killed.returncode == 9 and "finished the undo an earlier run left" in r.stdout and snapshot(root) == before, r.stdout + r.stderr)
    finally:
        shutil.rmtree(tmp)


def stops(t):
    """Stopped after each of its files, a mend or an undo leaves a version no reader takes for a
    whole one until its last file, .complete, is written; the next run of either finishes it."""
    names = {1: "checksums.sha256", 2: "the first init", 3: "the second init", 4: "package.json", 5: ".complete"}
    base_tmp, base = tree()
    try:
        vp0, item = shape(base)
        pristine = snapshot(base)
        mended_tmp = tempfile.mkdtemp(prefix="mend-")
        mended = os.path.join(mended_tmp, "library")
        shutil.copytree(base, mended)
        run(mended, "--items", item, "--apply", "--by", "a test")
        for op, src in (("apply", base), ("undo", mended)):
            for at in range(1, 6):
                tmp = tempfile.mkdtemp(prefix="mend-")
                try:
                    root = os.path.join(tmp, "library")
                    shutil.copytree(src, root)
                    vp = os.path.join(root, os.path.relpath(vp0, base))
                    stopped(root, item, op, at)
                    t.eq(f"stops: an {op} killed after {names[at]} leaves a version whose chain {'holds' if at == 5 else 'fails'}",
                         chain_broken(vp), at != 5)
                    r = run(root, "--items", item, f"--{op}", "--by", "a test")
                    t.ok(f"stops: the next --{op} finishes it", "finished the" in r.stdout and not chain_broken(vp)
                         and (op == "apply" or snapshot(root) == pristine), r.stdout + r.stderr)
                finally:
                    shutil.rmtree(tmp)
        # a rename refused (no space, no permission): its temporary file goes, the version is left mid-way
        tmp = tempfile.mkdtemp(prefix="mend-")
        try:
            root = os.path.join(tmp, "library")
            shutil.copytree(base, root)
            r = stopped(root, item, "apply", 2, how="refused")
            left = glob.glob(os.path.join(root, "**", "*.mending"), recursive=True)
            t.eq("stops: a write refused takes its temporary file away, and the run exits 1 with the version interrupted",
                 (left, r.returncode, statuses(run(root, "--items", item, "--json").stdout).get(item)), ([], 1, "interrupted"))
            r = run(root, "--items", item, "--apply", "--by", "a test")
            validates(t, "stops: the next --apply finishes it, and the tree validates", root)
        finally:
            shutil.rmtree(tmp)
        shutil.rmtree(mended_tmp)
    finally:
        shutil.rmtree(base_tmp)


def one_at_a_time(t):
    """One run at a time on a library, whatever its name; a version another run left mid-way is
    refused, and only the items a run is given are finished."""
    import fcntl
    tmp, root = tree()
    try:
        vp, item = shape(root)
        before = snapshot(root)
        os.makedirs(os.path.join(root, ".work", "migration"), exist_ok=True)
        with open(os.path.join(root, ".work", "migration", "mends.lock"), "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            r = run(root, "--items", item, "--apply", "--by", "a test", "--run", "another")
            fcntl.flock(held, fcntl.LOCK_UN)
        t.ok("one at a time: a run is refused while another holds the library's lock, and changes nothing",
             r.returncode != 0 and "mend lock" in r.stderr and snapshot(root) == before, r.stdout + r.stderr)
        stopped(root, item, "apply", 2, run_name="mend-a")
        r = run(root, "--items", item, "--apply", "--by", "a test", "--json")
        got = [json.loads(x) for x in r.stdout.splitlines() if json.loads(x).get("itemId") == item][-1]
        t.ok("one at a time: a version another run left mid-way is refused, naming that run, exit 1",
             got["status"] == "interrupted" and "mend-a" in got["reason"] and r.returncode == 1, r.stdout)
        other = os.path.basename(glob.glob(os.path.join(root, "series", "*", "*", "episodes", "*"))[0])
        r = run(root, "--items", other, "--apply", "--by", "a test", "--run", "mend-a")
        t.ok("one at a time: a run given other items does not finish it", "finished" not in r.stdout and chain_broken(vp),
             r.stdout)
        r = run(root, "--items", item, "--apply", "--by", "a test", "--run", "mend-a")
        t.ok("one at a time: its own run, given the item, does", "finished the mend" in r.stdout and not chain_broken(vp), r.stdout)
    finally:
        shutil.rmtree(tmp)


def ladders(t):
    """Every video rendition is held to the rule: the version is affected when any is, only those
    are mended, and one shifted for another reason is reported and left."""
    cases = {
        "the top rung in sync, the second affected": ({0: {"v_elst": 0}}, "affected", ["hls/v1/init.mp4"], []),
        "the top rung affected, the second shifted for another reason":
            ({1: {"v_elst": 0, "v_tfdt": 10672}}, "affected", ["hls/v0/init.mp4"], ["hls/v1"]),
        "the top rung in sync, the second shifted for another reason":
            ({0: {"v_elst": 0}, 1: {"v_elst": 0, "v_tfdt": 10672}}, "shifted-other", [], ["hls/v1"]),
    }
    for name, (per, want, files, left) in cases.items():
        tmp, root = tree()
        try:
            vp, item = shape(root, per=per)
            r = run(root, "--items", item, "--json")
            d = [json.loads(x) for x in r.stdout.splitlines() if json.loads(x).get("itemId") == item][-1]
            t.eq(f"ladders: {name}", (d["status"], [x["file"] for x in d["renditions"]], [x["dir"] for x in d["renditionsLeft"]]),
                 (want, files, left))
            if want == "affected":
                init = {f: sha(os.path.join(vp, f)) for f in ("hls/v0/init.mp4", "hls/v1/init.mp4")}
                run(root, "--items", item, "--apply", "--by", "a test")
                t.eq(f"ladders: {name}: only that rung's init changes",
                     sorted(f for f in init if sha(os.path.join(vp, f)) != init[f]), files)
        finally:
            shutil.rmtree(tmp)


def refuses(t):
    """Input it cannot hold to the rule fails that version alone: undecidable, the run goes on."""
    good = init_segment(16000, 1328)
    many = good[:good.index(b"elst") + 8] + struct.pack(">I", 1000) + good[good.index(b"elst") + 12:]
    cases = {
        "an edit list whose count runs past its box": dict(per={0: {"init": many}}),
        "a video init whose handler is not vide": dict(per={0: {"init": init_segment(16000, 1328, b"soun")}}),
        "a video init with a box after its moov": dict(per={0: {"init": good + box(b"uuid", bytes(24))}}),
        "a video init over 4 MiB": dict(per={0: {"init": good + box(b"free", bytes(4 << 20))}}),
        "a probe its source record keeps no hash of": dict(probe_sha=False),
    }
    for name, kw in cases.items():
        tmp, root = tree()
        try:
            vp, item = shape(root, **kw)
            other = os.path.basename(glob.glob(os.path.join(root, "series", "*", "*", "episodes", "*"))[0])
            before = snapshot(root)
            r = run(root, "--items", f"{item},{other}", "--apply", "--by", "a test", "--json")
            got = statuses(r.stdout)
            t.ok(f"refuses: {name} is undecidable, nothing changes, the run goes on",
                 got.get(item) == "undecidable" and other in got and snapshot(root) == before, r.stdout + r.stderr)
        finally:
            shutil.rmtree(tmp)
    # the stereo rendition made from a stream the probe does not have
    tmp, root = tree()
    try:
        vp, item = shape(root)
        pj = os.path.join(vp, "package.json")
        pkg = json.load(open(pj))
        pkg["renditions"]["audio"][0]["sourceStreamIndex"] = 7
        write(pj, json_bytes(pkg))
        write(os.path.join(vp, ".complete"), "sha256:" + sha(pj) + "\n")
        t.eq("refuses: a stereo rendition made from a stream its probe does not have is undecidable",
             statuses(run(root, "--items", item, "--json").stdout).get(item), "undecidable")
    finally:
        shutil.rmtree(tmp)


def forged(t):
    """An entry whose hashes are the file's history but whose bytes are not: the init changed
    somewhere its entry does not name. --undo refuses it, and so does --apply of another rung."""
    tmp, root = tree()
    try:
        vp, item = shape(root)
        run(root, "--items", item, "--apply", "--by", "a test")
        p = os.path.join(vp, "hls/v0/init.mp4")
        data = bytearray(open(p, "rb").read())
        data[data.index(b"tkhd") + 20] ^= 0xFF
        write(p, bytes(data))
        pkg = json.load(open(os.path.join(vp, "package.json")))
        pkg["mends"][0]["sha256After"] = "sha256:" + sha(p)
        sums = open(os.path.join(vp, "checksums.sha256")).read().replace(
            [line.split("  ")[0] for line in open(os.path.join(vp, "checksums.sha256")) if line.rstrip().endswith("hls/v0/init.mp4")][0], sha(p))
        write(os.path.join(vp, "checksums.sha256"), sums)
        pkg["checksums"]["sha256"] = "sha256:" + sha(os.path.join(vp, "checksums.sha256"))
        write(os.path.join(vp, "package.json"), json_bytes(pkg))
        write(os.path.join(vp, ".complete"), "sha256:" + sha(os.path.join(vp, "package.json")) + "\n")
        before = snapshot(root)
        r = run(root, "--items", item, "--undo", "--by", "a test", "--json")
        t.ok("forged: --undo refuses an entry that does not lead back to the bytes it names, exit 1",
             statuses(r.stdout).get(item) == "conflict" and r.returncode == 1 and snapshot(root) == before, r.stdout + r.stderr)
    finally:
        shutil.rmtree(tmp)


def claims_and_strays(t):
    """A version another run claimed is not written; a run that died with its temporary file beside
    a file leaves it named by a dry run, and the next run of its name takes it away; a CSV row that
    names this item's folder for another item finishes nothing of it."""
    tmp, root = tree()
    try:
        vp, item = shape(root)
        before = snapshot(root)
        vid = os.path.basename(vp)
        claims = os.path.join(root, ".work", "migration", "claims")
        os.makedirs(claims)
        write(os.path.join(claims, vid + ".claim"), json.dumps({"run": "elsewhere", "host": "another-host", "pid": 1,
                                                                 "at": "2026-11-17T00:00:00.000000Z"}) + "\n")
        r = run(root, "--items", item, "--apply", "--by", "a test", "--json")
        got = [json.loads(x) for x in r.stdout.splitlines() if json.loads(x).get("itemId") == item][-1]
        t.ok("claims: a version another run claimed is not written, the claim named, exit 1",
             got["status"] == "conflict" and "elsewhere" in got["reason"] and "another-host" in got["reason"]
             and r.returncode == 1 and snapshot(root) == before, r.stdout + r.stderr)
        os.remove(os.path.join(claims, vid + ".claim"))
        killed = stopped(root, item, "apply", 2, how="torn")
        left = [p for p in glob.glob(os.path.join(vp, "**", "*.mending"), recursive=True)]
        r = run(root, "--items", item, "--json")
        got = [json.loads(x) for x in r.stdout.splitlines() if json.loads(x).get("itemId") == item][-1]
        t.ok("strays: a run killed between writing a file and renaming it leaves its temporary file, which a dry run names",
             killed.returncode == 9 and len(left) == 1 and got["status"] == "interrupted"
             and os.path.relpath(left[0], vp) in got["reason"], got["reason"])
        count = os.path.join(tmp, "rows.csv")
        write(count, "itemId,itemDir\n00000000-0000-4000-8000-0000000000ff," + os.path.relpath(os.path.dirname(os.path.dirname(vp)), root) + "\n")
        r = run(root, "--csv", count, "--apply", "--by", "a test")
        t.ok("claims: a CSV row naming this item's folder for another item finishes nothing of it, exit 1",
             "finished" not in r.stdout and chain_broken(vp) and r.returncode == 1, r.stdout)
        r = run(root, "--items", item, "--apply", "--by", "a test")
        t.ok("strays: the next run of its name takes the temporary file away and finishes the mend; no claim is left",
             "finished the mend" in r.stdout and not glob.glob(os.path.join(vp, "**", "*.mending"), recursive=True)
             and not chain_broken(vp) and not os.listdir(claims), r.stdout + r.stderr)
    finally:
        shutil.rmtree(tmp)


NOLOCK = """
import errno, fcntl, runpy, sys
def flock(fd, op):
    raise OSError(errno.ENOLCK, "No locks available")
fcntl.flock = flock
sys.argv = sys.argv[1:]
runpy.run_path(sys.argv[0], run_name="__main__")
"""


def journals_and_shares(t):
    """The journal is ASCII, and a line cut in the middle of a character is skipped with a warning; a
    file system that gives no lock, and a library that cannot be written, are said, not crashed on."""
    tmp, root = tree()
    try:
        vp, item = shape(root)
        r = run(root, "--items", item, "--apply", "--by", "Zoë Müller")
        journal = os.path.join(root, ".work", "migration", "mend-avsync-editlist", "mends.jsonl")
        data = open(journal, "rb").read()
        t.ok("journals: written ASCII, the name given kept", data.isascii() and "Zoë Müller" in [json.loads(x).get("by") for x in data.splitlines()],
             data[:200])
        with open(journal, "ab") as f:
            f.write(b'{"at": "2026-11-17T00:00:00Z", "op": "mend", "by": "Zo\xc3')
        r = run(root, "--items", item, "--json")
        t.ok("journals: a line cut in the middle of a character is skipped with a warning, and the run goes on",
             r.returncode == 0 and "Traceback" not in r.stderr and "cannot be read" in r.stderr
             and statuses(r.stdout).get(item) == "mended", r.stdout + r.stderr)
        r = run(root, "--items", item, "--undo", "--by", "a test")
        t.ok("journals: and an undo after it writes on a line of its own", r.returncode == 0 and
             [json.loads(x)["op"] for x in open(journal, "rb").read().splitlines()[-2:]] == ["undo", "undone"], r.stdout + r.stderr)
    finally:
        shutil.rmtree(tmp)
    tmp, root = tree()
    try:
        vp, item = shape(root)
        before = snapshot(root, outside_work=False)
        r = subprocess.run([sys.executable, "-c", NOLOCK, TOOL, root, "--items", item, "--apply", "--by", "a test"],
                           capture_output=True, text=True)
        t.ok("shares: a file system that gives no lock is said, nothing written",
             r.returncode != 0 and "Traceback" not in r.stderr and "gives no lock" in r.stderr, r.stderr)
        snap = snapshot(root, outside_work=False)
        t.eq("shares: nothing of the library changes but the lock file", sorted(k for k in snap if before.get(k) != snap[k]),
             [os.path.join(".work", "migration", "mends.lock")])
        if os.geteuid() != 0:
            subprocess.run(["chmod", "-R", "a-w", root], check=True)
            try:
                r = run(root, "--items", item, "--apply", "--by", "a test")
            finally:
                subprocess.run(["chmod", "-R", "u+w", root], check=True)
            t.ok("shares: a library that cannot be written is said, not crashed on",
                 r.returncode != 0 and "Traceback" not in r.stderr and "must be writable" in r.stderr, r.stderr)
    finally:
        shutil.rmtree(tmp)


def moments(t):
    """Entries are ordered by when they were made, not by their text."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("mend", TOOL)
    mend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mend)
    t.ok("moments: a moment with a fraction comes after the same second without",
         mend.moment("2026-11-17T10:00:00Z") < mend.moment("2026-11-17T10:00:00.500Z"))
    t.ok("moments: one in another zone is ordered by when it was",
         mend.moment("2026-11-17T10:30:00+01:00") < mend.moment("2026-11-17T10:00:00Z"))
    t.eq("moments: one with no zone is none", mend.moment("2026-11-17T10:00:00"), None)


def main():
    t = Tally()
    decides(t)
    applies_again_undoes(t)
    resumes(t)
    stops(t)
    one_at_a_time(t)
    ladders(t)
    refuses(t)
    forged(t)
    claims_and_strays(t)
    journals_and_shares(t)
    moments(t)
    print(f"{t.passed}/{t.passed + t.failed} checks pass")
    sys.exit(1 if t.failed else 0)


if __name__ == "__main__":
    main()
