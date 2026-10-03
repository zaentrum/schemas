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
                 order a viewer sees it, with what never finished left out
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
                 does not say which state it reflects fails nothing
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
  sweep          a dry run finds every kind of garbage and only it, --apply removes exactly that
                 through a quarantine it checks again — putting back what is referenced by then — and
                 finishes one an interrupted run left; whatever is referenced, younger than the
                 grace, unclassifiable, or a person the log does not name as one is left alone, with
                 the reason; a deleted person goes once no item record on storage credits them, in
                 the same sweep as a deleted item that does; of an extra, only a package that never
                 finished is ever garbage
  v1 -> v2       the v1 example tree converts, the result passes validate-library-v2.py and the
                 media check, the texts and the packages survive, and a second run does nothing
  catalog -> v2  an export, a package store and source files become a tree that validates; the
                 rebuild of that tree agrees with the export it came from; two runs write the same
                 bytes; an item whose original is missing keeps its texts and loses its versions; a
                 trailer the catalog downloaded becomes an extra of its movie or series, never an
                 episode's, and its link stays a link
  credits        every field of an export's credits crosses into metadata.json — the job in the
                 source's own words, the character, the billing order, a series' episode count — in
                 the order a reader shows them, whatever order the export lists them in; an export
                 from before credits were general still works, and a role, an order or a count a
                 record cannot hold is left out with a note
  media check    every check it makes fails on a tree that breaks it and passes on one that does not
  the pieces     the JPEG and PNG header parsing, the qh1 fingerprint and the generated ids
