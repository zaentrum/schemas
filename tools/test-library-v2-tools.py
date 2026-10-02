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
                 with nothing to play
  events         each kind changes the rebuilt rows as the README says, and removing the event
                 changes them back: a superseded package reappears, a removed version reappears, a
                 deleted original becomes a playback asset again, and a note changes nothing
  compare        an item only on storage is an orphan when the deletion log explains it and lost
                 when it does not, an item only in the database is a missing record, and only the
                 last two fail; without a log nothing on storage can be called an orphan; a person
                 only on storage takes the class of the items that credit them
  people         a credited person gets a record that holds what the credit knows; a people list in
                 the export fills every field it carries; --people-only touches no item record; a
                 projection that drops a portrait removes it
  upgrade        a tree in the layout before 2026-10-02 (b) upgrades, piped into the pod's Python,
                 to one that validates, with every record the same record and no media read; a dry
                 run changes nothing, a stopped run is finished by the next, a second run does
                 nothing, and whatever contradicts its records is refused and left as it was
  proves itself  sha256sum -c passes in every write-once folder, and each version is one chain
  v1 -> v2       the v1 example tree converts, the result passes validate-library-v2.py and the
                 media check, the texts and the packages survive, and a second run does nothing
  catalog -> v2  an export, a package store and source files become a tree that validates; the
                 rebuild of that tree agrees with the export it came from; two runs write the same
                 bytes; an item whose original is missing keeps its texts and loses its versions
  media check    every check it makes fails on a tree that breaks it and passes on one that does not
  the pieces     the JPEG and PNG header parsing, the qh1 fingerprint and the generated ids
