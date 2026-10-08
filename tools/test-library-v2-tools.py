#!/usr/bin/env python3
"""Prove the v2 library tools do what they say.

Run it with an interpreter that can import jsonschema — the cases that run validate-library-v2.py
need it, and say so when it is missing. The tools themselves use nothing but the standard library,
and these cases run them the way a pod does: as scripts, with arguments.

  python tools/test-library-v2-tools.py [section words]   # words: run only the sections they name

What it proves, and how each rule is shown to bite — every case makes the change that breaks the
rule and checks the tool notices:

  round trip     rebuilding library/v2/examples gives the rows the example set states: four items,
                 the movie's two versions, the episode whose package was superseded, and the series
                 with nothing to play — and the bonus material beside them as rows of its own, in the
                 order a viewer sees it, with what never finished left out; an episode another's file
                 covers is linked to the holder whose source covers it, whatever its projection says,
                 and plays nothing of its own
  events         each kind changes the rebuilt rows as the README says, and removing the event
                 changes them back: a superseded package reappears, a removed version reappears, a
                 deleted original becomes a playback asset again, a removed extra is a row again, and
                 a note changes nothing
  compare        an item only on storage is an orphan when the deletion log explains it and lost
                 when it does not, an item only in the database is a missing record, and only the
                 last two fail; without a log nothing on storage can be called an orphan; a person
                 only on storage is an orphan when the log names them as a person and nothing in
                 their folder is newer — an untyped entry names an item — and otherwise takes the
                 class of the items that credit them; a projection whose row was modified since is
                 stale and one that reflects a later state is ahead, and both fail, while one that
                 does not say which state it reflects fails nothing; a covered episode's link is
                 compared where the export carries one
  people         a credited person gets a record that holds what the credit knows; a people list in
                 the export fills every field it carries; --people-only touches no item record; a
                 projection that drops a portrait removes it
  people in full every field of the export's people list crosses into person.json and back into a
                 row, --compare catches a change to each one, and a person who died and got a new
                 portrait after being projected is stale until projected again; an entry of only an
                 id and a name is a valid record, and what a record cannot hold stays out of it
  projections    --projections-only writes, from the export the tree was built from, the projections
                 the build wrote, byte for byte, and changes no byte of any record nor touches one —
                 every record hashed before and after; a stale projection is gone after one run, an
                 image it drops is left for the sweep, what the tree does not hold is not created, and
                 a person a stale title still credits is swept once the title is projected again
  upgrade        a tree in the layout before 2026-10-02 (b) upgrades, piped into the pod's Python,
                 to one that validates, with every record the same record and no media read; a dry
                 run changes nothing, a stopped run is finished by the next, a second run does
                 nothing, and whatever contradicts its records is refused and left as it was
  proves itself  sha256sum -c passes in every write-once folder, and each version and packaged
                 extra is one chain
  neutral names  a tree written before 2026-10-08 is given the names the library gives its files: a
                 dry run lists every change and makes none, --apply — piped into the pod's Python —
                 leaves byte for byte the tree the record logic writes now, a copy that is no subtitle
                 kept in the run's folder, journaled; a second run does nothing, a stopped one is
                 finished by the next, two never act at once, and whatever contradicts its records is
                 refused and left as it was
  sweep          a dry run finds every kind of garbage and only it, --apply removes exactly that
                 through a quarantine it checks again — putting back what is referenced by then — and
                 finishes one an interrupted run left; whatever is referenced, younger than the
                 grace, unclassifiable, or a person the log does not name as one is left alone, with
                 the reason; a deleted person goes once no item record on storage credits them, in
                 the same sweep as a deleted item that does; of an extra, only a package that never
                 finished is ever garbage
  v1 -> v2       the v1 example tree converts, and given the names the library gives its files by
                 library-v2-neutral-names.py the result passes validate-library-v2.py and the media
                 check, the texts and the packages survive, and a second run does nothing
  catalog -> v2  an export, a package store and source files become a tree that validates; the
                 rebuild of that tree agrees with the export it came from; two runs write the same
                 bytes; an item whose original is missing keeps its texts and loses its versions; a
                 trailer the catalog downloaded becomes an extra of its movie or series, never an
                 episode's, and its link stays a link
  credits        every field of an export's credits crosses into metadata.json — the job in the
                 source's own words, the character, the billing order, a series' episode count — in
                 the order a reader shows them, whatever order the export lists them in; an export
                 from before credits were general still works, and a role, an order or a count a
                 record cannot hold is left out with a note; the rebuild gives every field back, and
                 --compare matches a credit by its person and role and catches a change to each
                 field, an export from before makes no tree differ, and a tree from before reads as
                 the database knowing more until it is projected again
  media check    every check it makes fails on a tree that breaks it and passes on one that does not
  the pieces     the JPEG and PNG header parsing, the qh1 fingerprint and the generated ids
  records        the record module the packager vendors writes, from the probes and manifests of
                 testdata/libv2_records, the source, version, package and extra records expected/
                 holds, byte for byte; each chain holds, and the check of one bites; it names an
                 original, its parts and the copy of a subtitle file as the library does, its probe
                 is the tool's output with two edits, and nothing it writes names a file as it
                 arrived; a source of a file of several episodes covers them as given, the holder
                 first; piped behind a tool, the module has none of its names redefined by it
"""
import base64, datetime, glob, hashlib, importlib.util, json, os, re, shutil, subprocess, sys, tempfile, time, uuid

TOOLS = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(TOOLS, "..", "library", "v2", "examples")
V1_EXAMPLES = os.path.join(TOOLS, "..", "library", "v1", "examples")
FROM_CATALOG = os.path.join(TOOLS, "library-v2-from-catalog.py")
FROM_V1 = os.path.join(TOOLS, "library-v2-from-v1.py")
REBUILD = os.path.join(TOOLS, "library-v2-rebuild.py")
MEDIA_CHECK = os.path.join(TOOLS, "library-v2-media-check.py")
UPGRADE = os.path.join(TOOLS, "library-v2-upgrade.py")
SWEEP = os.path.join(TOOLS, "library-v2-sweep.py")
VALIDATOR = os.path.join(TOOLS, "validate-library-v2.py")
RECORDS = os.path.join(TOOLS, "libv2_records.py")
NEUTRAL = os.path.join(TOOLS, "library-v2-neutral-names.py")
# The tools that import the record module the packager vendors, and so are piped behind it.
USES_RECORDS = (FROM_CATALOG, NEUTRAL)


class Tally:
    def __init__(self):
        self.passed = self.failed = self.skipped = 0

    def ok(self, name, condition, detail=""):
        if condition:
            self.passed += 1
            print(f"pass  {name}")
        else:
            self.failed += 1
            print(f"FAIL  {name}")
            if detail:
                print("        " + "\n        ".join(str(detail).strip().splitlines()[-8:]))

    def eq(self, name, got, want):
        self.ok(name, got == want, f"got {got!r}\nwant {want!r}")

    def skip(self, name, why):
        self.skipped += 1
        print(f"skip  {name}: {why}")


def load_tool(path):
    """The tools are scripts; the pieces inside them are still importable by path."""
    spec = importlib.util.spec_from_file_location("t_" + os.path.basename(path).replace("-", "_")[:-3], path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run(*args):
    r = subprocess.run([sys.executable, *[str(a) for a in args]], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def run_piped(tool, *args):
    """A tool the way a pod runs it: python3 - <args> < tool.py, with no file of its own to find — and a
    tool that uses the record module piped behind it, cat libv2_records.py tool.py | python3 - <args>,
    from a folder that holds no copy of the module to import."""
    body = b"".join(open(p, "rb").read() for p in ([RECORDS] if tool in USES_RECORDS else []) + [tool])
    with tempfile.TemporaryDirectory() as nowhere:
        r = subprocess.run([sys.executable, "-", *[str(a) for a in args]], input=body, capture_output=True,
                           cwd=nowhere)
    return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")


def jload(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def jwrite(p, doc):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)


def tree_files(root):
    """Every file under root with its bytes, for comparing two runs."""
    out = {}
    for base, dirs, files in os.walk(root):
        dirs.sort()
        for f in sorted(files):
            p = os.path.join(base, f)
            out[os.path.relpath(p, root)] = open(p, "rb").read()
    return out


def digest(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def write_sums(folder, names):
    """folder/checksums.sha256 over exactly names, as a writer would write it."""
    with open(os.path.join(folder, "checksums.sha256"), "w") as f:
        f.write("".join(f"{digest(os.path.join(folder, n))}  {n}\n" for n in sorted(names)))


def listing(folder):
    return [line.split("  ", 1)[1].rstrip("\n") for line in open(os.path.join(folder, "checksums.sha256"))]


def relist(vp, names):
    """A version's checksums over exactly names, with package.json's record of them and .complete
    closed over them again."""
    write_sums(vp, names)
    p = os.path.join(vp, "package.json")
    doc = jload(p)
    doc["checksums"].update(sha256="sha256:" + digest(os.path.join(vp, "checksums.sha256")), files=len(names),
                            bytes=sum(os.path.getsize(os.path.join(vp, n)) for n in names))
    jwrite(p, doc)
    close(vp)


def close(vp):
    with open(os.path.join(vp, ".complete"), "w") as f:
        f.write("sha256:" + digest(os.path.join(vp, "package.json")) + "\n")


def write_event(item_dir, doc):
    """events/<stamp>-<eventId8>-<kind>/: the event and the checksums written with it."""
    stamp = doc["at"].replace("-", "").replace(":", "")
    folder = os.path.join(item_dir, "events", f"{stamp}-{doc['eventId'][:8]}-{doc['kind']}")
    jwrite(os.path.join(folder, "event.json"), doc)
    with open(os.path.join(folder, "checksums.sha256"), "w") as f:
        f.write(hashlib.sha256(open(os.path.join(folder, "event.json"), "rb").read()).hexdigest() + "  event.json\n")
    return folder


def stamps(root):
    """Every file under root with its size and modification time: a file written again, even with
    the same bytes, has a new time."""
    out = {}
    for base, dirs, files in os.walk(root):
        for f in files:
            st = os.stat(os.path.join(base, f))
            out[os.path.relpath(os.path.join(base, f), root)] = (st.st_size, st.st_mtime_ns)
    return out


def rows_of(root, *extra):
    """Rebuild a tree and return its rows, keyed by item id."""
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "rows.json")
        code, text = run(REBUILD, root, "--out", out, "--text-language", "en", *extra)
        assert code == 0, text
        doc = jload(out)
    return {r["id"]: r for r in doc["items"]}, doc


def assets(rows, kind=None):
    return sorted((iid, a["kind"], a.get("durationMs")) for iid, r in rows.items()
                  for a in r["playbackAssets"] if kind is None or a["kind"] == kind)


def have_jsonschema():
    return importlib.util.find_spec("jsonschema") is not None


def extra_of(root, kind):
    """The folder of the example extra of this kind."""
    return next(os.path.dirname(p) for p in sorted(glob.glob(os.path.join(root, "*", "*", "*", "extras", "*", "extra.json")))
                if jload(p).get("kind") == kind)


# ---------------------------------------------------------------- the pieces
def test_pieces(t):
    cat, rec = load_tool(FROM_CATALOG), load_tool(RECORDS)

    # the catalog's export keeps every key of externalIds and writes null for an id it does not
    # know; person.json leaves the key out, so the compare must not read the gap as a difference
    reb = load_tool(REBUILD)
    t.eq("an export's null reference id is no id at all, as person.json keeps it",
         reb.person_ids({"tmdbPerson": "102", "imdb": None}), {"tmdbPerson": "102"})
    t.eq("and an empty one neither", reb.person_ids({"tmdbPerson": "102", "imdb": ""}), {"tmdbPerson": "102"})

    # a JPEG that a naive scanner gets wrong: an APP0 header and a comment segment sit before the
    # SOF0 that actually carries the size, and the size is width != height so a swap shows up.
    jpeg = (b"\xff\xd8" + b"\xff\xe0" + (16).to_bytes(2, "big") + b"JFIF\x00" + b"\x00" * 9 +
            b"\xff\xfe" + (6).to_bytes(2, "big") + b"note" +
            b"\xff\xc0" + (17).to_bytes(2, "big") + b"\x08" + (480).to_bytes(2, "big") +
            (640).to_bytes(2, "big") + b"\x03" + b"\x00" * 9 + b"\xff\xd9")
    t.eq("a JPEG header gives its type and size, past the segments before it",
         cat.sniff_image(jpeg), ("image/jpeg", 640, 480))

    png = (b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" +
           (1280).to_bytes(4, "big") + (720).to_bytes(4, "big") + b"\x08\x06\x00\x00\x00" + b"\x00" * 4)
    t.eq("a PNG header gives its type and size", cat.sniff_image(png), ("image/png", 1280, 720))
    t.eq("bytes that are not an image are not one", cat.sniff_image(b"not an image at all"), (None, None, None))
    t.eq("a truncated JPEG is a JPEG of unknown size", cat.sniff_image(b"\xff\xd8\xff\xe0\x00\x10JFIF"),
         ("image/jpeg", None, None))
    t.eq("the same parsing in the v1 converter", load_tool(FROM_V1).sniff_image(jpeg), ("image/jpeg", 640, 480))
    t.eq("and in the media check's extension table", sorted(load_tool(MEDIA_CHECK).EXT_TYPE.values()),
         ["image/jpeg", "image/png", "image/webp"])

    with tempfile.TemporaryDirectory() as tmp:
        small = os.path.join(tmp, "small")
        with open(small, "wb") as f:
            f.write(b"a" * 1000)
        big = os.path.join(tmp, "big")
        body = bytes((i * 7 + 3) % 251 for i in range(200000))
        with open(big, "wb") as f:
            f.write(body)
        t.eq("qh1 of a file smaller than the window is its bytes and its size", rec.qh1(small),
             "sha256:" + hashlib.sha256(b"a" * 1000 + (1000).to_bytes(8, "big")).hexdigest())
        t.eq("qh1 of a large file is its first and last 64 KiB and its size", rec.qh1(big),
             "sha256:" + hashlib.sha256(body[:65536] + body[-65536:] +
                                        len(body).to_bytes(8, "big")).hexdigest())
        # the rule bites: a file with the same ends and a different size is a different fingerprint
        longer = os.path.join(tmp, "longer")
        with open(longer, "wb") as f:
            f.write(body[:65536] + b"\x00" * 10 + body[65536:])
        t.ok("qh1 tells two files with the same ends and different sizes apart",
             rec.qh1(big) != rec.qh1(longer))
        t.eq("the media check computes the same fingerprint", load_tool(MEDIA_CHECK).qh1(big), rec.qh1(big))

    t.eq("a generated id is a v5 UUID of the item and a stable name",
         rec.did("item", "version", "x"),
         str(uuid.uuid5(uuid.uuid5(uuid.NAMESPACE_URL, "https://zaentrum.github.io/schemas/library"),
                        "item:version:x")))
    t.ok("a different name is a different id", rec.did("item", "version", "x") != rec.did("item", "version", "y"))
    t.eq("every tool derives ids the same way", rec.did("i", "package", "p"),
         load_tool(REBUILD).did("i", "package", "p"))

    t.eq("a file name that numbers a range says so", rec.naming("Show - S07E23-24.mkv"),
         {"scheme": "unknown", "seasonNumber": 7, "episodeNumber": 23, "episodeEnd": 24, "raw": "S07E23-24"})
    t.eq("and the ordering it used stays unknown, because the name does not say",
         rec.naming("Show 1x05.mkv"),
         {"scheme": "unknown", "seasonNumber": 1, "episodeNumber": 5, "episodeEnd": None, "raw": "1x05"})
    t.eq("a quality token is not an episode range", rec.naming("Show S01E02-1080p.mkv")["episodeEnd"], None)
    t.eq("a name that numbers nothing says nothing", rec.naming("Example Film (2024).mkv"), None)
    # the names the catalog's scanner reads (katalog-manager internal/scanner, TestEpisodeRanges), each as it
    # reads it: season, first and last episode, or nothing
    ranges = {"Show.S05E15.mkv": (5, 15, 15), "Show.S05E15-E16.mkv": (5, 15, 16), "Show.S05E15E16.mkv": (5, 15, 16),
              "Show.S05E15-16.mkv": (5, 15, 16), "Show.S05E15-E17.mkv": (5, 15, 17), "show.s05e15e16.mkv": (5, 15, 16),
              "Show.s05E15-e16.mkv": (5, 15, 16), "Show.S05E15.E16.mkv": (5, 15, 16),
              "Show S05E15 E16 The Finale.mkv": (5, 15, 16), "Show.S05E15_E16.mkv": (5, 15, 16), "Show_S05E15.mkv": None,
              "Show.S05E15E16E17.mkv": (5, 15, 17), "Show.S05E15-E16-E17.mkv": (5, 15, 17),
              "Show.S05E15-16.720p.The.Finale.mkv": (5, 15, 16), "Show.S01E01-E02.1080p.mkv": (1, 1, 2),
              "Show.S05E15-720p.mkv": (5, 15, 15), "Show.S05E15-1080p.mkv": (5, 15, 15), "Show.S05E15-2160P.mkv": (5, 15, 15),
              "Show.S05E15-576i.mkv": (5, 15, 15), "Show.S05E15-264.mkv": (5, 15, 15), "Show.S05E15-E14.mkv": (5, 15, 15),
              "Show.S05E15-E26.mkv": (5, 15, 15), "Show.S05E15-E24.mkv": (5, 15, 24), "Show.S05E15.720p.mkv": (5, 15, 15),
              "Show.S05E15-E16x.mkv": (5, 15, 15), "Show.S05E15Finale.mkv": None, "Show.S05E1500.mkv": None,
              "Big Buck Bunny (2008).mp4": None}
    read = lambda n: (lambda x: x and (x["seasonNumber"], x["episodeNumber"], x["episodeEnd"] or x["episodeNumber"]))(
        rec.naming(n))
    t.eq("a name numbers the episodes its file holds as the catalog's scanner reads them: from the first to the last, "
         "each after the one before and at most ten, never a resolution, the token standing apart",
         {n: read(n) for n in ranges}, ranges)
    t.eq("and records the token it read them from, and no end for a file of one episode",
         [(rec.naming(n)["raw"], rec.naming(n)["episodeEnd"]) for n in ("Show.S05E15E16E17.mkv", "Show.S05E15-E16x.mkv",
                                                                        "Show.S05E15-264.mkv")],
         [("S05E15E16E17", 17), ("S05E15", None), ("S05E15", None)])
    t.eq("the v1 converter reads a name the same way", load_tool(FROM_V1).Convert.naming(None, "Show - S07E23-24.mkv"),
         rec.naming("Show - S07E23-24.mkv"))

    gate = load_tool(REBUILD).deletion_gate
    source = {"surround": True, "maxAudioChannels": 6, "subtitleLanguages": ["en", "de"], "chapters": True}
    package = {"surround": False, "maxAudioChannels": 2, "subtitleLanguages": ["en"]}
    t.eq("the deletion gate names what the package failed to carry", gate([source], package),
         ["maxAudioChannels", "subtitleLanguages:de", "surround"])
    t.eq("a package that carries everything costs nothing", gate([package], package), [])
    t.eq("chapter marks are never a loss: the version keeps them, not the file",
         gate([{"chapters": True}], {}), [])


# ---------------------------------------------------------------- the records the packager writes
GOLDEN = os.path.join(TOOLS, "testdata", "libv2_records")
GOLDEN_ITEM = "f0f0f0f0-1111-4222-8333-444444444444"
GOLDEN_EXTRA = "16aa63f3-5555-4666-8777-888888888888"
GOLDEN_AT = "2026-10-06T10:00:00Z"
GOLDEN_ORIGINAL = "Example Film (2024) - 2160p.mkv"
GOLDEN_SIDECAR = "Example Film (2024) - 2160p.de.srt"
GOLDEN_COMPANION = "Example Film (2024) - 2160p.nfo"
# The records a writer builds with the module, each compared byte for byte with expected/<name>.
GOLDEN_RECORDS = {"source.json": "sources/{sid}/source.json", "ffprobe.json": "sources/{sid}/ffprobe.json",
                  "source.checksums.sha256": "sources/{sid}/checksums.sha256",
                  "version.json": "versions/{vid}/version.json",
                  "version.checksums.sha256": "versions/{vid}/checksums.sha256",
                  "package.json": "versions/{vid}/package.json", "complete": "versions/{vid}/.complete",
                  "extra.json": "extras/{xid}/extra.json", "extra.checksums.sha256": "extras/{xid}/checksums.sha256",
                  "extra.package.json": "extras/{xid}/package.json", "extra.complete": "extras/{xid}/.complete"}


def package_placeholders(folder, manifest):
    """The files of the package a manifest describes, each a placeholder holding its own path — but a
    master playlist naming its bandwidths and a trickplay track whose cues cover the duration."""
    def put(rel, body):
        p = os.path.join(folder, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(body if isinstance(body, bytes) else body.encode())
    ren = manifest["renditions"]
    for r in ren["video"] + ren["audio"] + ren.get("audioSurround", []):
        for name in ("init.mp4", "seg-00001.m4s", "playlist.m3u8"):
            put(f"{r['dir']}/{name}", f"{r['dir']}/{name}\n")
    for s in manifest.get("subtitles") or []:
        put(s["path"], f"{s['path']}\n")
        if s.get("hls"):
            put(f"{s['hls']}/playlist.m3u8", f"{s['hls']}/playlist.m3u8\n")
    put("hls/master.m3u8", "#EXTM3U\n" + "".join(f"#EXT-X-STREAM-INF:BANDWIDTH={v['peakBitrateBps'] + 192000}\n"
                                                 f"{v['id']}/playlist.m3u8\n" for v in ren["video"]))
    tp = manifest.get("trickplay")
    if tp:
        cues = manifest["durationMs"] // (tp["intervalSec"] * 1000)
        put(tp["vttPath"], "WEBVTT\n\n" + "".join(f"00:00:{i * 10:02d}.000 --> 00:00:{i * 10 + 10:02d}.000\n"
                                                  f"sprite-0000.jpg#xywh={i * 320},0,320,134\n\n" for i in range(cues)))
        put("trickplay/sprite-0000.jpg", "trickplay/sprite-0000.jpg\n")


def golden_tree(root, rec):
    """The item folder a packager writes when it establishes a version — it first packages an original
    that came with a German subtitle file and a .nfo beside it — and the folder of a trailer of the
    movie it packaged too: every record built with the record module from the fixtures' probes and
    manifests, every moment and id fixed, and the original renamed last into the version folder, under
    the name the library gives it. Returns (item folder, sourceId, versionId, notes)."""
    arrivals = os.path.join(root, "arrivals", "Example Film (2024)")
    os.makedirs(arrivals)
    original = os.path.join(arrivals, GOLDEN_ORIGINAL)
    with open(original, "wb") as f:
        f.write(bytes((i * 7 + 3) % 251 for i in range(200000)))
    with open(os.path.join(arrivals, GOLDEN_SIDECAR), "w") as f:
        f.write("1\n00:00:01,000 --> 00:00:02,000\nHallo\n")
    with open(os.path.join(arrivals, GOLDEN_COMPANION), "w") as f:
        f.write("<movie><title>Example Film</title></movie>\n")
    moment = datetime.datetime(2026, 10, 6, 9, 0, tzinfo=datetime.timezone.utc).timestamp()
    os.utime(original, (moment, moment))
    item = os.path.join(root, "library", "movies", GOLDEN_ITEM[:2], GOLDEN_ITEM)
    jwrite(os.path.join(item, "item.json"), {"schema": "zaentrum.library.item/2", "itemId": GOLDEN_ITEM, "type": "movie",
                                             "title": "Example Film", "externalIds": {}, "createdAt": GOLDEN_AT})
    write_sums(item, ["item.json"])
    jwrite(os.path.join(item, "metadata.json"), {"schema": "zaentrum.library.metadata/2", "itemId": GOLDEN_ITEM,
                                                 "type": "movie", "asOf": GOLDEN_AT,
                                                 "titles": {"primary": "Example Film"}, "images": []})

    # sources/<sourceId>/: the probe, the copy of the subtitle file — and nothing of the .nfo — the record,
    # the checksums
    sid, vid, pid = (rec.did(GOLDEN_ITEM, kind, GOLDEN_ORIGINAL) for kind in ("source", "version", "package"))
    sp = os.path.join(item, "sources", sid)
    os.makedirs(sp)
    sidecar = os.path.join(arrivals, GOLDEN_SIDECAR)
    sidecars = [rec.sidecar_entry(sid, 1, sidecar, GOLDEN_SIDECAR, "de")]
    shutil.copyfile(sidecar, os.path.join(item, sidecars[0]["file"]))
    probe = jload(os.path.join(GOLDEN, "probe.json"))
    source, raw = rec.source_record(sid, GOLDEN_ORIGINAL, os.path.getsize(original), taken_at=GOLDEN_AT,
                                    taken_by="packager", library_path=f"Example Film (2024)/{GOLDEN_ORIGINAL}",
                                    qh1=rec.qh1(original), mtime=rec.ts_of_mtime(original), probe=probe,
                                    probe_version="ffprobe version 7.1", sidecars=sidecars)
    for name, data in (("source.json", rec.json_bytes(source)), ("ffprobe.json", raw)):
        with open(os.path.join(sp, name), "wb") as f:
            f.write(data)
    sums = rec.checksums([rec.record_entry(n, open(os.path.join(sp, n), "rb").read()) for n in rec.listdir(sp)])[0]
    with open(os.path.join(sp, "checksums.sha256"), "wb") as f:
        f.write(sums)

    # versions/<versionId>/: the package, the record, then the chain — and last the original, which no link of
    # the chain lists: the version keeps it beside its package, so the package is derived
    manifest = jload(os.path.join(GOLDEN, "manifest.json"))
    vp = os.path.join(item, "versions", vid)
    package_placeholders(vp, manifest)
    version = rec.version_record(vid, source, created_at=GOLDEN_AT, created_by="katalog-manager",
                                 chapters=rec.probe_chapters(probe), chapters_from="original-file",
                                 segments=rec.segments([{"kind": "credits", "startMs": 50000, "endMs": 60000,
                                                         "source": "chapter", "confidence": 0.9, "label": None},
                                                        {"kind": "outro", "startMs": 55000, "endMs": 60000,
                                                         "source": "blackframe", "confidence": None, "label": None}]),
                                 original_files=[source["file"]["name"]])
    version_bytes = rec.json_bytes(version)
    listed = rec.package_files(vp) + [rec.record_entry("version.json", version_bytes)]
    package, notes = rec.package_record(pid, manifest, listed, source=source, role="derived",
                                        peak_bandwidth_bps=rec.peak_bandwidth(vp, manifest["hls"]["master"]),
                                        sidecars={"sub3": sidecars[0]["file"]})
    package_bytes = rec.json_bytes(package)
    for name, data in (("version.json", version_bytes), ("checksums.sha256", rec.checksums(listed)[0]),
                       ("package.json", package_bytes), (".complete", rec.complete(package_bytes))):
        with open(os.path.join(vp, name), "wb") as f:
            f.write(data)
    os.rename(original, os.path.join(vp, rec.library_original_name(GOLDEN_ORIGINAL)))

    # extras/<extraId>/: an extra keeps no original; its record names the one it was packaged from
    trailer = os.path.join(root, "arrivals", "extras", "trailer.mov")
    os.makedirs(os.path.dirname(trailer))
    with open(trailer, "wb") as f:
        f.write(b"a trailer that is only an example\n" * 100)
    xp = os.path.join(item, "extras", GOLDEN_EXTRA)
    xman = jload(os.path.join(GOLDEN, "extra-manifest.json"))
    package_placeholders(xp, xman)
    extra = rec.extra_record(GOLDEN_EXTRA, created_at="2026-10-06T08:00:00Z", created_by="katalog-manager/api",
                             kind="trailer", title="Trailer", language="zxx",
                             probe=jload(os.path.join(GOLDEN, "extra-probe.json")), probe_version="ffprobe version 7.1",
                             probed_at=GOLDEN_AT, packaged_from=[{"name": "trailer.mov", "sizeBytes": os.path.getsize(trailer),
                                                                  "fixity": {"qh1": rec.qh1(trailer)}}])
    extra_bytes = rec.json_bytes(extra)
    xlisted = rec.package_files(xp, rec.EXTRA_DIRS) + [rec.record_entry("extra.json", extra_bytes)]
    xpackage, xnotes = rec.package_record(rec.did(GOLDEN_EXTRA, "package"), xman, xlisted, source=extra,
                                          peak_bandwidth_bps=rec.peak_bandwidth(xp))
    xpackage_bytes = rec.json_bytes(xpackage)
    for name, data in (("extra.json", extra_bytes), ("checksums.sha256", rec.checksums(xlisted)[0]),
                       ("package.json", xpackage_bytes), (".complete", rec.complete(xpackage_bytes))):
        with open(os.path.join(xp, name), "wb") as f:
            f.write(data)
    return item, sid, vid, notes + xnotes


def test_records(t):
    """The records the packager writes with the module it vendors — a source, a version, its package and
    an extra — are the ones expected/ holds, byte for byte; their chains hold, and the media check finds
    the tree they make whole."""
    rec = load_tool(RECORDS)
    with tempfile.TemporaryDirectory() as tmp:
        item, sid, vid, notes = golden_tree(tmp, rec)
        for name, where in sorted(GOLDEN_RECORDS.items()):
            got = open(os.path.join(item, where.format(sid=sid, vid=vid, xid=GOLDEN_EXTRA)), "rb").read()
            want = os.path.join(GOLDEN, "expected", name)
            t.ok(f"the module writes {name} as expected/ holds it",
                 os.path.isfile(want) and got == open(want, "rb").read(),
                 got.decode("utf-8", "replace")[:600])
        t.eq("it says what it normalised: the forced track the packager flagged default",
             notes, ["subtitle sub0 was a forced track flagged default; cleared"])
        vp, xp = os.path.join(item, "versions", vid), os.path.join(item, "extras", GOLDEN_EXTRA)
        t.eq("the version's chain holds", rec.chain_problems(vp, full=True), [])
        t.eq("and the extra's", rec.chain_problems(xp, "extra.json", rec.EXTRA_DIRS, full=True), [])
        code, text = run(MEDIA_CHECK, "--checksums", os.path.join(tmp, "library"))
        t.ok("the media check finds the tree they make whole", code == 0 and text.strip().endswith("OK"), text)
        if have_jsonschema():
            code, text = run(VALIDATOR, "--check-checksums", os.path.join(tmp, "library"))
            t.ok("and validate-library-v2.py finds them valid records", code == 0 and text.strip().endswith("OK"), text)
        else:
            t.skip("and validate-library-v2.py finds them valid records", "jsonschema is not importable here")

        package = jload(os.path.join(vp, "package.json"))
        t.eq("the 5.1 companion counts for what the package carries, so surround is no loss, and the copied "
             "PQ stream keeps its HDR10 metadata: only the attached font is lost",
             (package["essence"]["surround"], package["essence"]["maxAudioChannels"],
              package["essence"]["hdr10Metadata"], [x["kind"] for x in package["fidelity"]["losses"]]),
             (True, 6, True, ["attachments-dropped"]))
        t.eq("the deletion gate is what the original carries and the package does not",
             rec.deletion_gate([jload(os.path.join(item, "sources", sid, "source.json"))["essence"]], package["essence"]),
             ["fonts"])
        source = jload(os.path.join(item, "sources", sid, "source.json"))["essence"]
        encoded = dict(package["renditions"], video=[dict(package["renditions"]["video"][0], encoder="hevc_nvenc")])
        stereo_only = dict(encoded, audioSurround=[])
        t.eq("a re-encoded PQ stream is taken to have lost the metadata, and without the 5.1 companion so is surround",
             (rec.package_essence({"renditions": encoded, "subtitles": package["subtitles"]}, source)["hdr10Metadata"],
              rec.deletion_gate([source], rec.package_essence({"renditions": stereo_only,
                                                               "subtitles": package["subtitles"]}, source))),
             (False, ["fonts", "hdr10Metadata", "maxAudioChannels", "surround"]))
        t.eq("a rendition made from a sidecar names the copy the source folder keeps",
             [s.get("fromSidecar") for s in package["subtitles"]],
             [None, None, None, f"sources/{sid}/subtitle-1.de.srt"])
        recorded = jload(os.path.join(item, "sources", sid, "source.json"))
        t.eq("the source folder keeps a copy of the subtitle file that came with the original, under the name the "
             "library gives it, described without the name it came with — and nothing of the .nfo beside it",
             ([(x["file"], x["kind"], x.get("language"), x.get("purpose"), "originalName" in x) for x in recorded["sidecars"]],
              rec.listdir(os.path.join(item, "sources", sid))),
             ([(f"sources/{sid}/subtitle-1.de.srt", "subtitle", "de", "dialogue", False)],
              ["checksums.sha256", "ffprobe.json", "source.json", "subtitle-1.de.srt"]))
        t.eq("the record names the original as the library does, keeps what its name and folder claimed and no "
             "name, and not where it came from",
             (recorded["file"]["name"], "origin" in recorded, recorded["labels"]["quality"],
              recorded["container"]["title"], sorted(recorded["container"]["tags"])),
             ("original.mkv", False, "2160p", None, ["ENCODER"]))
        written = jload(os.path.join(item, "sources", sid, "ffprobe.json"))
        fixture = jload(os.path.join(GOLDEN, "probe.json"))
        t.eq("its probe is the tool's output with two edits: the library's name for the file, and no title tag",
             (written["format"].pop("filename"), written["format"]["tags"].pop("title", None),
              written == dict(fixture, format={k: v for k, v in fixture["format"].items() if k != "filename"}
                              | {"tags": {k: v for k, v in fixture["format"]["tags"].items() if k != "title"}})),
             ("original.mkv", None, True))
        t.eq("the version keeps the original beside its package, renamed in last under the same name, so the "
             "package is derived",
             (jload(os.path.join(vp, "version.json"))["originalFiles"], package["role"],
              os.path.getsize(os.path.join(vp, "original.mkv")), "original.mkv" in listing(vp)),
             (["original.mkv"], "derived", recorded["file"]["sizeBytes"], False))
        extra = jload(os.path.join(xp, "extra.json"))
        t.eq("an extra names what it was packaged from as the library names an original, and keeps no title tag",
             ([x["name"] for x in extra["packagedFrom"]], extra["container"]["title"]), (["original.mov"], None))
        arrived = [n.encode() for n in (os.path.splitext(GOLDEN_ORIGINAL)[0], "Example Film (2024)", "trailer.mov",
                                        os.path.splitext(GOLDEN_COMPANION)[1])]
        named = sorted(rel for rel, data in tree_files(os.path.join(tmp, "library")).items() if any(a in data for a in arrived))
        odd = sorted(rel for rel in tree_files(os.path.join(tmp, "library"))
                     if not all(re.fullmatch(r"[a-z0-9.-]+", part) for part in rel.split(os.sep)))
        t.eq("no file the packager wrote names the original, its folder or the trailer as they arrived, and every "
             "name in the tree is of letters, digits, dots and dashes", (named, odd), ([], []))

        # a subtitle made from a sidecar: the sidecar's own row stands for it while the original is there, so the
        # rebuild gives it none; once the original is deleted it is a row in the sidecar's language
        rows, _ = rows_of(os.path.join(tmp, "library"), "--text-language", "und")
        t.eq("the rebuild gives no row to a subtitle made from a sidecar while the original is there, and plays "
             "the original from its version folder",
             ([(x["language"], os.path.basename(x["path"])) for x in rows[GOLDEN_ITEM]["subtitleAssets"]],
              sorted((a["kind"], os.path.basename(a["path"]), a["sizeBytes"]) for a in rows[GOLDEN_ITEM]["playbackAssets"])),
             ([("eng", "0.vtt"), ("eng", "1.vtt"), ("ger", "2.sup")],
              [("packaged", "package.json", package["sizeBytes"]), ("primary", "original.mkv", recorded["file"]["sizeBytes"])]))
        os.makedirs(os.path.join(tmp, "trash"))
        os.rename(os.path.join(vp, "original.mkv"), os.path.join(tmp, "trash", "original.mkv"))
        write_event(item, {"schema": "zaentrum.library.event/2", "eventId": "0d000000-0000-4000-8000-000000000001",
                           "at": "2026-10-06T11:00:00Z", "by": "test", "kind": "original-deleted", "versionId": vid,
                           "sourceId": sid, "accepted": ["fonts"]})
        rows, _ = rows_of(os.path.join(tmp, "library"), "--text-language", "und")
        t.eq("and once the original is deleted from its version folder, a row of the package's subtitle in the "
             "sidecar's language, and the package plays alone",
             ([(x["language"], os.path.basename(x["path"])) for x in rows[GOLDEN_ITEM]["subtitleAssets"]],
              [a["kind"] for a in rows[GOLDEN_ITEM]["playbackAssets"]]),
             ([("eng", "0.vtt"), ("eng", "1.vtt"), ("ger", "2.sup"), ("de", "3.vtt")], ["packaged"]))
        if have_jsonschema():
            code, text = run(VALIDATOR, "--check-media", os.path.join(tmp, "library"))
            t.ok("and the tree its retire leaves is valid, the original named by its version and gone from it",
                 code == 0 and text.strip().endswith("OK"), text)

        # the chain check bites: a package file changed, a file nobody listed, a record changed
        with open(os.path.join(vp, "subs", "1.vtt"), "ab") as f:
            f.write(b"x")
        t.ok("a package file changed after the chain was closed is caught by the full check",
             any("subs/1.vtt does not match its checksum" in p for p in rec.chain_problems(vp, full=True))
             and not any("subs/1.vtt" in p for p in rec.chain_problems(vp)))
        with open(os.path.join(vp, "hls", "v0", "seg-00002.m4s"), "w") as f:
            f.write("x")
        t.ok("a file the checksums do not list is caught",
             "hls/v0/seg-00002.m4s is not listed in checksums.sha256" in rec.chain_problems(vp))
        with open(os.path.join(xp, "extra.json"), "a") as f:
            f.write(" ")
        t.ok("and a record changed after it was covered",
             "extra.json does not match the digest checksums.sha256 lists for it"
             in rec.chain_problems(xp, "extra.json", rec.EXTRA_DIRS))

    try:
        rec.source_record(GOLDEN_ITEM, "gone.mkv", 1, taken_at=GOLDEN_AT, taken_by="x", library_path="gone.mkv")
        refused = False
    except ValueError:
        refused = True
    t.ok("a source record without fixity is refused unless a note says why it has none", refused)
    gone, raw = rec.source_record(GOLDEN_ITEM, "Gone Film (2023).MKV", 1, taken_at=GOLDEN_AT, taken_by="x",
                                  library_path="Gone Film (2023)/Gone Film (2023).MKV",
                                  note="the original was gone before the library was recorded")
    t.ok("and with one it is a record of the file's name — the library's — and size alone",
         raw is None and "fixity" not in gone["file"] and gone["file"]["name"] == "original.mkv"
         and "origin" not in gone and gone["probe"]["note"].startswith("the original was gone"), gone)

    # a file of several episodes: the record says which, the holder first, as the database linked them
    holder, covered = "c0000000-0000-4000-8000-0000000000e2", "c0000000-0000-4000-8000-0000000000e3"
    double = lambda **kw: rec.source_record(GOLDEN_ITEM, "Example Show - S01E02-E03.mkv", 1, taken_at=GOLDEN_AT,
                                            taken_by="x", library_path="Example Show/Example Show - S01E02-E03.mkv",
                                            qh1="sha256:" + "0" * 64, **kw)[0]
    probed_double = rec.source_record(GOLDEN_ITEM, "Example Show - S01E02-E03.mkv", 1, taken_at=GOLDEN_AT, taken_by="x",
                                      library_path="x/Example Show - S01E02-E03.mkv", qh1="sha256:" + "0" * 64,
                                      probe=jload(os.path.join(GOLDEN, "probe.json")), covers=(holder, covered))[0]
    t.eq("a source of a file that holds several episodes covers them as given, the holder first, probed or not, and "
         "its name's numbering stays what the release claimed",
         (double(covers=[holder, covered])["covers"], probed_double["covers"], double(covers=[holder, covered])["naming"]),
         ([holder, covered], [holder, covered],
          {"scheme": "unknown", "seasonNumber": 1, "episodeNumber": 2, "episodeEnd": 3, "raw": "S01E02-E03"}))
    t.eq("and a file of one episode covers nothing, whatever its name claims",
         (double()["covers"], double(covers=None)["covers"]), ([], []))
    for bad in ([holder, holder], [holder, "S01E03"]):
        try:
            double(covers=bad)
            refused = False
        except ValueError:
            refused = True
        t.ok(f"a file covering {bad!r} is refused: it covers episodes by their item ids, each once", refused)
    t.eq("a version made from a source says what its name claimed of its edition, and never the name",
         [rec.version_record("v", rec.source_record(GOLDEN_ITEM, arrived, 1, taken_at=GOLDEN_AT, taken_by="x",
                                                    library_path=f"{folder}/{arrived}", note="no fixity")[0],
                             created_at=GOLDEN_AT, created_by="x")["edition"]
          for arrived, folder in (("Example Film (2024) - Director's Cut.mkv", "Example Film (2024)"),
                                  ("Example Film (2024).mkv", "Example Film - Unrated (2024)"),
                                  ("Example Film (2024).mkv", "Example Film (2024)"))],
         [{"kind": "directors-cut", "label": None, "decidedBy": "inferred", "decidedAt": GOLDEN_AT,
           "evidence": [{"signal": "filename", "value": "directors-cut", "weight": 0.6}]},
          {"kind": "unrated", "label": "Unrated", "decidedBy": "inferred", "decidedAt": GOLDEN_AT,
           "evidence": [{"signal": "folder-name", "value": "Unrated", "weight": 0.6}]},
          {"kind": "unknown", "label": None, "decidedBy": "inferred", "decidedAt": GOLDEN_AT, "evidence": []}])

    # the names the library gives: of an original, its parts, and the copy of a subtitle file that came with it
    t.eq("an original is original.<ext>, its extension lower-cased, and bin when it is no extension of letters "
         "and digits", [rec.library_original_name(n) for n in ("Example Film (2024) - 2160p.mkv", "FILM.M2TS",
                                                              "a/b/Show - S01E02.Mp4", "no extension", "odd.mk_v",
                                                              "long.extension9", ".mkv", "C:\\x\\film.ts")],
         ["original.mkv", "original.m2ts", "original.mp4", "original.bin", "original.bin", "original.bin",
          "original.bin", "original.ts"])
    t.eq("the parts of a version split into several are numbered in part order",
         [rec.library_original_name("Example Film - part1.mkv", 1), rec.library_original_name("Example Film - part2.mkv", 2)],
         ["original-1.mkv", "original-2.mkv"])
    for bad in (0, -1, "2", True):
        try:
            rec.library_original_name("a.mkv", bad)
            refused = False
        except ValueError:
            refused = True
        t.ok(f"a part numbered {bad!r} is refused: parts are numbered from 1", refused)
    t.eq("a subtitle file's copy is subtitle-<n>.<lang>[.forced][.sdh].<ext>, lang the BCP 47 primary subtag",
         [rec.subtitle_copy_name(1, "ger", False, False, ".SRT"), rec.subtitle_copy_name(2, "en-GB", True, False, "srt"),
          rec.subtitle_copy_name(3, None, False, True, "ass"), rec.subtitle_copy_name(4, "pt_BR", True, True, "vtt"),
          rec.subtitle_copy_name(5, "Deutsch", False, False, "s-r-t"), rec.subtitle_copy_name(6, "zh-Hant-TW", False, False, "")],
         ["subtitle-1.de.srt", "subtitle-2.en.forced.srt", "subtitle-3.und.sdh.ass", "subtitle-4.pt.forced.sdh.vtt",
          "subtitle-5.und.bin", "subtitle-6.zh.bin"])
    t.ok("and every name either gives is a neutral one",
         all(rec.ORIGINAL_NAME_RE.match(rec.library_original_name(n, p)) for n in ("x.mkv", "y", "z.TS") for p in (None, 3))
         and all(rec.SUBTITLE_COPY_RE.match(rec.subtitle_copy_name(n, l, f, s, e)) for n in (1, 12)
                 for l in ("de", "eng", None, "x-klingon") for f in (False, True) for s in (False, True)
                 for e in ("srt", ".VTT", "")))
    with tempfile.TemporaryDirectory() as tmp:
        arrived = os.path.join(tmp, "Example Film (2024).en.forced.SDH.srt")
        with open(arrived, "w") as f:
            f.write("1\n00:00:01,000 --> 00:00:02,000\nHello\n")
        entry, size = rec.sidecar_entry(GOLDEN_ITEM, 2, arrived, os.path.basename(arrived), "eng"), os.path.getsize(arrived)
    t.eq("a sidecar entry names the copy and keeps what the name and the catalog said of it, never the name",
         (entry["file"], entry["language"], entry["forced"], entry["hearingImpaired"], entry["purpose"],
          "originalName" in entry, entry["format"], entry["sizeBytes"]),
         (f"sources/{GOLDEN_ITEM}/subtitle-2.en.forced.sdh.srt", "eng", True, True, "forced", False, "srt", size))
    probe = {"format": {"filename": "/media/Example Film (2024)/Example Film (2024).mkv", "duration": "1.0",
                        "tags": {"TITLE": "Example.Film.2024", "title": "Example Film", "Title": "x", "ENCODER": "e",
                                 "subtitle": "kept"}},
             "streams": [{"index": 0, "codec_type": "audio", "tags": {"title": "Commentary"}}]}
    before = json.dumps(probe, sort_keys=True)
    scrubbed = rec.scrub_probe(probe, "original.mkv")
    t.eq("a probe is kept with the library's name for its file and no title tag in any case, and nothing else changed",
         scrubbed, {"format": {"filename": "original.mkv", "duration": "1.0", "tags": {"ENCODER": "e", "subtitle": "kept"}},
                    "streams": [{"index": 0, "codec_type": "audio", "tags": {"title": "Commentary"}}]})
    t.ok("and the probe it was given is left as it was", json.dumps(probe, sort_keys=True) == before)
    t.eq("a probe with no format, or no tags, keeps what it has",
         (rec.scrub_probe({"streams": []}, "original.mkv"), rec.scrub_probe({"format": {}}, "original.mkv")),
         ({"streams": []}, {"format": {"filename": "original.mkv"}}))
    t.eq("an unknown kind of detected range is 'other', labelled with the catalog's kind",
         [(s["kind"], s["label"]) for s in rec.segments([{"kind": "outro", "startMs": 1, "endMs": 2, "label": "x"},
                                                         {"kind": "credits", "startMs": 3, "endMs": 4, "label": "black"}])],
         [("other", "outro"), ("credits", "black")])

    # piped behind a tool, the module runs as part of it: the tool must not shadow a name it defines
    import ast
    defined = lambda path: {n.name for n in ast.parse(open(path).read()).body if isinstance(n, (ast.FunctionDef, ast.ClassDef))} \
        | {t.id for n in ast.parse(open(path).read()).body if isinstance(n, ast.Assign) for t in n.targets
           if isinstance(t, ast.Name)}
    for tool in USES_RECORDS:
        t.eq(f"{os.path.basename(tool)} redefines no name of the module it is piped behind",
             sorted(defined(tool) & defined(RECORDS)), [])


# ---------------------------------------------------------------- the arrivals are not the record
def test_arrivals(t):
    """An original waits at the arrivals until its package is recorded, and is deleted then: --compare
    does not hold the tree to the export's rows of files there, nor ever to a retired original's row."""
    with tempfile.TemporaryDirectory() as tmp:
        export, media, packages, iid, _ = fake_export(os.path.join(tmp, "share"))
        out = os.path.join(tmp, "library")
        code, text = run(FROM_CATALOG, "--export", export, "--packages", packages, "--media", media, "--out", out,
                         "--media-mode", "none")
        e = jload(export)
        row = e["items"][0]
        row["subtitleAssets"].append({"id": "s9", "path": "/var/lib/katalog/media/Example Film (2024).de.srt",
                                      "format": "srt", "language": "de", "label": "Deutsch", "isDefault": False})
        jwrite(export, e)
        ignore = "id,path,hash,codec,resolution,bitrateKbps,durationMs,sizeBytes"
        code, text = run(REBUILD, out, "--compare", export, "--ignore-fields", ignore)
        t.ok("the export's rows of an original and its sidecar waiting outside the library differ from the tree",
             code == 1 and "playbackAssets" in text and "subtitleAssets" in text, text)
        code, text = run(REBUILD, out, "--compare", export, "--ignore-fields", ignore, "--arrivals-root",
                         "/var/lib/katalog/media")
        t.ok("--arrivals-root leaves them out, and the tree agrees with the database",
             code == 0 and "the tree and the database agree" in text, text)
        primary = next(a for a in row["playbackAssets"] if a["kind"] == "primary")
        primary.update(kind="original", isPrimary=False,
                       path=os.path.join(out, "movies", iid[:2], iid, "sources", "x"))
        row["subtitleAssets"] = row["subtitleAssets"][:1]
        jwrite(export, e)
        code, text = run(REBUILD, out, "--compare", export, "--ignore-fields", ignore)
        t.ok("and the row of an original that was retired is never compared",
             code == 0 and "the tree and the database agree" in text, text)


# ---------------------------------------------------------------- the example tree
def test_round_trip(t):
    rows, doc = rows_of(EXAMPLES)
    # what the example set states, read from the records themselves rather than from the rebuild
    stated = {}
    for p in sorted(glob.glob(os.path.join(EXAMPLES, "movies", "*", "*")) +
                    glob.glob(os.path.join(EXAMPLES, "series", "*", "*")) +
                    glob.glob(os.path.join(EXAMPLES, "series", "*", "*", "episodes", "*"))):
        item = jload(os.path.join(p, "item.json"))
        meta = jload(os.path.join(p, "metadata.json"))
        events = [jload(x) for x in sorted(glob.glob(os.path.join(p, "events", "*", "event.json")))]
        superseded = {e.get("packageId") for e in events if e["kind"] == "package-superseded"}
        removed = {e.get("versionId") for e in events if e["kind"] == "version-removed"}
        deleted = {e.get("versionId") for e in events if e["kind"] == "original-deleted"}
        packaged = originals = subs = 0
        for vp in sorted(glob.glob(os.path.join(p, "versions", "*"))):
            if os.path.basename(vp) in removed or not os.path.isfile(os.path.join(vp, "version.json")):
                continue
            version = jload(os.path.join(vp, "version.json"))
            if os.path.basename(vp) not in deleted:
                originals += len(version["originalFiles"])
            if os.path.isfile(os.path.join(vp, "package.json")):
                package = jload(os.path.join(vp, "package.json"))
                if package["packageId"] not in superseded:
                    packaged += 1
                    subs += len(package.get("subtitles") or [])
        stated[item["itemId"]] = {"type": item["type"], "title": meta["titles"]["primary"],
                                  "packaged": packaged, "primary": originals, "subtitles": subs,
                                  "images": len(meta["images"])}
    t.eq("the rebuild finds exactly the items the example set holds", sorted(rows), sorted(stated))
    for iid, want in sorted(stated.items()):
        row = rows.get(iid) or {}
        got = {"type": row.get("type"), "title": row.get("title"),
               "packaged": sum(1 for a in row.get("playbackAssets") or [] if a["kind"] == "packaged"),
               "primary": sum(1 for a in row.get("playbackAssets") or [] if a["kind"] == "primary"),
               "subtitles": len(row.get("subtitleAssets") or []),
               "images": len(row.get("artwork") or [])}
        t.eq(f"the row for {want['title']} is the one the records state", got, want)
    movie = next(r for r in rows.values() if r["type"] == "movie")
    t.eq("a movie's series fields stay empty", (movie["parentId"], movie["seasonNumber"]), (None, None))
    episode = next(r for r in rows.values() if r["type"] == "episode" and r["seasonNumber"] == 1
                   and r["episodeNumber"] == 2)
    t.ok("an episode carries its series and its numbers",
         episode["parentId"] and episode["episodeNumber"] == 2)
    covers = {c: jload(p)["covers"][0] for p in glob.glob(os.path.join(EXAMPLES, "series", "*", "*", "episodes", "*",
                                                                       "sources", "*", "source.json"))
              for c in jload(p)["covers"][1:]}
    t.eq("an episode another's source covers is covered by that holder, as the catalog links it, and no other row is",
         {iid: r["coveredBy"] for iid, r in rows.items() if r["coveredBy"]}, covers)
    t.ok("and a covered episode has nothing of its own to play: it plays its holder's",
         covers and all(not rows[c]["playbackAssets"] and not rows[c]["subtitleAssets"] for c in covers)
         and all(rows[h]["playbackAssets"] for h in covers.values()), covers)
    t.ok("the rebuild says which version lost its original for good",
         any(v["permanentLoss"] for s in doc["storage"] for v in s["versions"]))
    t.ok("running it twice gives the same rows", rows_of(EXAMPLES)[0] == rows)
    people = {p["personId"]: p for p in (jload(x) for x in glob.glob(os.path.join(EXAMPLES, "people", "*", "*",
                                                                               "person.json")))}
    t.eq("the rebuild finds exactly the people the example set holds", sorted(p["id"] for p in doc["people"]),
         sorted(people))
    t.ok("with each person's texts, dates and portraits as the record states them",
         all(p["name"] == people[p["id"]]["name"] and p["biography"] == people[p["id"]]["biography"]
             and p["birthDate"] == people[p["id"]]["birthDate"]
             and [a["sha256"] for a in p["artwork"]] == [i["sha256"] for i in people[p["id"]]["images"]]
             for p in doc["people"]))
    credited = {c["personId"] for r in rows.values() for c in r["people"]}
    t.eq("and every person a credit names is one of them", credited, set(people))

    # ---- bonus material: rows of its own, as extra.json and the projection state them
    stated = {}
    for xj in sorted(glob.glob(os.path.join(EXAMPLES, "*", "*", "*", "extras", "*", "extra.json"))):
        xp, x = os.path.dirname(xj), jload(xj)
        item_dir = os.path.dirname(os.path.dirname(xp))
        decision = (jload(os.path.join(item_dir, "metadata.json"))["library"].get("extras") or {}).get(x["extraId"]) or {}
        packaged = os.path.isfile(os.path.join(xp, ".complete"))
        stated[x["extraId"]] = {
            "itemId": jload(os.path.join(item_dir, "item.json"))["itemId"], "kind": x["kind"], "title": x["title"],
            "seasonNumber": x.get("seasonNumber"), "order": decision.get("order"),
            "hidden": bool(decision.get("hidden")), "label": decision.get("label"),
            "assets": sorted(["primary"] * len(x["originalFiles"]) + ["packaged"] * packaged),
            "subtitles": len(jload(os.path.join(xp, "package.json"))["subtitles"]) if packaged else 0}
    got = {r["id"]: {"itemId": r["itemId"], "kind": r["kind"], "title": r["title"], "seasonNumber": r["seasonNumber"],
                     "order": r["order"], "hidden": r["hidden"], "label": r["label"],
                     "assets": sorted(a["kind"] for a in r["playbackAssets"]), "subtitles": len(r["subtitleAssets"])}
           for r in doc["extras"]}
    t.eq("the rebuild finds exactly the extras the example set holds, as their records and the projection state them",
         got, stated)
    t.ok("and each one's original is played from its own folder",
         all(a["path"].startswith(os.path.join(os.path.abspath(EXAMPLES), "")) and "/extras/" + r["id"] + "/" in a["path"]
             for r in doc["extras"] for a in r["playbackAssets"]), doc["extras"])
    sizes = {(jload(p)["extraId"], o["name"]): o["sizeBytes"]
             for p in glob.glob(os.path.join(EXAMPLES, "*", "*", "*", "extras", "*", "extra.json")) for o in jload(p)["originals"]}
    handed = [(r["id"], os.path.basename(a["path"]), a["sizeBytes"]) for r in doc["extras"] for a in r["playbackAssets"]
              if a["kind"] == "primary"]
    t.ok("with the size extra.json records of it", handed and all(s == sizes[(x, n)] for x, n, s in handed), handed)
    trailer = extra_of(os.path.abspath(EXAMPLES), "trailer")
    kept = os.path.join(trailer, jload(os.path.join(trailer, "extra.json"))["originalFiles"][0])
    movie_row = next(r for r in rows.values() if r["type"] == "movie")
    t.eq("a link an extra was downloaded from gets its local copy back: the original that extra keeps",
         [(v["site"], v["externalId"], v["localPath"]) for v in movie_row["trailers"]],
         [("example.org", "tears-of-steel-trailer", kept)])

    with tempfile.TemporaryDirectory() as tmp:
        tree = os.path.join(tmp, "library")
        shutil.copytree(EXAMPLES, tree)
        movie_dir = glob.glob(os.path.join(tree, "movies", "*", "*"))[0]
        featurette, trailer, scene = extra_of(tree, "featurette"), extra_of(tree, "trailer"), extra_of(tree, "deleted-scene")
        early, late = "00000000-0000-4000-8000-0000000000e1", "00000000-0000-4000-8000-0000000000e2"
        for xid, at in ((late, "2026-09-19T09:00:00Z"), (early, "2026-09-19T07:00:00Z")):
            shutil.copytree(featurette, os.path.join(movie_dir, "extras", xid))
            jwrite(os.path.join(movie_dir, "extras", xid, "extra.json"),
                   dict(jload(os.path.join(featurette, "extra.json")), extraId=xid, createdAt=at))
        meta = os.path.join(movie_dir, "metadata.json")
        projected = jload(meta)
        projected["library"]["extras"][late] = {"hidden": True}
        jwrite(meta, projected)
        extras = rows_of(tree)[1]["extras"]
        t.eq("an item's extras are listed as a viewer sees them: by the order a person gave, then as they were taken in",
             [r["id"] for r in extras if r["itemId"] == os.path.basename(movie_dir)],
             [os.path.basename(featurette), early, late, os.path.basename(scene), os.path.basename(trailer)])
        t.ok("and one a person hid is still a row, marked hidden",
             next(r for r in extras if r["id"] == late)["hidden"] is True)

        bts = glob.glob(os.path.join(tree, "series", "*", "*", "extras", "*"))[0]
        os.unlink(os.path.join(bts, "checksums.sha256"))
        os.unlink(os.path.join(featurette, ".complete"))
        _, built = rows_of(tree)
        ids = {r["id"] for r in built["extras"]}
        t.ok("an extra that never finished is left out — one with no checksums, a packaged one without its .complete — "
             "and the rebuild says so",
             not ids & {os.path.basename(bts), os.path.basename(featurette)}
             and ids == {early, late, os.path.basename(scene), os.path.basename(trailer)}
             and sum("never finished, so it is left out" in n for n in built["notes"]) == 2, built["notes"])

        episode = glob.glob(os.path.join(tree, "series", "*", "*", "episodes", "*"))[0]
        shutil.copytree(bts, os.path.join(episode, "extras", os.path.basename(bts)))
        write_sums(os.path.join(episode, "extras", os.path.basename(bts)), ["extra.json", jload(
            os.path.join(bts, "extra.json"))["originalFiles"][0]])
        _, built = rows_of(tree)
        t.ok("an episode's extras/ folder is ignored, and the rebuild says so",
             os.path.basename(bts) not in {r["id"] for r in built["extras"]}
             and any("an episode has no extras" in n for n in built["notes"]), built["notes"])

    # a covered episode's link is the record's: its projection decides only where nothing records the holder's file
    with tempfile.TemporaryDirectory() as tmp:
        tree = os.path.join(tmp, "library")
        shutil.copytree(EXAMPLES, tree)
        (covered, holder), = covers.items()
        folder = {jload(p)["itemId"]: os.path.dirname(p)
                  for p in glob.glob(os.path.join(tree, "series", "*", "*", "episodes", "*", "item.json"))}
        meta = os.path.join(folder[covered], "metadata.json")
        decided = jload(meta)["library"]
        jwrite(meta, dict(jload(meta), library={k: v for k, v in decided.items() if k != "coveredBy"}))
        t.eq("a covered episode whose projection does not name its holder is covered by it all the same: the record "
             "restores the link", rows_of(tree)[0][covered]["coveredBy"], holder)
        source = glob.glob(os.path.join(folder[holder], "sources", "*", "source.json"))[0]
        jwrite(source, dict(jload(source), covers=[]))
        jwrite(meta, dict(jload(meta), library=decided))
        rebuilt, built = rows_of(tree)
        t.ok("one whose projection names a holder no source of which covers it is covered by nothing, and the rebuild "
             "says the record won", rebuilt[covered]["coveredBy"] is None
             and any("the record wins, and the row is covered by nothing" in n for n in built["notes"]), built["notes"])
        shutil.rmtree(os.path.dirname(source))
        t.eq("and one whose holder has no source recorded yet is covered as its projection says: nothing contradicts it",
             rows_of(tree)[0][covered]["coveredBy"], holder)


def test_work_tree(t):
    """The share's root holds .work/ beside the record: every tool reads past it."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "library")
        shutil.copytree(EXAMPLES, root)
        before, _ = rows_of(root)
        work_tree(root)
        after, _ = rows_of(root)
        t.eq("the rebuild gives the same rows with the work tree beside the record", after, before)
        code, text = run(SWEEP, root, "--grace", "0")
        t.ok("and the sweep finds nothing in it to sweep", code == 0 and swept(text, root) == [], text)
        if have_jsonschema():
            code, text = run(VALIDATOR, root)
            t.ok("and the validator does not look at it", code == 0 and text.strip().endswith("OK"), text)


def test_proves_itself(t):
    """Every folder written once carries the checksums of what it holds, in the format the
    system's own sha256sum checks; a version's chain holds link by link; projections carry none."""
    folders = sorted(os.path.dirname(p) for p in glob.glob(os.path.join(EXAMPLES, "**", "checksums.sha256"),
                                                           recursive=True))
    kinds = {}
    for f in folders:
        kind = ("item" if os.path.isfile(os.path.join(f, "item.json")) else
                "source" if os.path.isfile(os.path.join(f, "source.json")) else
                "event" if os.path.isfile(os.path.join(f, "event.json")) else
                "version" if os.path.isfile(os.path.join(f, "version.json")) else
                "extra" if os.path.isfile(os.path.join(f, "extra.json")) else "other")
        kinds[kind] = kinds.get(kind, 0) + 1
    items = len(glob.glob(os.path.join(EXAMPLES, "**", "item.json"), recursive=True))
    t.eq("every item, source, event, version and extra folder has its checksums, and nothing else does",
         kinds, {"item": items, "source": len(glob.glob(os.path.join(EXAMPLES, "**", "source.json"), recursive=True)),
                 "event": len(glob.glob(os.path.join(EXAMPLES, "**", "event.json"), recursive=True)),
                 "version": len(glob.glob(os.path.join(EXAMPLES, "**", "versions", "*", ".complete"), recursive=True)),
                 "extra": len(glob.glob(os.path.join(EXAMPLES, "**", "extra.json"), recursive=True))})
    tool = shutil.which("sha256sum") and ["sha256sum", "-c", "--quiet"] or \
        shutil.which("shasum") and ["shasum", "-a", "256", "-c", "--quiet"]
    if tool:
        failed = [f for f in folders if subprocess.run(tool + ["checksums.sha256"], cwd=f, capture_output=True).returncode]
        t.ok(f"`{' '.join(tool[:-1])} checksums.sha256` passes in all {len(folders)} of them", not failed, failed)
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "library")
            shutil.copytree(EXAMPLES, root)
            vj = glob.glob(os.path.join(root, "movies", "*", "*", "versions", "*", "version.json"))[0]
            jwrite(vj, dict(jload(vj), runtimeMs=1))
            t.ok("and fails in a version folder whose version.json changed",
                 subprocess.run(tool + ["checksums.sha256"], cwd=os.path.dirname(vj), capture_output=True).returncode != 0)
    else:
        t.skip("sha256sum -c passes in every one of them", "neither sha256sum nor shasum is on PATH")
    chain = True
    for mark in glob.glob(os.path.join(EXAMPLES, "**", ".complete"), recursive=True):
        vp = os.path.dirname(mark)
        package = jload(os.path.join(vp, "package.json"))
        record = "version.json" if os.path.isfile(os.path.join(vp, "version.json")) else "extra.json"
        chain &= open(mark).read().strip() == "sha256:" + digest(os.path.join(vp, "package.json"))
        chain &= package["checksums"]["sha256"] == "sha256:" + digest(os.path.join(vp, "checksums.sha256"))
        chain &= record in listing(vp) and not {".complete", "package.json", "checksums.sha256"} & set(listing(vp))
    t.ok("each version and each packaged extra is one chain: .complete names package.json, which names "
         "checksums.sha256, which lists the record it was made for and never a link above it", chain)
    extras = [os.path.dirname(p) for p in glob.glob(os.path.join(EXAMPLES, "**", "extra.json"), recursive=True)]
    t.ok("an extra's checksums list its originals too, as a version's never do: an extra is written whole and keeps them",
         extras and all(set(jload(os.path.join(x, "extra.json"))["originalFiles"]) <= set(listing(x)) for x in extras)
         and not any(n.endswith(".mkv") for vp in glob.glob(os.path.join(EXAMPLES, "**", "versions", "*"), recursive=True)
                     if os.path.isfile(os.path.join(vp, "checksums.sha256")) for n in listing(vp)), extras)
    listed = [n for f in folders for n in listing(f)]
    t.ok("and no checksums file covers a projection or an image: those are replaced whole, or named by their hash",
         not {"metadata.json", "person.json"} & set(listed)
         and not any(n.startswith("metadata/") or n.endswith((".jpg", ".png", ".webp")) and "trickplay/" not in n
                     for n in listed), listed)


def test_events(t):
    with tempfile.TemporaryDirectory() as tmp:
        base = os.path.join(tmp, "library")
        shutil.copytree(EXAMPLES, base)
        before, doc = rows_of(base)

        # ---- package-superseded: the successor is the one to use
        ev = glob.glob(os.path.join(base, "series", "*", "*", "episodes", "*", "events",
                                    "*-package-superseded"))[0]
        aside = os.path.join(tmp, "aside")
        shutil.move(ev, aside)
        after, _ = rows_of(base)
        t.ok("package-superseded keeps the superseded package out of the rows",
             len(assets(after, "packaged")) == len(assets(before, "packaged")) + 1)
        shutil.move(aside, ev)
        t.eq("and putting the event back takes it out again", assets(rows_of(base)[0]), assets(before))

        # ---- version-removed: the folder is ignored even when it is still there
        item = os.path.dirname(os.path.dirname(ev))
        vp = sorted(glob.glob(os.path.join(item, "versions", "*")))[0]
        vid = os.path.basename(vp)
        removal = write_event(item, {"schema": "zaentrum.library.event/2", "eventId": str(uuid.uuid4()),
                                     "at": "2026-09-21T10:00:00Z", "by": "test", "kind": "version-removed",
                                     "versionId": vid})
        after, doc2 = rows_of(base)
        gone = set(assets(before)) - set(assets(after))
        t.ok("version-removed drops everything the version held", bool(gone))
        t.ok("and the rebuild says the folder was ignored",
             any("was removed by an event" in n for n in doc2["notes"]))
        shutil.rmtree(removal)
        t.eq("and without the event the version is part of the item again", assets(rows_of(base)[0]),
             assets(before))

        # ---- original-deleted: the original is not a playback asset any more
        deletion = glob.glob(os.path.join(base, "movies", "*", "*", "events", "*-original-deleted"))[0]
        shutil.move(deletion, aside)
        after, _ = rows_of(base)
        t.ok("original-deleted keeps the deleted original out of the rows",
             len(assets(after, "primary")) == len(assets(before, "primary")) + 1)
        shutil.move(aside, deletion)
        t.eq("and putting it back removes it again", assets(rows_of(base)[0]), assets(before))
        loss = [v for s in rows_of(base)[1]["storage"] for v in s["versions"] if v["permanentLoss"]]
        t.ok("the version it names is canonical and its losses are permanent",
             bool(loss) and loss[0]["canonical"] and "surround" in loss[0]["permanentLoss"])

        # ---- extra-removed: the extra has no row, even while its folder is still there
        featurette = extra_of(base, "featurette")
        movie_dir = os.path.dirname(os.path.dirname(featurette))
        xid = os.path.basename(featurette)
        retirement = write_event(movie_dir, {"schema": "zaentrum.library.event/2", "eventId": str(uuid.uuid4()),
                                             "at": "2026-09-21T10:30:00Z", "by": "test", "kind": "extra-removed",
                                             "extraId": xid})
        _, doc3 = rows_of(base)
        t.ok("extra-removed drops the extra's row, though its folder is still there",
             xid not in [r["id"] for r in doc3["extras"]] and os.path.isdir(featurette)
             and any(f"extra {xid} was removed by an event" in n for n in doc3["notes"]), doc3["notes"])
        shutil.rmtree(retirement)
        t.ok("and without the event it is a row again", xid in [r["id"] for r in rows_of(base)[1]["extras"]])

        # ---- note: nothing
        write_event(item, {"schema": "zaentrum.library.event/2", "eventId": str(uuid.uuid4()),
                           "at": "2026-09-21T11:00:00Z", "by": "test", "kind": "note",
                           "reason": "something no other record holds"})
        t.eq("a note changes nothing", assets(rows_of(base)[0]), assets(before))


# ---------------------------------------------------------------- an orphan, a loss, a missing record
def report_section(text, label):
    """The ids listed under one class of a compare report."""
    out, inside = [], False
    for line in text.splitlines():
        if line.startswith("  ") and not line.startswith("      "):
            inside = line.strip().startswith(label)
        elif inside and line.startswith("      "):
            out.append(line.split()[0])
    return out


# What the catalog's export query prints, verbatim: a catalog with its deletion log, and one from
# before the log existed, where deletedItems is null rather than empty.
EXPORT_SAMPLE = r'''{"exportedAt" : "2026-10-02T14:01:54Z", "items" : [{"id" : "7a9c1e3a-5b7d-4e9f-8a1c-3e5a7c9e1b3d", "type" : "movie", "title" : "Kept Film", "sortTitle" : "kept film", "year" : 2022, "description" : null, "tagline" : null, "rating" : null, "durationMs" : null, "parentId" : null, "seasonNumber" : null, "episodeNumber" : null, "metadataLocked" : false, "createdAt" : "2026-10-02T16:01:20Z", "createdBy" : null, "modifiedAt" : "2026-10-02T16:01:20Z", "externalIds" : [], "genres" : [], "tags" : [], "people" : [], "chapters" : [], "segments" : [], "playbackAssets" : [], "subtitleAssets" : [], "trailers" : [], "artwork" : []}], "deletedItems" : [{"id" : "2c4e6a8b-0d1f-4a3c-9b5d-7e9f1a3c5e7a", "deletedAt" : "2026-10-02T14:01:37Z", "deletedBy" : "anonymous"}, {"id" : "4e6a8c0e-2f3b-4c5e-8d7f-9a1b3c5e7a9c", "deletedAt" : "2026-10-02T14:01:37Z", "deletedBy" : "anonymous"}, {"id" : "6d2f1c3e-8a4b-4c1d-9e2f-0a1b2c3d4e5f", "deletedAt" : "2026-10-02T14:01:37Z", "deletedBy" : "anonymous"}, {"id" : "9b7e5d3c-1a2b-4c3d-8e4f-5a6b7c8d9e0f", "deletedAt" : "2026-10-02T14:01:37Z", "deletedBy" : "anonymous"}]}'''
EXPORT_BEFORE_LOG = r'''{"exportedAt" : "2026-10-02T14:01:03Z", "items" : [], "deletedItems" : null}'''


def outlive(tree, iid):
    """A folder the catalog deleted that outlived its item: the example movie under another id, its
    records as old as the example's."""
    src = glob.glob(os.path.join(EXAMPLES, "movies", "*", "*"))[0]
    d = os.path.join(tree, "movies", iid[:2], iid)
    shutil.copytree(src, d)
    for name in ("item.json", "metadata.json"):
        jwrite(os.path.join(d, name), dict(jload(os.path.join(d, name)), itemId=iid))
    write_sums(d, ["item.json"])
    return d


def test_export_sample(t):
    """The export the catalog's query prints, read by the tools as it is: four ids in the deletion
    log whose folders outlived them, one item kept, and the same tree against a catalog that keeps
    no log yet."""
    sample = json.loads(EXPORT_SAMPLE)
    deleted = sorted(e["id"] for e in sample["deletedItems"])
    kept = sample["items"][0]["id"]
    with tempfile.TemporaryDirectory() as tmp:
        export, before = os.path.join(tmp, "catalog.json"), os.path.join(tmp, "before-the-log.json")
        with open(export, "w") as f:
            f.write(EXPORT_SAMPLE)
        with open(before, "w") as f:
            f.write(EXPORT_BEFORE_LOG)
        empty = os.path.join(tmp, "share")
        os.makedirs(empty)
        out = os.path.join(tmp, "library")
        code, text = run(FROM_CATALOG, "--export", export, "--packages", empty, "--media", empty, "--out", out)
        t.ok("the export query's output becomes a tree",
             code == 0 and os.path.isfile(os.path.join(out, "movies", kept[:2], kept, "item.json")), text)
        for iid in deleted:
            outlive(out, iid)
        code, text = run(REBUILD, out, "--compare", export)
        t.ok("every folder its deletion log names is an orphan, and the item it keeps agrees",
             code == 0 and sorted(report_section(text, "orphan")) == deleted
             and not report_section(text, "lost") and "4 orphan(s) on storage are safe to remove" in text, text)
        code, text = run(REBUILD, out, "--compare", before)
        t.ok("against a catalog from before the log, every folder is lost and none an orphan",
             code == 1 and sorted(report_section(text, "lost")) == sorted(deleted + [kept])
             and not report_section(text, "orphan") and "deletedItems is null" in text, text)

        # the sweep reads the same export; its deletions are recent, so the grace is said, not assumed
        code, text = run(SWEEP, out, "--export", export, "--grace", "0")
        t.eq("the sweep would remove exactly the folders the log names, and keeps the item that exists",
             swept(text, out), sorted(os.path.join(out, "movies", i[:2], i) for i in deleted))
        deleted_at = datetime.datetime(2026, 10, 2, 14, 1, 37, tzinfo=datetime.timezone.utc)
        grace = int((datetime.datetime.now(datetime.timezone.utc) - deleted_at).total_seconds()) + 86400
        for iid in deleted:
            aged(os.path.join(out, "movies", iid[:2], iid), days=grace / 86400 + 1)
        code, text = run(SWEEP, out, "--export", export, "--grace", f"{grace}s")
        t.ok("but not while the deletion is younger than the grace, however old the folder", swept(text, out) == []
             and "deleted 2026-10-02T14:01:37Z, within the grace period" in text, text)
        code, text = run(SWEEP, out, "--export", before, "--grace", "0")
        t.ok("and against a catalog from before the log it sweeps no item folder at all",
             swept(text, out) == [] and "deletedItems is null" in text, text)
        code, text = run(SWEEP, out, "--export", export, "--grace", "0", "--apply")
        t.ok("--apply removes them, and the item the database holds is still there",
             code == 0 and not any(os.path.exists(os.path.join(out, "movies", i[:2], i)) for i in deleted)
             and os.path.isfile(os.path.join(out, "movies", kept[:2], kept, "item.json")), text)

    # the same export as the catalog prints it once it logs people: every entry says what it deleted, and
    # a person no title credits any more is deleted and logged as a person
    typed = json.loads(EXPORT_SAMPLE)
    for e in typed["deletedItems"]:
        e["type"] = "movie"
    person = "5f5f5f5f-0000-4000-8000-0000000000aa"
    typed["deletedItems"].append({"id": person, "type": "person", "deletedAt": "2026-10-02T14:01:37Z", "deletedBy": "catalog"})
    with tempfile.TemporaryDirectory() as tmp:
        export, empty, out = os.path.join(tmp, "catalog.json"), os.path.join(tmp, "share"), os.path.join(tmp, "library")
        jwrite(export, typed)
        os.makedirs(empty)
        run(FROM_CATALOG, "--export", export, "--packages", empty, "--media", empty, "--out", out)
        for iid in deleted:
            outlive(out, iid)
        folder = os.path.join(out, "people", person[:2], person)
        shutil.copytree(sorted(glob.glob(os.path.join(EXAMPLES, "people", "*", "*")))[0], folder)
        jwrite(os.path.join(folder, "person.json"), dict(jload(os.path.join(folder, "person.json")), personId=person))
        code, text = run(REBUILD, out, "--compare", export)
        t.ok("with typed entries the item folders it names are orphans, and so is the folder of the person it names",
             code == 0 and sorted(report_section(text, "orphan")) == deleted and report_section(text, "people: orphan") == [person]
             and "5 orphan(s) on storage are safe to remove" in text, text)
        code, text = run(SWEEP, out, "--export", export, "--grace", "0", "--apply")
        t.ok("and the sweep removes them all, the person's folder with them, and keeps the item that exists",
             code == 0 and "removed 5 target(s)" in text and not os.path.exists(folder)
             and not any(os.path.exists(os.path.join(out, "movies", i[:2], i)) for i in deleted)
             and os.path.isfile(os.path.join(out, "movies", kept[:2], kept, "item.json")), text)


def test_compare(t):
    """The export a tree is compared with starts as the rows the tree rebuilds to, so the two agree;
    each case then changes what the database holds or remembers deleting, and checks the class the
    item lands in, the id listed under it, and the exit code."""
    rows, _ = rows_of(EXAMPLES)
    movie = next(r for r in rows.values() if r["type"] == "movie")
    series = next(r for r in rows.values() if r["type"] == "series")
    episodes = sorted(iid for iid, r in rows.items() if r["type"] == "episode")

    def compare(change, *extra, tree=EXAMPLES):
        export = {"exportedAt": "2026-10-01T12:00:00Z", "items": json.loads(json.dumps(list(rows.values()))),
                  "deletedItems": []}
        change(export)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "catalog.json")
            jwrite(path, export)
            code, text = run(REBUILD, tree, "--compare", path, "--text-language", "en", *extra)
        return code, text

    section = report_section

    def drop(*ids):
        return lambda e: e.update(items=[r for r in e["items"] if r["id"] not in ids])

    def deleted(iid, at="2026-09-30T10:00:00Z", by="librarian"):
        return lambda e: e["deletedItems"].append({"id": iid, "deletedAt": at, "deletedBy": by})

    def both(*changes):
        return lambda e: [c(e) for c in changes]

    code, text = compare(lambda e: None)
    t.ok("a tree and the export it rebuilds to agree", code == 0 and "the tree and the database agree" in text, text)
    t.ok("and its extras are counted, not compared: the catalog has no table for them yet",
         "extras: 4 on storage, not compared" in text, text)

    subtitled = next(r for r in rows.values() if r["subtitleAssets"])

    def subtitle(change):
        """Change the database's row of one subtitle of an item that has one."""
        return lambda e: change(next(r for r in e["items"] if r["id"] == subtitled["id"])["subtitleAssets"][0])

    code, text = compare(subtitle(lambda x: x.update(isDefault=True)), "--ignore-fields", "id,path,hash")
    t.ok("a subtitle default a person chose is behaviour the database keeps, which no record holds: never "
         "compared, whatever --ignore-fields says", code == 0 and "the tree and the database agree" in text, text)
    code, text = compare(subtitle(lambda x: x.update(label="Chosen")), "--ignore-fields", "id,path,hash")
    t.ok("while the rest of a subtitle row is compared",
         code == 1 and "subtitleAssets.label: 1 difference(s)" in text, text)

    covered = next(r for r in rows.values() if r["coveredBy"])
    code, text = compare(lambda e: next(r for r in e["items"] if r["id"] == covered["id"]).update(coveredBy=None))
    t.ok("a covered episode the database does not link to its holder is a difference",
         code == 1 and "coveredBy: 1 difference(s)" in text and f"storage {covered['coveredBy']!r} != database None" in text,
         text)
    code, text = compare(lambda e: [r.pop("coveredBy") for r in e["items"]])
    t.ok("and an export from before the catalog linked covered episodes, which carries no coveredBy, makes no tree "
         "differ", code == 0 and "the tree and the database agree" in text, text)

    code, text = compare(both(drop(movie["id"]), deleted(movie["id"])))
    t.ok("an item the database deleted is an orphan, and an orphan alone does not fail",
         code == 0 and section(text, "orphan") == [movie["id"]] and "1 orphan(s) on storage are safe" in text, text)
    t.ok("the orphan says when and by whom", "deleted 2026-09-30T10:00:00Z by librarian" in text, text)
    director = movie["people"][0]["personId"]
    t.ok("a person only a deleted item credits, whom the log does not name, is unreferenced: kept, and not a failure",
         section(text, "people: unreferenced") == [director] and "only items the database deleted credit them" in text
         and not section(text, "people: lost"), text)

    code, text = compare(drop(movie["id"]))
    t.ok("an item the database neither holds nor deleted is lost, and fails",
         code == 1 and section(text, "lost —") == [movie["id"]] and not section(text, "orphan"), text)

    code, text = compare(both(drop(movie["id"]), deleted(movie["id"]), lambda e: e.update(deletedItems=None)))
    t.ok("a log that is null is no log: nothing can be called deleted, so the item is lost",
         code == 1 and section(text, "lost —") == [movie["id"]] and not section(text, "orphan")
         and "deletedItems is null" in text, text)
    code, text = compare(both(drop(movie["id"]), lambda e: e.pop("deletedItems")))
    t.ok("and so is an export without one",
         code == 1 and section(text, "lost —") == [movie["id"]] and "deletedItems is absent" in text, text)

    code, text = compare(lambda e: e["items"].append(
        {"id": "00000000-0000-4000-8000-0000000000aa", "type": "movie", "title": "Only Here"}))
    t.ok("an item only the database holds is a missing record, and fails",
         code == 1 and section(text, "missing record") == ["00000000-0000-4000-8000-0000000000aa"], text)
    code, text = compare(lambda e: e["items"].append(
        {"id": "00000000-0000-4000-8000-0000000000aa", "type": "movie", "title": "Only Here"}), "--subset")
    t.ok("and in a subset it is listed without failing",
         code == 0 and section(text, "missing record") == ["00000000-0000-4000-8000-0000000000aa"], text)

    code, text = compare(both(drop(series["id"], *episodes), deleted(series["id"])))
    t.ok("deleting a series makes orphans of its episodes too",
         code == 0 and sorted(section(text, "orphan")) == sorted([series["id"], *episodes]), text)
    t.ok("and says the episodes went with their series", "its series was deleted" in text, text)

    code, text = compare(both(drop(movie["id"]), deleted(movie["id"], at="2026-09-01T00:00:00Z")))
    t.ok("a record created after the deletion the log names is lost, not an orphan",
         code == 1 and section(text, "lost —") == [movie["id"]] and "created again since" in text, text)

    # the movie was created on 2026-09-18 and projected on 2026-09-20: a deletion in between explains
    # its item.json, but not the projection written after it
    code, text = compare(both(drop(movie["id"]), deleted(movie["id"], at="2026-09-19T00:00:00Z")))
    t.ok("any record newer than the deletion makes a lost item, not only its creation",
         code == 1 and section(text, "lost —") == [movie["id"]] and "holds a record of 2026-09-20T12:00:00Z" in text, text)

    with tempfile.TemporaryDirectory() as tmp:
        tree = os.path.join(tmp, "library")
        shutil.copytree(EXAMPLES, tree)
        newer = os.path.join(tree, "series", series["id"][:2], series["id"], "episodes", episodes[0], "metadata.json")
        jwrite(newer, dict(jload(newer), asOf="2026-10-01T08:00:00Z"))
        code, text = compare(both(drop(series["id"], *episodes), deleted(series["id"])), tree=tree)
        t.ok("an episode's newer record keeps its deleted series, and every episode in it, from being an orphan",
             code == 1 and sorted(section(text, "lost —")) == sorted([series["id"], *episodes])
             and not section(text, "orphan"), text)

        extra = os.path.join(extra_of(tree, "featurette"), "extra.json")
        jwrite(extra, dict(jload(extra), createdAt="2026-10-01T08:00:00Z"))
        code, text = compare(both(drop(movie["id"]), deleted(movie["id"])), tree=tree)
        t.ok("an extra taken in after the deletion keeps its item from being an orphan",
             code == 1 and section(text, "lost —") == [movie["id"]] and "holds a record of 2026-10-01T08:00:00Z" in text, text)
        jwrite(extra, dict(jload(extra), createdAt="2026-09-19T08:00:00Z"))

        # a projection written before the deletion, of a row the database modified after it: on the
        # database's own clock the row outlived the deletion, so the id was created again since
        projection = os.path.join(tree, "movies", movie["id"][:2], movie["id"], "metadata.json")
        written = jload(projection)
        for what, field, value in (("reflects a row modified", "databaseUpdatedAt", "2026-10-01T08:00:00Z"),
                                   ("holds TMDB data fetched", "sources", {"tmdb": {"fetchedAt": "2026-10-01T08:00:00Z"}})):
            jwrite(projection, dict(written, **{field: value}))
            code, text = compare(both(drop(movie["id"]), deleted(movie["id"])), tree=tree)
            t.ok(f"a projection that {what} after the deletion keeps its item from being an orphan",
                 code == 1 and section(text, "lost —") == [movie["id"]] and "holds a record of 2026-10-01T08:00:00Z" in text,
                 text)
        jwrite(projection, written)

        meta = os.path.join(tree, "movies", movie["id"][:2], movie["id"], "metadata.json")
        doc = jload(meta)
        doc.pop("asOf")
        jwrite(meta, doc)
        code, text = compare(both(drop(movie["id"]), deleted(movie["id"])), tree=tree)
        t.ok("a projection without its asOf still states the moments of the row it reflects",
             code == 0 and section(text, "orphan") == [movie["id"]], text)
        for field in ("databaseUpdatedAt", "sources"):
            doc.pop(field)
        jwrite(meta, doc)
        code, text = compare(both(drop(movie["id"]), deleted(movie["id"])), tree=tree)
        t.ok("a record that states no moment counts with its file's time, which is after the deletion",
             code == 1 and section(text, "lost —") == [movie["id"]], text)
        old = datetime.datetime(2026, 9, 1, tzinfo=datetime.timezone.utc).timestamp()
        os.utime(meta, (old, old))
        code, text = compare(both(drop(movie["id"]), deleted(movie["id"])), tree=tree)
        t.ok("and before it once the file is older than the deletion",
             code == 0 and section(text, "orphan") == [movie["id"]], text)

    code, text = compare(both(drop(movie["id"]), lambda e: e["deletedItems"].append({"id": movie["id"]})))
    t.ok("a log entry with no deletedAt proves nothing, so the item is lost",
         code == 1 and section(text, "lost —") == [movie["id"]] and "no deletedAt" in text, text)

    code, text = compare(deleted(movie["id"]))
    t.ok("an item the database deleted and holds again is live, and compared as one",
         code == 0 and "held by the database again: 1" in text and not section(text, "orphan"), text)

    # ---- people: the database's people are its list when it has one, else whom its items credit
    lead = series["people"][0]["personId"]
    creator = next(c["personId"] for c in series["people"] if c["role"] == "creator")

    def uncredit(iid):
        return lambda e: [r.update(people=[]) for r in e["items"] if r["id"] == iid]

    code, text = compare(drop(movie["id"]))
    t.ok("a person a lost item credits is lost with it",
         code == 1 and section(text, "people: lost") == [director] and "which is lost" in text, text)

    code, text = compare(both(drop(movie["id"]), lambda e: e.update(deletedItems=None)))
    t.ok("with no log the item is lost, and so is the person it credits",
         code == 1 and section(text, "people: lost") == [director], text)

    with tempfile.TemporaryDirectory() as tmp:
        tree = os.path.join(tmp, "library")
        shutil.copytree(EXAMPLES, tree)
        stray = "00000000-0000-4000-8000-0000000000dd"
        p = os.path.join(tree, "people", stray[:2], stray)
        shutil.copytree(os.path.join(tree, "people", director[:2], director), p)
        doc = jload(os.path.join(p, "person.json"))
        jwrite(os.path.join(p, "person.json"), dict(doc, personId=stray, name="Credited By Nothing"))
        code, text = compare(lambda e: None, tree=tree)
        t.ok("a person nothing on storage credits, whom the database does not hold, is unreferenced",
             code == 0 and section(text, "people: unreferenced") == [stray]
             and "nothing on storage credits them" in text, text)

        # ---- the catalog deletes a person no title credits any more, and logs them as a person
        def logged(pid, at="2026-09-30T10:00:00Z", **entry):
            return lambda e: e["deletedItems"].append({"id": pid, "type": "person", "deletedAt": at,
                                                       "deletedBy": "catalog", **entry})

        code, text = compare(logged(stray), tree=tree)
        t.ok("a person the log names as deleted, nothing in their folder newer, is an orphan, and an orphan alone "
             "does not fail", code == 0 and section(text, "people: orphan") == [stray]
             and not section(text, "people: unreferenced") and "deleted 2026-09-30T10:00:00Z by catalog" in text
             and "1 orphan(s) on storage are safe to remove" in text, text)
        code, text = compare(lambda e: e["deletedItems"].append({"id": stray, "deletedAt": "2026-09-30T10:00:00Z"}),
                             tree=tree)
        t.ok("an entry without a type is an item's, as in an export from before people were logged: the person "
             "stays unreferenced, and the compare says why", code == 0 and section(text, "people: unreferenced") == [stray]
             and not section(text, "people: orphan") and "no person on storage can be called deleted" in text, text)
        for kind in ("movie", "collection"):
            code, text = compare(logged(stray, type=kind), tree=tree)
            t.ok(f"and one of the type {kind!r} names no person either",
                 code == 0 and section(text, "people: unreferenced") == [stray] and not section(text, "people: orphan"), text)
        code, text = compare(logged(stray, at="2026-09-20T11:00:00Z"), tree=tree)
        t.ok("a person whose record is newer than the deletion was created again since: not an orphan",
             code == 0 and section(text, "people: unreferenced") == [stray] and "created again since" in text, text)
        code, text = compare(logged(stray, deletedAt=None), tree=tree)
        t.ok("and a log entry with no deletedAt proves nothing", code == 0 and section(text, "people: unreferenced") == [stray]
             and "no deletedAt to prove it" in text, text)

        record = jload(os.path.join(p, "person.json"))
        jwrite(os.path.join(p, "person.json"), dict(record, images=[dict(record["images"][0], fetchedAt="2026-10-01T08:00:00Z",
                                                                          origin={"source": "manual"})]))
        code, text = compare(logged(stray), tree=tree)
        t.ok("a portrait fetched after the deletion keeps the person from being an orphan",
             code == 0 and section(text, "people: unreferenced") == [stray] and "holds a record of 2026-10-01T08:00:00Z" in text,
             text)
        jwrite(os.path.join(p, "person.json"), record)
        dropped = os.path.join(p, hashlib.sha256(b"\xff\xd8 dropped portrait").hexdigest() + ".jpg")
        with open(dropped, "wb") as f:
            f.write(b"\xff\xd8 dropped portrait")
        code, text = compare(logged(stray), tree=tree)
        t.ok("and so does a file the record says nothing of, by its time, written after the deletion",
             code == 0 and section(text, "people: unreferenced") == [stray] and "created again since" in text, text)
        old = datetime.datetime(2026, 9, 1, tzinfo=datetime.timezone.utc).timestamp()
        os.utime(dropped, (old, old))
        code, text = compare(logged(stray), tree=tree)
        t.ok("but not once that file is older than the deletion", code == 0 and section(text, "people: orphan") == [stray], text)

    code, text = compare(both(uncredit(movie["id"]), logged(director)))
    t.ok("an item record on storage that still credits a deleted person is a note: they are an orphan all the same, "
         "and not one safe to remove", section(text, "people: orphan") == [director]
         and f"a note: {movie['id']} on storage still credits them" in text
         and "orphan(s) on storage are safe to remove" not in text, text)
    code, text = compare(logged(lead))
    t.ok("a deleted person the database holds again is present, and compared as the live person they are",
         code == 0 and not section(text, "people: orphan") and "people in the deletion log but held by the database again: 1" in text,
         text)
    typed = lambda kind: lambda e: e["deletedItems"].append({"id": movie["id"], "type": kind, "deletedAt": "2026-09-30T10:00:00Z",
                                                            "deletedBy": "librarian"})
    code, text = compare(both(drop(movie["id"]), typed("movie")))
    t.ok("an entry of an item's own type is the item's", code == 0 and section(text, "orphan") == [movie["id"]], text)
    for kind in ("person", "collection"):
        code, text = compare(both(drop(movie["id"]), typed(kind)))
        t.ok(f"and one of the type {kind!r} proves nothing of an item, which is lost",
             code == 1 and section(text, "lost —") == [movie["id"]] and not section(text, "orphan"), text)

    code, text = compare(uncredit(series["id"]))
    t.ok("a person on storage that an item the database holds still credits is lost",
         code == 1 and section(text, "people: lost") == sorted([lead, creator]) and "which the database holds" in text, text)

    code, text = compare(lambda e: e.update(people=[{"id": director, "name": "Ian Hubert"},
                                                    {"id": creator, "name": "Noor Example"}]))
    t.ok("with a people list, a person it does not hold is judged by the list",
         code == 1 and section(text, "people: lost") == [lead], text)

    code, text = compare(lambda e: e["items"][0].setdefault("people", []).append(
        {"personId": "00000000-0000-4000-8000-0000000000bb", "name": "Nobody Recorded", "role": "actor"}))
    t.ok("a person the database credits who has no record on storage is a missing record",
         code == 1 and section(text, "people: missing record") == ["00000000-0000-4000-8000-0000000000bb"], text)

    code, text = compare(lambda e: e.update(people=[
        {"id": director, "name": "Ian Hubert", "biography": "Someone else's life."},
        {"id": lead, "name": "Mara Example"}, {"id": creator, "name": "Noor Example"}]), "--text-language", "en")
    t.ok("a field the people list carries is compared", code == 1 and "people.biography: 1 difference" in text, text)
    code, text = compare(lambda e: e.update(people=[{"id": director, "name": "Ian Hubert"},
                                                    {"id": lead, "name": "Mara Example"},
                                                    {"id": creator, "name": "Noor Example"}]))
    t.ok("and a field it does not carry is not", code == 0 and "the tree and the database agree" in text, text)

    listed = lambda e: e.update(people=[{"id": director, "name": "Ian Hubert"}, {"id": lead, "name": "Mara Example"},
                                        {"id": creator, "name": "Noor Example"},
                                        {"id": "00000000-0000-4000-8000-0000000000cc", "name": "Only Listed"}])
    code, text = compare(listed)
    t.ok("a person the people list holds who is not on storage is a missing record",
         code == 1 and section(text, "people: missing record") == ["00000000-0000-4000-8000-0000000000cc"], text)
    code, text = compare(listed, "--subset")
    t.ok("and in a subset, when no item on storage credits them, it is listed without failing",
         code == 0 and section(text, "people: missing record") == ["00000000-0000-4000-8000-0000000000cc"]
         and "1 of which a subset is expected to lack" in text, text)

    # ---- how fresh a projection is: the state of its row it reflects, against the row the export holds
    def modified(iid, at):
        return lambda e: [r.update(modifiedAt=at) for r in e["items"] if r["id"] == iid]

    code, text = compare(modified(movie["id"], "2026-09-25T00:00:00Z"))
    t.ok("a projection whose row the database modified since is stale, and fails though no field it holds differs",
         code == 1 and section(text, "stale projection") == [movie["id"]] and "1 difference(s)" in text
         and "project it again, never edit the file" in text, text)
    code, text = compare(both(modified(movie["id"], "2026-09-25T00:00:00Z"),
                              lambda e: [r.update(title="Tears of Steel (Remastered)") for r in e["items"] if r["id"] == movie["id"]]))
    t.ok("and the differences a stale projection explains are marked as its",
         code == 1 and f"{movie['id']}: storage 'Tears of Steel' != database 'Tears of Steel (Remastered)' — stale projection"
         in text, text)
    code, text = compare(modified(movie["id"], "2026-09-01T00:00:00Z"))
    t.ok("a projection that reflects a later state of its row than the export holds is ahead of it, and fails",
         code == 1 and section(text, "projection ahead of the database") == [movie["id"]]
         and not section(text, "stale projection"), text)
    code, text = compare(modified(movie["id"], "2026-09-20T11:55:00.400Z"))
    t.ok("to the second: a row modified within the second its projection reflects is not stale",
         code == 0 and not section(text, "stale projection"), text)
    code, text = compare(modified(movie["id"], "2026-09-20T13:55:00+02:00"))
    t.ok("and one moment written in another zone is the same moment", code == 0, text)
    code, text = compare(lambda e: [r.pop("modifiedAt") for r in e["items"]])
    t.ok("a row the export gives no modification time is not judged",
         code == 0 and "the tree and the database agree" in text, text)
    code, text = compare(modified(movie["id"], "2026-09-25T00:00:00Z"), "--ignore-fields", "id,path,hash,modifiedAt")
    t.ok("and with modifiedAt ignored, no projection is", code == 0 and not section(text, "stale projection"), text)
    with tempfile.TemporaryDirectory() as tmp:
        tree = os.path.join(tmp, "library")
        shutil.copytree(EXAMPLES, tree)
        meta = os.path.join(tree, "movies", movie["id"][:2], movie["id"], "metadata.json")
        jwrite(meta, {k: v for k, v in jload(meta).items() if k != "databaseUpdatedAt"})
        code, text = compare(modified(movie["id"], "2026-09-25T00:00:00Z"), tree=tree)
        t.ok("a projection that does not say which state it reflects is of unknown freshness: said, and not a failure",
             code == 0 and "freshness unknown: 1 projection(s)" in text and not section(text, "stale projection"), text)

    _, built = rows_of(EXAMPLES)

    def people_list(**lead_changes):
        """The export's people list as the tree's own people rows, the lead's changed."""
        return lambda e: e.update(people=[dict(p, **(lead_changes if p["id"] == lead else {}))
                                          for p in json.loads(json.dumps(built["people"]))])

    code, text = compare(people_list())
    t.ok("an export whose people list is the tree's own people agrees: every field of it compared, portraits included",
         code == 0 and "the tree and the database agree" in text, text)
    code, text = compare(people_list(modifiedAt="2026-09-25T00:00:00Z"))
    t.ok("a person whose row changed after their projection is a stale projection, and fails",
         code == 1 and section(text, "people: stale projection") == [lead] and not section(text, "stale projection"), text)

    code, text = compare(lambda e: [r.update(tmdbFetchedAt="2026-09-21T00:00:00Z") for r in e["items"] if r["id"] == movie["id"]])
    t.ok("an item row's TMDB freshness is compared where the export carries it",
         code == 1 and "tmdbFetchedAt: 1 difference(s)" in text, text)
    code, text = compare(lambda e: [[r.pop(k) for k in ("tmdbFetchedAt", "tmdbChangedAt")] for r in e["items"]])
    t.ok("and an export that does not carry it, as the catalog's items do not yet, makes no difference", code == 0, text)
    code, text = compare(lambda e: [a.update(isPrimary=False) for r in e["items"] if r["id"] == movie["id"] for a in r["artwork"]])
    t.ok("an image row's primary flag is compared where the export carries it",
         code == 1 and "artwork.isPrimary: 1 difference(s)" in text, text)
    code, text = compare(lambda e: [[a.pop(k) for k in ("isPrimary", "sourcePath", "width", "height")]
                                    for r in e["items"] for a in r["artwork"]])
    t.ok("and an export from before image rows carried it, its dimensions or its TMDB path makes no difference",
         code == 0, text)


# ---------------------------------------------------------------- v1 -> v2
def test_from_v1(t):
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "v1")
        shutil.copytree(V1_EXAMPLES, src)
        v1_manifests = {os.path.basename(os.path.dirname(p)): jload(p)
                        for p in glob.glob(os.path.join(src, "**", "manifest.json"), recursive=True)}
        code, text = run(FROM_V1, "--in", src, "--in-place")
        t.ok("the v1 example tree converts", code == 0 and "converted" in text, text)
        t.ok("no manifest.json is left", not glob.glob(os.path.join(src, "**", "manifest.json"), recursive=True))
        t.ok("every item folder has an item.json",
             len(glob.glob(os.path.join(src, "**", "item.json"), recursive=True)) == len(v1_manifests))

        if have_jsonschema():
            code, text = run(VALIDATOR, src)
            t.ok("the records keep the names v1 gave the files, which the validator names the tool for",
                 code == 1 and "is a name the original arrived under" in text and "library-v2-neutral-names.py" in text,
                 text)
        code, text = run(NEUTRAL, src, "--apply")
        t.ok("library-v2-neutral-names.py gives the tree the names the library gives its files", code == 0, text)
        if have_jsonschema():
            code, text = run(VALIDATOR, "--check-checksums", src)
            t.ok("the result passes validate-library-v2.py", code == 0 and text.strip().endswith("OK"), text)
        else:
            t.skip("the result passes validate-library-v2.py", "jsonschema is not importable here")
        code, text = run(MEDIA_CHECK, "--checksums", src)
        t.ok("the result passes the media check", code == 0, text)

        rows, _ = rows_of(src)
        for iid, man in v1_manifests.items():
            row = rows.get(iid)
            t.ok(f"{man['title']} survived the split", row is not None and row["title"] == man["title"])
            if row and man.get("durationMs"):
                packaged = [a for a in row["playbackAssets"] if a["kind"] == "packaged"]
                t.ok(f"{man['title']} kept the duration of the package v1 held",
                     any(a["durationMs"] == man["durationMs"] for a in packaged))
        episode = next(r for r in rows.values() if r["type"] == "episode")
        t.ok("the episode kept both of its versions",
             len([a for a in episode["playbackAssets"] if a["kind"] == "packaged"]) == 2)
        item = jload(glob.glob(os.path.join(src, "movies", "*", "*", "item.json"))[0])
        t.ok("the item records where it came from",
             (item.get("provenance") or {}).get("legacyItemId") == item["itemId"])
        meta = jload(glob.glob(os.path.join(src, "movies", "*", "*", "metadata.json"))[0])
        t.ok("the images are named by their own content",
             all(i["file"].split(".")[0] == i["sha256"].split(":")[1] for i in meta["images"]))
        t.ok("a backdrop v1 held as the poster's bytes is still a backdrop, sharing the poster's one file",
             {i["kind"] for i in meta["images"]} >= {"poster", "backdrop"}
             and len({i["file"] for i in meta["images"] if i["kind"] in ("poster", "backdrop")}) == 1
             and len(os.listdir(os.path.join(os.path.dirname(glob.glob(os.path.join(src, "movies", "*", "*",
                                                                                     "metadata.json"))[0]),
                                             "metadata"))) == len({i["file"] for i in meta["images"]}),
             meta["images"])
        t.ok("the decisions the database held are projected, not lost",
             meta["library"]["primaryVersionId"] and meta["library"]["match"]["status"] == "matched")

        before = tree_files(src)
        code, text = run(FROM_V1, "--in", src, "--in-place")
        t.ok("a second run changes nothing", code == 0 and tree_files(src) == before, text)
        code, text = run(NEUTRAL, src, "--apply")
        t.ok("and neither does the tool a second time", code == 0 and "nothing to do" in text and tree_files(src) == before,
             text)

    # a deleted original becomes an event
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "v1")
        shutil.copytree(V1_EXAMPLES, src)
        mp = glob.glob(os.path.join(src, "movies", "*", "*", "manifest.json"))[0]
        man = jload(mp)
        source = man["versions"][0]["sources"][0]
        source["state"] = "deleted"
        source["file"]["deletedAt"] = "2026-09-15T08:00:00Z"
        source["file"]["path"] = None
        jwrite(mp, man)
        os.unlink(os.path.join(os.path.dirname(mp), source["file"]["name"]))
        code, text = run(FROM_V1, "--in", src, "--in-place")
        run(NEUTRAL, src, "--apply")
        events = glob.glob(os.path.join(src, "movies", "*", "*", "events", "*-original-deleted", "event.json"))
        t.ok("a v1 source that said it was deleted becomes an original-deleted event",
             code == 0 and len(events) == 1, text)
        if events:
            ev = jload(events[0])
            t.ok("the event accepts what the records say the package failed to carry",
                 ev["accepted"] and all(":" in x or x[0].islower() for x in ev["accepted"]))
            t.eq("and its folder is named for the moment it happened, its id and its kind",
                 os.path.basename(os.path.dirname(events[0])),
                 f"20260915T080000Z-{ev['eventId'][:8]}-original-deleted")
        if have_jsonschema():
            code, text = run(VALIDATOR, "--check-media", src)
            t.ok("a tree with a deletion still validates", code == 0, text)


# ---------------------------------------------------------------- catalog -> v2
TRAILER = "Example Film - Trailer.mp4"


def fake_export(root, with_original=True, trailer=False):
    """A catalog export, a package store and a source file, as the demo share holds them; with
    trailer, the trailer link was downloaded too, to media/trailers/."""
    media, packages = os.path.join(root, "media"), os.path.join(root, "packages")
    iid = "11111111-2222-4333-8444-555555555555"
    name = "Example Film (2024).mkv"
    os.makedirs(media, exist_ok=True)
    original = b"an original that no probe here can read\n" * 100
    if with_original:
        with open(os.path.join(media, name), "wb") as f:
            f.write(original)
    if trailer:
        os.makedirs(os.path.join(media, "trailers"), exist_ok=True)
        with open(os.path.join(media, "trailers", TRAILER), "wb") as f:
            f.write(b"a downloaded trailer that no probe here can read\n" * 20)
    pkg = os.path.join(packages, "movies", iid[:2], iid)
    for rel, body in (("hls/master.m3u8", "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=4200000\nv0/playlist.m3u8\n"),
                      ("hls/v0/playlist.m3u8", "#EXTM3U\n#EXT-X-ENDLIST\n"),
                      ("hls/a0/playlist.m3u8", "#EXTM3U\n#EXT-X-ENDLIST\n"),
                      ("subs/0.vtt", "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nhello\n"),
                      (".complete", "2026-09-01T10:00:00+00:00\n")):
        os.makedirs(os.path.dirname(os.path.join(pkg, rel)), exist_ok=True)
        with open(os.path.join(pkg, rel), "w") as f:
            f.write(body)
    jwrite(os.path.join(pkg, "manifest.json"), {
        "version": 2, "itemId": iid, "type": "movie", "title": "Example Film", "year": 2024,
        "tmdbId": "1234", "durationMs": 600000, "packagedAt": "2026-09-01T10:00:00+00:00",
        "packager": "shaka-packager version 3.0.4",
        "renditions": {"video": [{"id": "v0", "dir": "hls/v0", "codec": "hev1.1.6.L120.B0", "width": 1920,
                                  "height": 800, "bitrateBps": 4000000, "hdr": False, "frameRate": "24/1",
                                  "segments": 100, "targetDuration": 6}],
                       "audio": [{"id": "a0", "dir": "hls/a0", "codec": "mp4a.40.2", "language": "eng",
                                  "title": "", "default": True, "channels": 2, "bitrateBps": 192000,
                                  "segments": 100, "visible": True}]},
        "subtitles": [{"id": "sub0", "path": "subs/0.vtt", "language": "ger", "title": "", "default": True,
                       "forced": False, "format": "webvtt", "visible": True}]})
    png = (b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + (2).to_bytes(4, "big") +
           (3).to_bytes(4, "big") + b"\x08\x06\x00\x00\x00" + b"\x00" * 4)
    export = {"exportedAt": "2026-09-21T17:00:00Z", "items": [{
        "id": iid, "type": "movie", "title": "Example Film", "sortTitle": "example film", "year": 2024,
        "description": "A film that stands in for a real one.", "tagline": "Only an example.",
        "rating": 7.5, "durationMs": 600000, "parentId": None, "seasonNumber": None,
        "episodeNumber": None, "metadataLocked": False, "createdAt": "2026-07-01T09:00:00Z",
        "createdBy": None, "modifiedAt": "2026-08-01T09:00:00Z",
        "externalIds": [{"source": "tmdb", "externalId": "1234"}, {"source": "imdb", "externalId": "tt9999999"}],
        "genres": ["Drama"], "tags": ["example"],
        "people": [{"personId": "99999999-8888-4777-8666-555555555555", "name": "A Director", "role": "director"}],
        "chapters": [{"startMs": 0, "endMs": 300000, "title": "One", "ordinal": 1},
                     {"startMs": 300000, "endMs": 600000, "title": "Two", "ordinal": 2}],
        "segments": [{"kind": "credits", "startMs": 580000, "endMs": 600000, "source": "blackframe",
                      "confidence": 0.8, "label": "black 20.0s"}],
        "playbackAssets": [
            {"id": "a1", "path": f"/var/lib/katalog/media/{name}", "kind": "primary", "codec": None,
             "resolution": None, "bitrateKbps": None, "sizeBytes": len(original), "hash": None, "isPrimary": True,
             "audioCodec": None, "audioLanguage": None, "audioChannels": None, "audioBitrateKbps": None,
             "audioTrackCount": None, "subtitleTrackCount": None, "durationMs": None},
            {"id": "a2", "path": f"/var/lib/katalog/packages/movies/{iid[:2]}/{iid}/manifest.json",
             "kind": "packaged", "codec": "hev1.1.6.L120.B0", "resolution": "1920x800", "bitrateKbps": 4200,
             "sizeBytes": 0, "hash": None, "isPrimary": False, "audioCodec": "mp4a.40.2",
             "audioLanguage": "eng", "audioChannels": 2, "audioBitrateKbps": 192, "audioTrackCount": 1,
             "subtitleTrackCount": 1, "durationMs": 600000}],
        "subtitleAssets": [{"id": "s1", "path": f"/var/lib/katalog/packages/movies/{iid[:2]}/{iid}/subs/0.vtt",
                            "format": "webvtt", "language": "ger", "label": "", "isDefault": True}],
        "trailers": [{"source": "tmdb", "site": "YouTube", "externalId": "abc123",
                      "url": "https://example.org/abc123", "title": "Trailer", "durationSec": 90,
                      "localPath": f"/var/lib/katalog/media/trailers/{TRAILER}" if trailer else None}],
        "artwork": [{"kind": "poster", "contentType": "image/png", "fetchedAt": "2026-07-01T09:05:00Z",
                     "base64": base64.b64encode(png).decode()}]}]}
    path = os.path.join(root, "catalog.json")
    jwrite(path, export)
    return path, media, packages, iid, len(original)


def test_images_of_several_kinds(t):
    """An artwork row is an image of its kind, so rows of several kinds with one image's bytes — a backdrop
    that is the poster, as the catalog makes an episode's — are an entry of each kind sharing the one
    file, listed by kind whatever order the export lists them in, and the rebuild gives every kind back."""
    ignore = "id,path,hash,codec,resolution,bitrateKbps,durationMs,sizeBytes"
    for kinds in (("poster", "backdrop"), ("thumb", "poster", "backdrop")):
        with tempfile.TemporaryDirectory() as tmp:
            export, media, packages, iid, _ = fake_export(os.path.join(tmp, "share"))
            e = jload(export)
            art = e["items"][0]["artwork"][0]
            e["items"][0]["artwork"] = [dict(art, kind=k) for k in kinds]
            jwrite(export, e)
            out = os.path.join(tmp, "library")
            code, text = run(FROM_CATALOG, "--export", export, "--packages", packages, "--media", media, "--out", out)
            d = os.path.join(out, "movies", iid[:2], iid)
            images = jload(os.path.join(d, "metadata.json"))["images"]
            named = f"{len(kinds)} kinds of one image's bytes"
            t.eq(f"{named} are an entry of each kind, listed by kind whatever order the export lists them in",
                 [i["kind"] for i in images], sorted(kinds))
            digest = hashlib.sha256(base64.b64decode(art["base64"])).hexdigest()
            t.ok(f"{named} share the one file, which metadata/ holds once, and the run has nothing to say of it",
                 code == 0 and {i["file"] for i in images} == {digest + ".png"}
                 and os.listdir(os.path.join(d, "metadata")) == [digest + ".png"] and "byte-identical" not in text,
                 text)
            if have_jsonschema():
                code, vtext = run(VALIDATOR, "--check-media", out)
                t.ok(f"{named}: the validator accepts them", code == 0 and vtext.strip().endswith("OK"), vtext)
            else:
                t.skip(f"{named}: the validator accepts them", "jsonschema is not importable here")
            code, mtext = run(MEDIA_CHECK, out)
            t.ok(f"{named}: and the media check", code == 0 and mtext.strip().endswith("OK"), mtext)
            rows, _ = rows_of(out, "--text-language", "und")
            t.eq(f"{named}: the rebuild gives every kind back, each of those bytes",
                 sorted((a["kind"], a["sha256"]) for a in rows[iid]["artwork"]),
                 sorted((k, "sha256:" + digest) for k in kinds))
            code, ctext = run(REBUILD, out, "--compare", export, "--text-language", "und", "--ignore-fields", ignore)
            t.ok(f"{named}: and --compare of the title is clean", code == 0 and "the tree and the database agree" in ctext,
                 ctext)
            if len(kinds) == 2:
                mp = os.path.join(d, "metadata.json")
                jwrite(mp, dict(jload(mp), images=[i for i in images if i["kind"] == "poster"]))
                code, ctext = run(REBUILD, out, "--compare", export, "--text-language", "und", "--ignore-fields",
                                  ignore)
                t.ok("a tree that lists the backdrop only as the poster, as one written before did, has lost the "
                     "backdrop, and --compare says so",
                     code == 1 and "artwork: 1 difference(s)" in ctext and "storage 'missing'" in ctext
                     and "'kind': 'backdrop'" in ctext, ctext)


def test_from_catalog(t):
    with tempfile.TemporaryDirectory() as tmp:
        share = os.path.join(tmp, "share")
        export, media, packages, iid, size = fake_export(share)
        out = os.path.join(tmp, "library")
        code, text = run(FROM_CATALOG, "--export", export, "--packages", packages, "--media", media,
                         "--out", out)
        t.ok("an export, a package store and an original become a tree", code == 0, text)
        if have_jsonschema():
            code, text = run(VALIDATOR, "--check-checksums", out)
            t.ok("the tree passes validate-library-v2.py", code == 0 and text.strip().endswith("OK"), text)
        else:
            t.skip("the tree passes validate-library-v2.py", "jsonschema is not importable here")
        code, text = run(MEDIA_CHECK, "--checksums", out)
        t.ok("the tree passes the media check", code == 0, text)

        code, text = run(REBUILD, out, "--compare", export, "--text-language", "und",
                         "--ignore-fields", "id,path,hash,codec,resolution,bitrateKbps,"
                                            "durationMs,sizeBytes")
        t.ok("the rebuilt rows agree with the export they came from — its subtitle's default, behaviour the "
             "database keeps, not compared, as the compare says",
             code == 0 and "the tree and the database agree" in text
             and "(ignoring bitrateKbps, codec, durationMs, hash, id, isDefault, path, resolution, sizeBytes)" in text,
             text)

        rows, _ = rows_of(out, "--text-language", "und")
        row = rows[iid]
        t.eq("the texts come back", (row["title"], row["tagline"], row["description"][:5]),
             ("Example Film", "Only an example.", "A fil"))
        t.eq("the chapters come back on the version's timeline", [c["startMs"] for c in row["chapters"]],
             [0, 300000])
        t.eq("the segments keep the detector that found them",
             [(s["kind"], s["source"]) for s in row["segments"]], [("credits", "blackframe")])
        t.eq("the package's subtitle is a subtitle asset again, and no default — which subtitle a viewer gets is "
             "behaviour, which no record holds, not the default its playlist says",
             [(s["language"], s["isDefault"]) for s in row["subtitleAssets"]], [("ger", False)])
        t.eq("the image comes back with the hash of its own bytes",
             [a["sha256"] == "sha256:" + hashlib.sha256(
                 open(os.path.join(out, "movies", iid[:2], iid, "metadata",
                                   a["file"].split(os.sep)[-1]), "rb").read()).hexdigest()
              for a in row["artwork"]], [True])

        source = jload(glob.glob(os.path.join(out, "movies", "*", "*", "sources", "*", "source.json"))[0])
        t.ok("a source that could not be probed says so rather than guessing",
             source["probe"]["at"] is not None or "not available" in (source["probe"].get("note") or ""))
        t.ok("and it still carries the file's size, mtime and fingerprint",
             source["file"]["sizeBytes"] == size and source["file"]["mtime"].endswith("Z")
             and source["file"]["fixity"]["qh1"].startswith("sha256:"))
        version = jload(glob.glob(os.path.join(out, "movies", "*", "*", "versions", "*", "version.json"))[0])
        vp = glob.glob(os.path.join(out, "movies", "*", "*", "versions", "*"))[0]
        t.eq("the version keeps the original it was given, under the name the library gives it, which its source "
             "record names too", (version["originalFiles"], source["file"]["name"], "origin" in source,
                                  os.path.getsize(os.path.join(vp, "original.mkv"))),
             (["original.mkv"], "original.mkv", False, size))
        package = jload(glob.glob(os.path.join(out, "movies", "*", "*", "versions", "*", "package.json"))[0])
        t.eq("the package is derived while the original is beside it", package["role"], "derived")
        t.eq("its peak bandwidth is the one the master playlist advertises",
             package["peakBandwidthBps"], 4200000)
        t.eq("every playback field crossed over", (package["durationMs"], package["renditions"]["video"][0]["codec"],
                                                   package["renditions"]["audio"][0]["language"],
                                                   package["subtitles"][0]["path"]),
             (600000, "hev1.1.6.L120.B0", "eng", "subs/0.vtt"))

        # two runs, the same bytes
        again = os.path.join(tmp, "library-again")
        code, text = run(FROM_CATALOG, "--export", export, "--packages", packages, "--media", media,
                         "--out", again)
        t.ok("a second run into a fresh folder writes the same bytes",
             code == 0 and tree_files(out) == tree_files(again), text)

    # --media-mode none leaves the bytes where they are and the package is the only copy
    with tempfile.TemporaryDirectory() as tmp:
        share = os.path.join(tmp, "share")
        export, media, packages, iid, size = fake_export(share)
        out = os.path.join(tmp, "library")
        code, text = run(FROM_CATALOG, "--export", export, "--packages", packages, "--media", media,
                         "--out", out, "--media-mode", "none")
        package = jload(glob.glob(os.path.join(out, "movies", "*", "*", "versions", "*", "package.json"))[0])
        t.ok("with the original left where it is, the package is canonical",
             code == 0 and package["role"] == "canonical", text)
        t.ok("and the original is still on the share", os.path.isfile(os.path.join(media, os.listdir(media)[0])))

    # an item whose original is gone keeps its texts and loses its versions
    with tempfile.TemporaryDirectory() as tmp:
        share = os.path.join(tmp, "share")
        export, media, packages, iid, size = fake_export(share, with_original=False)
        out = os.path.join(tmp, "library")
        code, text = run(FROM_CATALOG, "--export", export, "--packages", packages, "--media", media,
                         "--out", out)
        d = os.path.join(out, "movies", iid[:2], iid)
        t.ok("an item whose original cannot be read keeps its identity and its texts",
             os.path.isfile(os.path.join(d, "item.json")) and os.path.isfile(os.path.join(d, "metadata.json")))
        t.ok("and says why it has no version", "no original could be read" in text, text)
        t.ok("and writes no version folder", not os.path.isdir(os.path.join(d, "versions")))

    # a dry run touches nothing
    with tempfile.TemporaryDirectory() as tmp:
        share = os.path.join(tmp, "share")
        export, media, packages, iid, size = fake_export(share)
        out = os.path.join(tmp, "library")
        before = tree_files(share)
        code, text = run(FROM_CATALOG, "--export", export, "--packages", packages, "--media", media,
                         "--out", out, "--dry-run")
        t.ok("a dry run writes nothing and moves nothing",
             code == 0 and not os.path.exists(out) and tree_files(share) == before, text)


def fake_ffprobe(bin_dir):
    """An ffprobe that reports one 1080p video and one English stereo audio stream for any file, so a
    probed record can be written where no real probe could read the placeholder bytes."""
    os.makedirs(bin_dir, exist_ok=True)
    probe = {"format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "90.000", "bit_rate": "4000000", "tags": {}},
             "streams": [{"index": 0, "codec_type": "video", "codec_name": "h264", "profile": "High", "width": 1920,
                          "height": 1080, "pix_fmt": "yuv420p", "avg_frame_rate": "24/1", "field_order": "progressive",
                          "disposition": {"default": 1}, "tags": {}},
                         {"index": 1, "codec_type": "audio", "codec_name": "aac", "channels": 2,
                          "channel_layout": "stereo", "sample_rate": "48000", "disposition": {"default": 1},
                          "tags": {"language": "eng"}}],
             "chapters": []}
    path = os.path.join(bin_dir, "ffprobe")
    with open(path, "w") as f:
        f.write(f"#!{sys.executable}\nimport sys\n"
                f"print('ffprobe version 9.9-test') if '-version' in sys.argv else print({json.dumps(json.dumps(probe))})\n")
    os.chmod(path, 0o755)
    return dict(os.environ, PATH=bin_dir + os.pathsep + os.environ.get("PATH", ""))


def run_with(env, *args):
    r = subprocess.run([sys.executable, *[str(a) for a in args]], capture_output=True, text=True, env=env)
    return r.returncode, r.stdout + r.stderr


def test_from_catalog_extras(t):
    """A trailer the catalog downloaded is a local file of its own: an extra of kind trailer beside its
    movie or series, while its link stays a link."""
    with tempfile.TemporaryDirectory() as tmp:
        share = os.path.join(tmp, "share")
        export, media, packages, iid, _ = fake_export(share, trailer=True)
        out = os.path.join(tmp, "library")
        code, text = run(FROM_CATALOG, "--export", export, "--packages", packages, "--media", media, "--out", out)
        d = os.path.join(out, "movies", iid[:2], iid)
        found = glob.glob(os.path.join(d, "extras", "*", "extra.json"))
        x = jload(found[0]) if found else {}
        xp = os.path.dirname(found[0]) if found else tmp
        t.ok("a downloaded trailer becomes an extra of kind trailer beside its movie",
             code == 0 and len(found) == 1 and x.get("kind") == "trailer", text)
        t.eq("which keeps the file under the name the library gives an original, with the title of its link",
             (x.get("originalFiles"), x.get("title")), (["original.mp4"], "Trailer"))
        t.ok("copied in, so the share keeps its own",
             os.path.isfile(os.path.join(xp, "original.mp4")) and open(os.path.join(xp, "original.mp4"), "rb").read()
             == open(os.path.join(media, "trailers", TRAILER), "rb").read())
        kept, mc = os.path.join(xp, "original.mp4"), load_tool(MEDIA_CHECK)
        t.eq("and described as a source record describes its file: its size, its qh1 and its sha256",
             x.get("originals"), [{"name": "original.mp4", "sizeBytes": os.path.getsize(kept) if found else None,
                                   "fixity": {"qh1": mc.qh1(kept) if found else None,
                                              "sha256": mc.sha_file(kept) if found else None,
                                              "sha256At": "2026-09-21T17:00:00Z"}}])
        t.eq("and finished by the checksums written last, over the record and the file",
             sorted(listing(xp)) if os.path.isfile(os.path.join(xp, "checksums.sha256")) else None,
             sorted(["extra.json", "original.mp4"]))
        t.ok("and nothing in its folder names the file as it came", not any(
            b"Trailer.mp4" in open(os.path.join(xp, n), "rb").read() for n in ("extra.json", "checksums.sha256")))
        t.ok("a trailer nothing could probe says so rather than guessing", "probe" not in x and x.get("runtimeMs") is None
             and "trailer Example Film - Trailer.mp4 was not probed" in text, text)
        t.eq("its link is still a link", [(v["site"], v["key"]) for v in jload(os.path.join(d, "metadata.json"))["videos"]],
             [("YouTube", "abc123")])
        if have_jsonschema():
            code, vtext = run(VALIDATOR, "--check-checksums", out)
            t.ok("the tree passes validate-library-v2.py", code == 0 and vtext.strip().endswith("OK"), vtext)
        else:
            t.skip("the tree passes validate-library-v2.py", "jsonschema is not importable here")
        code, mtext = run(MEDIA_CHECK, "--checksums", out)
        t.ok("and the media check", code == 0 and "'extras': 1" in mtext, mtext)
        t.eq("whose origin names the link it was downloaded from", x.get("origin"),
             {"kind": "link", "site": "YouTube", "externalId": "abc123", "url": "https://example.org/abc123"})
        rows, built = rows_of(out, "--text-language", "und")
        t.eq("the rebuild gives the trailer back as an extra row, played from its folder",
             [(r["itemId"], r["kind"], [a["kind"] for a in r["playbackAssets"]]) for r in built["extras"]],
             [(iid, "trailer", ["primary"])])
        t.eq("and gives the link its local copy back: the trailer the extra keeps",
             [v["localPath"] for v in rows[iid]["trailers"]], [os.path.join(xp, "original.mp4")])
        code, ctext = run(REBUILD, out, "--compare", export, "--text-language", "und",
                          "--ignore-fields", "id,path,hash,codec,resolution,bitrateKbps,durationMs,sizeBytes")
        t.ok("so it agrees with the export it came from, the link's localPath included: it has its local copy",
             code == 0 and "the tree and the database agree" in ctext and "localPath" not in ctext, ctext)
        e = jload(export)
        e["items"][0]["trailers"][0]["localPath"] = None
        jwrite(os.path.join(tmp, "no-copy.json"), e)
        code, ctext = run(REBUILD, out, "--compare", os.path.join(tmp, "no-copy.json"), "--text-language", "und",
                          "--ignore-fields", "id,path,hash,codec,resolution,bitrateKbps,durationMs,sizeBytes")
        t.ok("and a link the database holds no local copy of differs from one the tree gives its copy back",
             code == 1 and "localPath" in ctext, ctext)
        again = os.path.join(tmp, "library-again")
        code, text = run(FROM_CATALOG, "--export", export, "--packages", packages, "--media", media, "--out", again)
        t.ok("a second run into a fresh folder writes the same bytes", code == 0 and tree_files(out) == tree_files(again), text)
        dry = os.path.join(tmp, "library-dry")
        code, text = run(FROM_CATALOG, "--export", export, "--packages", packages, "--media", media, "--out", dry, "--dry-run")
        t.ok("and a dry run writes nothing", code == 0 and not os.path.exists(dry), text)

    with tempfile.TemporaryDirectory() as tmp:
        export, media, packages, iid, _ = fake_export(os.path.join(tmp, "share"), trailer=True)
        out = os.path.join(tmp, "library")
        code, text = run(FROM_CATALOG, "--export", export, "--packages", packages, "--media", media, "--out", out,
                         "--media-mode", "move")
        xp = (glob.glob(os.path.join(out, "movies", "*", "*", "extras", "*")) or [tmp])[0]
        t.ok("with --media-mode move the trailer moves into its extra",
             code == 0 and os.path.isfile(os.path.join(xp, "original.mp4"))
             and not os.path.exists(os.path.join(media, "trailers", TRAILER)), text)

    with tempfile.TemporaryDirectory() as tmp:
        export, media, packages, iid, _ = fake_export(os.path.join(tmp, "share"), trailer=True)
        out = os.path.join(tmp, "library")
        code, text = run(FROM_CATALOG, "--export", export, "--packages", packages, "--media", media, "--out", out,
                         "--media-mode", "none")
        t.ok("with --media-mode none it stays a link: an extra holds its own file",
             code == 0 and not glob.glob(os.path.join(out, "movies", "*", "*", "extras"))
             and "--media-mode none leaves its file where it is" in text, text)
        os.unlink(os.path.join(media, "trailers", TRAILER))
        out = os.path.join(tmp, "library-gone")
        code, text = run(FROM_CATALOG, "--export", export, "--packages", packages, "--media", media, "--out", out)
        t.ok("and so does a trailer whose localPath is not on the share",
             code == 0 and not glob.glob(os.path.join(out, "movies", "*", "*", "extras"))
             and "is not on this share, so it stays a link" in text, text)

    # ---- a series' trailer is the series' extra; an episode has none
    with tempfile.TemporaryDirectory() as tmp:
        media = os.path.join(tmp, "share", "media")
        os.makedirs(os.path.join(media, "trailers"))
        sid, eid = "aaaaaaaa-2222-4333-8444-555555555555", "bbbbbbbb-2222-4333-8444-555555555555"
        for who in ("series", "episode"):
            with open(os.path.join(media, "trailers", f"{who}.mp4"), "wb") as f:
                f.write(f"the {who}'s trailer\n".encode() * 20)

        def trailer_of(who):
            return [{"source": "tmdb", "site": "YouTube", "externalId": who, "url": None, "title": None,
                     "durationSec": None, "localPath": f"/var/lib/katalog/media/trailers/{who}.mp4"}]
        export = os.path.join(tmp, "catalog.json")
        jwrite(export, {"exportedAt": "2026-09-21T17:00:00Z", "deletedItems": [], "items": [
            {"id": sid, "type": "series", "title": "Example Series", "createdAt": "2026-07-01T09:00:00Z",
             "trailers": trailer_of("series")},
            {"id": eid, "type": "episode", "title": "Pilot", "parentId": sid, "seasonNumber": 1, "episodeNumber": 1,
             "createdAt": "2026-07-01T09:00:00Z", "trailers": trailer_of("episode")}]})
        out = os.path.join(tmp, "library")
        env = fake_ffprobe(os.path.join(tmp, "bin"))
        code, text = run_with(env, FROM_CATALOG, "--export", export, "--packages", os.path.join(tmp, "share"),
                              "--media", media, "--out", out)
        sdir = os.path.join(out, "series", sid[:2], sid)
        found = glob.glob(os.path.join(sdir, "extras", "*", "extra.json"))
        x = jload(found[0]) if found else {}
        t.ok("a series' downloaded trailer is the series' extra, of no particular season",
             code == 0 and len(found) == 1 and x.get("kind") == "trailer" and "seasonNumber" not in x, text)
        t.eq("titled Trailer when its link has no title — never by the name of its file — its origin naming only "
             "what the link says",
             (x.get("title"), x.get("origin")), ("Trailer", {"kind": "link", "site": "YouTube", "externalId": "series"}))
        t.ok("an episode's stays a link, because an episode has no extras",
             not os.path.exists(os.path.join(sdir, "episodes", eid, "extras"))
             and "trailer episode.mp4 stays a link: an episode has no extras" in text, text)
        t.eq("a trailer that was probed carries what the probe found, as a source record would",
             (x.get("language"), x.get("runtimeMs"), (x.get("probe") or {}).get("version"),
              [s["type"] for s in x.get("streams") or []], (x.get("essence") or {}).get("maxVideoHeight")),
             ("en", 90000, "ffprobe version 9.9-test", ["video", "audio"], 1080))
        if have_jsonschema():
            code, vtext = run(VALIDATOR, "--check-checksums", out)
            t.ok("and the tree passes validate-library-v2.py", code == 0 and vtext.strip().endswith("OK"), vtext)
        else:
            t.skip("and the tree passes validate-library-v2.py", "jsonschema is not importable here")


# ---------------------------------------------------------------- the platform's library, staged and adopted
PF_MOVIE = "a1000000-0000-4000-8000-000000000001"     # an original, a sidecar and a companion beside it, a package
PF_GONE = "b2000000-0000-4000-8000-000000000002"      # a package whose original is gone
PF_SERIES = "c3000000-0000-4000-8000-000000000003"
PF_EPISODE = "d4000000-0000-4000-8000-000000000004"
PF_EXTRA = "e5000000-0000-4000-8000-000000000005"     # the movie's trailer, packaged
PF_PENDING = "f6000000-0000-4000-8000-000000000006"   # an extra never packaged
PF_TAKEN = "a7a00000-0000-4000-8000-000000000007"     # an original and a forced subtitle beside it, no package
PF_PERSON = "99000000-0000-4000-8000-000000000009"
PF_RUN = "2026-10-07a"


def legacy_package(folder, duration_ms, subtitles=(), extra=False):
    """A package folder of the store before the library: manifest.json, the HLS folders, the subtitles,
    trickplay covering the duration, and .complete holding when it finished."""
    files = {"hls/master.m3u8": "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=4400000\nv0/playlist.m3u8\n",
             "hls/v0/playlist.m3u8": "#EXTM3U\n#EXT-X-ENDLIST\n", "hls/v0/seg-00001.m4s": "video",
             "hls/a0/playlist.m3u8": "#EXTM3U\n#EXT-X-ENDLIST\n", "hls/a0/seg-00001.m4s": "audio"}
    if not extra:
        cues = duration_ms // 10000
        files["trickplay/thumbnails.vtt"] = "WEBVTT\n\n" + "".join(
            f"00:00:{i * 10:02d}.000 --> 00:00:{i * 10 + 10:02d}.000\nsprite-0000.jpg#xywh=0,0,320,180\n\n"
            for i in range(cues))
        files["trickplay/sprite-0000.jpg"] = "sprite"
    for sub in subtitles:
        files[sub["path"]] = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nline\n"
    files[".complete"] = "2026-09-01T10:00:00+00:00\n"
    for rel, body in files.items():
        os.makedirs(os.path.dirname(os.path.join(folder, rel)), exist_ok=True)
        with open(os.path.join(folder, rel), "w") as f:
            f.write(body)
    manifest = {"version": 2, "durationMs": duration_ms, "packagedAt": "2026-09-01T10:00:00+00:00",
                "packager": "packager example",
                "renditions": {"video": [{"id": "v0", "dir": "hls/v0", "codec": "hev1.1.6.L120.B0", "width": 1920,
                                          "height": 800, "bitrateBps": 4000000, "peakBitrateBps": 4200000,
                                          "hdr": False, "videoRange": "SDR", "frameRate": "24/1", "segments": 1,
                                          "targetDuration": 6, "label": "source", "encoder": "copy"}],
                               "audio": [{"id": "a0", "dir": "hls/a0", "codec": "mp4a.40.2", "language": "eng",
                                          "title": "", "default": True, "channels": 2, "bitrateBps": 192000,
                                          "segments": 1, "visible": True, "group": "audio", "name": "English"}],
                               "audioSurround": []},
                "subtitles": list(subtitles),
                "hls": {"master": "hls/master.m3u8", "segmentSeconds": 6, "audioGroups": ["audio"],
                        "subtitleGroup": None}}
    if not extra:
        manifest["trickplay"] = {"vttPath": "trickplay/thumbnails.vtt", "spritePattern": "trickplay/sprite-%04d.jpg",
                                 "intervalSec": 10, "thumbWidth": 320, "thumbHeight": 180, "gridCols": 10,
                                 "gridRows": 10}
    jwrite(os.path.join(folder, "manifest.json"), manifest)


def store_asset(aid, path, kind, **fields):
    """A playback row of the catalog's export, as the store before the library held it."""
    return {"id": aid, "path": path, "kind": kind, "codec": None, "resolution": None, "bitrateKbps": 4400,
            "sizeBytes": fields.pop("sizeBytes", None), "hash": None, "isPrimary": kind == "primary",
            "audioCodec": "mp4a.40.2", "audioLanguage": "eng", "audioChannels": 2, "audioBitrateKbps": 192,
            "audioTrackCount": 1, "subtitleTrackCount": fields.pop("subtitleTrackCount", 0),
            "durationMs": 20000, **fields}


def store_row(iid, kind, title, **fields):
    """An item row of the catalog's export."""
    return {"id": iid, "type": kind, "title": title, "sortTitle": title.lower(), "year": 2024,
            "description": f"{title}, an example.", "tagline": None, "rating": None, "durationMs": 20000,
            "parentId": None, "seasonNumber": None, "episodeNumber": None, "metadataLocked": False,
            "createdAt": "2026-07-01T09:00:00Z", "createdBy": "scanner", "modifiedAt": "2026-09-30T09:00:00Z",
            "externalIds": [], "genres": [], "tags": [], "people": [], "chapters": [], "segments": [],
            "playbackAssets": [], "subtitleAssets": [], "trailers": [], "artwork": [], **fields}


def store_original(aid, path, size):
    """A primary row as the catalog fills it from the probe of its file: codec, resolution, bitrate and
    duration, and no audio fields."""
    return store_asset(aid, path, "primary", sizeBytes=size, codec="h264", resolution="1920x1080", bitrateKbps=4000,
                       durationMs=90000, audioCodec=None, audioLanguage=None, audioChannels=None, audioBitrateKbps=None,
                       audioTrackCount=None, subtitleTrackCount=None)


def legacy_store(root):
    """A share as the platform holds it before the library: its originals under media/, its packages
    under packages/, an extra taken in under extras/ — and the catalog's export of it, with paths as
    the services see the share. Returns the export's path."""
    asset, row, original = store_asset, store_row, store_original
    media, packages, extras = (os.path.join(root, n) for n in ("media", "packages", "extras"))
    svc = "/var/lib/katalog"
    film, other = "Example Film (2024)/Example Film (2024).mkv", "Other Film (2025)/Other Film (2025).mkv"
    episode = "tv/Example Show/Season 01/Example Show - S01E01.mkv"
    bodies = {film: b"an original of an example film\n" * 500, other: b"an original nothing packaged yet\n" * 300,
              episode: b"an episode\n" * 400}
    for rel, body in (*bodies.items(),
                      ("Example Film (2024)/Example Film (2024).de.srt", b"1\n00:00:01,000 --> 00:00:02,000\nHallo\n"),
                      ("Example Film (2024)/Example Film (2024).nfo", b"<movie/>\n"),
                      ("Other Film (2025)/Other Film (2025).en.forced.srt", b"1\n00:00:01,000 --> 00:00:02,000\nHi\n")):
        os.makedirs(os.path.dirname(os.path.join(media, rel)), exist_ok=True)
        with open(os.path.join(media, rel), "wb") as f:
            f.write(body)
    os.makedirs(os.path.join(extras, "example-film"))
    with open(os.path.join(extras, "example-film", "trailer.mov"), "wb") as f:
        f.write(b"a trailer\n" * 300)
    with open(os.path.join(extras, "example-film", "making-of.mov"), "wb") as f:
        f.write(b"a making-of\n" * 300)
    movie_subs = [{"id": "sub0", "path": "subs/0.vtt", "language": "eng", "title": "", "default": False,
                   "forced": False, "format": "webvtt", "visible": True},
                  {"id": "sub1", "path": "subs/1.vtt", "language": "ger", "title": "Deutsch", "default": False,
                   "forced": False, "format": "webvtt", "visible": True, "external": True}]
    legacy_package(os.path.join(packages, "movies", PF_MOVIE[:2], PF_MOVIE), 20000, movie_subs)
    legacy_package(os.path.join(packages, "movies", PF_GONE[:2], PF_GONE), 20000)
    legacy_package(os.path.join(packages, "shows", PF_EPISODE[:2], PF_EPISODE), 20000)
    legacy_package(os.path.join(packages, "extras", PF_EXTRA[:2], PF_EXTRA), 20000, extra=True)
    png = (b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + (2).to_bytes(4, "big") +
           (3).to_bytes(4, "big") + b"\x08\x06\x00\x00\x00" + b"\x00" * 4)
    pm, pg, pe = (f"{svc}/packages/{cat}/{i[:2]}/{i}" for cat, i in (("movies", PF_MOVIE), ("movies", PF_GONE),
                                                                      ("shows", PF_EPISODE)))
    items = [
        row(PF_MOVIE, "movie", "Example Film", externalIds=[{"source": "tmdb", "externalId": "1234"}],
            people=[{"personId": PF_PERSON, "name": "A Director", "role": "director"}],
            chapters=[{"startMs": 0, "endMs": 10000, "title": "One", "ordinal": 1},
                      {"startMs": 10000, "endMs": 20000, "title": "Two", "ordinal": 2}],
            segments=[{"kind": "credits", "startMs": 18000, "endMs": 20000, "source": "blackframe", "confidence": 0.8,
                       "label": None}],
            playbackAssets=[original("pa1", f"{svc}/media/{film}", len(bodies[film])),
                            asset("pa2", f"{pm}/manifest.json", "packaged", subtitleTrackCount=2)],
            # sa1 a default a person chose, which the database keeps: behaviour, which no record holds
            subtitleAssets=[{"id": "sa1", "path": f"{pm}/subs/0.vtt", "format": "webvtt", "language": "eng",
                             "label": "", "isDefault": True},
                            {"id": "sa2", "path": f"{svc}/media/Example Film (2024)/Example Film (2024).de.srt",
                             "format": "srt", "language": "de", "label": "German", "isDefault": False}],
            # a backdrop that is the poster's bytes, as the catalog makes an episode's
            artwork=[{"kind": kind, "contentType": "image/png", "fetchedAt": "2026-07-01T09:05:00Z",
                      "base64": base64.b64encode(png).decode()} for kind in ("poster", "backdrop")]),
        row(PF_GONE, "movie", "Gone Film",
            playbackAssets=[asset("pb1", f"{svc}/media/Gone Film (2023).mkv", "primary", sizeBytes=777),
                            asset("pb2", f"{pg}/manifest.json", "packaged")]),
        row(PF_SERIES, "series", "Example Show", durationMs=None),
        row(PF_EPISODE, "episode", "Pilot", parentId=PF_SERIES, seasonNumber=1, episodeNumber=1,
            playbackAssets=[original("pd1", f"{svc}/media/{episode}", len(bodies[episode])),
                            asset("pd2", f"{pe}/manifest.json", "packaged")]),
        # nothing packaged it yet: it is taken in, its version the original alone
        row(PF_TAKEN, "movie", "Other Film",
            playbackAssets=[original("pt1", f"{svc}/media/{other}", len(bodies[other]))],
            subtitleAssets=[{"id": "st1", "path": f"{svc}/media/Other Film (2025)/Other Film (2025).en.forced.srt",
                             "format": "srt", "language": "en", "label": "English", "isDefault": False}])]
    extras_rows = [
        {"id": PF_EXTRA, "itemId": PF_MOVIE, "kind": "trailer", "title": "Trailer", "localizedTitles": {"de": "Vorschau"},
         "language": None, "seasonNumber": None, "origin": None,
         "sourcePath": f"{svc}/extras/example-film/trailer.mov", "sourceSize": 3000, "sourceQh1": None,
         "recordPath": None, "registeredBy": "api", "sortOrder": 1, "hidden": False, "label": "The Trailer",
         "state": "ready", "packageId": None, "packagePath": f"{svc}/packages/extras/{PF_EXTRA[:2]}/{PF_EXTRA}",
         "packagedAt": "2026-09-02T10:00:00Z", "recordedAt": None, "durationMs": 20000,
         "createdAt": "2026-09-02T09:00:00Z", "createdBy": "api", "modifiedAt": "2026-09-02T10:00:00Z"},
        {"id": PF_PENDING, "itemId": PF_MOVIE, "kind": "making-of", "title": "Making of", "localizedTitles": {},
         "language": None, "seasonNumber": None, "origin": None,
         "sourcePath": f"{svc}/extras/example-film/making-of.mov", "sourceSize": 3600, "sourceQh1": None,
         "recordPath": None, "registeredBy": "api", "sortOrder": None, "hidden": False, "label": None,
         "state": "failed", "packageId": None, "packagePath": None, "packagedAt": None, "recordedAt": None,
         "durationMs": None, "createdAt": "2026-09-03T09:00:00Z", "createdBy": "api",
         "modifiedAt": "2026-09-03T09:00:00Z"}]
    export = os.path.join(os.path.dirname(root), "catalog.json")
    jwrite(export, {"exportedAt": "2026-10-01T12:00:00Z", "shard": None, "items": items, "deletedItems": [],
                    "people": [{"id": PF_PERSON, "name": "A Director", "modifiedAt": "2026-09-30T09:00:00Z"}],
                    "extras": extras_rows, "versions": None, "sources": None})
    return export


def services_to(root, export):
    """The export with the services' paths as this share's: the adopt and a fresh export see the share where
    the services do, and so does this test, at root."""
    text_ = open(export, encoding="utf-8").read().replace("/var/lib/katalog", root)
    with open(export, "w", encoding="utf-8") as f:
        f.write(text_)


def listing_of(folders):
    """The guard a unit records of its package folders, computed as its plan documents it: sha256 over
    '<size> <path>' lines, the folders in order, each one's files by path."""
    h = hashlib.sha256()
    for folder in folders:
        rels = sorted(os.path.relpath(os.path.join(b, f), folder) for b, _, fs in os.walk(folder) for f in fs)
        for rel in rels:
            h.update(f"{os.path.getsize(os.path.join(folder, rel))} {os.path.join(folder, rel)}\n".encode())
    return "sha256:" + h.hexdigest()


def adopt(root, run, export, fresh):
    """katalog-manager's adopt, by hand, as the unit plans say: per unit — series before episodes — the
    guards checked, then every move in its order, then the database changes the db block names, written
    into fresh as an export taken afterwards shows them: the packaged row as packaging-complete writes it
    from package.json, the subtitle rows' paths — each keeps its default, a person's choice the database
    keeps and no record holds — an original gone before the library was recorded a retired one, with
    its original-deleted event, and every episode a source covers after its holder linked to the holder
    (coveredBy). The staged people last."""
    rundir = os.path.join(root, ".work", "migration", run)
    units = sorted((jload(p) for p in glob.glob(os.path.join(rundir, "units", "*.json"))),
                   key=lambda u: ({"series": 0, "movie": 1, "episode": 2}[u["type"]], u["itemId"]))
    e = jload(export)
    rows = {r["id"]: r for r in e["items"]}
    stale = []
    for u in units:
        g = u["guards"]
        packages = [m["from"] for m in u["moves"] if m["kind"] == "package"]
        mp = next((os.path.join(os.path.dirname(m["from"]), "manifest.json") for m in u["moves"] if m["kind"] == "package"
                   and os.path.dirname(m["from"]).find(u["itemId"]) >= 0), None)
        if (g["listingSha256"] or None) != (listing_of(packages) if packages else None) or \
                (g["manifestSha256"] and g["manifestSha256"] != "sha256:" + digest(mp)):
            stale.append(u["itemId"])
            continue
        for m in u["moves"]:
            os.makedirs(os.path.dirname(m["to"]), exist_ok=True)
            os.rename(m["from"], m["to"])
        row, db = rows[u["itemId"]], u["db"]
        assets = {a["id"]: a for a in row["playbackAssets"]}
        keep = set()
        for a in db["assets"]:
            asset = assets[a["id"]]
            keep.add(a["id"])
            asset["path"] = a["path"]
            if a.get("versionId"):
                pkg = jload(a["path"])
                ren = pkg["renditions"]
                v0 = ren["video"][0]
                aud = next((x for x in ren["audio"] if x.get("default")), ren["audio"][0] if ren["audio"] else {})
                asset.update(codec=v0["codec"], resolution=f"{v0['width']}x{v0['height']}",
                             bitrateKbps=pkg["peakBandwidthBps"] // 1000, sizeBytes=pkg["sizeBytes"],
                             audioCodec=aud.get("codec"), audioLanguage=aud.get("language"),
                             audioChannels=aud.get("channels"),
                             audioBitrateKbps=aud["bitrateBps"] // 1000 if aud.get("bitrateBps") else None,
                             audioTrackCount=len(ren["audio"]), subtitleTrackCount=len(pkg["subtitles"]),
                             durationMs=pkg["durationMs"])
            elif a["path"].startswith(os.path.join(u["itemDir"], "sources", "")):
                asset.update(kind="original", isPrimary=False)
        row["playbackAssets"] = [a for a in row["playbackAssets"] if a["kind"] != "packaged" or a["id"] in keep]
        subs = {x["id"]: x for x in row["subtitleAssets"]}
        for x in db["subtitles"]:
            subs[x["id"]]["path"] = x["path"]
        for src in db["sources"]:
            for covered in src["covers"][1:]:
                rows[covered]["coveredBy"] = u["itemId"]
            if src["arrivalPath"] is None and src["recordDir"]:
                write_event(u["itemDir"], {"schema": "zaentrum.library.event/2",
                                           "eventId": str(uuid.uuid5(uuid.NAMESPACE_URL, src["sourceId"])),
                                           "at": "2026-10-07T10:00:00Z", "by": "katalog-manager (migration)",
                                           "kind": "original-deleted", "versionId": db["versions"][0]["versionId"],
                                           "sourceId": src["sourceId"],
                                           "reason": "gone before the library was recorded", "accepted": []})
    for p in glob.glob(os.path.join(rundir, "staged-people", "*")):
        pid = os.path.basename(p)
        target = os.path.join(root, "people", pid[:2], pid)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        os.rename(p, target)
    jwrite(fresh, e)
    return units, stale


def test_platform(t):
    """--platform stages the library from the store before it, every record under .work/migration/<run>/
    and a plan per item that moves every original into its version folder, under the name the library
    gives it — a title nothing packaged yet taken in, its version the original alone — and the adopt the
    plans describe leaves a library that validates, passes the media check, names no file as it arrived,
    and rebuilds to the database the adopt left, the arrivals aside."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "katalog")
        export = legacy_store(root)
        services_to(root, export)
        env = fake_ffprobe(os.path.join(tmp, "bin"))
        before = tree_files(root)
        code, text = run_with(env, FROM_CATALOG, "--platform", "--export", export, "--root", root, "--run", PF_RUN,
                              "--dry-run")
        rundir = os.path.join(root, ".work", "migration", PF_RUN)
        report = jload(os.path.join(rundir, "report.json")) if os.path.isfile(os.path.join(rundir, "report.json")) else {}
        t.ok("a dry run writes report.json and nothing else",
             code == 0 and sorted(os.listdir(rundir)) == ["report.json"]
             and {k: v for k, v in tree_files(root).items() if not k.startswith(".work")} == before, text)
        t.eq("it says what is ready and what is not, by class, and how many titles are taken in without a package",
             (report.get("dryRun"), report.get("counts", {}).get("staged"), report.get("counts", {}).get("takenIn"),
              report.get("problems")),
             (True, 5, 1, {"extra not packaged": 1, "missing original": 1}))
        t.ok("and what deleting every original would cost, from the probes", report.get("loss", {}).get("measured") == 2
             and report["loss"]["unmeasured"] == 1, report.get("loss"))

        code, text = run_with(env, FROM_CATALOG, "--platform", "--export", export, "--root", root, "--run", PF_RUN)
        t.ok("--platform stages every item", code == 0 and len(glob.glob(os.path.join(rundir, "units", "*.json"))) == 5,
             text)
        t.ok("and touches nothing outside the run's folder",
             {k: v for k, v in tree_files(root).items() if not k.startswith(".work")} == before)
        staged = {k: v for k, v in tree_files(os.path.join(rundir, "staged")).items()}
        code, text = run_with(env, FROM_CATALOG, "--platform", "--export", export, "--root", root, "--run", PF_RUN)
        t.ok("a second run stages the same records", code == 0 and tree_files(os.path.join(rundir, "staged")) == staged,
             text)
        unit = jload(os.path.join(rundir, "units", PF_MOVIE + ".json"))
        movie_dir = os.path.join(root, "movies", PF_MOVIE[:2], PF_MOVIE)
        vid = unit["db"]["versions"][0]["versionId"]
        vdir = os.path.join(movie_dir, "versions", vid)
        staged_rel = os.path.relpath(unit["stagedDir"], root)
        legacy = os.path.join(".work", "migration", PF_RUN, "legacy")
        pkg_rel = os.path.join("packages", "movies", PF_MOVIE[:2], PF_MOVIE)
        xpkg_rel = os.path.join("packages", "extras", PF_EXTRA[:2], PF_EXTRA)
        t.eq("a unit plan moves the package folders into the staged records, publishes the item, moves the original "
             "into its version folder under the name the library gives it, the subtitle file beside it and the "
             "extras' originals to the arrivals and what is left of the old package folders aside, in that order",
             [(m["kind"], os.path.relpath(m["from"], root), os.path.relpath(m["to"], root)) for m in unit["moves"]],
             [("package", os.path.join(pkg_rel, d), os.path.join(staged_rel, "versions", vid, d))
              for d in ("hls", "subs", "trickplay")]
             + [("package", os.path.join(xpkg_rel, "hls"), os.path.join(staged_rel, "extras", PF_EXTRA, "hls")),
                ("publish", staged_rel, os.path.relpath(movie_dir, root)),
                ("original", "media/Example Film (2024)/Example Film (2024).mkv",
                 os.path.relpath(os.path.join(vdir, "original.mkv"), root)),
                ("sidecar", "media/Example Film (2024)/Example Film (2024).de.srt",
                 ".work/incoming/Example Film (2024)/Example Film (2024).de.srt"),
                ("original", "extras/example-film/trailer.mov", ".work/extras/example-film/trailer.mov"),
                ("original", "extras/example-film/making-of.mov", ".work/extras/example-film/making-of.mov"),
                ("legacy", pkg_rel, os.path.join(legacy, "movies", PF_MOVIE[:2], PF_MOVIE)),
                ("legacy", xpkg_rel, os.path.join(legacy, "extras", PF_EXTRA[:2], PF_EXTRA))])
        sid = unit["db"]["sources"][0]["sourceId"]
        source = unit["db"]["sources"][0]
        t.eq("the database changes it names: the source by the library's name and at its version folder, and its "
             "sidecar's rendition, the version verified in full, the rows' new paths, the extras",
             ((source["filename"], source["arrivalPath"], source["libraryPath"], source["sidecars"]),
              (unit["db"]["versions"][0]["verifiedLevel"], unit["db"]["versions"][0]["takenIn"]),
              sorted((a["id"], a["path"]) for a in unit["db"]["assets"]), sorted(x["id"] for x in unit["db"]["subtitles"]),
              sorted((x["id"], x["dir"] is not None) for x in unit["db"]["extras"])),
             (("original.mkv", os.path.join(vdir, "original.mkv"), None,
               [{"subtitleAssetId": "sa2", "rendition": "sub1", "path": "subs/1.vtt"}]),
              ("full", False), [("pa1", os.path.join(vdir, "original.mkv")), ("pa2", os.path.join(vdir, "package.json"))],
              ["sa1", "sa2"], [(PF_EXTRA, True), (PF_PENDING, False)]))
        staged_images = jload(os.path.join(unit["stagedDir"], "metadata.json"))["images"]
        t.ok("the backdrop that is the poster's bytes is staged as both, sharing one file",
             [i["kind"] for i in staged_images] == ["backdrop", "poster"] and len({i["file"] for i in staged_images}) == 1
             and os.listdir(os.path.join(unit["stagedDir"], "metadata")) == [staged_images[0]["file"]], staged_images)
        staged_version = os.path.join(unit["stagedDir"], "versions", vid)
        package = jload(os.path.join(staged_version, "package.json"))
        t.eq("the version names the original it keeps beside its package, which is derived, and the staged folder "
             "holds the records over the package but neither the package nor the original",
             (jload(os.path.join(staged_version, "version.json"))["originalFiles"], package["role"],
              sorted(os.listdir(staged_version))),
             (["original.mkv"], "derived", [".complete", "checksums.sha256", "package.json", "version.json"]))
        t.eq("the subtitle made from the sidecar names the copy the source folder keeps, under the name the library "
             "gives it — and the .nfo beside the original is neither copied nor moved",
             ([s.get("fromSidecar") for s in package["subtitles"]],
              sorted(os.listdir(os.path.join(unit["stagedDir"], "sources", sid)))),
             ([None, f"sources/{sid}/subtitle-1.de.srt"],
              ["checksums.sha256", "ffprobe.json", "source.json", "subtitle-1.de.srt"]))
        gone = jload(os.path.join(rundir, "units", PF_GONE + ".json"))
        gone_source = jload(glob.glob(os.path.join(gone["stagedDir"], "sources", "*", "source.json"))[0])
        t.ok("a package whose original is gone gets a source record without fixity, which says why, and a version "
             "that keeps no original, its package canonical",
             "fixity" not in gone_source["file"] and gone_source["file"]["sizeBytes"] == 777
             and "gone before the library was recorded" in gone_source["probe"]["note"]
             and gone["db"]["sources"][0]["arrivalPath"] is None
             and jload(glob.glob(os.path.join(gone["stagedDir"], "versions", "*", "version.json"))[0])["originalFiles"] == []
             and jload(glob.glob(os.path.join(gone["stagedDir"], "versions", "*", "package.json"))[0])["role"] == "canonical",
             gone_source)
        t.ok("every id is the one the item and a stable name make, and the extra's is its row's",
             os.path.isdir(os.path.join(unit["stagedDir"], "extras", PF_EXTRA))
             and unit["db"]["versions"][0]["versionId"] == load_tool(RECORDS).did(
                 PF_MOVIE, "version", os.path.join("movies", PF_MOVIE[:2], PF_MOVIE)))

        # a title nothing packaged yet is taken in: its version is the original alone
        taken = jload(os.path.join(rundir, "units", PF_TAKEN + ".json"))
        taken_dir = os.path.join(root, "movies", PF_TAKEN[:2], PF_TAKEN)
        tvid = taken["db"]["versions"][0]["versionId"]
        tsid = taken["db"]["sources"][0]["sourceId"]
        t.eq("a title nothing packaged yet is staged with its source and a version holding version.json alone, which "
             "names the original the adopt renames in, and nothing guards a package it has not got",
             (sorted(os.listdir(os.path.join(taken["stagedDir"], "versions", tvid))),
              jload(os.path.join(taken["stagedDir"], "versions", tvid, "version.json"))["originalFiles"],
              sorted(os.listdir(os.path.join(taken["stagedDir"], "sources", tsid))),
              [(m["kind"], os.path.relpath(m["to"], root)) for m in taken["moves"]], taken["guards"]),
             (["version.json"], ["original.mkv"],
              ["checksums.sha256", "ffprobe.json", "source.json", "subtitle-1.en.forced.srt"],
              [("publish", os.path.relpath(taken_dir, root)),
               ("original", os.path.relpath(os.path.join(taken_dir, "versions", tvid, "original.mkv"), root)),
               ("sidecar", ".work/incoming/Other Film (2025)/Other Film (2025).en.forced.srt")],
              {"manifestSha256": None, "completeMtime": None, "listingSha256": None}))
        t.eq("and the database records the version taken in, with no package",
             (taken["db"]["versions"], taken["db"]["sources"][0]["recordDir"], taken["db"]["assets"]),
             ([{"versionId": tvid, "packageId": None, "dir": os.path.join(taken_dir, "versions", tvid), "completedAt": None,
                "sourceIds": [tsid], "verifiedAt": None, "verifiedLevel": None, "takenIn": True}],
              os.path.join(taken_dir, "sources", tsid),
              [{"id": "pt1", "path": os.path.join(taken_dir, "versions", tvid, "original.mkv"), "sourceId": tsid}]))

        fresh = os.path.join(tmp, "after.json")
        units, stale = adopt(root, PF_RUN, export, fresh)
        t.ok("the guards hold: nothing changed since the plans were made", stale == [] and len(units) == 5, stale)
        t.ok("the share holds the library now, every original in its version folder, the subtitle files and the "
             "extras' originals among the arrivals, and what is no part of the record where it was",
             os.path.isfile(os.path.join(movie_dir, "item.json")) and os.path.isfile(os.path.join(vdir, "original.mkv"))
             and os.path.isfile(os.path.join(taken_dir, "versions", tvid, "original.mkv"))
             and os.path.isfile(os.path.join(root, ".work", "incoming", "Example Film (2024)", "Example Film (2024).de.srt"))
             and os.path.isfile(os.path.join(root, ".work", "extras", "example-film", "trailer.mov"))
             and not glob.glob(os.path.join(root, ".work", "incoming", "**", "*.mkv"), recursive=True)
             and not glob.glob(os.path.join(root, "media", "**", "*.mkv"), recursive=True)
             and os.path.isfile(os.path.join(root, "media", "Example Film (2024)", "Example Film (2024).nfo"))
             and not glob.glob(os.path.join(root, "packages", "*", "*", "*", "hls")))
        library = {rel: data for rel, data in tree_files(root).items() if rel.split(os.sep)[0] in ("movies", "series", "people")}
        arrived = (b"Example Film (2024)", b"Other Film (2025)", b"Example Show - S01E01", b"trailer.mov", b"making-of.mov")
        t.eq("and nothing in the library names a file as it arrived, every name in it of letters, digits, dots and "
             "dashes",
             (sorted(rel for rel, data in library.items() if any(a in data for a in arrived)),
              sorted(rel for rel in library if not all(re.fullmatch(r"[A-Za-z0-9.-]+", p) for p in rel.split(os.sep)))),
             ([], []))
        if have_jsonschema():
            code, vtext = run(VALIDATOR, "--check-checksums", root)
            t.ok("validate-library-v2.py --check-checksums finds the adopted library valid, .work/ beside it",
                 code == 0 and vtext.strip().endswith("OK"), vtext)
        else:
            t.skip("validate-library-v2.py finds the adopted library valid", "jsonschema is not importable here")
        code, mtext = run(MEDIA_CHECK, "--checksums", root)
        t.ok("and the media check", code == 0 and mtext.strip().endswith("OK"), mtext)
        after = {x["id"]: x for r in jload(fresh)["items"] for x in r["subtitleAssets"]}
        t.ok("the adopt keeps the subtitle default a person chose, of a row it carries into the version folder",
             after["sa1"]["isDefault"] is True
             and after["sa1"]["path"] == os.path.join(vdir, "subs", "0.vtt"), after["sa1"])
        rebuilt, _ = rows_of(root, "--text-language", "und")
        t.eq("which the rebuild cannot know: the subtitle row it writes has no default",
             [x["isDefault"] for x in rebuilt[PF_MOVIE]["subtitleAssets"]], [False])
        t.eq("and it plays each original from its version folder, the one taken in with no package",
             [sorted((a["kind"], os.path.relpath(a["path"], root)) for a in rebuilt[i]["playbackAssets"])
              for i in (PF_MOVIE, PF_TAKEN)],
             [[("packaged", os.path.relpath(os.path.join(vdir, "package.json"), root)),
               ("primary", os.path.relpath(os.path.join(vdir, "original.mkv"), root))],
              [("primary", os.path.relpath(os.path.join(taken_dir, "versions", tvid, "original.mkv"), root))]])
        code, ctext = run(REBUILD, root, "--compare", fresh, "--arrivals-root", os.path.join(root, ".work"),
                          "--ignore-fields", "id,path,hash", "--text-language", "und")
        t.ok("and the rebuild agrees with the database the adopt left — the originals in their version folders "
             "compared, the arrivals aside and the chosen default not compared",
             code == 0 and "the tree and the database agree" in ctext, ctext)

        code, text = run_with(env, FROM_CATALOG, "--platform", "--export", fresh, "--root", root, "--run", PF_RUN + "b")
        report = jload(os.path.join(root, ".work", "migration", PF_RUN + "b", "report.json"))
        t.ok("a run after the adopt stages nothing: every item is recorded",
             code == 0 and report["counts"]["staged"] == 0 and report["counts"]["recorded"] == 5, report["counts"])


def test_platform_problems(t):
    """What the platform's library cannot hold as the store has it is reported by its class, and the
    rest is staged: an episode under a season is in its series' folder, music is skipped, and a shard
    narrows the run to the items whose folder is in it."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "katalog")
        export = legacy_store(root)
        services_to(root, export)
        e = jload(export)
        season = "c3110000-0000-4000-8000-000000000031"
        e["items"] += [{"id": season, "type": "season", "title": "Season 1", "parentId": PF_SERIES, "seasonNumber": 1},
                       {"id": "d4220000-0000-4000-8000-000000000042", "type": "episode", "title": "Second",
                        "parentId": season, "seasonNumber": 1, "episodeNumber": 2, "createdAt": "2026-07-01T09:00:00Z"},
                       {"id": "d4330000-0000-4000-8000-000000000043", "type": "episode", "title": "Lost",
                        "parentId": "ff000000-0000-4000-8000-0000000000ff", "seasonNumber": 1, "episodeNumber": 3},
                       {"id": "a7000000-0000-4000-8000-000000000007", "type": "album", "title": "A Record"}]
        e["items"][0]["externalIds"].append({"source": "letterboxd", "externalId": "x"})
        jwrite(export, e)
        code, text = run(FROM_CATALOG, "--platform", "--export", export, "--root", root, "--run", "p")
        rundir = os.path.join(root, ".work", "migration", "p")
        report = jload(os.path.join(rundir, "report.json"))
        classes = {p["class"] for item in report["items"] for p in item["problems"]}
        t.ok("each problem is reported by its class", {"season parent", "episode without series", "music",
                                                      "dropped external-id source", "missing original",
                                                      "extra not packaged"} <= classes, sorted(classes))
        second = jload(os.path.join(rundir, "units", "d4220000-0000-4000-8000-000000000042.json"))
        t.eq("an episode under a season is in its series' folder",
             second["itemDir"], os.path.join(root, "series", PF_SERIES[:2], PF_SERIES, "episodes",
                                             "d4220000-0000-4000-8000-000000000042"))
        t.eq("and its record names the series, not the season",
             jload(os.path.join(second["stagedDir"], "item.json"))["seriesId"], PF_SERIES)
        t.ok("music and an episode no series holds are not staged",
             not os.path.exists(os.path.join(rundir, "units", "a7000000-0000-4000-8000-000000000007.json"))
             and not os.path.exists(os.path.join(rundir, "units", "d4330000-0000-4000-8000-000000000043.json")))

        code, text = run(FROM_CATALOG, "--platform", "--export", export, "--root", root, "--run", "s", "--shard", "c3")
        sharded = jload(os.path.join(root, ".work", "migration", "s", "report-c3.json"))
        t.eq("a shard stages the items whose folder is in it: a series with its episodes, under a season too",
             sorted(x["itemId"] for x in sharded["items"] if x["staged"]),
             sorted([PF_SERIES, PF_EPISODE, "d4220000-0000-4000-8000-000000000042"]))
        code, text = run(FROM_CATALOG, "--platform", "--export", export, "--root", root, "--run", "x", "--out", tmp)
        t.ok("and --platform writes only into the run's folder: --out is refused", code == 2 and "--out" in text, text)


# ---------------------------------------------------------------- one file, several episodes; a disc image
CV_SERIES = "c5000000-0000-4000-8000-000000000050"
CV_DISC = "a8000000-0000-4000-8000-000000000008"


def cv_episode(n):
    """The id of the example serial's episode n."""
    return f"d5000000-0000-4000-8000-0000000000{n:02d}"


def covering_store(root):
    """A share before the library whose series has files of several episodes, beside a movie whose original
    is a disc image — and the catalog's export of it, every row saying whom it is covered by. Episode 1's
    file is S01E01-E02, packaged, and nothing else holds episode 2; episode 3's is S01E03, nothing packaged
    it, and the catalog links episode 4 to it, whatever the name says; episode 5's is S01E05-06, and the
    export holds no episode 6; episode 7's is S01E07-E08, and episode 8 has a file of its own; episode 9's
    is S01E09-720p, a resolution and no range; episode 10's is S01E10E11E12, three episodes. Returns the
    export's path."""
    media, svc, show = os.path.join(root, "media"), "/var/lib/katalog", "tv/Example Serial/Season 01"
    names = {1: "Example Serial - S01E01-E02.mkv", 3: "Example Serial - S01E03.mkv", 5: "Example Serial - S01E05-06.mkv",
             7: "Example Serial - S01E07-E08.mkv", 8: "Example Serial - S01E08.mkv", 9: "Example Serial - S01E09-720p.mkv",
             10: "Example Serial - S01E10E11E12.mkv"}
    os.makedirs(os.path.join(media, show))
    for n, name in names.items():
        with open(os.path.join(media, show, name), "wb") as f:
            f.write(f"episode {n} of an example serial\n".encode() * 200)
    with open(os.path.join(media, "Disc Film (2020).iso"), "wb") as f:
        f.write(b"a disc image\n" * 100)
    legacy_package(os.path.join(root, "packages", "shows", cv_episode(1)[:2], cv_episode(1)), 20000)
    package = f"{svc}/packages/shows/{cv_episode(1)[:2]}/{cv_episode(1)}/manifest.json"
    file_of = lambda n: [store_original(f"p{n}", f"{svc}/media/{show}/{names[n]}",
                                        os.path.getsize(os.path.join(media, show, names[n])))]
    episode = lambda n, title, **fields: store_row(cv_episode(n), "episode", title, parentId=CV_SERIES, seasonNumber=1,
                                                   episodeNumber=n, **{"coveredBy": None, **fields})
    items = [store_row(CV_SERIES, "series", "Example Serial", durationMs=None, coveredBy=None),
             episode(1, "Front", playbackAssets=file_of(1) + [store_asset("q1", package, "packaged")]),
             episode(2, "Back"), episode(3, "Ebb", playbackAssets=file_of(3)), episode(4, "Flow", coveredBy=cv_episode(3)),
             episode(5, "Rise", playbackAssets=file_of(5)), episode(7, "Dusk", playbackAssets=file_of(7)),
             episode(8, "Dark", playbackAssets=file_of(8)), episode(9, "Dawn", playbackAssets=file_of(9)),
             episode(10, "Calm", playbackAssets=file_of(10)), episode(11, "Gust"), episode(12, "Squall"),
             store_row(CV_DISC, "movie", "Disc Film", coveredBy=None,
                       playbackAssets=[store_original("pz", f"{svc}/media/Disc Film (2020).iso", 1300)])]
    export = os.path.join(os.path.dirname(root), "catalog.json")
    jwrite(export, {"exportedAt": "2026-10-08T12:00:00Z", "shard": None, "items": items, "deletedItems": [],
                    "people": [], "extras": [], "versions": None, "sources": None})
    return export


def test_platform_covers(t):
    """A file that holds several episodes is staged in its holder's folder, its source covering them, the
    holder first, and every episode it covers as its record and its projection alone, which name the holder
    and the version the holder is staged with; the name's numbering is used only where the catalog says
    nothing, and never for an episode the export does not hold — a problem — or one with a file of its own;
    a disc image is left out of the plan, a problem until it is accepted; and the adopt the plans describe
    leaves a library that validates and rebuilds to the database it left, every covered episode linked."""
    with tempfile.TemporaryDirectory() as tmp:
        root = os.path.join(tmp, "katalog")
        export = covering_store(root)
        services_to(root, export)
        env = fake_ffprobe(os.path.join(tmp, "bin"))
        rundir = os.path.join(root, ".work", "migration", PF_RUN)
        code, text = run_with(env, FROM_CATALOG, "--platform", "--export", export, "--root", root, "--run", PF_RUN,
                              "--dry-run")
        report = jload(os.path.join(rundir, "report.json"))
        t.eq("a dry run counts the files of several episodes it stages and the episodes they cover, and says the "
             "problems by class", (code, report["counts"]["covering"], report["counts"]["covered"], report["problems"]),
             (0, 3, 4, {"covered episode missing": 1, "disc image": 1}))
        entry = {e["itemId"]: e for e in report["items"]}
        t.ok("a disc image is listed with its problem, and nothing of it is staged",
             not entry[CV_DISC]["staged"] and [p["class"] for p in entry[CV_DISC]["problems"]] == ["disc image"]
             and "Disc Film (2020).iso is a disc image" in entry[CV_DISC]["problems"][0]["detail"], entry[CV_DISC])
        t.ok("an episode the name numbers and the export does not hold is a problem of its holder's, and no id is made "
             "up for it", [p["class"] for p in entry[cv_episode(5)]["problems"]] == ["covered episode missing"]
             and "no episode S01E06" in entry[cv_episode(5)]["problems"][0]["detail"], entry[cv_episode(5)])

        code, text = run_with(env, FROM_CATALOG, "--platform", "--export", export, "--root", root, "--run", PF_RUN)
        units = {u["itemId"]: u for u in (jload(p) for p in glob.glob(os.path.join(rundir, "units", "*.json")))}
        t.ok("the disc image is left out of the plan, its original where it was",
             code == 0 and CV_DISC not in units and os.path.isfile(os.path.join(root, "media", "Disc Film (2020).iso")),
             text)
        e1, e2, e3, e4 = (units[cv_episode(n)] for n in (1, 2, 3, 4))
        source = lambda u: jload(glob.glob(os.path.join(u["stagedDir"], "sources", "*", "source.json"))[0])
        decided = lambda u: jload(os.path.join(u["stagedDir"], "metadata.json"))["library"]
        t.eq("a packaged file of two episodes, as its name says: its source and its row in the plan cover both, the "
             "holder first, and the holder's place ends where the other's is",
             (source(e1)["covers"], e1["db"]["sources"][0]["covers"], decided(e1)["numbering"]["aired"]["episodeEnd"]),
             ([cv_episode(1), cv_episode(2)], [cv_episode(1), cv_episode(2)], 2))
        t.eq("the episode it covers is staged as its record and its projection alone — published, nothing else moved, "
             "none of the database's rows changed — which names the holder and the version the holder is staged with, "
             "where katalog-manager's projector puts them",
             ([m["kind"] for m in e2["moves"]], [e2["db"][k] for k in ("sources", "versions", "assets", "subtitles", "extras")],
              sorted(os.listdir(e2["stagedDir"])), decided(e2).get("coveredBy"), decided(e2).get("primaryVersionId"),
              decided(e2)["numbering"]["aired"], list(decided(e2))),
             (["publish"], [[]] * 5, ["checksums.sha256", "item.json", "metadata.json"], cv_episode(1),
              e1["db"]["versions"][0]["versionId"], {"season": 1, "episode": 2, "episodeEnd": None},
              ["match", "primaryVersionId", "coveredBy", "reference", "numbering"]))
        e10 = units[cv_episode(10)]
        t.eq("a file of three episodes, as its name chains them, covers all three, and its holder's place ends at the third",
             (source(e10)["covers"], decided(e10)["numbering"]["aired"]["episodeEnd"],
              [decided(units[cv_episode(n)]).get("coveredBy") for n in (11, 12)]),
             ([cv_episode(10), cv_episode(11), cv_episode(12)], 12, [cv_episode(10)] * 2))
        t.eq("a file the catalog says covers an episode covers it, whatever its name: taken in, its source covers both",
             (source(e3)["covers"], e3["db"]["versions"][0]["takenIn"], decided(e3)["numbering"]["aired"]["episodeEnd"],
              decided(e4).get("coveredBy"), decided(e4).get("primaryVersionId"), e4["db"]["sources"]),
             ([cv_episode(3), cv_episode(4)], True, 4, cv_episode(3), e3["db"]["versions"][0]["versionId"], []))
        t.eq("a name that numbers an episode the export does not hold, or one with a file of its own, or a resolution, "
             "covers nothing, as a file of one episode does",
             [units[cv_episode(n)]["db"]["sources"][0]["covers"] for n in (5, 7, 8, 9)]
             + [decided(units[cv_episode(n)])["numbering"]["aired"]["episodeEnd"] for n in (5, 7, 9)],
             [[], [], [], [], None, None, None])
        t.ok("and the episode with a file of its own keeps it: its own file wins, and the run says so",
             f"episode {cv_episode(8)} (S01E08) has a file of its own, which wins: it is not covered" in text
             and units[cv_episode(8)]["db"]["versions"], text)
        staged = tree_files(os.path.join(rundir, "staged"))
        code, again = run_with(env, FROM_CATALOG, "--platform", "--export", export, "--root", root, "--run", PF_RUN)
        t.ok("a second run stages the same records", code == 0 and tree_files(os.path.join(rundir, "staged")) == staged,
             again)

        fresh = os.path.join(tmp, "after.json")
        _, stale = adopt(root, PF_RUN, export, fresh)
        t.eq("the adopt links every episode a source covers after its holder to the holder",
             (stale, {r["id"]: r["coveredBy"] for r in jload(fresh)["items"] if r.get("coveredBy")}),
             ([], {cv_episode(2): cv_episode(1), cv_episode(4): cv_episode(3), cv_episode(11): cv_episode(10),
                   cv_episode(12): cv_episode(10)}))
        if have_jsonschema():
            code, vtext = run(VALIDATOR, "--check-checksums", root)
            t.ok("validate-library-v2.py finds the adopted library valid: each covered episode names its holder, plays "
                 "its version and keeps nothing of its own", code == 0 and vtext.strip().endswith("OK"), vtext)
        else:
            t.skip("validate-library-v2.py finds the adopted library valid", "jsonschema is not importable here")
        code, mtext = run(MEDIA_CHECK, "--checksums", root)
        t.ok("and the media check", code == 0 and mtext.strip().endswith("OK"), mtext)
        rebuilt, _ = rows_of(root, "--text-language", "und")
        t.eq("the rebuild gives each covered episode its holder, from the source that covers it, and nothing of its own "
             "to play", {iid: (r["coveredBy"], r["playbackAssets"]) for iid, r in rebuilt.items() if r["coveredBy"]},
             {cv_episode(2): (cv_episode(1), []), cv_episode(4): (cv_episode(3), []), cv_episode(11): (cv_episode(10), []),
              cv_episode(12): (cv_episode(10), [])})
        compare = lambda *extra: run(REBUILD, root, "--compare", fresh, "--arrivals-root", os.path.join(root, ".work"),
                                     "--ignore-fields", "id,path,hash", "--text-language", "und", *extra)
        code, ctext = compare()
        t.ok("the compare finds the disc image the plan left out the database's alone: a record storage does not hold",
             code == 1 and report_section(ctext, "missing record") == [CV_DISC], ctext)
        code, ctext = compare("--subset")
        t.ok("and otherwise agrees with the database the adopt left, the covered episodes linked",
             code == 0 and "the tree and the database agree" in ctext, ctext)


# ---------------------------------------------------------------- credits
CREDITED_SERIES = "22222222-3333-4444-8555-777777777777"


def cid(n):
    """The id of the nth person the credit cases credit."""
    return f"c1000000-0000-4000-8000-{n:012x}"


def credited(pid, name, role, job=None, character=None, order=None, episodeCount=None):
    """One of an item's people entries as the catalog exports it once credits are general."""
    return {"personId": pid, "name": name, "role": role, "job": job, "character": character, "order": order,
            "episodeCount": episodeCount}


def recorded(entry):
    """The credit metadata.json holds for one such entry: every field of it, and no TMDB person id."""
    return dict(entry, tmdbPerson=None)


def movie_credits():
    """A movie's credits in the order a reader shows them: by role — the ones it knows first, in their
    order, any other after them alphabetically, so casting comes after editor — then by billing order,
    a credit without one last, then by name, and namesakes by their personId."""
    return [
        credited(cid(1), "First Lead", "actor", character="The Hero", order=0),
        credited(cid(2), "Second Lead", "actor", character="The Friend", order=1),
        credited(cid(7), "Sam Same", "actor", character="A Twin", order=2),
        credited(cid(8), "Sam Same", "actor", character="The Other Twin", order=2),
        credited(cid(6), "An Extra", "actor", character="Passer-by"),
        credited(cid(5), "Bea Bystander", "actor", character="Passer-by"),
        credited(DIRECTOR, "A Director", "director", "Director", order=0),
        credited(cid(3), "Ari Author", "writer", "Novel", order=0),
        credited(cid(4), "Wren Writer", "writer", "Screenplay, Story", order=1),
        credited(DIRECTOR, "A Director", "writer", "Screenplay", order=2),
        credited(cid(10), "Pia Producer", "producer", "Executive Producer, Producer", order=0),
        credited(cid(9), "Cam Composer", "composer", "Original Music Composer", order=0),
        credited(cid(11), "Ed Editor", "editor", "Editor", order=0),
        credited(cid(13), "Cas Ting", "casting", "Casting"),
        credited(cid(12), "Pat Designer", "production-designer", "Production Design", order=0),
    ]


def series_credits():
    """A series' credits in that order: a character joined across its episodes, how many episodes credit
    each person, and a count of none."""
    return [
        credited(cid(1), "First Lead", "actor", character="The Hero / The Hero's Double", order=0, episodeCount=10),
        credited(cid(14), "Guest Star", "actor", character="A Visitor", order=5, episodeCount=1),
        credited(cid(15), "Cree Ator", "creator"),
        credited(cid(15), "Cree Ator", "writer", "Story, Teleplay", order=0, episodeCount=4),
        credited(cid(16), "Dee Photography", "cinematographer", "Director of Photography", order=0, episodeCount=0),
    ]


def credits_export(share, shuffle=lambda xs: list(reversed(xs))):
    """fake_export's catalog with the movie's credits general and a series beside it, each item's
    people listed in the order shuffle gives them — the reverse of a reader's, by default."""
    export, media, packages, iid, _ = fake_export(share)
    e = jload(export)
    e["items"][0]["people"] = shuffle(movie_credits())
    e["items"].append({"id": CREDITED_SERIES, "type": "series", "title": "Example Series", "createdAt": "2026-07-01T09:00:00Z",
                       "metadataLocked": False, "people": shuffle(series_credits())})
    jwrite(export, e)
    return export, media, packages, iid


def credits_of(out, iid, kind="movies"):
    return jload(os.path.join(out, kind, iid[:2], iid, "metadata.json"))["credits"]


def test_credits(t):
    """Every field of the export's credits, into metadata.json in the order a reader shows them,
    whatever order the export lists them in; an export from before credits were general still works,
    and what a record cannot hold stays out of it."""
    with tempfile.TemporaryDirectory() as tmp:
        export, media, packages, iid = credits_export(os.path.join(tmp, "share"))
        out = os.path.join(tmp, "library")
        code, text = run(FROM_CATALOG, "--export", export, "--packages", packages, "--media", media, "--out", out)
        t.eq("a credit carries every field the export has: the job in the source's own words, the character, "
             "the billing order within the role and a series' episode count",
             (code, credits_of(out, iid), credits_of(out, CREDITED_SERIES, "series")),
             (0, [recorded(c) for c in movie_credits()], [recorded(c) for c in series_credits()]))
        t.eq("listed by role, the ones a reader knows in their order and any other after them alphabetically, then "
             "by billing order, none last, then by name, and namesakes by their personId",
             [(c["role"], c["name"], c["personId"][-2:]) for c in credits_of(out, iid)],
             [(c["role"], c["name"], c["personId"][-2:]) for c in movie_credits()])
        if have_jsonschema():
            code, vtext = run(VALIDATOR, "--check-checksums", out)
            t.ok("the tree passes validate-library-v2.py", code == 0 and vtext.strip().endswith("OK"), vtext)
        else:
            t.skip("the tree passes validate-library-v2.py", "jsonschema is not importable here")

        # the order the export lists them in makes no difference
        for how, shuffle in (("in a reader's order", list), ("interleaved", lambda xs: xs[1::2] + xs[::2])):
            again, other = os.path.join(tmp, f"again-{how}"), os.path.join(tmp, f"share-{how}")
            export2, media2, packages2, _ = credits_export(other, shuffle)
            code, text = run(FROM_CATALOG, "--export", export2, "--packages", packages2, "--media", media2, "--out", again)
            t.ok(f"the same credits listed {how} write the same projections, byte for byte",
                 code == 0 and all(open(os.path.join(again, k, i[:2], i, "metadata.json"), "rb").read() ==
                                   open(os.path.join(out, k, i[:2], i, "metadata.json"), "rb").read()
                                   for k, i in (("movies", iid), ("series", CREDITED_SERIES))), text)

    # ---- an export from before credits were general: a person, a name and a role
    with tempfile.TemporaryDirectory() as tmp:
        export, media, packages, iid, _ = fake_export(os.path.join(tmp, "share"))
        out = os.path.join(tmp, "library")
        code, text = run(FROM_CATALOG, "--export", export, "--packages", packages, "--media", media, "--out", out)
        t.eq("an export whose credits carry only a person, a name and a role still works, every other field null",
             (code, credits_of(out, iid)),
             (0, [{"personId": DIRECTOR, "name": "A Director", "role": "director", "job": None, "character": None,
                   "order": None, "episodeCount": None, "tmdbPerson": None}]))

    # ---- what a record cannot hold is left out, with a note
    with tempfile.TemporaryDirectory() as tmp:
        export, media, packages, iid, _ = fake_export(os.path.join(tmp, "share"))
        e = jload(export)
        e["items"][0]["people"] = [
            credited(cid(1), "Shouted Role", "Director", "Director", order=0),
            credited(cid(2), "Spaced Role", "production designer", "Production Design", order=0),
            credited(cid(3), "Half Order", "actor", character="", order=1.5),
            credited(cid(4), "Text Order", "actor", character="Someone", order="3"),
            credited(cid(5), "Float Order", "actor", character="Someone Else", order=2.0),
            credited(cid(6), "Fewer Than None", "writer", "Story,\nTeleplay", order=0, episodeCount=-2),
            credited(cid(7), "Some Episodes", "writer", "Screenplay", order=1, episodeCount=2.5)]
        jwrite(export, e)
        out = os.path.join(tmp, "library")
        code, text = run(FROM_CATALOG, "--export", export, "--packages", packages, "--media", media, "--out", out)
        got = {c["name"]: c for c in credits_of(out, iid)}
        t.ok("a role that is not a token is no credit at all, and the run says so",
             code == 0 and not {"Shouted Role", "Spaced Role"} & set(got)
             and "'Shouted Role' is credited as 'Director', which is not a role token" in text
             and "'Spaced Role' is credited as 'production designer', which is not a role token" in text, text)
        t.eq("a billing order that is not a whole number is dropped, and one written as 2.0 is 2",
             [(got[n]["order"]) for n in ("Half Order", "Text Order", "Float Order")], [None, None, 2])
        t.ok("and the run says which", "the billing order of 'Half Order' as actor 1.5 is not a whole number, dropped" in text
             and "the billing order of 'Text Order' as actor '3' is not a whole number, dropped" in text, text)
        t.eq("an episode count below nothing, or not a whole number, is dropped, and the run says so",
             ([got[n]["episodeCount"] for n in ("Fewer Than None", "Some Episodes")],
              "the episode count of 'Fewer Than None' as writer -2 is not a whole number of at least 0, dropped" in text,
              "the episode count of 'Some Episodes' as writer 2.5 is not a whole number of at least 0, dropped" in text),
             ([None, None], True, True))
        t.eq("a job and a character are one line, and an empty one is none",
             (got["Fewer Than None"]["job"], got["Half Order"]["character"]), ("Story, Teleplay", None))
        if have_jsonschema():
            code, vtext = run(VALIDATOR, out)
            t.ok("and what is written is a valid record", code == 0, vtext)

    # ---- the rebuild gives every field back, and --compare compares them both ways
    ignore = "id,path,hash,codec,resolution,bitrateKbps,durationMs,sizeBytes"
    with tempfile.TemporaryDirectory() as tmp:
        export, media, packages, iid = credits_export(os.path.join(tmp, "share"))
        out = os.path.join(tmp, "library")
        run(FROM_CATALOG, "--export", export, "--packages", packages, "--media", media, "--out", out)
        rows, _ = rows_of(out, "--text-language", "und")
        t.eq("the rebuild gives every credit back, each field under the export's name, in the order on storage",
             (rows[iid]["people"], rows[CREDITED_SERIES]["people"]), (movie_credits(), series_credits()))
        code, text = run(REBUILD, out, "--compare", export, "--ignore-fields", ignore)
        t.ok("and the tree agrees with the export it came from, which lists the credits otherwise, two namesakes in "
             "one role among them: a credit is matched by its person and role",
             code == 0 and "the tree and the database agree" in text, text)

        pristine = jload(export)

        def against(change, tree=out):
            """--compare of tree against the export with change made to it."""
            e = json.loads(json.dumps(pristine))
            change(e)
            path = os.path.join(tmp, "changed.json")
            jwrite(path, e)
            return run(REBUILD, tree, "--compare", path, "--ignore-fields", ignore)

        def credit_in(e, name, role):
            return next(c for r in e["items"] for c in r.get("people") or [] if c["name"] == name and c["role"] == role)

        for what, change, phrase in (
                ("a job", lambda e: credit_in(e, "Wren Writer", "writer").update(job="Screenplay"), "people.job: 1 difference"),
                ("a character", lambda e: credit_in(e, "Second Lead", "actor").update(character="The Rival"),
                 "people.character: 1 difference"),
                ("a billing order", lambda e: credit_in(e, "Ari Author", "writer").update(order=3), "people.order: 1 difference"),
                ("an episode count", lambda e: credit_in(e, "Guest Star", "actor").update(episodeCount=2),
                 "people.episodeCount: 1 difference"),
                ("whom it credits", lambda e: e["items"][0]["people"].remove(credit_in(e, "Cas Ting", "casting")),
                 "people: 1 difference")):
            code, text = against(change)
            t.ok(f"--compare catches the database changing {what}", code == 1 and phrase in text, text)
        code, text = against(lambda e: credit_in(e, "Ed Editor", "editor").update(name="Edwin Editor"))
        t.ok("and the name a credit records, as that credit changed — not one credit lost and another gained",
             code == 1 and f"{iid}: storage 'Ed Editor' != database 'Edwin Editor'" in text and "'missing'" not in text, text)

        code, text = against(lambda e: [[c.pop(k) for k in ("job", "character", "order", "episodeCount")]
                                        for r in e["items"] for c in r.get("people") or []])
        t.ok("an export from before credits were general, a person, a name and a role, makes no tree differ that has "
             "every field", code == 0 and "the tree and the database agree" in text, text)

        # a tree from before: the credits the catalog kept then, an actor's and a director's, as they were written
        old = os.path.join(tmp, "before")
        shutil.copytree(out, old)
        for kind, i in (("movies", iid), ("series", CREDITED_SERIES)):
            p = os.path.join(old, kind, i[:2], i, "metadata.json")
            jwrite(p, dict(jload(p), credits=[{"personId": c["personId"], "name": c["name"], "role": c["role"], "character": None,
                                               "order": None, "tmdbPerson": None}
                                              for c in jload(p)["credits"] if c["role"] in ("actor", "director")]))
        code, text = run(REBUILD, old, "--compare", export, "--ignore-fields", ignore)
        lines = [line.strip() for line in text.splitlines() if ": storage " in line]
        t.ok("a tree from before, against an export that has them, reads as the database knowing more: each "
             "difference is a credit or a field of one that storage has nothing of, and it fails the compare",
             code == 1 and lines and all(x.split(": storage ", 1)[1].startswith(("None != database ", "'missing' != database "))
                                         for x in lines), text)
        t.ok("credit by credit, matched by person and role: the credits the catalog did not keep then, and of the ones "
             "it did the job, the character, the billing order and the episode count the database knows",
             all(f"{key}: {n} difference(s)" in text for key, n in (("people", 11), ("people.job", 1), ("people.character", 8),
                                                                     ("people.order", 7), ("people.episodeCount", 2)))
             and "29 difference(s)" in text, text)
        code, text = against(lambda e: [r.update(people=[dict(c, job=None, character=None, order=None, episodeCount=None)
                                                         for c in r["people"] if c["role"] in ("actor", "director")])
                                        for r in e["items"] if r.get("people")], tree=old)
        t.ok("and where the database knows no more than that tree, a field it writes null is no difference",
             code == 0 and "the tree and the database agree" in text, text)
        code, text = run(FROM_CATALOG, "--export", export, "--out", old, "--projections-only")
        code, text = run(REBUILD, old, "--compare", export, "--ignore-fields", ignore)
        t.ok("projected again, the tree knows what the database knows, and agrees with it",
             code == 0 and "the tree and the database agree" in text, text)


# ---------------------------------------------------------------- people
def test_people(t):
    """A catalog without person records knows a personId and a name per credit, and that is what the
    record holds; an export with a people list fills every field it carries."""
    png = (b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + (4).to_bytes(4, "big") +
           (5).to_bytes(4, "big") + b"\x08\x06\x00\x00\x00" + b"\x00" * 4)
    director, other = "99999999-8888-4777-8666-555555555555", "77777777-6666-4555-8444-333333333333"

    def person_doc(out, pid):
        p = os.path.join(out, "people", pid[:2], pid, "person.json")
        return jload(p) if os.path.isfile(p) else None

    with tempfile.TemporaryDirectory() as tmp:
        share = os.path.join(tmp, "share")
        export, media, packages, iid, _ = fake_export(share)
        out = os.path.join(tmp, "library")
        code, text = run(FROM_CATALOG, "--export", export, "--packages", packages, "--media", media, "--out", out)
        doc = person_doc(out, director)
        t.ok("a credited person gets a record", code == 0 and doc is not None, text)
        t.eq("which holds what the credit knows and nothing else",
             (doc or {}).get("name"), "A Director")
        t.ok("and leaves the rest empty rather than guessed",
             doc and not doc["biography"] and not doc["externalIds"] and not doc["images"] and doc["sortName"] is None)
        rows, built = rows_of(out, "--text-language", "und")
        t.eq("the rebuild restores the person row", [(p["id"], p["name"]) for p in built["people"]],
             [(director, "A Director")])

        # ---- an export that lists its people: every field it carries crosses over
        e = jload(export)
        e["people"] = [{"id": director, "name": "A Director", "sortName": "Director, A", "alsoKnownAs": ["A. D."],
                        "birthDate": "1970-01-02T00:00:00Z", "birthPlace": "Example Town",
                        "biography": "Directs examples.", "metadataLocked": True,
                        "externalIds": [{"source": "tmdb", "externalId": "42"}, {"source": "imdb", "externalId": "tt123"}],
                        "artwork": [{"kind": "profile", "base64": base64.b64encode(png).decode(),
                                     "fetchedAt": "2026-07-01T09:05:00Z"},
                                    {"kind": "poster", "base64": base64.b64encode(png).decode()}]},
                       {"id": other, "name": "Credited Nowhere"}]
        jwrite(export, e)
        items_before = stamps(os.path.join(out, "movies"))
        code, text = run(FROM_CATALOG, "--export", export, "--out", out, "--people-only")
        doc = person_doc(out, director)
        t.ok("--people-only needs neither the package store nor the originals", code == 0, text)
        t.ok("and rewrites no item record: every file under movies/ is the one that was there",
             stamps(os.path.join(out, "movies")) == items_before)
        t.eq("the fields a people list carries become the person's",
             {k: doc[k] for k in ("sortName", "alsoKnownAs", "birthDate", "birthPlace", "biography", "externalIds")},
             {"sortName": "Director, A", "alsoKnownAs": ["A. D."], "birthDate": "1970-01-02",
              "birthPlace": "Example Town", "biography": {"und": "Directs examples."},
              "externalIds": {"tmdbPerson": "42"}})
        t.ok("and a lock on the person is projected with them", doc["curation"]["metadataLocked"] is True)
        t.ok("a title id is not a person's imdb id, and is dropped with a note",
             "'tt123' is not a valid imdb id" in text and "imdb" not in doc["externalIds"], text)
        portrait = os.path.join(out, "people", director[:2], director, doc["images"][0]["file"]) \
            if doc["images"] else ""
        t.ok("the portrait is written beside the record, named by its own content",
             len(doc["images"]) == 1 and os.path.isfile(portrait)
             and doc["images"][0]["sha256"] == "sha256:" + hashlib.sha256(png).hexdigest(), doc["images"])
        t.ok("and an item's kind of image is not a person's", "kind 'poster' is not one a person record holds" in text, text)
        t.ok("a person the list holds is written even when nothing credits them",
             (person_doc(out, other) or {}).get("name") == "Credited Nowhere")
        if have_jsonschema():
            code, vtext = run(VALIDATOR, out)
            t.ok("a tree with its people passes validate-library-v2.py", code == 0, vtext)

        # ---- a projection is replaced whole: an image it stops naming goes with it
        e["people"][0]["artwork"] = []
        jwrite(export, e)
        run(FROM_CATALOG, "--export", export, "--out", out, "--people-only")
        t.ok("a portrait the new projection drops is removed by the writer that dropped it",
             not os.path.exists(portrait) and person_doc(out, director)["images"] == [])

        # ---- one person, credited under two names: the record says which, and the run says so
        e = jload(export)
        e.pop("people")
        e["items"][0]["people"].append({"personId": director, "name": "A. Director", "role": "writer"})
        jwrite(export, e)
        code, text = run(FROM_CATALOG, "--export", export, "--out", out, "--people-only")
        t.ok("a person credited under two names is recorded once, with a note",
             code == 0 and "credited under 2 names" in text and person_doc(out, director)["name"] == "A Director", text)

    with tempfile.TemporaryDirectory() as tmp:
        export, media, packages, iid, _ = fake_export(os.path.join(tmp, "share"))
        out = os.path.join(tmp, "library")
        code, text = run(FROM_CATALOG, "--export", export, "--out", out, "--people-only", "--dry-run")
        t.ok("a dry run of --people-only writes nothing", code == 0 and not os.path.exists(out), text)
        code, text = run(FROM_CATALOG, "--export", export, "--out", out)
        t.ok("without --people-only the package store and the originals are required",
             code != 0 and "--packages and --media are needed" in text, text)


# ---------------------------------------------------------------- the people list in full
def png(width, height):
    """The header of a PNG of this size: enough for every reader of the format to know it by."""
    return (b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + width.to_bytes(4, "big") +
            height.to_bytes(4, "big") + b"\x08\x06\x00\x00\x00" + b"\x00" * 4)


OLD_PORTRAIT, NEW_PORTRAIT = png(4, 5), png(6, 7)
DIRECTOR = "99999999-8888-4777-8666-555555555555"


def full_person(**changes):
    """A person as the catalog's people list carries one, every field filled: the shape
    library-v2-from-catalog.py maps into person.json and --compare compares. TMDB reported a change
    the day after the person died, the database fetched it the day after that, and the row was
    modified with what it fetched: a new portrait, now the primary one, beside the old."""
    entry = {"id": DIRECTOR, "name": "A Director", "sortName": "Director, A", "alsoKnownAs": ["A. D.", "Ann Director"],
             "birthDate": "1970-01-02", "deathDate": "2026-09-28", "birthPlace": "Example Town",
             "biography": {"en": "Directs examples.", "de": "Führt Beispiele vor."},
             "externalIds": {"tmdbPerson": "42", "imdb": "nm0000042"}, "knownForDepartment": "Directing",
             "metadataLocked": False, "lockedFields": ["biography"],
             "fieldOrigins": {"name": "tmdb", "birthDate": "tmdb", "deathDate": "tmdb", "biography": "manual"},
             "tmdbFetchedAt": "2026-09-30T08:00:00Z", "tmdbChangedAt": "2026-09-29", "modifiedAt": "2026-09-30T08:00:05Z",
             "artwork": [{"kind": "profile", "contentType": "image/png", "base64": base64.b64encode(OLD_PORTRAIT).decode(),
                          "sha256": hashlib.sha256(OLD_PORTRAIT).hexdigest(), "width": 4, "height": 5,
                          "isPrimary": False, "sourcePath": "/old-portrait.png", "fetchedAt": "2026-07-01T09:05:00Z"},
                         {"kind": "profile", "contentType": "image/png", "base64": base64.b64encode(NEW_PORTRAIT).decode(),
                          "sha256": "sha256:" + hashlib.sha256(NEW_PORTRAIT).hexdigest(), "width": 6, "height": 7,
                          "isPrimary": True, "sourcePath": "/new-portrait.png", "fetchedAt": "2026-09-30T08:00:01Z"}]}
    entry.update(changes)
    return entry


def full_export(share, trailer=False, **changes):
    """fake_export's catalog with its people list in full, the item's own freshness with it, and an
    export taken after all of it; with trailer, the downloaded trailer too."""
    export, media, packages, iid, _ = fake_export(share, trailer=trailer)
    e = jload(export)
    e["exportedAt"] = "2026-10-01T12:00:00Z"
    e["items"][0].update(tmdbFetchedAt="2026-08-01T08:59:00Z", tmdbChangedAt="2026-07-30")
    e["people"] = [full_person(**changes), {"id": "77777777-6666-4555-8444-333333333333", "name": "Credited Nowhere"}]
    jwrite(export, e)
    return export, media, packages, iid


def portrait(data, fetched, ref, primary=False):
    """The image entry person.json holds for one of full_person's portraits."""
    digest = hashlib.sha256(data).hexdigest()
    return {"kind": "profile", **({"primary": True} if primary else {}), "file": digest + ".png",
            "sha256": "sha256:" + digest, "contentType": "image/png", "sizeBytes": len(data),
            "width": int.from_bytes(data[16:20], "big"), "height": int.from_bytes(data[20:24], "big"),
            "language": None, "sourceUrl": None, "fetchedAt": fetched,
            "origin": {"source": "tmdb", "ref": ref, "fetchedAt": fetched}}


def test_people_in_full(t):
    """The people list the catalog exports, every field of it, into person.json — and what is not
    there, or not right, stays out of the record rather than being guessed."""
    other = "77777777-6666-4555-8444-333333333333"

    def person_doc(out, pid):
        p = os.path.join(out, "people", pid[:2], pid, "person.json")
        return jload(p) if os.path.isfile(p) else {}

    with tempfile.TemporaryDirectory() as tmp:
        export, media, packages, iid = full_export(os.path.join(tmp, "share"))
        out = os.path.join(tmp, "library")
        code, text = run(FROM_CATALOG, "--export", export, "--packages", packages, "--media", media, "--out", out)
        t.ok("an export with its people list in full becomes a tree", code == 0, text)
        t.eq("every field the people list carries becomes the person's, under the name person.json gives it",
             person_doc(out, DIRECTOR),
             {"schema": "zaentrum.library.person/2", "personId": DIRECTOR, "asOf": "2026-10-01T12:00:00Z",
              "projectedBy": "library-v2-from-catalog", "databaseUpdatedAt": "2026-09-30T08:00:05Z",
              "sources": {"tmdb": {"fetchedAt": "2026-09-30T08:00:00Z", "changedAt": "2026-09-29"}},
              "name": "A Director", "sortName": "Director, A", "alsoKnownAs": ["A. D.", "Ann Director"],
              "birthDate": "1970-01-02", "deathDate": "2026-09-28", "birthPlace": "Example Town",
              "knownForDepartment": "Directing",
              "biography": {"en": "Directs examples.", "de": "Führt Beispiele vor."},
              "externalIds": {"tmdbPerson": "42", "imdb": "nm0000042"},
              "images": [portrait(OLD_PORTRAIT, "2026-07-01T09:05:00Z", "/old-portrait.png"),
                         portrait(NEW_PORTRAIT, "2026-09-30T08:00:01Z", "/new-portrait.png", primary=True)],
              "curation": {"metadataLocked": False, "lockedFields": ["biography"], "notes": None},
              "fieldOrigins": {"name": "tmdb", "birthDate": "tmdb", "deathDate": "tmdb", "biography": "manual"}})
        folder = os.path.join(out, "people", DIRECTOR[:2], DIRECTOR)
        t.ok("each portrait is beside it, named by its own content",
             all(os.path.isfile(os.path.join(folder, i["file"])) for i in person_doc(out, DIRECTOR)["images"]))
        meta = jload(os.path.join(out, "movies", iid[:2], iid, "metadata.json"))
        t.eq("the item's projection says which state of its row it reflects, and when TMDB was last asked",
             (meta.get("databaseUpdatedAt"), meta.get("sources")),
             ("2026-08-01T09:00:00Z", {"tmdb": {"fetchedAt": "2026-08-01T08:59:00Z", "changedAt": "2026-07-30"}}))
        t.eq("its image, which the row says nothing more of, came from the catalog and is not marked primary",
             [({k: v for k, v in i["origin"].items()}, "primary" in i) for i in meta["images"]],
             [({"source": "legacy-catalog", "fetchedAt": "2026-07-01T09:05:00Z"}, False)])
        t.eq("a person the list holds by id and name alone is a record with nothing else in it",
             {k: v for k, v in person_doc(out, other).items() if k not in ("schema", "personId", "asOf", "projectedBy")},
             {"name": "Credited Nowhere", "sortName": None, "alsoKnownAs": [], "birthDate": None, "deathDate": None,
              "birthPlace": None, "knownForDepartment": None, "biography": {}, "externalIds": {}, "images": [],
              "curation": {"metadataLocked": False, "lockedFields": [], "notes": None},
              "fieldOrigins": {"name": "legacy-catalog"}})
        if have_jsonschema():
            code, vtext = run(VALIDATOR, "--check-checksums", out)
            t.ok("the tree passes validate-library-v2.py, the full person and the bare one alike",
                 code == 0 and vtext.strip().endswith("OK"), vtext)
        else:
            t.skip("the tree passes validate-library-v2.py", "jsonschema is not importable here")
        code, mtext = run(MEDIA_CHECK, "--checksums", out)
        t.ok("and the media check", code == 0 and "'people': 2" in mtext, mtext)

        # ---- the rebuild gives all of it back, and --compare compares all of it
        _, built = rows_of(out, "--text-language", "und")
        row = next((p for p in built["people"] if p["id"] == DIRECTOR), {})
        t.eq("the rebuild restores every field the people list carried into the person's row",
             {k: v for k, v in row.items() if k != "artwork"},
             {"id": DIRECTOR, "name": "A Director", "sortName": "Director, A", "alsoKnownAs": ["A. D.", "Ann Director"],
              "birthDate": "1970-01-02", "deathDate": "2026-09-28", "birthPlace": "Example Town",
              "knownForDepartment": "Directing", "biography": {"en": "Directs examples.", "de": "Führt Beispiele vor."},
              "externalIds": {"tmdbPerson": "42", "imdb": "nm0000042"}, "metadataLocked": False,
              "lockedFields": ["biography"],
              "fieldOrigins": {"name": "tmdb", "birthDate": "tmdb", "deathDate": "tmdb", "biography": "manual"},
              "tmdbFetchedAt": "2026-09-30T08:00:00Z", "tmdbChangedAt": "2026-09-29", "modifiedAt": "2026-09-30T08:00:05Z"})
        t.eq("and each portrait: its bytes by their hash, size and dimensions, whether it is primary, TMDB's path, "
             "when it was fetched",
             [{k: a.get(k) for k in ("kind", "contentType", "sha256", "sizeBytes", "width", "height", "isPrimary",
                                     "sourcePath", "fetchedAt")} for a in row.get("artwork") or []],
             [{"kind": "profile", "contentType": "image/png", "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
               "sizeBytes": len(data), "width": w, "height": h, "isPrimary": primary, "sourcePath": ref,
               "fetchedAt": fetched}
              for data, w, h, primary, ref, fetched in ((OLD_PORTRAIT, 4, 5, False, "/old-portrait.png", "2026-07-01T09:05:00Z"),
                                                        (NEW_PORTRAIT, 6, 7, True, "/new-portrait.png", "2026-09-30T08:00:01Z"))])
        item_row = rows_of(out, "--text-language", "und")[0][iid]
        t.eq("and the item's row the state it reflects and its TMDB freshness",
             (item_row["modifiedAt"], item_row["tmdbFetchedAt"], item_row["tmdbChangedAt"]),
             ("2026-08-01T09:00:00Z", "2026-08-01T08:59:00Z", "2026-07-30"))
        ignore = "id,path,hash,codec,resolution,bitrateKbps,durationMs,sizeBytes"
        code, ctext = run(REBUILD, out, "--compare", export, "--text-language", "und", "--ignore-fields", ignore)
        t.ok("the tree agrees with the export it came from, every field and every projection's freshness compared",
             code == 0 and "the tree and the database agree" in ctext and "freshness unknown" not in ctext, ctext)

        pristine = jload(export)

        def changed(change):
            """--compare of the tree against the export with the person's row changed, and nothing
            projected again."""
            e = json.loads(json.dumps(pristine))
            change(e["people"][0])
            path = os.path.join(tmp, "changed.json")
            jwrite(path, e)
            return run(REBUILD, out, "--compare", path, "--text-language", "und", "--ignore-fields", ignore)

        def portrait_row(i, **fields):
            return lambda p: p["artwork"][i].update(fields)

        for what, change, phrase in (
                ("the name", lambda p: p.update(name="Ann Director"), "people.name: 1 difference"),
                ("the name it sorts under", lambda p: p.update(sortName="Director, Ann"), "people.sortName: 1 difference"),
                ("another name", lambda p: p.update(alsoKnownAs=["A. D."]), "people.alsoKnownAs: 1 difference"),
                ("the birth date", lambda p: p.update(birthDate="1970-01-03"), "people.birthDate: 1 difference"),
                ("the death date", lambda p: p.update(deathDate=None), "people.deathDate: 1 difference"),
                ("the birthplace", lambda p: p.update(birthPlace="Another Town"), "people.birthPlace: 1 difference"),
                ("the department", lambda p: p.update(knownForDepartment="Writing"), "people.knownForDepartment: 1 difference"),
                ("the biography", lambda p: p.update(biography={"en": "Directs examples."}), "people.biography: 1 difference"),
                ("a reference id", lambda p: p["externalIds"].update(tmdbPerson="43"), "people.externalIds: 1 difference"),
                ("the lock", lambda p: p.update(metadataLocked=True), "people.metadataLocked: 1 difference"),
                ("the locked fields", lambda p: p.update(lockedFields=["biography", "name"]), "people.lockedFields: 1 difference"),
                ("where a field came from", lambda p: p["fieldOrigins"].update(name="manual"), "people.fieldOrigins: 1 difference"),
                ("when TMDB was asked", lambda p: p.update(tmdbFetchedAt="2026-09-30T09:00:00Z"), "people.tmdbFetchedAt: 1 difference"),
                ("when TMDB last changed the person", lambda p: p.update(tmdbChangedAt="2026-09-30"), "people.tmdbChangedAt: 1 difference"),
                ("which portrait is primary", lambda p: (portrait_row(0, isPrimary=True)(p), portrait_row(1, isPrimary=False)(p)),
                 "people.artwork.isPrimary: 2 difference"),
                ("the path TMDB lists a portrait under", portrait_row(1, sourcePath="/newer.png"), "people.artwork.sourcePath: 1 difference"),
                ("a portrait's width", portrait_row(1, width=60), "people.artwork.width: 1 difference"),
                ("a portrait's height", portrait_row(1, height=70), "people.artwork.height: 1 difference"),
                ("when a portrait was fetched", portrait_row(1, fetchedAt="2026-09-30T08:30:00Z"), "people.artwork.fetchedAt: 1 difference"),
                ("a portrait's type", portrait_row(1, contentType="image/jpeg"), "people.artwork.contentType: 1 difference"),
                # an image is one kind of its bytes: of another kind, it is another image
                ("a portrait's kind", portrait_row(1, kind="poster"), "people.artwork: 2 difference"),
                ("a portrait's bytes", portrait_row(1, base64=base64.b64encode(png(8, 9)).decode(), sha256=None),
                 "people.artwork: 2 difference"),
                ("the row, modified since", lambda p: p.update(modifiedAt="2026-09-30T09:00:00Z"),
                 "people: stale projection — the database changed after it was projected")):
            code, ctext = changed(change)
            t.ok(f"--compare catches the database changing {what}", code == 1 and phrase in ctext, ctext)

        # ---- what the export says that a record cannot hold, or that its bytes contradict
        e = jload(export)
        e["items"][0].update(tmdbFetchedAt=None)
        e["people"][0] = full_person(
            modifiedAt="last tuesday", tmdbFetchedAt=None, fieldOrigins={"name": "tmdb", "biography": "wikipedia"},
            artwork=[dict(a, isPrimary=True, contentType="image/jpeg", width=99) if i == 0 else a
                     for i, a in enumerate(full_person()["artwork"])] +
                    [dict(full_person()["artwork"][0], sha256="0" * 64, isPrimary=False)])
        jwrite(export, e)
        code, text = run(FROM_CATALOG, "--export", export, "--out", out, "--people-only")
        doc = person_doc(out, DIRECTOR)
        t.ok("a modifiedAt that is not a moment is dropped with a note, and the record does not say which state it is",
             code == 0 and "modifiedAt 'last tuesday' is not a moment, dropped" in text and "databaseUpdatedAt" not in doc, text)
        t.ok("a TMDB change date without the fetch it belongs to is not recorded, and the run says so",
             "sources" not in doc and "tmdbChangedAt 2026-09-29 without a tmdbFetchedAt is not recorded" in text, text)
        t.ok("an origin of a field a record cannot name is dropped with a note, the rest kept as the database has them",
             doc["fieldOrigins"] == {"name": "tmdb"} and "'wikipedia', which a record cannot name" in text, text)
        t.eq("two primary portraits: the first the export lists stays primary, and the second is not",
             [i.get("primary", False) for i in doc["images"]], [True, False])
        t.ok("and the run says which", "a second primary profile" in text, text)
        t.ok("the bytes decide what an image is, whatever the row says, and the run says where they disagree",
             doc["images"][0]["contentType"] == "image/png" and doc["images"][0]["width"] == 4
             and "says contentType 'image/jpeg', width 99 where its bytes say 'image/png', 4" in text, text)
        t.ok("an entry whose bytes are another's of its kind is that image, listed once, and the run says so",
             len(doc["images"]) == 2 and "profile artwork is byte-identical to another profile, listed once" in text, text)
        if have_jsonschema():
            code, vtext = run(VALIDATOR, out)
            t.ok("and what is written is still a valid record", code == 0, vtext)

        e["people"][0] = full_person(artwork=[dict(full_person()["artwork"][1], sha256="1" * 64)])
        jwrite(export, e)
        code, text = run(FROM_CATALOG, "--export", export, "--out", out, "--people-only")
        t.ok("a hash the row records that its bytes do not have is said, and the record names the bytes",
             code == 0 and "is not the hash of its bytes" in text
             and person_doc(out, DIRECTOR)["images"][0]["file"] == hashlib.sha256(NEW_PORTRAIT).hexdigest() + ".png", text)
        t.ok("and the portrait the new projection no longer names is removed with it",
             not os.path.exists(os.path.join(folder, hashlib.sha256(OLD_PORTRAIT).hexdigest() + ".png")))

    # ---- a person record goes stale — a death, a new photo — and projecting again makes it current
    with tempfile.TemporaryDirectory() as tmp:
        export, media, packages, iid = full_export(os.path.join(tmp, "share"))
        e = jload(export)
        e["people"][0] = full_person(deathDate=None, tmdbChangedAt="2026-06-30", tmdbFetchedAt="2026-07-01T09:05:00Z",
                                     modifiedAt="2026-07-01T09:05:30Z",
                                     artwork=[dict(full_person()["artwork"][0], isPrimary=True)])
        jwrite(export, e)
        out = os.path.join(tmp, "library")
        run(FROM_CATALOG, "--export", export, "--packages", packages, "--media", media, "--out", out)
        ignore = "id,path,hash,codec,resolution,bitrateKbps,durationMs,sizeBytes"
        code, text = run(REBUILD, out, "--compare", export, "--ignore-fields", ignore)
        t.ok("a person projected while alive agrees with the database of then", code == 0, text)
        e["people"][0] = full_person()
        jwrite(export, e)
        code, text = run(REBUILD, out, "--compare", export, "--ignore-fields", ignore)
        t.ok("once the database records the death and takes a new portrait, the record is a stale projection",
             code == 1 and report_section(text, "people: stale projection") == [DIRECTOR]
             and "the row was modified 2026-09-30T08:00:05Z, after the 2026-07-01T09:05:30Z this projection reflects" in text
             and "people.deathDate: 1 difference(s)" in text, text)
        items_before = stamps(os.path.join(out, "movies"))
        code, text = run(FROM_CATALOG, "--export", export, "--out", out, "--people-only")
        code, text = run(REBUILD, out, "--compare", export, "--ignore-fields", ignore)
        t.ok("projected again, it is current: the compare agrees, and no item record was written",
             code == 0 and "the tree and the database agree" in text and stamps(os.path.join(out, "movies")) == items_before,
             text)


# ---------------------------------------------------------------- projecting again, and nothing else
def is_projection(rel):
    """Whether a path under a library root is a projection or an image one lists — a metadata.json, a
    file in an item's metadata/, anything under people/ — rather than a record written once or the
    bytes one describes."""
    parts = rel.split(os.sep)
    return parts[0] == "people" or parts[-1] == "metadata.json" or (len(parts) > 1 and parts[-2] == "metadata")


def records_of(root):
    """Every file under root that is not a projection — every record written once, every checksums
    file, every byte a record describes — by its hash, size and modification time: a file written
    again, even with the same bytes, has a new time."""
    out = {}
    for base, dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(base, f)
            if not is_projection(os.path.relpath(p, root)):
                st = os.stat(p)
                out[os.path.relpath(p, root)] = (digest(p), st.st_size, st.st_mtime_ns)
    return out


def projections_of(root):
    """Every projection and every image one lists under root, by its bytes and modification time."""
    out = {}
    for base, dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(base, f)
            if is_projection(os.path.relpath(p, root)):
                out[os.path.relpath(p, root)] = (open(p, "rb").read(), os.stat(p).st_mtime_ns)
    return out


def export_of(tree):
    """The export of a database whose rows are the ones a tree rebuilds to, as the catalog prints it:
    every image with its bytes."""
    _, doc = rows_of(tree)
    folders = {jload(p)["itemId"]: os.path.dirname(p) for p in glob.glob(os.path.join(tree, "**", "item.json"), recursive=True)}
    for row in doc["items"]:
        for a in row["artwork"]:
            a["base64"] = base64.b64encode(open(os.path.join(folders[row["id"]], a["file"]), "rb").read()).decode()
    for p in doc["people"]:
        for a in p["artwork"]:
            a["base64"] = base64.b64encode(open(os.path.join(tree, "people", p["id"][:2], p["id"], a["file"]), "rb")
                                           .read()).decode()
    return {"exportedAt": "2026-10-02T08:00:00Z", "items": doc["items"], "people": doc["people"], "deletedItems": []}


def test_projections_only(t):
    """--projections-only writes the projections of what a tree holds from the export, and touches no
    record: a stale projection --compare reports is gone after one run, and every record keeps the
    bytes and the time it had."""
    ignore = "id,path,hash,codec,resolution,bitrateKbps,durationMs,sizeBytes"
    with tempfile.TemporaryDirectory() as tmp:
        export, media, packages, iid = full_export(os.path.join(tmp, "share"), trailer=True)
        out = os.path.join(tmp, "library")
        code, text = run(FROM_CATALOG, "--export", export, "--packages", packages, "--media", media, "--out", out)
        records, built = records_of(out), projections_of(out)
        t.ok("a tree built in full holds records of every kind beside its projections",
             code == 0 and all(any(k in r for r in records) for k in ("item.json", "/sources/", "/versions/", "/extras/",
                                                                       "checksums.sha256", ".complete"))
             and any(r.endswith("metadata.json") for r in built) and any(r.endswith("person.json") for r in built), text)
        code, text = run(FROM_CATALOG, "--export", export, "--out", out, "--projections-only")
        t.ok("--projections-only needs neither the package store nor the originals", code == 0, text)
        t.eq("and changes no byte of any record, nor touches one: each has the hash, size and time it had",
             records_of(out), records)
        t.eq("from the export the tree was built from, it writes the projections the build wrote, byte for byte",
             {k: v[0] for k, v in projections_of(out).items()}, {k: v[0] for k, v in built.items()})
        t.ok("and leaves every image already there as it is: an image is written once",
             all(projections_of(out)[k][1] == v[1] for k, v in built.items() if not k.endswith(".json")))
        t.ok("each projection goes through a temporary file and a rename, and none is left behind",
             not glob.glob(os.path.join(out, "**", "*.tmp"), recursive=True))

        # ---- the database changes, the projections go stale, and one run makes them current
        e = jload(export)
        e["exportedAt"] = "2026-10-02T08:00:00Z"
        e["items"][0].update(modifiedAt="2026-10-01T09:00:00Z", title="Example Film (Restored)", tagline="Restored.",
                             artwork=[{"kind": "poster", "contentType": "image/png", "fetchedAt": "2026-10-01T08:59:00Z",
                                       "base64": base64.b64encode(png(5, 5)).decode()}],
                             # matched again: the reference ids are others now
                             externalIds=[{"source": "tmdb", "externalId": "1234"},
                                          {"source": "imdb", "externalId": "tt8888888"}])
        e["people"][0] = full_person(modifiedAt="2026-10-01T09:00:00Z", biography={"en": "Directed examples."},
                                     artwork=[full_person()["artwork"][1]])
        changed = os.path.join(tmp, "changed.json")
        jwrite(changed, e)
        code, text = run(REBUILD, out, "--compare", changed, "--ignore-fields", ignore)
        t.ok("after the database changed, --compare reports the item's projection and the person's stale",
             code == 1 and report_section(text, "stale projection") == [iid]
             and report_section(text, "people: stale projection") == [DIRECTOR], text)
        dropped = os.path.join(out, "people", DIRECTOR[:2], DIRECTOR, hashlib.sha256(OLD_PORTRAIT).hexdigest() + ".png")
        poster = os.path.join(out, "movies", iid[:2], iid, "metadata", hashlib.sha256(png(2, 3)).hexdigest() + ".png")
        t.ok("(the item's poster before the change is in its metadata/)", os.path.isfile(poster))
        code, text = run_piped(FROM_CATALOG, "--export", changed, "--out", out, "--projections-only")
        t.ok("--projections-only runs piped into a pod's Python", code == 0, text)
        code, ctext = run(REBUILD, out, "--compare", changed, "--ignore-fields", ignore)
        t.ok("one run later no projection is stale, and the tree agrees with the database",
             code == 0 and "the tree and the database agree" in ctext and "stale projection" not in ctext, ctext)
        t.eq("and still no record has changed", records_of(out), records)
        item_dir = os.path.join(out, "movies", iid[:2], iid)
        rebuilt, _ = rows_of(out, "--text-language", "und")
        t.eq("the projection holds the reference ids the item has now, and item.json the ones it was created with",
             (jload(os.path.join(item_dir, "metadata.json"))["externalIds"],
              jload(os.path.join(item_dir, "item.json"))["externalIds"],
              [x["externalId"] for x in rebuilt[iid]["externalIds"] if x["source"] == "imdb"]),
             ({"tmdbMovie": "1234", "imdb": "tt8888888"}, {"tmdbMovie": "1234", "imdb": "tt9999999"}, ["tt8888888"]))
        t.ok("a portrait the new projection no longer lists is left where it was, for the sweep, and so is a poster",
             os.path.isfile(dropped) and os.path.isfile(poster))
        if have_jsonschema():
            code, vtext = run(VALIDATOR, "--check-checksums", out)
            t.ok("the tree it leaves is a valid record, the image and the portrait it dropped notes",
                 code == 0 and "person.json no longer lists" in vtext and "metadata.json no longer lists" in vtext, vtext)
        else:
            t.skip("the tree it leaves is a valid record", "jsonschema is not importable here")
        code, stext = run(SWEEP, out, "--export", changed, "--grace", "0")
        t.ok("and the sweep is what collects them", dropped in swept(stext, out) and poster in swept(stext, out), stext)

        # ---- a projection is replaced whole, a second run writes the same, a dry run writes nothing
        meta = os.path.join(out, "movies", iid[:2], iid, "metadata.json")
        jwrite(meta, dict(jload(meta), junk="a key no export holds"))
        run(FROM_CATALOG, "--export", changed, "--out", out, "--projections-only")
        t.ok("a projection is replaced whole, never merged: what the export does not hold is gone", "junk" not in jload(meta))
        before = projections_of(out)
        run(FROM_CATALOG, "--export", changed, "--out", out, "--projections-only")
        t.eq("a second run writes the same projections", {k: v[0] for k, v in projections_of(out).items()},
             {k: v[0] for k, v in before.items()})
        e["items"][0].update(title="Example Film (Dry)")
        jwrite(changed, e)
        snapshot = stamps(out)
        code, text = run(FROM_CATALOG, "--export", changed, "--out", out, "--projections-only", "--dry-run")
        t.ok("a dry run writes nothing", code == 0 and stamps(out) == snapshot, text)

        # ---- what the tree does not hold is not created
        e["items"].append({"id": "22222222-3333-4444-8555-666666666666", "type": "movie", "title": "Not On Storage",
                           "createdAt": "2026-10-01T09:00:00Z"})
        e["people"].append({"id": "33333333-4444-4555-8666-777777777777", "name": "Not On Storage Either"})
        jwrite(changed, e)
        code, text = run(FROM_CATALOG, "--export", changed, "--out", out, "--projections-only")
        t.ok("an item the tree does not hold is skipped with a note, and no folder is made for it",
             code == 0 and "22222222-3333-4444-8555-666666666666: has no folder on storage" in text
             and not os.path.exists(os.path.join(out, "movies", "22")), text)
        t.ok("and so is a person", "33333333-4444-4555-8666-777777777777: has no folder on storage" in text
             and not os.path.exists(os.path.join(out, "people", "33")), text)

        # ---- the version that plays by default: the projection's, while it is there; else the build's
        primary = jload(meta)["library"]["primaryVersionId"]
        undecided = lambda: jwrite(meta, dict(jload(meta), library={k: v for k, v in jload(meta)["library"].items()
                                                                     if k != "primaryVersionId"}))
        for given in ((), ("--packages", packages)):
            undecided()
            run(FROM_CATALOG, "--export", changed, "--out", out, "--projections-only", *given)
            t.eq("a projection that names no default version gets the one the build derived from the export's packages"
                 + (", the store given" if given else ", the store not given"),
                 jload(meta)["library"].get("primaryVersionId"), primary)
        e["items"][0]["playbackAssets"][1]["path"] = "/elsewhere/stores/manifest.json"
        jwrite(changed, e)
        run(FROM_CATALOG, "--export", changed, "--out", out, "--projections-only")
        t.eq("and one whose packages the export now names otherwise keeps the one the projection named",
             jload(meta)["library"].get("primaryVersionId"), primary)
        write_event(os.path.dirname(meta), {"schema": "zaentrum.library.event/2", "eventId": str(uuid.uuid4()),
                                           "at": "2026-10-01T10:00:00Z", "by": "test", "kind": "version-removed",
                                           "versionId": primary})
        records = records_of(out)
        run(FROM_CATALOG, "--export", changed, "--out", out, "--projections-only")
        t.ok("a version an event removed is not one a projection names", "primaryVersionId" not in jload(meta)["library"])
        t.eq("and the event, a record like any other, is left as it is", records_of(out), records)
        if have_jsonschema():
            code, vtext = run(VALIDATOR, out)
            t.ok("so the projection is valid", code == 0, vtext)

        code, text = run(FROM_CATALOG, "--export", changed, "--out", out, "--projections-only", "--people-only")
        t.ok("--projections-only and --people-only exclude each other", code != 0 and "exclude each other" in text, text)

    # ---- the example tree: every kind of record there is, and none of them touched
    with tempfile.TemporaryDirectory() as tmp:
        tree = os.path.join(tmp, "library")
        shutil.copytree(EXAMPLES, tree)
        export = os.path.join(tmp, "catalog.json")
        jwrite(export, export_of(tree))
        records = records_of(tree)
        code, text = run(FROM_CATALOG, "--export", export, "--out", tree, "--projections-only", "--text-language", "en")
        t.ok("on the example tree — removed versions, a deleted original, a superseded package, extras, a retired "
             "extra, episodes — it projects every item and person",
             code == 0 and f"'items': {len(glob.glob(os.path.join(tree, '**', 'item.json'), recursive=True))}" in text
             and "'people': 3" in text, text)
        t.eq("and changes no byte of any of its records, nor touches one", records_of(tree), records)
        if have_jsonschema():
            code, vtext = run(VALIDATOR, "--check-checksums", tree)
            t.ok("and leaves a valid record", code == 0, vtext)

    # ---- the catalog corrects a mismatched title: a person credited wrongly is deleted, and swept once the
    # title is projected again
    with tempfile.TemporaryDirectory() as tmp:
        wrong = "44444444-5555-4666-8777-888888888888"
        export, media, packages, iid = full_export(os.path.join(tmp, "share"))
        e = jload(export)
        e["items"][0]["people"].append({"personId": wrong, "name": "Wrongly Credited", "role": "actor"})
        e["people"].append({"id": wrong, "name": "Wrongly Credited", "modifiedAt": "2026-09-01T10:00:00Z"})
        jwrite(export, e)
        out = os.path.join(tmp, "library")
        run(FROM_CATALOG, "--export", export, "--packages", packages, "--media", media, "--out", out)
        folder = os.path.join(out, "people", wrong[:2], wrong)
        e["exportedAt"] = "2026-10-02T08:00:00Z"
        e["items"][0]["people"] = [c for c in e["items"][0]["people"] if c["personId"] != wrong]
        e["items"][0]["modifiedAt"] = "2026-10-02T05:00:00Z"
        e["people"] = [p for p in e["people"] if p["id"] != wrong]
        e["deletedItems"] = [{"id": wrong, "type": "person", "deletedAt": "2026-10-02T06:00:00Z", "deletedBy": "catalog"}]
        fixed = os.path.join(tmp, "fixed.json")
        jwrite(fixed, e)
        aged(out)
        code, text = run(SWEEP, out, "--export", fixed, "--grace", "0")
        t.ok("a deleted person the title's stale projection still credits is kept by the sweep",
             folder not in swept(text, out) and "still credits them" in text, text)
        code, text = run(REBUILD, out, "--compare", fixed, "--ignore-fields", ignore)
        t.ok("and --compare says why: they are an orphan, the title's projection stale",
             report_section(text, "people: orphan") == [wrong] and report_section(text, "stale projection") == [iid]
             and "still credits them" in text, text)
        run(FROM_CATALOG, "--export", fixed, "--out", out, "--projections-only")
        code, text = run(SWEEP, out, "--export", fixed, "--grace", "0", "--apply")
        t.ok("once the title is projected again, the sweep takes them", code == 0 and not os.path.exists(folder), text)
        code, text = run(REBUILD, out, "--compare", fixed, "--ignore-fields", ignore)
        t.ok("and the tree agrees with the database", code == 0 and "the tree and the database agree" in text, text)


# ---------------------------------------------------------------- upgrading a tree in place
def downgrade(root):
    """Turn a tree in today's layout into the one before 2026-10-02 (b), as the live trees are:
    no item checksums, sources/<id>.json beside sources/<id>/ffprobe.json, events/<stamp>-<kind>.json,
    each version's checksums over the package and its marker, a marker that holds anything, and no
    people/."""
    shutil.rmtree(os.path.join(root, "people"), ignore_errors=True)
    for d in glob.glob(os.path.join(root, "movies", "*", "*")) + glob.glob(os.path.join(root, "series", "*", "*")) + \
            glob.glob(os.path.join(root, "series", "*", "*", "episodes", "*")):
        os.unlink(os.path.join(d, "checksums.sha256"))
        for sp in glob.glob(os.path.join(d, "sources", "*", "source.json")):
            folder = os.path.dirname(sp)
            shutil.move(sp, folder + ".json")
            os.unlink(os.path.join(folder, "checksums.sha256"))
            if not os.listdir(folder):
                os.rmdir(folder)
        for ep in glob.glob(os.path.join(d, "events", "*", "event.json")):
            folder = os.path.dirname(ep)
            stamp, _, kind = os.path.basename(folder).split("-", 2)
            shutil.move(ep, os.path.join(d, "events", f"{stamp}-{kind}.json"))
            shutil.rmtree(folder)
        for vp in glob.glob(os.path.join(d, "versions", "*")):
            if not os.path.isfile(os.path.join(vp, ".complete")):
                continue
            with open(os.path.join(vp, ".complete"), "w") as f:
                f.write("packager example 2026-09-18\n")
            relisted = [n for n in listing(vp) if n != "version.json"] + [".complete"]
            write_sums(vp, relisted)
            p = os.path.join(vp, "package.json")
            jwrite(p, dict(jload(p), checksums=dict(jload(p)["checksums"],
                                                    sha256="sha256:" + digest(os.path.join(vp, "checksums.sha256")),
                                                    files=len(relisted),
                                                    bytes=sum(os.path.getsize(os.path.join(vp, n)) for n in relisted))))


def test_upgrade(t):
    def records(root):
        """Every JSON record by where it belongs, whichever layout keeps it there, with its content:
        sources/<id>.json and sources/<id>/source.json are one place, and so are
        events/<stamp>-<kind>.json and events/<stamp>-<eventId8>-<kind>/event.json."""
        out = {}
        for p in glob.glob(os.path.join(root, "**", "*.json"), recursive=True):
            parts = os.path.relpath(p, root).split(os.sep)
            if parts[-1] == "source.json":
                parts = parts[:-1]
            elif parts[-1] == "event.json":
                stamp, _, kind = parts[-2].split("-", 2)
                parts = parts[:-2] + [f"{stamp}-{kind}"]
            elif len(parts) > 1 and parts[-2] in ("sources", "events"):
                parts = parts[:-1] + [parts[-1][:-5]]
            out["/".join(parts)] = jload(p)
        return out

    def fresh(tmp, name="library"):
        root = os.path.join(tmp, name)
        shutil.rmtree(root, ignore_errors=True)
        shutil.copytree(EXAMPLES, root)
        downgrade(root)
        return root

    with tempfile.TemporaryDirectory() as tmp:
        root = fresh(tmp)
        code, text = run(MEDIA_CHECK, root)
        t.ok("a tree in the layout before fails the media check, which names the upgrade",
             code == 1 and "library-v2-upgrade.py" in text, text)
        before, files_before = records(root), stamps(root)
        code, dry = run(UPGRADE, root, "--dry-run")
        t.ok("a dry run says what it would do and changes nothing",
             code == 0 and "would upgrade" in dry and stamps(root) == files_before, dry)
        code, text = run_piped(UPGRADE, root)
        t.ok("the upgrade runs piped into a pod's Python, as the pod runs it", code == 0 and "upgraded" in text, text)
        t.eq("and does what its dry run said it would", [l for l in text.splitlines()[1:] if not l.startswith("  note")],
             [l for l in dry.splitlines()[1:] if not l.startswith("  note")])
        if have_jsonschema():
            code, vtext = run(VALIDATOR, "--check-checksums", root)
            t.ok("the upgraded tree passes validate-library-v2.py --check-checksums", code == 0, vtext)
        else:
            t.skip("the upgraded tree passes validate-library-v2.py", "jsonschema is not importable here")
        code, mtext = run(MEDIA_CHECK, "--checksums", root)
        t.ok("and the media check", code == 0, mtext)
        after, files_after = records(root), stamps(root)
        t.eq("every record is where it belongs and is the record it was: item, source, event, version, metadata",
             sorted(k for k, v in before.items() if after.get(k) != v and not k.endswith("package.json")), [])
        t.ok("and package.json differs only in what it records of its checksums",
             all({**v, "checksums": None} == {**after[k], "checksums": None}
                 for k, v in before.items() if k.endswith("package.json")))
        t.ok("no package file and no original was written again",
             all(files_before[k] == files_after[k] for k in files_before
                 if "/hls/" in k or "/subs/" in k or "/trickplay/" in k or k.endswith(".mkv")))
        t.ok("and every extra is exactly as it was: an extra has no earlier layout to upgrade from",
             any("/extras/" in k for k in files_before)
             and {k: v for k, v in files_after.items() if "/extras/" in k} == {k: v for k, v in files_before.items()
                                                                              if "/extras/" in k})
        code, text = run(UPGRADE, root)
        t.ok("a second run changes nothing", code == 0 and "nothing to do" in text and stamps(root) == files_after, text)

        # ---- an interrupted run is finished by the next one, from wherever it stopped
        done = root
        for stopped, written in (("after package.json", ("package.json",)),
                                 ("after the checksums", ("package.json", "checksums.sha256"))):
            half = fresh(tmp, "half")
            vp = sorted(glob.glob(os.path.join(half, "movies", "*", "*", "versions", "*")))[0]
            ref = os.path.join(done, os.path.relpath(vp, half))
            for name in written:
                shutil.copy2(os.path.join(ref, name), os.path.join(vp, name))
            code, text = run(UPGRADE, half)
            t.ok(f"a run stopped {stopped} is finished by the next, to the same bytes",
                 code == 0 and tree_files(vp) == tree_files(ref), text)
        half = fresh(tmp, "half")
        src = sorted(glob.glob(os.path.join(half, "movies", "*", "*", "sources", "*.json")))[0]
        os.makedirs(src[:-5], exist_ok=True)
        shutil.move(src, os.path.join(src[:-5], "source.json"))
        ev = sorted(glob.glob(os.path.join(half, "movies", "*", "*", "events", "*.json")))[0]
        doc = jload(ev)
        folder = os.path.join(os.path.dirname(ev), f"{os.path.basename(ev)[:16]}-{doc['eventId'][:8]}-{doc['kind']}")
        os.makedirs(folder)
        shutil.move(ev, os.path.join(folder, "event.json"))
        code, text = run(UPGRADE, half)
        t.ok("a source and an event a stopped run moved, but did not cover, are covered by the next",
             code == 0 and os.path.isfile(os.path.join(src[:-5], "checksums.sha256"))
             and os.path.isfile(os.path.join(folder, "checksums.sha256")), text)

    # ---- what contradicts the records is reported, and left exactly as it was
    def movie_dir(r):
        return glob.glob(os.path.join(r, "movies", "*", "*"))[0]

    def first_version(r):
        return sorted(glob.glob(os.path.join(movie_dir(r), "versions", "*")))[0]

    def snapshot(paths):
        return {p: (stamps(p) if os.path.isdir(p) else os.stat(p).st_mtime_ns) for p in paths}

    def refused(name, change, phrase):
        with tempfile.TemporaryDirectory() as tmp:
            root = fresh(tmp)
            paths = change(root)
            before = snapshot(paths)
            code, text = run(UPGRADE, root)
            t.ok(f"the upgrade refuses {name}, and leaves it as it was",
                 code == 1 and phrase in text and snapshot(paths) == before, text)

    def unlisted(r):
        vp = first_version(r)
        open(os.path.join(vp, "hls", "v0", "seg-9999.m4s"), "wb").write(b"x")
        return [vp]

    def recorded_otherwise(r):
        vp = first_version(r)
        open(os.path.join(vp, "checksums.sha256"), "a").write(f"{'0' * 64}  hls/extra\n")
        return [vp]

    def version_changed(r):
        vp = first_version(r)
        jwrite(os.path.join(vp, "version.json"), dict(jload(os.path.join(vp, "version.json")), runtimeMs=1))
        relist(vp, [n for n in listing(vp) if n != ".complete"] + ["version.json"])  # the new form, with a stale digest
        jwrite(os.path.join(vp, "version.json"), dict(jload(os.path.join(vp, "version.json")), runtimeMs=2))
        return [vp]

    def probe_contradicted(r):
        probe = sorted(glob.glob(os.path.join(movie_dir(r), "sources", "*", "ffprobe.json")))[0]
        open(probe, "a").write(" ")
        return [os.path.dirname(probe), os.path.dirname(probe) + ".json"]

    def not_its_folder(r):
        jwrite(os.path.join(movie_dir(r), "item.json"), dict(jload(os.path.join(movie_dir(r), "item.json")),
                                                            itemId="00000000-0000-4000-8000-000000000000"))
        return [movie_dir(r)]

    def misnamed_event(r):
        ev = sorted(glob.glob(os.path.join(movie_dir(r), "events", "*-original-deleted.json")))[0]
        shutil.move(ev, ev.replace("20260920T081500Z", "20261231T235959Z"))
        return [ev.replace("20260920T081500Z", "20261231T235959Z")]

    def taken_folder(r):
        src = sorted(glob.glob(os.path.join(movie_dir(r), "sources", "*.json")))[0]
        os.makedirs(src[:-5], exist_ok=True)
        jwrite(os.path.join(src[:-5], "source.json"), dict(jload(src), takenBy="someone else"))
        return [src, src[:-5]]

    def v1_folder(r):
        jwrite(os.path.join(movie_dir(r), "manifest.json"), {"version": 3})
        return [movie_dir(r)]

    refused("a package file its checksums do not list", unlisted, "does not list exactly the package's files")
    refused("checksums that do not match what package.json recorded", recorded_otherwise,
            "matches neither the hash package.json records")
    refused("a version.json changed after its checksums were written", version_changed,
            "does not match the checksum checksums.sha256 recorded for it")
    refused("a probe that contradicts its source record", probe_contradicted, "not the probe its source record hashed")
    refused("an item.json that does not name its folder", not_its_folder, "does not name its folder")
    refused("an event whose name contradicts its moment", misnamed_event, "its name contradicts its moment")
    refused("a source whose folder already holds another record", taken_folder, "already holds another record")
    refused("a v1 item folder", v1_folder, "library-v2-from-v1.py converts it")

    with tempfile.TemporaryDirectory() as tmp:
        root = fresh(tmp)
        rotted = os.path.join(first_version(root), "trickplay", "thumbnails.vtt")
        data = open(rotted, "rb").read()
        with open(rotted, "wb") as f:
            f.write(data[:-1] + (b"X" if data[-1:] != b"X" else b"Y"))
        code, text = run(UPGRADE, root)
        code, mtext = run(MEDIA_CHECK, "--checksums", root)
        t.ok("a package file that changed after it was packaged keeps the digest it was packaged with, "
             "so the media check still catches it", code == 1 and "thumbnails.vtt: does not match its checksum" in mtext,
             mtext)

    with tempfile.TemporaryDirectory() as tmp:
        root = fresh(tmp)
        removed = jload(sorted(glob.glob(os.path.join(movie_dir(root), "events", "*-version-removed.json")))[0])
        vp = os.path.join(movie_dir(root), "versions", removed["versionId"])
        shutil.copytree(first_version(root), vp)
        before = stamps(vp)
        code, text = run(UPGRADE, root)
        t.ok("a version an event removed is not upgraded: its folder is ignored, and the run says so",
             code == 0 and stamps(vp) == before and "removed by an event" in text, text)


# ---------------------------------------------------------------- a tree from before, given neutral names
NAMED_FEATURETTE = "Example Film (2024) - Featurette.mkv"
NAMED_EXTRA = "27aa63f3-5555-4666-8777-999999999999"


def named_tree(root, rec):
    """The golden tree twice over: as the record logic writes it now — an extra keeping its original
    added — and, in place, as it wrote it before 2026-10-08: the source naming its original as it
    arrived and where it came from, the container's title tag kept, its probe verbatim, a copy of the
    subtitle file and of the .nfo under the names they came with, the version keeping the original under
    its own name, the subtitle made from the copy naming it so, and the extras naming their originals
    as they arrived — every chain closed over them as a writer then closed it. Returns (the library, the
    tree as it is written now: {path: bytes}, its sourceId)."""
    item, sid, vid, _ = golden_tree(root, rec)
    library = os.path.join(root, "library")
    xp2 = os.path.join(item, "extras", NAMED_EXTRA)
    os.makedirs(xp2)
    body = b"a featurette that is only an example\n" * 50
    with open(os.path.join(xp2, "original.mkv"), "wb") as f:
        f.write(body)
    fixity = {"qh1": rec.qh1(os.path.join(xp2, "original.mkv"))}
    extra2 = lambda name: rec.json_bytes(rec.extra_record(
        NAMED_EXTRA, created_at=GOLDEN_AT, created_by="test", kind="featurette", title="On Set", original_files=[name],
        originals=[{"name": name, "sizeBytes": len(body), "fixity": fixity}]))
    with open(os.path.join(xp2, "extra.json"), "wb") as f:
        f.write(extra2("original.mkv"))
    write_sums(xp2, ["extra.json", "original.mkv"])
    now = tree_files(library)

    def put(path, data):
        with open(path, "wb") as f:
            f.write(data)

    def chain(folder, record):
        """The chain closed again over a record, from the bottom up, as a writer closes it."""
        names = sorted({n for n in listing(folder)})
        write_sums(folder, names)
        pp = os.path.join(folder, "package.json")
        doc = jload(pp)
        doc["checksums"].update(sha256="sha256:" + digest(os.path.join(folder, "checksums.sha256")), files=len(names),
                                bytes=sum(os.path.getsize(os.path.join(folder, n)) for n in names))
        put(pp, rec.json_bytes(doc))
        close(folder)

    sp = os.path.join(item, "sources", sid)
    doc = jload(os.path.join(sp, "source.json"))
    probe = rec.json_bytes(jload(os.path.join(GOLDEN, "probe.json")))
    old = {}
    for key, value in doc.items():
        old[key] = value
        if key == "file":
            old["origin"] = {"libraryPath": f"Example Film (2024)/{GOLDEN_ORIGINAL}", "takenBy": "import",
                             "folder": "Example Film (2024)"}
    title = jload(os.path.join(GOLDEN, "probe.json"))["format"]["tags"]["title"]
    old["file"] = dict(doc["file"], name=GOLDEN_ORIGINAL)
    old["container"] = dict(doc["container"], title=title, tags={"title": title, **doc["container"]["tags"]})
    nfo = b"<movie><title>Example Film</title></movie>\n"
    old["sidecars"] = [{"file": f"sources/{sid}/{GOLDEN_SIDECAR}", "originalName": GOLDEN_SIDECAR,
                        **{k: v for k, v in doc["sidecars"][0].items() if k != "file"}},
                       {"file": f"sources/{sid}/{GOLDEN_COMPANION}", "originalName": GOLDEN_COMPANION, "kind": "nfo",
                        "format": "nfo", "sizeBytes": len(nfo), "sha256": "sha256:" + hashlib.sha256(nfo).hexdigest()}]
    old["probe"] = dict(doc["probe"], sha256=rec.sha_bytes(probe))
    os.rename(os.path.join(sp, "subtitle-1.de.srt"), os.path.join(sp, GOLDEN_SIDECAR))
    put(os.path.join(sp, GOLDEN_COMPANION), nfo)
    put(os.path.join(sp, "ffprobe.json"), probe)
    put(os.path.join(sp, "source.json"), rec.json_bytes(old))
    write_sums(sp, ["source.json", "ffprobe.json", GOLDEN_SIDECAR, GOLDEN_COMPANION])

    vp = os.path.join(item, "versions", vid)
    os.rename(os.path.join(vp, "original.mkv"), os.path.join(vp, GOLDEN_ORIGINAL))
    put(os.path.join(vp, "version.json"), rec.json_bytes(dict(jload(os.path.join(vp, "version.json")),
                                                              originalFiles=[GOLDEN_ORIGINAL])))
    package = jload(os.path.join(vp, "package.json"))
    for s in package["subtitles"]:
        if s.get("fromSidecar"):
            s["fromSidecar"] = f"sources/{sid}/{GOLDEN_SIDECAR}"
    put(os.path.join(vp, "package.json"), rec.json_bytes(package))
    chain(vp, "version.json")

    xp = os.path.join(item, "extras", GOLDEN_EXTRA)
    x = jload(os.path.join(xp, "extra.json"))
    put(os.path.join(xp, "extra.json"), rec.json_bytes(dict(x, packagedFrom=[dict(x["packagedFrom"][0], name="trailer.mov")])))
    chain(xp, "extra.json")
    os.rename(os.path.join(xp2, "original.mkv"), os.path.join(xp2, NAMED_FEATURETTE))
    put(os.path.join(xp2, "extra.json"), extra2(NAMED_FEATURETTE))
    write_sums(xp2, ["extra.json", NAMED_FEATURETTE])
    return library, now, sid


def test_neutral_names(t):
    """A tree written before 2026-10-08 is given the names the library gives its files: every rename and
    rewrite listed by a dry run that changes nothing, made by --apply — piped into a pod's Python — with
    the journal under the run's folder, to exactly the tree the record logic writes now; a copy that is
    no subtitle leaves the record for the run's folder; a second run does nothing, an interrupted one is
    finished by the next, two runs never act at once, and a folder whose records contradict it is left
    as it was."""
    rec = load_tool(RECORDS)
    with tempfile.TemporaryDirectory() as tmp:
        library, now, sid = named_tree(tmp, rec)
        named = tree_files(library)
        before = stamps(library)
        code, text = run(NEUTRAL, library)
        t.ok("a dry run says what it would rename, rewrite and take out of the record, and changes nothing",
             code == 0 and stamps(library) == before and not os.path.exists(os.path.join(library, ".work"))
             and f"would rename  movies/{GOLDEN_ITEM[:2]}/{GOLDEN_ITEM}/sources/{sid}/{GOLDEN_SIDECAR} -> subtitle-1.de.srt"
             in text and f"{GOLDEN_ORIGINAL} -> original.mkv" in text and f"{NAMED_FEATURETTE} -> original.mkv" in text
             and f"would remove  movies/{GOLDEN_ITEM[:2]}/{GOLDEN_ITEM}/sources/{sid}/{GOLDEN_COMPANION}" in text
             and "run again with --apply" in text, text)
        code, text = run_piped(NEUTRAL, library, "--apply")
        rundir = os.path.join(library, ".work", "migration", "neutral-names")
        after = {k: v for k, v in tree_files(library).items() if not k.startswith(".work")}
        t.ok("--apply, piped into a pod's Python behind the record module, gives the tree its names",
             code == 0 and "gave" in text and "files renamed: 3" in text, text)
        t.eq("and leaves exactly the tree the record logic writes now", sorted(k for k in after if after[k] != now.get(k))
             + sorted(set(now) - set(after)), [])
        t.ok("the .nfo leaves the record, kept in the run's folder",
             open(os.path.join(rundir, "removed", "movies", GOLDEN_ITEM[:2], GOLDEN_ITEM, "sources", sid, GOLDEN_COMPANION),
                  "rb").read() == named[f"movies/{GOLDEN_ITEM[:2]}/{GOLDEN_ITEM}/sources/{sid}/{GOLDEN_COMPANION}"])
        journal = [json.loads(line) for line in open(os.path.join(rundir, "journal.jsonl"))]
        plan = next(e for e in journal if e["op"] == "plan")
        t.ok("the journal holds the item's plan, each step as it was done, then done — and every file it rewrote, as "
             "it was and as it is",
             [e["op"] for e in journal] == ["plan"] + [s["op"] for s in plan["steps"]] + ["done"]
             and all(open(os.path.join(rundir, "before", *s["path"].split("/")), "rb").read()
                     == named[s["path"].replace("/", os.sep)] for s in plan["steps"] if s["op"] == "rewrite")
             and all(rec.sha_file(os.path.join(rundir, "after", *s["path"].split("/"))) == s["after"]
                     for s in plan["steps"] if s["op"] == "rewrite")
             and {(s["from"].rsplit("/", 1)[-1], s["to"].rsplit("/", 1)[-1]) for s in plan["steps"] if s["op"] == "rename"}
             == {(GOLDEN_SIDECAR, "subtitle-1.de.srt"), (GOLDEN_ORIGINAL, "original.mkv"),
                 (NAMED_FEATURETTE, "original.mkv")}, journal[:2])
        if have_jsonschema():
            code, vtext = run(VALIDATOR, "--check-checksums", library)
            t.ok("the tree it leaves is valid", code == 0 and vtext.strip().endswith("OK"), vtext)
        else:
            t.skip("the tree it leaves is valid", "jsonschema is not importable here")
        code, mtext = run(MEDIA_CHECK, "--checksums", library)
        t.ok("and passes the media check", code == 0 and mtext.strip().endswith("OK"), mtext)
        stamped = stamps(library)
        code, text = run(NEUTRAL, library, "--apply")
        t.ok("a second run does nothing", code == 0 and "nothing to do" in text and stamps(library) == stamped, text)

    # ---- stopped after its third step, the run is finished by the next, to the same tree
    with tempfile.TemporaryDirectory() as tmp:
        library, now, sid = named_tree(tmp, rec)
        tool = load_tool(NEUTRAL)
        n = tool.Neutral(library, "neutral-names", True, False)
        logged, write = [], n.log

        def stop(item, entry):
            write(item, entry)
            logged.append(entry["op"])
            if logged.count("rename") + logged.count("remove") + logged.count("rewrite") == 3:
                raise SystemExit("stopped")
        n.log = stop
        try:
            n.run()
        except SystemExit:
            pass
        n.journal.close()
        half = tree_files(library)
        code, text = run(NEUTRAL, library, "--apply")
        after = {k: v for k, v in tree_files(library).items() if not k.startswith(".work")}
        t.ok("a run stopped after three steps is finished by the next, to the same tree",
             logged[:1] == ["plan"] and half != now and code == 0 and "an interrupted run of neutral-names is finished"
             in text and after == now, text)

    # ---- what contradicts its records is refused, and its item left exactly as it was
    def refused(name, change, phrase):
        with tempfile.TemporaryDirectory() as tmp:
            library, _, sid = named_tree(tmp, rec)
            change(os.path.join(library, "movies", GOLDEN_ITEM[:2], GOLDEN_ITEM), sid)
            snapshot = stamps(library)
            code, text = run(NEUTRAL, library, "--apply")
            t.ok(f"it refuses {name}, and leaves the item as it was",
                 code == 1 and phrase in text and {k: v for k, v in stamps(library).items() if not k.startswith(".work")}
                 == snapshot, text)

    def append(path):
        with open(path, "ab") as f:
            f.write(b"x")
    refused("a copy that changed after its checksums were written",
            lambda d, sid: append(os.path.join(d, "sources", sid, GOLDEN_SIDECAR)), "does not match its checksum")
    refused("a package whose .complete does not name its package.json",
            lambda d, sid: append(glob.glob(os.path.join(d, "versions", "*", ".complete"))[0]),
            ".complete does not name its package.json")
    refused("an original that is not the size its record says",
            lambda d, sid: append(glob.glob(os.path.join(d, "versions", "*", GOLDEN_ORIGINAL))[0]),
            "is not the size its source record says")
    refused("a source in the layout before 2026-10-02 (b)",
            lambda d, sid: shutil.move(os.path.join(d, "sources", sid, "source.json"), os.path.join(d, "sources", sid + ".json")),
            "library-v2-upgrade.py upgrades the tree first")

    with tempfile.TemporaryDirectory() as tmp:
        library, _, _ = named_tree(tmp, rec)
        rundir = os.path.join(library, ".work", "migration", "neutral-names")
        os.makedirs(rundir)
        import fcntl
        with open(os.path.join(rundir, "journal.jsonl"), "a") as held:
            fcntl.lockf(held, fcntl.LOCK_EX)
            code, text = run(NEUTRAL, library, "--apply")
        t.ok("and two runs never act on one tree at once", code == 1 and "is locked" in text
             and os.path.isfile(glob.glob(os.path.join(library, "movies", "*", "*", "versions", "*", GOLDEN_ORIGINAL))[0]),
             text)
        code, text = run(NEUTRAL, library, "--run", "../elsewhere")
        t.ok("a run is named as a folder is", code == 2 and "is not a folder name" in text, text)



# ---------------------------------------------------------------- sweeping provable garbage
GONE = "0d0d0d0d-0000-4000-8000-000000000001"           # an item the database deleted
GONE_PERSON = "0d0d0d0d-0000-4000-8000-0000000000aa"    # a person it deleted, because no title credits them
NOTHING_WROTE = "11111111-0000-4000-8000-00000000000b"  # a version folder with part of a package, no record
RECORDED = "22222222-0000-4000-8000-00000000000b"       # a version that wrote its record, kept no original
BESIDE = "33333333-0000-4000-8000-00000000000b"         # an unfinished package beside a kept original
TAKEN_IN = "88888888-0000-4000-8000-00000000000b"       # a version holding its original and no package: no garbage


def aged(root, days=3):
    """Every file and folder under root made days old: garbage counts only once it is older than
    the grace, and a fresh copy of the examples is as new as the moment it was made."""
    then = time.time() - days * 86400
    for base, dirs, files in os.walk(root):
        for n in dirs + files:
            os.utime(os.path.join(base, n), (then, then), follow_symlinks=False)
    os.utime(root, (then, then))


def garbage(tmp, age=True):
    """The examples with one of each kind of garbage in them, and the export of a database that holds
    every item they hold and remembers deleting one more. Returns the tree, the export's path and
    where each piece of garbage is."""
    root = os.path.join(tmp, "library")
    shutil.copytree(EXAMPLES, root)
    movie = glob.glob(os.path.join(root, "movies", "*", "*"))[0]
    kept = next(vp for vp in sorted(glob.glob(os.path.join(movie, "versions", "*")))
                if jload(os.path.join(vp, "version.json"))["originalFiles"]
                and all(os.path.isfile(os.path.join(vp, n)) for n in jload(os.path.join(vp, "version.json"))["originalFiles"]))
    where = {"gone": outlive(root, GONE), "series": glob.glob(os.path.join(root, "series", "*", "*"))[0]}
    where["nothing wrote"] = os.path.join(movie, "versions", NOTHING_WROTE)
    os.makedirs(os.path.join(where["nothing wrote"], "hls", "v0"))
    open(os.path.join(where["nothing wrote"], "hls", "v0", "seg-0001.m4s"), "wb").write(b"a segment")
    where["recorded"] = os.path.join(movie, "versions", RECORDED)
    os.makedirs(os.path.join(where["recorded"], "hls", "v0"))
    jwrite(os.path.join(where["recorded"], "version.json"),
           dict(jload(os.path.join(kept, "version.json")), versionId=RECORDED, originalFiles=[]))
    where["beside"] = os.path.join(movie, "versions", BESIDE)
    shutil.copytree(kept, where["beside"])
    for name in (".complete", "package.json", "checksums.sha256"):
        os.unlink(os.path.join(where["beside"], name))
    jwrite(os.path.join(where["beside"], "version.json"),
           dict(jload(os.path.join(where["beside"], "version.json")), versionId=BESIDE))
    # no garbage: a version taken in before anything packaged it, its record and its originals alone
    where["taken in"] = os.path.join(movie, "versions", TAKEN_IN)
    os.makedirs(where["taken in"])
    for name in jload(os.path.join(kept, "version.json"))["originalFiles"]:
        shutil.copyfile(os.path.join(kept, name), os.path.join(where["taken in"], name))
    jwrite(os.path.join(where["taken in"], "version.json"),
           dict(jload(os.path.join(kept, "version.json")), versionId=TAKEN_IN))
    image = b"\xff\xd8\xff\xfe\x00\x0bdropped\xff\xd9"
    where["dropped image"] = os.path.join(movie, "metadata", hashlib.sha256(image).hexdigest() + ".jpg")
    open(where["dropped image"], "wb").write(image)
    portrait = b"\xff\xd8\xff\xfe\x00\x0dportrait\xff\xd9"
    lead = next(p for p in glob.glob(os.path.join(root, "people", "*", "*"))
                if jload(os.path.join(p, "person.json"))["name"] == "Mara Example")
    where["dropped portrait"] = os.path.join(lead, hashlib.sha256(portrait).hexdigest() + ".jpg")
    open(where["dropped portrait"], "wb").write(portrait)
    director = next(p for p in glob.glob(os.path.join(root, "people", "*", "*"))
                    if jload(os.path.join(p, "person.json"))["name"] == "Ian Hubert")
    where["gone person"] = os.path.join(root, "people", GONE_PERSON[:2], GONE_PERSON)
    shutil.copytree(director, where["gone person"])
    jwrite(os.path.join(where["gone person"], "person.json"),
           dict(jload(os.path.join(director, "person.json")), personId=GONE_PERSON, name="Credited No More"))
    rows, _ = rows_of(EXAMPLES)
    export = os.path.join(tmp, "catalog.json")
    jwrite(export, {"exportedAt": "2026-10-01T12:00:00Z", "items": list(rows.values()),
                    "deletedItems": [{"id": GONE, "deletedAt": "2026-09-30T10:00:00Z", "deletedBy": "librarian"},
                                     {"id": GONE_PERSON, "type": "person", "deletedAt": "2026-09-30T10:00:00Z",
                                      "deletedBy": "catalog"}]})
    if age:
        aged(root)
    return root, export, where


def no_quarantine(root):
    """Nothing is left in a quarantine: the work tree's holds no run, and there is none at the root,
    where a sweep from before the work tree kept it."""
    q = os.path.join(root, ".work", "quarantine")
    return not (os.path.isdir(q) and os.listdir(q)) and not os.path.exists(os.path.join(root, "_swept"))


def credit(item_dir, pid, name="Credited No More"):
    """item_dir's metadata.json crediting pid as well, as a projection written before the database
    dropped the credit still does."""
    p = os.path.join(item_dir, "metadata.json")
    doc = jload(p)
    doc["credits"] = list(doc.get("credits") or []) + [{"personId": pid, "name": name, "role": "actor", "character": None,
                                                        "order": None, "tmdbPerson": None}]
    jwrite(p, doc)


def swept(text, root):
    """The targets a sweep lists, as absolute paths."""
    return sorted(os.path.join(root, line.split()[2 if line.startswith("  would") else 1])
                  for line in text.splitlines() if line.startswith(("  would remove  ", "  remove  ")))


def left_alone(text):
    return [line.strip() for line in text.splitlines() if line.startswith("    ")]


def test_sweep(t):
    with tempfile.TemporaryDirectory() as tmp:
        root, export, where = garbage(tmp)
        for name in os.listdir(where["taken in"]):  # written just now: were it garbage, it would be within the grace
            os.utime(os.path.join(where["taken in"], name), None)
        expected = sorted([where["gone"], where["nothing wrote"], where["recorded"],
                           os.path.join(where["beside"], "hls"), os.path.join(where["beside"], "trickplay"),
                           where["dropped image"], where["dropped portrait"], where["gone person"]])
        before = stamps(root)
        code, text = run(SWEEP, root, "--export", export)
        t.eq("a dry run finds every kind of garbage, and nothing else", swept(text, root), expected)
        t.ok("and says nothing of a version taken in before anything packaged it, which is finished as it is",
             TAKEN_IN not in text and os.path.isdir(where["taken in"]), text)
        t.ok("and says why each one is garbage",
             "deleted item: deleted 2026-09-30T10:00:00Z by librarian, and nothing in it is newer" in text
             and "deleted person: deleted 2026-09-30T10:00:00Z by catalog: nothing in the folder is newer, and no item "
                 "record credits them" in text
             and "no .complete and no version.json: nothing can have known it" in text
             and "no .complete, no original, and nothing names it" in text
             and "beside the original" in text and "metadata.json no longer lists it" in text
             and "person.json no longer lists it" in text, text)
        t.ok("and changes nothing", code == 0 and stamps(root) == before and "run again with --apply" in text, text)

        code, text = run_piped(SWEEP, root, "--export", export, "--apply")
        rest = {k: v for k, v in stamps(root).items()}
        gone = [k for k in before if not any(os.path.join(root, k) == e or os.path.join(root, k).startswith(e + os.sep)
                                             for e in expected)]
        t.ok("--apply, piped into a pod's Python, removes exactly those", code == 0
             and not any(os.path.lexists(e) for e in expected) and "removed 8 target(s)" in text, text)
        t.ok("and leaves every other file as it was", all(rest.get(k) == before[k] for k in gone))
        t.ok("and no quarantine behind it", no_quarantine(root))
        t.ok("the original beside the unfinished package stays, with its record",
             os.path.isfile(os.path.join(where["beside"], "version.json"))
             and all(os.path.isfile(os.path.join(where["beside"], n))
                     for n in jload(os.path.join(where["beside"], "version.json"))["originalFiles"]))
        if have_jsonschema():
            code, vtext = run(VALIDATOR, "--check-media", root)
            t.ok("what is left is a valid record", code == 0 and "FAILED" not in vtext, vtext)
        code, text = run(SWEEP, root, "--export", export)
        t.ok("and a second sweep finds nothing", code == 0 and swept(text, root) == [], text)

    # ---- what is not provably garbage is left alone, and the sweep says why
    def refused(name, change, phrase, what="gone", extra=(), export_too=True, age_after=False):
        with tempfile.TemporaryDirectory() as tmp:
            root, export, where = garbage(tmp)
            change(root, export, where)
            if age_after:
                aged(root)
            code, text = run(SWEEP, root, *(("--export", export) if export_too else ()), *extra)
            t.ok(f"the sweep leaves alone {name}", code == 0 and where[what] not in swept(text, root)
                 and any(phrase in line for line in left_alone(text) + text.splitlines()), text)

    def export_edit(export, change):
        doc = jload(export)
        change(doc)
        jwrite(export, doc)

    def touch(p):
        os.utime(p, None)

    def movie_of(where):
        return os.path.dirname(os.path.dirname(where["recorded"]))

    refused("a deleted item the database holds again",
            lambda r, e, w: export_edit(e, lambda d: d["items"].append({"id": GONE, "type": "movie", "title": "Back"})),
            "the database holds it again")
    refused("a deletion that is younger than the grace",
            lambda r, e, w: export_edit(e, lambda d: d["deletedItems"][0].update(
                deletedAt=(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"))),
            "within the grace period")
    refused("a deleted item's folder written to within the grace",
            lambda r, e, w: touch(os.path.join(w["gone"], "metadata.json")), "written to within the grace period")
    refused("a deleted item's folder that holds a record newer than the deletion",
            lambda r, e, w: export_edit(e, lambda d: d["deletedItems"][0].update(deletedAt="2026-09-19T00:00:00Z")),
            "created again since")
    refused("a deleted item's folder whose projection reflects a row modified after the deletion",
            lambda r, e, w: jwrite(os.path.join(w["gone"], "metadata.json"),
                                   dict(jload(os.path.join(w["gone"], "metadata.json")), databaseUpdatedAt="2026-10-01T08:00:00Z")),
            "holds a record of 2026-10-01T08:00:00Z", age_after=True)
    refused("a deleted item's folder that holds an extra taken in after the deletion",
            lambda r, e, w: [jwrite(x, dict(jload(x), createdAt="2026-10-01T08:00:00Z"))
                             for x in glob.glob(os.path.join(w["gone"], "extras", "*", "extra.json"))],
            "holds a record of 2026-10-01T08:00:00Z", age_after=True)
    refused("a deleted series one of whose episodes the database still holds",
            lambda r, e, w: export_edit(e, lambda d: (
                d["deletedItems"].append({"id": next(x["id"] for x in d["items"] if x["type"] == "series"),
                                          "deletedAt": "2026-09-30T10:00:00Z", "deletedBy": "librarian"}),
                d.update(items=[x for x in d["items"] if x["type"] != "series"]))),
            "still holds its episode", what="series")
    refused("a deleted item when the export carries no deletion log",
            lambda r, e, w: export_edit(e, lambda d: d.update(deletedItems=None)), "deletedItems is null")
    refused("a deleted item without an export", lambda r, e, w: None, "no --export", export_too=False)
    refused("a version that wrote its record, without an export to say the database does not know it",
            lambda r, e, w: None, "without --export the sweep cannot tell", what="recorded", export_too=False)
    refused("an unfinished version metadata.json names",
            lambda r, e, w: jwrite(os.path.join(movie_of(w), "metadata.json"), dict(
                jload(os.path.join(movie_of(w), "metadata.json")),
                library=dict(jload(os.path.join(movie_of(w), "metadata.json"))["library"], primaryVersionId=RECORDED))),
            "metadata.json names it", what="recorded", age_after=True)
    refused("an unfinished version an event names",
            lambda r, e, w: write_event(movie_of(w), {"schema": "zaentrum.library.event/2", "eventId": str(uuid.uuid4()),
                                                      "at": "2026-09-21T10:00:00Z", "by": "test", "kind": "note",
                                                      "versionId": RECORDED, "reason": "a person looked at it"}),
            "names it", what="recorded", age_after=True)
    refused("an unfinished version the database names",
            lambda r, e, w: export_edit(e, lambda d: d["items"][0]["playbackAssets"].append(
                {"id": "x", "kind": "packaged", "path": f"/library/versions/{RECORDED}/package.json"})),
            "the export names it", what="recorded")
    refused("an unfinished version holding a file the sweep cannot classify",
            lambda r, e, w: open(os.path.join(w["recorded"], "notes.txt"), "w").write("x"),
            "cannot classify", what="recorded", age_after=True)
    refused("an unfinished version written to within the grace",
            lambda r, e, w: touch(os.path.join(w["recorded"], "version.json")), "written to within the grace period",
            what="recorded")
    refused("an image not named by the hash of its own bytes",
            lambda r, e, w: open(w["dropped image"], "ab").write(b"x"), "not named by the hash of its own bytes",
            what="dropped image", age_after=True)
    refused("a file beside a projection that is not an image it can classify",
            lambda r, e, w: shutil.move(w["dropped image"], os.path.join(os.path.dirname(w["dropped image"]), "poster.jpg")),
            "not an image the sweep can classify", what="dropped image", age_after=True)
    refused("an image dropped by a projection written within the grace",
            lambda r, e, w: touch(os.path.join(movie_of(w), "metadata.json")),
            "dropped by a metadata.json written within the grace period", what="dropped image")
    refused("an image younger than the grace", lambda r, e, w: touch(w["dropped image"]), "written within the grace period",
            what="dropped image")

    # ---- a person the catalog deleted: swept only when nothing keeps them
    def person_entry(change):
        return lambda d: [change(x) for x in d["deletedItems"] if x["id"] == GONE_PERSON]

    def gone_record(w, **fields):
        p = os.path.join(w["gone person"], "person.json")
        jwrite(p, dict(jload(p), **fields))

    refused("a deleted person the database's people list holds again",
            lambda r, e, w: export_edit(e, lambda d: d.update(people=[{"id": GONE_PERSON, "name": "Back"}])),
            "the database holds them again", what="gone person")
    refused("a deleted person an item of the database credits again",
            lambda r, e, w: export_edit(e, lambda d: d["items"][0].setdefault("people", []).append(
                {"personId": GONE_PERSON, "name": "Back", "role": "actor"})), "the database holds them again", what="gone person")
    refused("a deleted person an item record on storage still credits",
            lambda r, e, w: credit(movie_of(w), GONE_PERSON), "still credits them", what="gone person", age_after=True)
    refused("a deleted person an episode's record on storage still credits",
            lambda r, e, w: credit(glob.glob(os.path.join(w["series"], "episodes", "*"))[0], GONE_PERSON),
            "still credits them", what="gone person", age_after=True)
    refused("a deleted person while a projection on storage cannot be read, which may credit them",
            lambda r, e, w: open(os.path.join(glob.glob(os.path.join(w["series"], "episodes", "*"))[0], "metadata.json"),
                                 "w").write("not JSON"), "cannot be read, so it may credit them", what="gone person",
            age_after=True)
    refused("a deleted person whose deletion is younger than the grace",
            lambda r, e, w: export_edit(e, person_entry(lambda x: x.update(deletedAt=(
                datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")))),
            "within the grace period", what="gone person")
    refused("a deleted person's folder written to within the grace",
            lambda r, e, w: touch(os.path.join(w["gone person"], "person.json")), "written to within the grace period",
            what="gone person")
    refused("a deleted person whose folder holds a record newer than the deletion",
            lambda r, e, w: gone_record(w, databaseUpdatedAt="2026-10-01T08:00:00Z"),
            "holds a record of 2026-10-01T08:00:00Z: created again since", what="gone person", age_after=True)
    refused("a deleted person whose portrait was fetched after the deletion",
            lambda r, e, w: gone_record(w, images=[dict(jload(os.path.join(w["gone person"], "person.json"))["images"][0],
                                                       fetchedAt="2026-10-01T08:00:00Z", origin={"source": "manual"})]),
            "holds a record of 2026-10-01T08:00:00Z", what="gone person", age_after=True)

    def written_after(w):
        """A file beside person.json that it says nothing of, written after the deletion and before the grace."""
        p = os.path.join(w["gone person"], hashlib.sha256(b"\xff\xd8 later").hexdigest() + ".jpg")
        with open(p, "wb") as f:
            f.write(b"\xff\xd8 later")
        at = datetime.datetime(2026, 10, 1, 8, tzinfo=datetime.timezone.utc).timestamp()
        os.utime(p, (at, at))

    refused("a deleted person whose folder holds a file written after the deletion",
            lambda r, e, w: written_after(w), "holds a record of 2026-10-01T08:00:00Z", what="gone person")
    refused("a deleted person whose person.json names someone else",
            lambda r, e, w: gone_record(w, personId=NOTHING_WROTE), "names another person", what="gone person", age_after=True)
    refused("a person an entry without a type names, as before people were logged",
            lambda r, e, w: export_edit(e, person_entry(lambda x: x.pop("type"))),
            "an entry without a type is an item's, so the person is not proved deleted", what="gone person")
    refused("a person an entry of an item's type names",
            lambda r, e, w: export_edit(e, person_entry(lambda x: x.update(type="movie"))),
            "names this id as an item's, not a person's", what="gone person")
    refused("a deleted person without an export", lambda r, e, w: None, "no --export", what="gone person", export_too=False)

    with tempfile.TemporaryDirectory() as tmp:
        root, export, where = garbage(tmp)
        export_edit(export, lambda d: [x.pop("type") for x in d["deletedItems"] if x["id"] == GONE_PERSON])
        code, text = run(SWEEP, root, "--export", export)
        t.ok("a log none of whose entries says what it deleted names no person, and the sweep says so",
             code == 0 and where["gone person"] not in swept(text, root) and "carry no type" in text, text)

    with tempfile.TemporaryDirectory() as tmp:
        root, export, where = garbage(tmp)
        lead = os.path.dirname(where["dropped portrait"])
        nobody = os.path.join(root, "people", "4e", "4e4e4e4e-0000-4000-8000-000000000001")
        shutil.copytree(lead, nobody)
        os.unlink(os.path.join(nobody, os.path.basename(where["dropped portrait"])))
        jwrite(os.path.join(nobody, "person.json"), dict(jload(os.path.join(lead, "person.json")),
                                                        personId=os.path.basename(nobody), name="Credited By Nothing"))
        aged(root)
        code, text = run(SWEEP, root, "--export", export)
        t.ok("a person's folder the deletion log does not name is never swept, not even when nothing credits the person",
             not any(p.startswith(nobody) for p in swept(text, root)) and os.path.isdir(nobody), text)

    with tempfile.TemporaryDirectory() as tmp:
        root, export, where = garbage(tmp, age=False)
        code, text = run(SWEEP, root, "--export", export)
        t.ok("garbage written moments ago is left for the grace period", swept(text, root) == [], text)
        code, text = run(SWEEP, root, "--export", export, "--grace", "0")
        t.ok("and --grace 0 sweeps it", where["dropped image"] in swept(text, root) and where["nothing wrote"] in swept(text, root), text)
        code, text = run(SWEEP, root, "--grace", "a while")
        t.ok("a grace that is not a duration is refused", code != 0 and "is not a grace period" in text, text)

    # ---- the quarantine: checked again before anything is deleted, and finished if it was interrupted
    sw = load_tool(SWEEP)
    now = datetime.datetime.now(datetime.timezone.utc)
    with tempfile.TemporaryDirectory() as tmp:
        root, export, where = garbage(tmp)
        s = sw.Sweep(root, sw.References(root, export), 86400, now)
        s.run()
        a = sw.Apply(s)
        check = a.finish

        def listed_again(q):
            meta = os.path.join(movie_of(where), "metadata.json")
            doc = jload(meta)
            doc["images"].append({"kind": "poster", "file": os.path.basename(where["dropped image"])})
            jwrite(meta, doc)
            check(q)
        a.finish = listed_again
        a.run(now)
        t.ok("a target a projection lists again by the time it is checked is put back, not deleted",
             os.path.isfile(where["dropped image"]) and any("lists it again" in n for n in a.put_back)
             and not os.path.exists(where["nothing wrote"]) and no_quarantine(root), a.put_back)

    with tempfile.TemporaryDirectory() as tmp:
        root, export, where = garbage(tmp)
        s = sw.Sweep(root, sw.References(root, export), 86400, now)
        s.run()
        a = sw.Apply(s)
        check = a.finish

        def held_again(q):
            doc = jload(export)
            doc["items"].append({"id": GONE, "type": "movie", "title": "Restored"})
            jwrite(export, doc)
            check(q)
        a.finish = held_again
        a.run(now)
        t.ok("an item the database holds again by the time it is checked is put back: the export is read again",
             os.path.isdir(where["gone"]) and any("the database holds it again" in n for n in a.put_back), a.put_back)

    with tempfile.TemporaryDirectory() as tmp:
        root, export, where = garbage(tmp)
        s = sw.Sweep(root, sw.References(root, export), 86400, now)
        s.run()
        a = sw.Apply(s)
        check = a.finish

        def credited_late(q):
            credit(movie_of(where), GONE_PERSON)
            check(q)
        a.finish = credited_late
        a.run(now)
        t.ok("a deleted person an item record credits by the time they are checked is put back",
             os.path.isdir(where["gone person"]) and any("metadata.json credits them" in n for n in a.put_back)
             and no_quarantine(root), a.put_back)

    # ---- a deleted item that credits a deleted person: one sweep takes both, and putting one back keeps both
    with tempfile.TemporaryDirectory() as tmp:
        root, export, where = garbage(tmp, age=False)
        credit(where["gone"], GONE_PERSON)
        aged(root)
        code, text = run(SWEEP, root, "--export", export)
        t.ok("a credit in an item folder the same sweep removes as a deleted item keeps nobody",
             where["gone person"] in swept(text, root) and where["gone"] in swept(text, root), text)
        code, text = run(SWEEP, root, "--export", export, "--apply")
        t.ok("and --apply takes both, so a second sweep finds neither",
             code == 0 and not os.path.exists(where["gone"]) and not os.path.exists(where["gone person"])
             and not {where["gone"], where["gone person"]} & set(swept(run(SWEEP, root, "--export", export)[1], root)), text)

    with tempfile.TemporaryDirectory() as tmp:
        root, export, where = garbage(tmp, age=False)
        credit(where["gone"], GONE_PERSON)
        aged(root)
        s = sw.Sweep(root, sw.References(root, export), 86400, now)
        s.run()
        a = sw.Apply(s)
        check = a.finish

        def item_back(q):
            doc = jload(export)
            doc["items"].append({"id": GONE, "type": "movie", "title": "Restored"})
            jwrite(export, doc)
            check(q)
        a.finish = item_back
        a.run(now)
        t.ok("a deleted item put back brings back the credits in it: the person it credits is checked after it, and "
             "put back too", os.path.isdir(where["gone"]) and os.path.isdir(where["gone person"])
             and any(n.startswith("put back people/") and "credits them" in n for n in a.put_back), a.put_back)

    with tempfile.TemporaryDirectory() as tmp:
        root, export, where = garbage(tmp)
        s = sw.Sweep(root, sw.References(root, export), 86400, now)
        s.run()
        a = sw.Apply(s)
        q = a.quarantine_dir(now)
        a.write_plan(q, s.targets, now)
        for target in s.targets:
            os.makedirs(os.path.dirname(os.path.join(q, target["path"])), exist_ok=True)
            os.rename(os.path.join(root, target["path"]), os.path.join(q, target["path"]))
        code, text = run(SWEEP, root, "--export", export)
        t.ok("a dry run says an earlier --apply did not finish", "did not finish" in text, text)
        code, text = run(SWEEP, root, "--export", export, "--apply")
        t.ok("the quarantine is a folder of the work tree, .work/quarantine/<stamp>/",
             os.path.dirname(q) == os.path.join(root, ".work", "quarantine"))
        t.ok("and the next --apply checks and finishes it", code == 0 and no_quarantine(root)
             and "removed 8 target(s)" in text, text)

    # a quarantine a sweep from before the work tree left at the library root is finished the same way
    with tempfile.TemporaryDirectory() as tmp:
        root, export, where = garbage(tmp)
        s = sw.Sweep(root, sw.References(root, export), 86400, now)
        s.run()
        q = os.path.join(root, "_swept", "20261002T120000Z")
        os.makedirs(q)
        sw.Apply(s).write_plan(q, s.targets, now)
        for target in s.targets:
            os.makedirs(os.path.dirname(os.path.join(q, target["path"])), exist_ok=True)
            os.rename(os.path.join(root, target["path"]), os.path.join(q, target["path"]))
        code, text = run(SWEEP, root, "--export", export, "--apply")
        t.ok("a quarantine a sweep from before the work tree left in _swept/ is finished by the next --apply, and "
             "_swept/ goes with it", code == 0 and "removed 8 target(s)" in text and no_quarantine(root), text)

    # --quarantine names another folder of the share
    with tempfile.TemporaryDirectory() as tmp:
        root, export, where = garbage(tmp)
        elsewhere = os.path.join(tmp, "quarantine-of-this-run")
        code, text = run(SWEEP, root, "--export", export, "--apply", "--quarantine", elsewhere)
        t.ok("--quarantine names the folder a run quarantines in instead, and it is emptied as the work tree's is",
             code == 0 and "removed 8 target(s)" in text and os.listdir(elsewhere) == []
             and not os.path.exists(os.path.join(root, ".work")), text)

    with tempfile.TemporaryDirectory() as tmp:
        root, export, where = garbage(tmp)
        stray = os.path.join(root, "_swept", "unknown", "something")
        os.makedirs(os.path.dirname(stray))
        open(stray, "w").write("x")
        code, text = run(SWEEP, root, "--export", export, "--apply")
        t.ok("a quarantine without its sweep.json is left for a person, and the run says it failed",
             code == 1 and "without its sweep.json" in text and os.path.isfile(stray), text)

    if os.geteuid() != 0:
        with tempfile.TemporaryDirectory() as tmp:
            root, export, where = garbage(tmp)
            folder = os.path.dirname(where["dropped image"])
            os.chmod(folder, 0o555)
            try:
                code, text = run(SWEEP, root, "--export", export, "--apply")
            finally:
                os.chmod(folder, 0o755)
            t.ok("a target that cannot be renamed into the quarantine is left where it is, and the run fails",
                 code == 1 and "could not be renamed into the quarantine" in text and os.path.isfile(where["dropped image"]), text)
    else:
        t.skip("a target that cannot be renamed is left where it is", "permissions do not bind root")


# ---------------------------------------------------------------- sweeping a version an event removed
def removed_version(tmp, at=None, change=None):
    """The examples with the folder of the version the movie's version-removed event retired still on
    storage, as a finished package left it, and the export of a database that holds every item. Returns
    the tree, the export and the folder."""
    root = os.path.join(tmp, "library")
    shutil.copytree(EXAMPLES, root)
    movie = glob.glob(os.path.join(root, "movies", "*", "*"))[0]
    removal = glob.glob(os.path.join(movie, "events", "*-version-removed", "event.json"))[0]
    vid = jload(removal)["versionId"]
    if at:
        shutil.rmtree(os.path.dirname(removal))
        write_event(movie, dict(jload(os.path.join(EXAMPLES, os.path.relpath(removal, root))), at=at))
    # what the folder holds does not matter — a removed version's folder is ignored, whatever is in it — but an
    # original: the version whose original was deleted, as a version is removed once its original is retired
    vp = os.path.join(movie, "versions", vid)
    shutil.copytree(next(p for p in sorted(glob.glob(os.path.join(movie, "versions", "*")))
                         if not any(os.path.isfile(os.path.join(p, n))
                                    for n in jload(os.path.join(p, "version.json"))["originalFiles"])), vp)
    jwrite(os.path.join(vp, "version.json"), dict(jload(os.path.join(vp, "version.json")), versionId=vid))
    rows, _ = rows_of(EXAMPLES)
    export = os.path.join(tmp, "catalog.json")
    jwrite(export, {"exportedAt": "2026-10-01T12:00:00Z", "items": list(rows.values()), "deletedItems": []})
    if change:
        change(root, export, vp)
    aged(root)
    return root, export, vp


def test_sweep_removed_versions(t):
    """The catalog deletes the folder of a version it removed, after the grace a superseded version
    keeps; a folder that delete missed is the sweep's — whatever it holds — once the removal is older than
    the grace and nothing names the version but its history."""
    with tempfile.TemporaryDirectory() as tmp:
        root, export, vp = removed_version(tmp)
        code, text = run(SWEEP, root, "--export", export)
        t.eq("a dry run finds the folder of a version an event removed, and nothing else", swept(text, root), [vp])
        t.ok("and says why", "removed version: a version-removed event of 2026-09-20T09:30:00Z says it is no longer "
                             "part of the item" in text, text)
        code, text = run_piped(SWEEP, root, "--export", export, "--apply")
        t.ok("--apply removes it through the quarantine, and leaves none behind",
             code == 0 and not os.path.lexists(vp) and "removed 1 target(s)" in text and no_quarantine(root), text)
        if have_jsonschema():
            code, vtext = run(VALIDATOR, "--check-checksums", root)
            t.ok("what is left is a valid record", code == 0 and vtext.strip().endswith("OK"), vtext)

    with tempfile.TemporaryDirectory() as tmp:
        def superseded(root, export, vp):
            # its history names it: superseded first, then removed
            movie = os.path.dirname(os.path.dirname(vp))
            current = next(p for p in sorted(glob.glob(os.path.join(movie, "versions", "*"))) if p != vp)
            write_event(movie, {"schema": "zaentrum.library.event/2", "eventId": "5e000000-0000-4000-8000-000000000001",
                                "at": "2026-09-20T09:00:00Z", "by": "test", "kind": "package-superseded",
                                "versionId": os.path.basename(vp), "packageId": jload(os.path.join(vp, "package.json"))["packageId"],
                                "supersededBy": {"versionId": os.path.basename(current),
                                                 "packageId": jload(os.path.join(current, "package.json"))["packageId"]}})
        root, export, vp = removed_version(tmp, change=superseded)
        code, text = run(SWEEP, root, "--export", export)
        t.eq("the event of its supersession is its history, and keeps nothing", swept(text, root), [vp])

    def kept(name, phrase, **kw):
        with tempfile.TemporaryDirectory() as tmp:
            root, export, vp = removed_version(tmp, **kw)
            code, text = run(SWEEP, root, "--export", export)
            t.ok(f"the sweep leaves alone the folder of a removed version {name}", code == 0 and swept(text, root) == []
                 and phrase in text and os.path.isdir(vp), text)
    recent = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    kept("whose removal is within the grace", "within the grace period", at=recent)
    kept("the projection still names", "a removed version, but metadata.json names it",
         change=lambda root, export, vp: jwrite(os.path.join(os.path.dirname(os.path.dirname(vp)), "metadata.json"), dict(
             jload(os.path.join(os.path.dirname(os.path.dirname(vp)), "metadata.json")),
             library=dict(jload(os.path.join(os.path.dirname(os.path.dirname(vp)), "metadata.json"))["library"],
                          primaryVersionId=os.path.basename(vp)))))
    kept("the database still holds", "a removed version, but the export names it",
         change=lambda root, export, vp: jwrite(export, dict(jload(export), versions=[
             {"id": os.path.basename(vp), "itemId": os.path.basename(os.path.dirname(os.path.dirname(vp))),
              "state": "superseded"}])))
    kept("that still holds the original its version names", "but its folder still holds its original",
         change=lambda root, export, vp: open(os.path.join(vp, jload(os.path.join(vp, "version.json"))["originalFiles"][0]),
                                              "wb").write(b"the only copy left"))
    kept("that still holds a file named as the library names an original", "still holds its original original-2.mkv",
         change=lambda root, export, vp: open(os.path.join(vp, "original-2.mkv"), "wb").write(b"an original"))


# ---------------------------------------------------------------- sweeping an extra that never finished
X_BESIDE = "44444444-0000-4000-8000-00000000000e"         # an extra's package that never finished beside its original
X_RECORDED = "55555555-0000-4000-8000-00000000000e"       # a packaged extra that wrote its record and kept no original
X_NOTHING_WROTE = "66666666-0000-4000-8000-00000000000e"  # part of an extra's package, and no record
X_UNPACKAGED = "77777777-0000-4000-8000-00000000000e"     # an extra with no package whose writer never finished


def extra_garbage(tmp, age=True):
    """The examples with every kind of extra that never finished beside the movie's featurette, and
    the export of a database that holds every item they hold. Returns the tree, the export's path and
    where each extra is."""
    root = os.path.join(tmp, "library")
    shutil.copytree(EXAMPLES, root)
    movie = glob.glob(os.path.join(root, "movies", "*", "*"))[0]
    featurette = extra_of(root, "featurette")
    record = jload(os.path.join(featurette, "extra.json"))
    original = record["originalFiles"][0]

    def copy(xid, drop, **fields):
        xp = os.path.join(movie, "extras", xid)
        shutil.copytree(featurette, xp)
        for name in drop:
            p = os.path.join(xp, name)
            shutil.rmtree(p) if os.path.isdir(p) else os.unlink(p)
        if os.path.isfile(os.path.join(xp, "extra.json")):
            jwrite(os.path.join(xp, "extra.json"), dict(record, extraId=xid, **fields))
        return xp

    where = {"featurette": featurette, "bts": glob.glob(os.path.join(root, "series", "*", "*", "extras", "*"))[0],
             "movie": movie, "original": original,
             "beside": copy(X_BESIDE, (".complete", "package.json")),
             "recorded": copy(X_RECORDED, (".complete", "package.json", "checksums.sha256", original), originalFiles=[],
                              originals=[]),
             "nothing wrote": copy(X_NOTHING_WROTE, (".complete", "package.json", "checksums.sha256", original,
                                                     "extra.json", "subs", "trickplay")),
             "unpackaged": copy(X_UNPACKAGED, (".complete", "package.json", "checksums.sha256", "hls", "subs", "trickplay"))}
    rows, _ = rows_of(EXAMPLES)
    export = os.path.join(tmp, "catalog.json")
    jwrite(export, {"exportedAt": "2026-10-01T12:00:00Z", "items": list(rows.values()), "deletedItems": []})
    if age:
        aged(root)
    return root, export, where


def test_sweep_extras(t):
    with tempfile.TemporaryDirectory() as tmp:
        root, export, where = extra_garbage(tmp)
        expected = sorted([os.path.join(where["beside"], n) for n in ("checksums.sha256", "hls", "subs", "trickplay")]
                          + [where["recorded"], where["nothing wrote"]])
        before = stamps(root)
        code, text = run(SWEEP, root, "--export", export)
        t.eq("a dry run finds the package of every extra that never finished, and nothing else in an extra",
             swept(text, root), expected)
        t.ok("and says why each one is garbage",
             "no .complete and no extra.json: nothing can have known it" in text
             and "no .complete, no original, and nothing names it" in text
             and "the extra's package never finished beside the original" in text, text)
        t.ok("an extra that holds no package is left alone, finished or not, and the one that never finished is said",
             any(line.startswith(os.path.relpath(where["unpackaged"], root)) and "holds no package" in line
                 for line in left_alone(text)) and not any(where["featurette"] in p or where["bts"] in p
                                                          for p in swept(text, root)), text)
        t.ok("and the dry run changes nothing", code == 0 and stamps(root) == before, text)

        code, text = run_piped(SWEEP, root, "--export", export, "--apply")
        t.ok("--apply, piped into a pod's Python, removes exactly those", code == 0
             and not any(os.path.lexists(e) for e in expected) and "removed 6 target(s)" in text
             and no_quarantine(root), text)
        t.ok("the record and the original beside the unfinished package stay",
             os.path.isfile(os.path.join(where["beside"], "extra.json"))
             and os.path.isfile(os.path.join(where["beside"], where["original"])))
        after = stamps(root)
        t.ok("and every finished extra is exactly as it was",
             all(after.get(k) == v for k, v in before.items()
                 if os.path.join(root, k).startswith((where["featurette"] + os.sep, where["bts"] + os.sep))))
        code, text = run(SWEEP, root, "--export", export)
        t.ok("and a second sweep finds nothing", code == 0 and swept(text, root) == [], text)

    def refused(name, change, phrase, what="recorded", export_too=True, age_after=False):
        with tempfile.TemporaryDirectory() as tmp:
            root, export, where = extra_garbage(tmp)
            change(root, export, where)
            if age_after:
                aged(root)
            code, text = run(SWEEP, root, *(("--export", export) if export_too else ()))
            t.ok(f"the sweep leaves alone {name}", code == 0 and where[what] not in swept(text, root)
                 and any(phrase in line for line in left_alone(text)), text)

    def decide(w, decision):
        meta = os.path.join(w["movie"], "metadata.json")
        doc = jload(meta)
        doc["library"]["extras"][X_RECORDED] = decision
        jwrite(meta, doc)

    def mention(export):
        doc = jload(export)
        doc["items"][0]["playbackAssets"].append({"id": "x", "kind": "packaged", "path": f"/library/extras/{X_RECORDED}/package.json"})
        jwrite(export, doc)

    refused("an unfinished extra the projection names", lambda r, e, w: decide(w, {"hidden": True}),
            "metadata.json names it", age_after=True)
    refused("an unfinished extra the database names", lambda r, e, w: mention(e), "the export names it")
    refused("an unfinished extra written to within the grace",
            lambda r, e, w: os.utime(os.path.join(w["recorded"], "extra.json"), None), "written to within the grace period")
    refused("an unfinished extra holding a file the sweep cannot classify",
            lambda r, e, w: open(os.path.join(w["recorded"], "notes.txt"), "w").write("x"), "cannot classify", age_after=True)
    refused("an unfinished extra whose record cannot be read",
            lambda r, e, w: open(os.path.join(w["recorded"], "extra.json"), "w").write("not JSON"), "cannot be read",
            age_after=True)
    refused("an unfinished extra that wrote its record, without an export to say the database does not know it",
            lambda r, e, w: None, "without --export the sweep cannot tell", export_too=False)
    def under_episode(r, e, w):
        w["episode's"] = os.path.join(glob.glob(os.path.join(r, "series", "*", "*", "episodes", "*"))[0], "extras",
                                      X_NOTHING_WROTE)
        shutil.copytree(w["nothing wrote"], w["episode's"])

    refused("an extras/ folder under an episode", under_episode, "an extras/ folder under an episode",
            what="episode's", age_after=True)

    # ---- checked again in the quarantine, against what is true by then
    sw = load_tool(SWEEP)
    now = datetime.datetime.now(datetime.timezone.utc)
    for name, late, why in (
            ("an unfinished extra the projection names by the time it is checked is put back",
             lambda w: decide(w, {"order": 3}), "metadata.json names it"),
            ("an extra whose package finished by the time it is checked gets its package back",
             lambda w: open(os.path.join(w["beside"], ".complete"), "w").write("sha256:" + "0" * 64 + "\n"),
             "its extra has finished since")):
        with tempfile.TemporaryDirectory() as tmp:
            root, export, where = extra_garbage(tmp)
            s = sw.Sweep(root, sw.References(root, export), 86400, now)
            s.run()
            a = sw.Apply(s)
            check = a.finish

            def checked_late(q, late=late, where=where, check=check):
                late(where)
                check(q)
            a.finish = checked_late
            a.run(now)
            back = where["recorded"] if "projection" in name else os.path.join(where["beside"], "hls")
            t.ok(name, os.path.exists(back) and any(why in n for n in a.put_back)
                 and no_quarantine(root), a.put_back)

    # ---- the folder of an extra an extra-removed event retired, still on storage
    def retired_garbage(tmp):
        """The examples with the folder of the trailer's old extra still there: the extra-removed event
        retired it, and the writer that packaged the trailer into a new folder left the old one."""
        root = os.path.join(tmp, "library")
        shutil.copytree(EXAMPLES, root)
        movie = glob.glob(os.path.join(root, "movies", "*", "*"))[0]
        event = glob.glob(os.path.join(movie, "events", "*-extra-removed", "event.json"))[0]
        xid = jload(event)["extraId"]
        old = os.path.join(movie, "extras", xid)
        shutil.copytree(extra_of(root, "trailer"), old)
        for name in (".complete", "package.json", "hls", "trickplay"):
            p = os.path.join(old, name)
            shutil.rmtree(p) if os.path.isdir(p) else os.unlink(p)
        jwrite(os.path.join(old, "extra.json"), dict(jload(os.path.join(old, "extra.json")), extraId=xid))
        rows, _ = rows_of(EXAMPLES)
        export = os.path.join(tmp, "catalog.json")
        jwrite(export, {"exportedAt": "2026-10-01T12:00:00Z", "items": list(rows.values()), "deletedItems": []})
        aged(root)
        return root, export, {"old": old, "movie": movie, "event": event, "xid": xid}

    with tempfile.TemporaryDirectory() as tmp:
        root, export, where = retired_garbage(tmp)
        before = stamps(root)
        code, text = run(SWEEP, root, "--export", export)
        t.eq("the folder of an extra an event removed is garbage, once the removal is older than the grace",
             swept(text, root), [where["old"]])
        t.ok("and the sweep says which event says so",
             "removed extra: an extra-removed event of 2026-09-19T10:05:00Z says it is no longer part of the item" in text, text)
        code, text = run_piped(SWEEP, root, "--export", export, "--apply")
        t.ok("--apply removes it through the quarantine, and nothing else",
             code == 0 and not os.path.exists(where["old"]) and "removed 1 target(s)" in text
             and no_quarantine(root)
             and all(stamps(root).get(k) == v for k, v in before.items() if not k.startswith(
                 os.path.relpath(where["old"], root) + os.sep)), text)

    def kept_alone(name, change, phrase):
        with tempfile.TemporaryDirectory() as tmp:
            root, export, where = retired_garbage(tmp)
            change(root, export, where)
            code, text = run(SWEEP, root, "--export", export)
            t.ok(f"the sweep leaves alone {name}", code == 0 and where["old"] not in swept(text, root)
                 and any(phrase in line for line in left_alone(text)), text)

    def decided(w):
        meta = os.path.join(w["movie"], "metadata.json")
        doc = jload(meta)
        doc["library"]["extras"][w["xid"]] = {"hidden": True}
        jwrite(meta, doc)
        aged(w["movie"])

    def export_edit_names(export, xid):
        doc = jload(export)
        doc["items"][0]["playbackAssets"].append({"id": "x", "kind": "packaged", "path": f"/library/extras/{xid}/package.json"})
        jwrite(export, doc)

    kept_alone("a removed extra whose removal is younger than the grace",
               lambda r, e, w: jwrite(w["event"], dict(jload(w["event"]), at=(datetime.datetime.now(datetime.timezone.utc)
                                                                            - datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"))),
               "within the grace period")
    kept_alone("a removed extra's folder written to within the grace",
               lambda r, e, w: os.utime(os.path.join(w["old"], "extra.json"), None), "written to within the grace period")
    kept_alone("a removed extra the projection still names", lambda r, e, w: decided(w), "metadata.json names it")
    kept_alone("a removed extra the database still names",
               lambda r, e, w: export_edit_names(e, w["xid"]), "the export names it")
    kept_alone("a removed extra a note names",
               lambda r, e, w: (write_event(w["movie"], {"schema": "zaentrum.library.event/2", "eventId": str(uuid.uuid4()),
                                                        "at": "2026-09-21T10:00:00Z", "by": "test", "kind": "note",
                                                        "extraId": w["xid"], "reason": "a person looked at it"}),
                                aged(w["movie"])), "names it")

    with tempfile.TemporaryDirectory() as tmp:
        root, export, where = retired_garbage(tmp)
        s = sw.Sweep(root, sw.References(root, export), 86400, now)
        s.run()
        a = sw.Apply(s)
        check = a.finish

        def named_late(q):
            decided(where)
            check(q)
        a.finish = named_late
        a.run(now)
        t.ok("a removed extra the projection names by the time it is checked is put back",
             os.path.isdir(where["old"]) and any("metadata.json names it" in n for n in a.put_back), a.put_back)


# ---------------------------------------------------------------- the media check
def test_media_check(t):
    def case(name, expect_ok, change, phrase="", extra=()):
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "library")
            shutil.copytree(EXAMPLES, root)
            change(root)
            code, text = run(MEDIA_CHECK, *extra, root)
            good = (code == 0) == expect_ok and phrase in text and "Traceback" not in text
            t.ok(f"the media check {'accepts' if expect_ok else 'catches'}: {name}", good, text)

    def movie(root):
        return glob.glob(os.path.join(root, "movies", "*", "*"))[0]

    def kept_version(root):
        for vp in sorted(glob.glob(os.path.join(movie(root), "versions", "*"))):
            if jload(os.path.join(vp, "version.json"))["originalFiles"]:
                return vp
        raise AssertionError("no version keeps an original")

    def package_version(root):
        return sorted(glob.glob(os.path.join(root, "series", "*", "*", "episodes", "*", "versions", "*")))[0]

    case("an untouched example tree", True, lambda r: None, "OK", ("--checksums",))

    def as_poster(*kinds):
        """The movie's images of kinds made the poster's bytes: entries sharing the poster's file, and
        the files they named before left for the sweep."""
        def change(r):
            mp = os.path.join(movie(r), "metadata.json")
            doc = jload(mp)
            poster = next(i for i in doc["images"] if i["kind"] == "poster")
            facts = {k: poster[k] for k in ("file", "sha256", "contentType", "sizeBytes", "width", "height")}
            doc["images"] = [dict(i, **facts) if i["kind"] in kinds else i for i in doc["images"]] + \
                [dict(poster, kind=k, primary=False) for k in kinds if k not in {i["kind"] for i in doc["images"]}]
            jwrite(mp, doc)
        return change
    case("a backdrop that is the poster: two kinds sharing one file", True, as_poster("backdrop"), "OK")
    case("three kinds sharing one file", True, as_poster("backdrop", "logo"), "OK")

    def poster_twice(r):
        mp = os.path.join(movie(r), "metadata.json")
        doc = jload(mp)
        doc["images"].append(next(i for i in doc["images"] if i["kind"] == "poster"))
        jwrite(mp, doc)
    case("one kind listing a file twice", False, poster_twice, "listed twice in metadata.json as a poster")
    case("an image a record names is gone", False,
         lambda r: os.unlink(glob.glob(os.path.join(movie(r), "metadata", "*"))[0]),
         "listed in metadata.json but not there")
    case("an image that is not named by its own content", False, lambda r: rename_image(movie(r)),
         "not named by the hash of its own content")
    case("an image that no record names", False,
         lambda r: open(os.path.join(movie(r), "metadata", "stray.jpg"), "wb").write(b"\xff\xd8"),
         "not listed in metadata.json")
    case("an image a later projection dropped, as a note for the sweep", True,
         lambda r: open(os.path.join(movie(r), "metadata", hashlib.sha256(b"\xff\xd8dropped").hexdigest() + ".jpg"),
                        "wb").write(b"\xff\xd8dropped"), "no longer lists; library-v2-sweep.py collects it")
    case("a file that claims a hash it does not have", False,
         lambda r: open(os.path.join(movie(r), "metadata", "b" * 64 + ".jpg"), "wb").write(b"\xff\xd8other"),
         "not listed in metadata.json")
    case("an original that is not there", False,
         lambda r: os.unlink(os.path.join(kept_version(r),
                                          jload(os.path.join(kept_version(r), "version.json"))["originalFiles"][0])),
         "original file missing")
    case("an original a deletion says is gone but is still there", False, restore_deleted,
         "still here")
    case("an original whose bytes changed", False, lambda r: open(
        os.path.join(kept_version(r),
                     jload(os.path.join(kept_version(r), "version.json"))["originalFiles"][0]), "ab").write(b"x"),
         "bytes")
    case("a package without its .complete marker", False,
         lambda r: os.unlink(os.path.join(package_version(r), ".complete")),
         "only package.json")
    case("a package file the checksums do not list", False,
         lambda r: open(os.path.join(package_version(r), "hls", "v0", "extra.m4s"), "wb").write(b"x"),
         "checksums.sha256 does not list")
    case("a checksums file that lists a file that is not there", False,
         lambda r: os.unlink(os.path.join(package_version(r), "trickplay", "sprite-0000.jpg")),
         "which is not a file of this package")
    case("a package file whose bytes changed", False, lambda r: open(
        os.path.join(package_version(r), "trickplay", "sprite-0000.jpg"), "ab").write(b"x"),
         "does not match its checksum", ("--checksums",))
    case("a rendition folder that is gone", False,
         lambda r: shutil.rmtree(os.path.join(package_version(r), "hls", "a0")),
         "rendition folder hls/a0 is missing")
    case("a subtitle the package names that is gone", False, lambda r: remove_subtitle(r),
         "is missing")

    def names(r, **fields):
        """An episode package naming, beside what it holds, what fields say: a 5.1 companion, a
        subtitle's HLS rendition, a master playlist. A subtitle's is named by the package that has some."""
        vp = next(v for v in sorted(glob.glob(os.path.join(r, "series", "*", "*", "episodes", "*", "versions", "*")))
                  if jload(os.path.join(v, "package.json")).get("subtitles") or not fields.get("subtitles"))
        doc = jload(os.path.join(vp, "package.json"))
        doc["renditions"].update(fields.get("renditions") or {})
        for s, extra in zip(doc["subtitles"], fields.get("subtitles") or []):
            s.update(extra)
        if fields.get("hls"):
            doc["hls"] = fields["hls"]
        jwrite(os.path.join(vp, "package.json"), doc)
        close(vp)
    def deleted_outside(r):
        """The original of an episode version that never kept it, deleted once its package was recorded."""
        vp = next(v for v in sorted(glob.glob(os.path.join(r, "series", "*", "*", "episodes", "*", "versions", "*")))
                  if not jload(os.path.join(v, "version.json"))["originalFiles"])
        write_event(os.path.dirname(os.path.dirname(vp)), {
            "schema": "zaentrum.library.event/2", "eventId": "0e000000-0000-4000-8000-000000000001",
            "at": "2026-09-21T10:00:00Z", "by": "test", "kind": "original-deleted", "versionId": os.path.basename(vp),
            "sourceId": jload(os.path.join(vp, "version.json"))["sourceIds"][0], "accepted": []})
    case("an original a version never kept, deleted once its package was recorded", True, deleted_outside, "OK",
         ("--checksums",))
    case("the work tree beside the record, which is none of it", True, work_tree, "OK", ("--checksums",))
    case("a 5.1 companion whose folder is gone", False,
         lambda r: names(r, renditions={"audioSurround": [{"id": "a9", "dir": "hls/a9", "channels": 6}]}),
         "rendition folder hls/a9 is missing")
    case("a subtitle's HLS rendition that is gone", False,
         lambda r: names(r, subtitles=[{"id": "sub0", "hls": "hls/s9"}]),
         "the HLS rendition hls/s9 of subtitle sub0 is missing")
    case("a master playlist that is gone", False,
         lambda r: names(r, hls={"master": "hls/gone.m3u8", "segmentSeconds": 6, "audioGroups": ["audio"],
                                 "subtitleGroup": None}),
         "master playlist hls/gone.m3u8 is missing")
    case("a probe a source record names that is gone", False,
         lambda r: os.unlink(glob.glob(os.path.join(movie(r), "sources", "*", "ffprobe.json"))[0]),
         "probe file a source record names is missing")
    case("a file that is a hard link to another path", False, hard_link, "hard links")
    # ---- every write-once folder proves its records, and each version is one chain
    def first(pattern, root):
        return sorted(glob.glob(os.path.join(root, *pattern.split("/"))))[0]

    def change(path, **fields):
        jwrite(path, dict(jload(path), **fields))

    case("an item record changed after it was written", False,
         lambda r: change(os.path.join(movie(r), "item.json"), title="Another"), "item.json: does not match the checksum")
    case("an item folder without its checksums", False,
         lambda r: os.unlink(os.path.join(movie(r), "checksums.sha256")), "missing: item.json is covered")
    case("an item's checksums listing more than item.json", False,
         lambda r: write_sums(movie(r), ["item.json", "metadata.json"]), "lists metadata.json, which is not item.json")
    case("a source record changed after it was written", False,
         lambda r: change(first("sources/*/source.json", movie(r)), takenBy="someone else"),
         "source.json: does not match the checksum")
    case("a source's checksums without its probe", False,
         lambda r: write_sums(os.path.dirname(first("sources/*/ffprobe.json", movie(r))), ["source.json"]),
         "does not list ffprobe.json")
    case("a source in the layout before each source was a folder", False,
         lambda r: shutil.move(first("sources/*/source.json", movie(r)),
                               os.path.dirname(first("sources/*/source.json", movie(r))) + ".json"),
         "not a sources/<sourceId>/ folder (library-v2-upgrade.py")
    case("an event changed after it was written", False,
         lambda r: change(first("events/*/event.json", movie(r)), reason="changed afterwards"),
         "event.json: does not match the checksum")
    case("an event folder without its checksums", False,
         lambda r: os.unlink(first("events/*/checksums.sha256", movie(r))), "missing: event.json is covered")
    case("an event in the layout before each event was a folder", False,
         lambda r: shutil.move(first("events/*/event.json", movie(r)),
                               os.path.join(movie(r), "events", "20260920T081500Z-original-deleted.json")),
         "not an events/<YYYYMMDDTHHMMSSZ>-<eventId8>-<kind>/ folder (library-v2-upgrade.py")
    case("a version record changed after its package completed", False,
         lambda r: change(os.path.join(kept_version(r), "version.json"), runtimeMs=1),
         "version.json: does not match the checksum checksums.sha256 recorded for it: changed after the package")
    case("a version's checksums that do not list version.json", False,
         lambda r: relist(kept_version(r), [n for n in listing(kept_version(r)) if n != "version.json"]),
         "does not list version.json")
    case("a version's checksums listing the marker above them", False,
         lambda r: relist(kept_version(r), listing(kept_version(r)) + [".complete"]), "lists .complete, a link of the chain")
    case("a package record changed after it completed", False,
         lambda r: change(os.path.join(kept_version(r), "package.json"), packagedBy="someone else"),
         "does not name this package.json")
    case("a marker that is not a hash", False,
         lambda r: open(os.path.join(kept_version(r), ".complete"), "w").write("packager example\n"),
         "not sha256:<hex> of package.json")
    case("a checksums total that leaves out version.json", False,
         lambda r: (change(os.path.join(kept_version(r), "package.json"), checksums=dict(
             jload(os.path.join(kept_version(r), "package.json"))["checksums"],
             bytes=jload(os.path.join(kept_version(r), "package.json"))["checksums"]["bytes"]
             - os.path.getsize(os.path.join(kept_version(r), "version.json")))), close(kept_version(r))),
         "the files it lists total")
    case("a portrait a person record names that is gone", False,
         lambda r: os.unlink(glob.glob(os.path.join(r, "people", "*", "*", "*.jpg"))[0]),
         "image listed in person.json but not there")
    case("a stray file in a person's folder", False,
         lambda r: open(os.path.join(glob.glob(os.path.join(r, "people", "*", "*"))[0], "notes.txt"), "w").write("x"),
         "not person.json and not an image person.json lists")
    case("a portrait that is a hard link to another path", False,
         lambda r: os.link(glob.glob(os.path.join(r, "people", "*", "*", "*.jpg"))[0],
                           os.path.join(os.path.dirname(r), "elsewhere")), "hard links")

    # ---- bonus material: finished or saying it is not, its files there and every one of them covered
    def featurette(root):
        """The movie's featurette, kept as its original and as a package of its own."""
        return extra_of(root, "featurette")

    def bts(root):
        """The series' extra, kept only as its original."""
        return extra_of(root, "behind-the-scenes")

    def x_original(xp):
        return os.path.join(xp, jload(os.path.join(xp, "extra.json"))["originalFiles"][0])

    def package_only(r):
        xp = featurette(r)
        os.unlink(x_original(xp))
        change(os.path.join(xp, "extra.json"), originalFiles=[], originals=[])
        relist(xp, [n for n in listing(xp) if os.path.isfile(os.path.join(xp, n))])

    def nothing_kept(r):
        xp = bts(r)
        os.unlink(x_original(xp))
        change(os.path.join(xp, "extra.json"), originalFiles=[], originals=[])
        write_sums(xp, ["extra.json"])

    def recorded_as(r, **fields):
        xp = featurette(r)
        change(os.path.join(xp, "package.json"),
               checksums=dict(jload(os.path.join(xp, "package.json"))["checksums"], **fields))
        close(xp)

    def changed_in_place(p):
        """The same number of bytes, one of them different: only a fingerprint tells."""
        data = open(p, "rb").read()
        with open(p, "wb") as f:
            f.write(data[:-1] + (b"X" if data[-1:] != b"X" else b"Y"))

    def recorded_sha(r):
        """extra.json saying another sha256 of its original, with the checksums written again over it."""
        xp = bts(r)
        doc = jload(os.path.join(xp, "extra.json"))
        doc["originals"][0]["fixity"]["sha256"] = "sha256:" + "0" * 64
        jwrite(os.path.join(xp, "extra.json"), doc)
        write_sums(xp, listing(xp))

    case("an extra's original that is not there", False, lambda r: os.unlink(x_original(bts(r))),
         "an original extra.json names is missing")
    case("an original-only extra's original that grew, without --checksums", False,
         lambda r: open(x_original(bts(r)), "ab").write(b"x"), "bytes, extra.json says")
    case("an original-only extra's original changed in place, by its qh1 and without --checksums", False,
         lambda r: changed_in_place(x_original(bts(r))), "does not match the qh1 fingerprint extra.json wrote down")
    case("an extra's original that is not the sha256 extra.json wrote down", False, recorded_sha,
         "does not match the sha256 extra.json wrote down", ("--checksums",))
    case("an extra's original whose bytes changed", False, lambda r: open(x_original(featurette(r)), "ab").write(b"x"),
         "original.mkv: does not match its checksum", ("--checksums",))
    case("a file of an extra that its checksums do not list", False,
         lambda r: open(os.path.join(featurette(r), "hls", "v0", "seg-0001.m4s"), "wb").write(b"x"),
         "is a file of this extra that checksums.sha256 does not list")
    case("an extra's checksums listing a file that is not there", False,
         lambda r: os.unlink(os.path.join(featurette(r), "subs", "0.vtt")), "lists subs/0.vtt, which is not a file of this extra")
    case("an extra record changed after it was written", False,
         lambda r: change(os.path.join(bts(r), "extra.json"), title="Another"), "extra.json: does not match the checksum")
    case("an extra's checksums listing its package record", False,
         lambda r: relist(featurette(r), listing(featurette(r)) + ["package.json"]), "lists package.json, a link of the chain")
    case("an extra's checksums file that is gone", False,
         lambda r: os.unlink(os.path.join(featurette(r), "checksums.sha256")), "the checksums file of a finished extra is missing")
    case("an extra's checksums file that is not the one package.json recorded", False,
         lambda r: open(os.path.join(featurette(r), "checksums.sha256"), "a").write(f"{'0' * 64}  hls/extra\n"),
         "does not match the sha256 package.json wrote down")
    case("an extra's checksums counted otherwise than they list", False,
         lambda r: recorded_as(r, files=jload(os.path.join(featurette(r), "package.json"))["checksums"]["files"] + 1),
         "files, package.json says")
    case("an extra's checksums total that leaves out its original", False,
         lambda r: recorded_as(r, bytes=jload(os.path.join(featurette(r), "package.json"))["checksums"]["bytes"]
                               - os.path.getsize(x_original(featurette(r)))), "the files it lists total")
    case("an extra's package without its .complete marker", False,
         lambda r: os.unlink(os.path.join(featurette(r), ".complete")), "only package.json")
    case("an extra's package record changed after it completed", False,
         lambda r: change(os.path.join(featurette(r), "package.json"), packagedBy="someone else"),
         "does not name this package.json")
    case("an extra's rendition folder that is gone", False,
         lambda r: shutil.rmtree(os.path.join(featurette(r), "hls", "a0")), "rendition folder hls/a0 is missing")
    case("an extra with neither an original nor a package", False, nothing_kept, "holds neither an original nor a package")
    case("extras in an episode", False,
         lambda r: os.makedirs(os.path.join(glob.glob(os.path.join(r, "series", "*", "*", "episodes", "*"))[0], "extras")),
         "an episode has no extras")
    case("a file among the extra folders", False,
         lambda r: open(os.path.join(os.path.dirname(bts(r)), "loose.json"), "w").write("{}"), "not an extras/<extraId>/ folder")
    case("an extra that never finished, as a note", True, lambda r: os.unlink(os.path.join(bts(r), "checksums.sha256")),
         "an extra that never finished")
    case("an extra whose package never finished, as a note for the sweep", True,
         lambda r: [os.unlink(os.path.join(featurette(r), n)) for n in (".complete", "package.json", "checksums.sha256")],
         "an extra whose package never finished")
    case("an extra kept only as its package", True, package_only, "OK", ("--checksums",))

    def resurrect_extra(r):
        """The folder of the extra the movie's extra-removed event retired, back on storage, full of junk."""
        ev = jload(glob.glob(os.path.join(movie(r), "events", "*-extra-removed", "event.json"))[0])
        xp = os.path.join(movie(r), "extras", ev["extraId"])
        os.makedirs(xp)
        with open(os.path.join(xp, "extra.json"), "w") as f:
            f.write("this is not even JSON")

    case("the folder of an extra an event removed, still on storage: not part of the item, so not checked", True,
         resurrect_extra, "OK", ("--checksums",))


def work_tree(root):
    """.work/ beside movies/, series/ and people/, as the platform keeps the share: arrivals, a worker's
    handoff, a packager's staging, the trash, a quarantine — files a record-reading tool must not see."""
    for rel in ("incoming/Example Film (2024)/Example Film (2024).mkv", "extras/trailers/trailer.mov",
                "inbox/f001aeff-0000-4000-8000-000000000000/renditions.json", "staging/9a2e0000/version/version.json",
                "trash/20261006/0b6c0000/old.mkv", "quarantine/20261006T120000Z/sweep.json"):
        os.makedirs(os.path.dirname(os.path.join(root, ".work", rel)), exist_ok=True)
        with open(os.path.join(root, ".work", rel), "w") as f:
            f.write("not the record\n")


def rename_image(d):
    meta = jload(os.path.join(d, "metadata.json"))
    old = meta["images"][0]["file"]
    new = "a" * 64 + os.path.splitext(old)[1]
    shutil.move(os.path.join(d, "metadata", old), os.path.join(d, "metadata", new))
    meta["images"][0]["file"] = new
    jwrite(os.path.join(d, "metadata.json"), meta)


def restore_deleted(root):
    """Put back an original that an original-deleted event says is gone."""
    ev = jload(glob.glob(os.path.join(root, "movies", "*", "*", "events", "*-original-deleted", "event.json"))[0])
    vp = os.path.join(glob.glob(os.path.join(root, "movies", "*", "*"))[0], "versions", ev["versionId"])
    version = jload(os.path.join(vp, "version.json"))
    with open(os.path.join(vp, version["originalFiles"][0]), "wb") as f:
        f.write(b"back from the dead")


def remove_subtitle(root):
    for vp in sorted(glob.glob(os.path.join(root, "series", "*", "*", "episodes", "*", "versions", "*"))):
        package = jload(os.path.join(vp, "package.json"))
        if package.get("subtitles"):
            os.unlink(os.path.join(vp, package["subtitles"][0]["path"]))
            return
    raise AssertionError("no package with a subtitle")


def hard_link(root):
    d = glob.glob(os.path.join(root, "movies", "*", "*"))[0]
    image = glob.glob(os.path.join(d, "metadata", "*"))[0]
    os.link(image, os.path.join(os.path.dirname(root), "elsewhere"))


def main():
    """Every section, or with arguments only the sections whose name contains one of them."""
    t = Tally()
    wanted = sys.argv[1:]
    for section, fn in (("the pieces", test_pieces), ("the records the packager writes", test_records),
                        ("the example tree", test_round_trip),
                        ("a record proves itself", test_proves_itself), ("the work tree beside the record", test_work_tree),
                        ("applying events", test_events), ("an orphan, a loss, a missing record", test_compare),
                        ("the catalog's own export", test_export_sample),
                        ("v1 -> v2", test_from_v1),
                        ("catalog -> v2", test_from_catalog), ("an image of several kinds", test_images_of_several_kinds),
                        ("the arrivals are not the record", test_arrivals),
                        ("the platform's library, staged and adopted", test_platform),
                        ("what the platform's library cannot hold", test_platform_problems),
                        ("one file, several episodes, staged; a disc image left out", test_platform_covers),
                        ("a downloaded trailer -> an extra", test_from_catalog_extras),
                        ("credits: a role, the source's own words, one order", test_credits), ("people", test_people),
                        ("the people list in full", test_people_in_full),
                        ("projecting again, and nothing else", test_projections_only),
                        ("upgrading a tree in place", test_upgrade),
                        ("neutral names for a tree from before", test_neutral_names), ("sweeping garbage", test_sweep),
                        ("sweeping a version an event removed", test_sweep_removed_versions),
                        ("sweeping an extra that never finished", test_sweep_extras),
                        ("the media check", test_media_check)):
        if wanted and not any(w in section for w in wanted):
            continue
        print(f"\n--- {section}")
        try:
            fn(t)
        except Exception as e:
            t.ok(f"{section} ran to the end", False, f"{type(e).__name__}: {e}")
    print(f"\n{t.passed} passed, {t.failed} failed, {t.skipped} skipped")
    sys.exit(1 if t.failed else 0)


if __name__ == "__main__":
    main()