"""
import base64, datetime, glob, hashlib, importlib.util, json, os, shutil, subprocess, sys, tempfile, time, uuid

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


def extra_of(root, kind):
    """The folder of the example extra of this kind."""
    return next(os.path.dirname(p) for p in sorted(glob.glob(os.path.join(root, "*", "*", "*", "extras", "*", "extra.json")))
                if jload(p).get("kind") == kind)


# ---------------------------------------------------------------- the pieces
def test_pieces(t):
    cat = load_tool(FROM_CATALOG)

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
        featurette, trailer = extra_of(tree, "featurette"), extra_of(tree, "trailer")
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
             [os.path.basename(featurette), early, late, os.path.basename(trailer)])
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
             and ids == {early, late, os.path.basename(trailer)}
             and sum("never finished, so it is left out" in n for n in built["notes"]) == 2, built["notes"])

        episode = glob.glob(os.path.join(tree, "series", "*", "*", "episodes", "*"))[0]
        shutil.copytree(bts, os.path.join(episode, "extras", os.path.basename(bts)))
        write_sums(os.path.join(episode, "extras", os.path.basename(bts)), ["extra.json", jload(
            os.path.join(bts, "extra.json"))["originalFiles"][0]])
        _, built = rows_of(tree)
        t.ok("an episode's extras/ folder is ignored, and the rebuild says so",
             os.path.basename(bts) not in {r["id"] for r in built["extras"]}
             and any("an episode has no extras" in n for n in built["notes"]), built["notes"])


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
                     for n in listing(vp)), extras)
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
         "extras: 3 on storage, not compared" in text, text)

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
        t.eq("which keeps the file under its own name, with the title of its link",
             (x.get("originalFiles"), x.get("title")), ([TRAILER], "Trailer"))
        t.ok("copied in, so the share keeps its own",
             os.path.isfile(os.path.join(xp, TRAILER)) and open(os.path.join(xp, TRAILER), "rb").read()
             == open(os.path.join(media, "trailers", TRAILER), "rb").read())
        kept, mc = os.path.join(xp, TRAILER), load_tool(MEDIA_CHECK)
        t.eq("and described as a source record describes its file: its size, its qh1 and its sha256",
             x.get("originals"), [{"name": TRAILER, "sizeBytes": os.path.getsize(kept) if found else None,
                                   "fixity": {"qh1": mc.qh1(kept) if found else None,
                                              "sha256": mc.sha_file(kept) if found else None,
                                              "sha256At": "2026-09-21T17:00:00Z"}}])
        t.eq("and finished by the checksums written last, over the record and the file",
             sorted(listing(xp)) if os.path.isfile(os.path.join(xp, "checksums.sha256")) else None,
             sorted(["extra.json", TRAILER]))
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
             [v["localPath"] for v in rows[iid]["trailers"]], [os.path.join(xp, TRAILER)])
        code, ctext = run(REBUILD, out, "--compare", export, "--text-language", "und",
                          "--ignore-fields", "id,path,hash,codec,resolution,bitrateKbps,durationMs,sizeBytes")
        t.ok("so it agrees with the export it came from, the link's localPath included: the same file, in its extra",
             code == 0 and "the tree and the database agree" in ctext and "localPath" not in ctext, ctext)
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
             code == 0 and os.path.isfile(os.path.join(xp, TRAILER))
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
        t.eq("titled by its file when its link has no title, its origin naming only what the link says",
             (x.get("title"), x.get("origin")), ("series", {"kind": "link", "site": "YouTube", "externalId": "series"}))
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
                       "people": shuffle(series_credits())})
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
                ("a portrait's kind", portrait_row(1, kind="poster"), "people.artwork.kind: 1 difference"),
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
        t.ok("an entry whose bytes are another's is that image, listed once, and the run says so",
             len(doc["images"]) == 2 and "profile artwork is byte-identical to the profile, listed once" in text, text)
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
                                       "base64": base64.b64encode(png(5, 5)).decode()}])
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
             "extra, episodes — it projects every item and person", code == 0 and "'items': 4" in text and "'people': 3" in text,
             text)
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


# ---------------------------------------------------------------- sweeping provable garbage
GONE = "0d0d0d0d-0000-4000-8000-000000000001"           # an item the database deleted
GONE_PERSON = "0d0d0d0d-0000-4000-8000-0000000000aa"    # a person it deleted, because no title credits them
NOTHING_WROTE = "11111111-0000-4000-8000-00000000000b"  # a version folder with part of a package, no record
RECORDED = "22222222-0000-4000-8000-00000000000b"       # a version that wrote its record, kept no original
BESIDE = "33333333-0000-4000-8000-00000000000b"         # an unfinished package beside a kept original


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
        expected = sorted([where["gone"], where["nothing wrote"], where["recorded"],
                           os.path.join(where["beside"], "hls"), os.path.join(where["beside"], "trickplay"),
                           where["dropped image"], where["dropped portrait"], where["gone person"]])
        before = stamps(root)
        code, text = run(SWEEP, root, "--export", export)
        t.eq("a dry run finds every kind of garbage, and nothing else", swept(text, root), expected)
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
        t.ok("and no quarantine behind it", not os.path.exists(os.path.join(root, "_swept")))
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
             and not os.path.exists(where["nothing wrote"]) and not os.path.exists(os.path.join(root, "_swept")), a.put_back)

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
             and not os.path.exists(os.path.join(root, "_swept")), a.put_back)

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
        t.ok("and the next --apply checks and finishes it", code == 0 and not os.path.exists(os.path.join(root, "_swept"))
             and "removed 8 target(s)" in text, text)

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
             and not os.path.exists(os.path.join(root, "_swept")), text)
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
                 and not os.path.exists(os.path.join(root, "_swept")), a.put_back)

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
             and not os.path.exists(os.path.join(root, "_swept"))
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
         "On Location in Amsterdam.mkv: does not match its checksum", ("--checksums",))
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
                        ("catalog -> v2", test_from_catalog),
                        ("a downloaded trailer -> an extra", test_from_catalog_extras),
                        ("credits: a role, the source's own words, one order", test_credits), ("people", test_people),
                        ("the people list in full", test_people_in_full),
                        ("projecting again, and nothing else", test_projections_only),
                        ("upgrading a tree in place", test_upgrade), ("sweeping garbage", test_sweep),
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