"""
import base64, datetime, glob, hashlib, importlib.util, json, os, shutil, subprocess, sys, tempfile, uuid

TOOLS = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(TOOLS, "..", "library", "v2", "examples")
V1_EXAMPLES = os.path.join(TOOLS, "..", "library", "v1", "examples")
FROM_CATALOG = os.path.join(TOOLS, "library-v2-from-catalog.py")
FROM_V1 = os.path.join(TOOLS, "library-v2-from-v1.py")
REBUILD = os.path.join(TOOLS, "library-v2-rebuild.py")
MEDIA_CHECK = os.path.join(TOOLS, "library-v2-media-check.py")
UPGRADE = os.path.join(TOOLS, "library-v2-upgrade.py")
VALIDATOR = os.path.join(TOOLS, "validate-library-v2.py")


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
    """A tool the way a pod runs it: python3 - <args> < tool.py, with no file of its own to find."""
    with open(tool, "rb") as f:
        r = subprocess.run([sys.executable, "-", *[str(a) for a in args]], stdin=f, capture_output=True)
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


# ---------------------------------------------------------------- the pieces
def test_pieces(t):
    cat = load_tool(FROM_CATALOG)

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
        t.eq("qh1 of a file smaller than the window is its bytes and its size", cat.qh1(small),
             "sha256:" + hashlib.sha256(b"a" * 1000 + (1000).to_bytes(8, "big")).hexdigest())
        t.eq("qh1 of a large file is its first and last 64 KiB and its size", cat.qh1(big),
             "sha256:" + hashlib.sha256(body[:65536] + body[-65536:] +
                                        len(body).to_bytes(8, "big")).hexdigest())
        # the rule bites: a file with the same ends and a different size is a different fingerprint
        longer = os.path.join(tmp, "longer")
        with open(longer, "wb") as f:
            f.write(body[:65536] + b"\x00" * 10 + body[65536:])
        t.ok("qh1 tells two files with the same ends and different sizes apart",
             cat.qh1(big) != cat.qh1(longer))
        t.eq("the media check computes the same fingerprint", load_tool(MEDIA_CHECK).qh1(big), cat.qh1(big))

    t.eq("a generated id is a v5 UUID of the item and a stable name",
         cat.did("item", "version", "x"),
         str(uuid.uuid5(uuid.uuid5(uuid.NAMESPACE_URL, "https://zaentrum.github.io/schemas/library"),
                        "item:version:x")))
    t.ok("a different name is a different id", cat.did("item", "version", "x") != cat.did("item", "version", "y"))
    t.eq("every tool derives ids the same way", cat.did("i", "package", "p"),
         load_tool(REBUILD).did("i", "package", "p"))

    t.eq("a file name that numbers a range says so", cat.naming("Show - S07E23-24.mkv"),
         {"scheme": "unknown", "seasonNumber": 7, "episodeNumber": 23, "episodeEnd": 24, "raw": "S07E23-24"})
    t.eq("and the ordering it used stays unknown, because the name does not say",
         cat.naming("Show 1x05.mkv"),
         {"scheme": "unknown", "seasonNumber": 1, "episodeNumber": 5, "episodeEnd": None, "raw": "1x05"})
    t.eq("a quality token is not an episode range", cat.naming("Show S01E02-1080p.mkv")["episodeEnd"], None)
    t.eq("a name that numbers nothing says nothing", cat.naming("Example Film (2024).mkv"), None)
    t.eq("the v1 converter reads a name the same way", load_tool(FROM_V1).Convert.naming(None, "Show - S07E23-24.mkv"),
         cat.naming("Show - S07E23-24.mkv"))

    gate = load_tool(REBUILD).deletion_gate
    source = {"surround": True, "maxAudioChannels": 6, "subtitleLanguages": ["en", "de"], "chapters": True}
    package = {"surround": False, "maxAudioChannels": 2, "subtitleLanguages": ["en"]}
    t.eq("the deletion gate names what the package failed to carry", gate([source], package),
         ["maxAudioChannels", "subtitleLanguages:de", "surround"])
    t.eq("a package that carries everything costs nothing", gate([package], package), [])
    t.eq("chapter marks are never a loss: the version keeps them, not the file",
         gate([{"chapters": True}], {}), [])


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
                "version" if os.path.isfile(os.path.join(f, "version.json")) else "other")
        kinds[kind] = kinds.get(kind, 0) + 1
    items = len(glob.glob(os.path.join(EXAMPLES, "**", "item.json"), recursive=True))
    t.eq("every item, source, event and version folder has its checksums, and nothing else does",
         kinds, {"item": items, "source": len(glob.glob(os.path.join(EXAMPLES, "**", "source.json"), recursive=True)),
                 "event": len(glob.glob(os.path.join(EXAMPLES, "**", "event.json"), recursive=True)),
                 "version": len(glob.glob(os.path.join(EXAMPLES, "**", ".complete"), recursive=True))})
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
        chain &= open(mark).read().strip() == "sha256:" + digest(os.path.join(vp, "package.json"))
        chain &= package["checksums"]["sha256"] == "sha256:" + digest(os.path.join(vp, "checksums.sha256"))
        chain &= "version.json" in listing(vp) and not {".complete", "package.json", "checksums.sha256"} & set(listing(vp))
    t.ok("each version is one chain: .complete names package.json, which names checksums.sha256, which lists "
         "version.json and never a link above it", chain)
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
        code, text = run(REBUILD, out, "--compare", export, "--ignore-fields", "id,path,hash,modifiedAt")
        t.ok("every folder its deletion log names is an orphan, and the item it keeps agrees",
             code == 0 and sorted(report_section(text, "orphan")) == deleted
             and not report_section(text, "lost") and "4 orphan(s) on storage are safe to remove" in text, text)
        code, text = run(REBUILD, out, "--compare", before, "--ignore-fields", "id,path,hash,modifiedAt")
        t.ok("against a catalog from before the log, every folder is lost and none an orphan",
             code == 1 and sorted(report_section(text, "lost")) == sorted(deleted + [kept])
             and not report_section(text, "orphan") and "deletedItems is null" in text, text)


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

    code, text = compare(both(drop(movie["id"]), deleted(movie["id"])))
    t.ok("an item the database deleted is an orphan, and an orphan alone does not fail",
         code == 0 and section(text, "orphan") == [movie["id"]] and "1 orphan(s) on storage are safe" in text, text)
    t.ok("the orphan says when and by whom", "deleted 2026-09-30T10:00:00Z by librarian" in text, text)
    director = movie["people"][0]["personId"]
    t.ok("a person only a deleted item credits is unreferenced: kept, never an orphan, and not a failure",
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

        meta = os.path.join(tree, "movies", movie["id"][:2], movie["id"], "metadata.json")
        doc = jload(meta)
        doc.pop("asOf")
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

    code, text = compare(uncredit(series["id"]))
    t.ok("a person on storage that an item the database holds still credits is lost",
         code == 1 and section(text, "people: lost") == [lead] and "which the database holds" in text, text)

    code, text = compare(lambda e: e.update(people=[{"id": director, "name": "Ian Hubert"}]))
    t.ok("with a people list, a person it does not hold is judged by the list",
         code == 1 and section(text, "people: lost") == [lead], text)

    code, text = compare(lambda e: e["items"][0].setdefault("people", []).append(
        {"personId": "00000000-0000-4000-8000-0000000000bb", "name": "Nobody Recorded", "role": "actor"}))
    t.ok("a person the database credits who has no record on storage is a missing record",
         code == 1 and section(text, "people: missing record") == ["00000000-0000-4000-8000-0000000000bb"], text)

    code, text = compare(lambda e: e.update(people=[
        {"id": director, "name": "Ian Hubert", "biography": "Someone else's life."},
        {"id": lead, "name": "Mara Example"}]), "--text-language", "en")
    t.ok("a field the people list carries is compared", code == 1 and "people.biography: 1 difference" in text, text)
    code, text = compare(lambda e: e.update(people=[{"id": director, "name": "Ian Hubert"},
                                                    {"id": lead, "name": "Mara Example"}]))
    t.ok("and a field it does not carry is not", code == 0 and "the tree and the database agree" in text, text)

    listed = lambda e: e.update(people=[{"id": director, "name": "Ian Hubert"}, {"id": lead, "name": "Mara Example"},
                                        {"id": "00000000-0000-4000-8000-0000000000cc", "name": "Only Listed"}])
    code, text = compare(listed)
    t.ok("a person the people list holds who is not on storage is a missing record",
         code == 1 and section(text, "people: missing record") == ["00000000-0000-4000-8000-0000000000cc"], text)
    code, text = compare(listed, "--subset")
    t.ok("and in a subset, when no item on storage credits them, it is listed without failing",
         code == 0 and section(text, "people: missing record") == ["00000000-0000-4000-8000-0000000000cc"]
         and "1 of which a subset is expected to lack" in text, text)


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
        t.ok("the decisions the database held are projected, not lost",
             meta["library"]["primaryVersionId"] and meta["library"]["match"]["status"] == "matched")

        before = tree_files(src)
        code, text = run(FROM_V1, "--in", src, "--in-place")
        t.ok("a second run changes nothing", code == 0 and tree_files(src) == before, text)

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
def fake_export(root, with_original=True):
    """A catalog export, a package store and a source file, as the demo share holds them."""
    media, packages = os.path.join(root, "media"), os.path.join(root, "packages")
    iid = "11111111-2222-4333-8444-555555555555"
    name = "Example Film (2024).mkv"
    os.makedirs(media, exist_ok=True)
    original = b"an original that no probe here can read\n" * 100
    if with_original:
        with open(os.path.join(media, name), "wb") as f:
            f.write(original)
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
                      "localPath": None}],
        "artwork": [{"kind": "poster", "contentType": "image/png", "fetchedAt": "2026-07-01T09:05:00Z",
                     "base64": base64.b64encode(png).decode()}]}]}
    path = os.path.join(root, "catalog.json")
    jwrite(path, export)
    return path, media, packages, iid, len(original)


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
                         "--ignore-fields", "id,path,hash,modifiedAt,codec,resolution,bitrateKbps,"
                                            "durationMs,sizeBytes")
        t.ok("the rebuilt rows agree with the export they came from",
             code == 0 and "the tree and the database agree" in text, text)

        rows, _ = rows_of(out, "--text-language", "und")
        row = rows[iid]
        t.eq("the texts come back", (row["title"], row["tagline"], row["description"][:5]),
             ("Example Film", "Only an example.", "A fil"))
        t.eq("the chapters come back on the version's timeline", [c["startMs"] for c in row["chapters"]],
             [0, 300000])
        t.eq("the segments keep the detector that found them",
             [(s["kind"], s["source"]) for s in row["segments"]], [("credits", "blackframe")])
        t.eq("the package's subtitle is a subtitle asset again",
             [(s["language"], s["isDefault"]) for s in row["subtitleAssets"]], [("ger", True)])
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
        t.eq("the version keeps the original it was given", version["originalFiles"],
             ["Example Film (2024).mkv"])
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
    case("an image a record names is gone", False,
         lambda r: os.unlink(glob.glob(os.path.join(movie(r), "metadata", "*"))[0]),
         "listed in metadata.json but not there")
    case("an image that is not named by its own content", False, lambda r: rename_image(movie(r)),
         "not named by the hash of its own content")
    case("an image that no record names", False,
         lambda r: open(os.path.join(movie(r), "metadata", "stray.jpg"), "wb").write(b"\xff\xd8"),
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
    for section, fn in (("the pieces", test_pieces), ("the example tree", test_round_trip),
                        ("a record proves itself", test_proves_itself),
                        ("applying events", test_events), ("an orphan, a loss, a missing record", test_compare),
                        ("the catalog's own export", test_export_sample),
                        ("v1 -> v2", test_from_v1),
                        ("catalog -> v2", test_from_catalog), ("people", test_people),
                        ("upgrading a tree in place", test_upgrade),
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
