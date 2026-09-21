#!/usr/bin/env python3
"""Prove the v2 library tools do what they say.

Run it with an interpreter that can import jsonschema — the cases that run validate-library-v2.py
need it, and say so when it is missing. The tools themselves use nothing but the standard library,
and these cases run them the way a pod does: as scripts, with arguments.

  python tools/test-library-v2-tools.py

What it proves, and how each rule is shown to bite — every case makes the change that breaks the
rule and checks the tool notices:

  round trip     rebuilding library/v2/examples gives the rows the example set states: four items,
                 the movie's two versions, the episode whose package was superseded, and the series
                 with nothing to play
  events         each kind changes the rebuilt rows as the README says, and removing the event
                 changes them back: a superseded package reappears, a removed version reappears, a
                 deleted original becomes a playback asset again, and a note changes nothing
  v1 -> v2       the v1 example tree converts, the result passes validate-library-v2.py and the
                 media check, the texts and the packages survive, and a second run does nothing
  catalog -> v2  an export, a package store and source files become a tree that validates; the
                 rebuild of that tree agrees with the export it came from; two runs write the same
                 bytes; an item whose original is missing keeps its texts and loses its versions
  media check    every check it makes fails on a tree that breaks it and passes on one that does not
  the pieces     the JPEG and PNG header parsing, the qh1 fingerprint and the generated ids
"""
import base64, glob, hashlib, importlib.util, json, os, shutil, subprocess, sys, tempfile, uuid

TOOLS = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(TOOLS, "..", "library", "v2", "examples")
V1_EXAMPLES = os.path.join(TOOLS, "..", "library", "v1", "examples")
FROM_CATALOG = os.path.join(TOOLS, "library-v2-from-catalog.py")
FROM_V1 = os.path.join(TOOLS, "library-v2-from-v1.py")
REBUILD = os.path.join(TOOLS, "library-v2-rebuild.py")
MEDIA_CHECK = os.path.join(TOOLS, "library-v2-media-check.py")
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
        events = [jload(x) for x in sorted(glob.glob(os.path.join(p, "events", "*.json")))]
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


def test_events(t):
    with tempfile.TemporaryDirectory() as tmp:
        base = os.path.join(tmp, "library")
        shutil.copytree(EXAMPLES, base)
        before, doc = rows_of(base)

        # ---- package-superseded: the successor is the one to use
        ev = glob.glob(os.path.join(base, "series", "*", "*", "episodes", "*", "events",
                                    "*-package-superseded.json"))[0]
        kept = jload(ev)
        os.unlink(ev)
        after, _ = rows_of(base)
        t.ok("package-superseded keeps the superseded package out of the rows",
             len(assets(after, "packaged")) == len(assets(before, "packaged")) + 1)
        jwrite(ev, kept)
        t.eq("and putting the event back takes it out again", assets(rows_of(base)[0]), assets(before))

        # ---- version-removed: the folder is ignored even when it is still there
        item = os.path.dirname(os.path.dirname(ev))
        vp = sorted(glob.glob(os.path.join(item, "versions", "*")))[0]
        vid = os.path.basename(vp)
        jwrite(os.path.join(item, "events", "20260921T100000Z-version-removed.json"),
               {"schema": "zaentrum.library.event/2", "eventId": str(uuid.uuid4()),
                "at": "2026-09-21T10:00:00Z", "by": "test", "kind": "version-removed", "versionId": vid})
        after, doc2 = rows_of(base)
        gone = set(assets(before)) - set(assets(after))
        t.ok("version-removed drops everything the version held", bool(gone))
        t.ok("and the rebuild says the folder was ignored",
             any("was removed by an event" in n for n in doc2["notes"]))
        os.unlink(os.path.join(item, "events", "20260921T100000Z-version-removed.json"))
        t.eq("and without the event the version is part of the item again", assets(rows_of(base)[0]),
             assets(before))

        # ---- original-deleted: the original is not a playback asset any more
        deletion = glob.glob(os.path.join(base, "movies", "*", "*", "events", "*-original-deleted.json"))[0]
        kept = jload(deletion)
        os.unlink(deletion)
        after, _ = rows_of(base)
        t.ok("original-deleted keeps the deleted original out of the rows",
             len(assets(after, "primary")) == len(assets(before, "primary")) + 1)
        jwrite(deletion, kept)
        t.eq("and putting it back removes it again", assets(rows_of(base)[0]), assets(before))
        loss = [v for s in rows_of(base)[1]["storage"] for v in s["versions"] if v["permanentLoss"]]
        t.ok("the version it names is canonical and its losses are permanent",
             bool(loss) and loss[0]["canonical"] and "surround" in loss[0]["permanentLoss"])

        # ---- note: nothing
        jwrite(os.path.join(item, "events", "20260921T110000Z-note.json"),
               {"schema": "zaentrum.library.event/2", "eventId": str(uuid.uuid4()),
                "at": "2026-09-21T11:00:00Z", "by": "test", "kind": "note",
                "reason": "something no other record holds"})
        t.eq("a note changes nothing", assets(rows_of(base)[0]), assets(before))


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
        events = glob.glob(os.path.join(src, "movies", "*", "*", "events", "*-original-deleted.json"))
        t.ok("a v1 source that said it was deleted becomes an original-deleted event",
             code == 0 and len(events) == 1, text)
        if events:
            ev = jload(events[0])
            t.ok("the event accepts what the records say the package failed to carry",
                 ev["accepted"] and all(":" in x or x[0].islower() for x in ev["accepted"]))
            t.eq("and the file is named for the moment it happened", os.path.basename(events[0]),
                 "20260915T080000Z-original-deleted.json")
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

        source = jload(glob.glob(os.path.join(out, "movies", "*", "*", "sources", "*.json"))[0])
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


def rename_image(d):
    meta = jload(os.path.join(d, "metadata.json"))
    old = meta["images"][0]["file"]
    new = "a" * 64 + os.path.splitext(old)[1]
    shutil.move(os.path.join(d, "metadata", old), os.path.join(d, "metadata", new))
    meta["images"][0]["file"] = new
    jwrite(os.path.join(d, "metadata.json"), meta)


def restore_deleted(root):
    """Put back an original that an original-deleted event says is gone."""
    ev = jload(glob.glob(os.path.join(root, "movies", "*", "*", "events", "*-original-deleted.json"))[0])
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
    t = Tally()
    for section, fn in (("the pieces", test_pieces), ("the example tree", test_round_trip),
                        ("applying events", test_events), ("v1 -> v2", test_from_v1),
                        ("catalog -> v2", test_from_catalog), ("the media check", test_media_check)):
        print(f"\n--- {section}")
        try:
            fn(t)
        except Exception as e:
            t.ok(f"{section} ran to the end", False, f"{type(e).__name__}: {e}")
    print(f"\n{t.passed} passed, {t.failed} failed, {t.skipped} skipped")
    sys.exit(1 if t.failed else 0)


if __name__ == "__main__":
    main()
