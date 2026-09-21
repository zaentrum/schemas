#!/usr/bin/env python3
"""Prove that validate-library-v2.py rejects broken libraries and accepts valid variations.

Each case copies library/v2/examples into a temporary folder, changes one thing, runs the
validator, and checks the exit code and a phrase of the reason. Every rule the validator carries
has a case here that fails without it. Run from anywhere; exits non-zero when any case behaves
unexpectedly.
"""
import glob, hashlib, json, os, shutil, subprocess, sys, tempfile

TOOLS = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(TOOLS, "..", "library", "v2", "examples")
VALIDATOR = os.path.join(TOOLS, "validate-library-v2.py")
NOWHERE = "00000000-0000-4000-8000-000000000000"


# ---------------------------------------------------------------- finding things in the fixture
def only(pattern, root):
    hits = glob.glob(os.path.join(root, pattern))
    assert len(hits) == 1, (pattern, hits)
    return hits[0]


def movie(root):
    return only("movies/*/*", root)


def series(root):
    return only("series/*/*", root)


def episode(root, number):
    for p in sorted(glob.glob(os.path.join(series(root), "episodes", "*"))):
        if json.load(open(os.path.join(p, "item.json")))["episodeNumber"] == number:
            return p
    raise AssertionError(f"no episode {number}")


def version_dirs(d):
    return sorted(glob.glob(os.path.join(d, "versions", "*")))


def version_of(d, keeps_original):
    """The version folder that does (or does not) hold its original on disk."""
    for vp in version_dirs(d):
        v = json.load(open(os.path.join(vp, "version.json")))
        here = v["originalFile"] is not None and os.path.isfile(os.path.join(vp, v["originalFile"]))
        if here == keeps_original:
            return vp
    raise AssertionError(f"no version with keeps_original={keeps_original} in {d}")


def kept(root):
    """The movie version that still has its original."""
    return version_of(movie(root), True)


def gone(root):
    """The movie version whose original was deleted: only the package is left."""
    return version_of(movie(root), False)


def canonical(root):
    """The episode version that was packaged without keeping an original."""
    return version_of(episode(root, 2), False)


def item(d):
    return os.path.join(d, "item.json")


def meta(d):
    return os.path.join(d, "metadata.json")


def ver(vp):
    return os.path.join(vp, "version.json")


def pkg(vp):
    return os.path.join(vp, "package.json")


def deletion(root):
    return only("movies/*/*/events/*-original-deleted.json", root)


def probe(root):
    return sorted(glob.glob(os.path.join(movie(root), "sources", "*", "ffprobe.json")))[0]


# ---------------------------------------------------------------- editing the fixture
def edit(path, change):
    with open(path) as f:
        doc = json.load(f)
    change(doc)
    with open(path, "w") as f:
        json.dump(doc, f, indent=2)


def write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(doc, f, indent=2)


def rechecksum(vp):
    """Rewrite checksums.sha256 and the package record after the package files changed."""
    files = []
    for d in ("hls", "subs", "trickplay", "trailers"):
        for root, dirs, names in os.walk(os.path.join(vp, d)):
            dirs.sort()
            files += [os.path.relpath(os.path.join(root, n), vp) for n in sorted(names)]
    if os.path.isfile(os.path.join(vp, ".complete")):
        files.append(".complete")
    files.sort()
    body = "".join(f"{hashlib.sha256(open(os.path.join(vp, r), 'rb').read()).hexdigest()}  {r}\n" for r in files)
    with open(os.path.join(vp, "checksums.sha256"), "w") as f:
        f.write(body)
    edit(pkg(vp), lambda d: d["checksums"].update(
        sha256="sha256:" + hashlib.sha256(body.encode()).hexdigest(), files=len(files),
        bytes=sum(os.path.getsize(os.path.join(vp, r)) for r in files)))


def note(root, at="2026-09-20T09:00:00Z", **fields):
    """Add a note event to the movie."""
    stamp = at.replace("-", "").replace(":", "")
    write_json(os.path.join(movie(root), "events", f"{stamp}-note.json"),
               {"schema": "zaentrum.library.event/2", "eventId": NOWHERE, "at": at, "by": "test",
                "kind": "note", "reason": "a fact no other record holds", **fields})


def repackage(root):
    """The re-package path: a new version folder beside the old one, and an event saying the old
    package was superseded. The old folder is not touched."""
    old = canonical(root)
    ep, new_id = episode(root, 2), "11111111-1111-4111-8111-111111111111"
    new = os.path.join(ep, "versions", new_id)
    shutil.copytree(old, new)
    edit(ver(new), lambda d: d.update(versionId=new_id))
    edit(pkg(new), lambda d: d.update(packageId="22222222-2222-4222-8222-222222222222"))
    stamp = "20260920T100000Z"
    write_json(os.path.join(ep, "events", f"{stamp}-package-superseded.json"),
               {"schema": "zaentrum.library.event/2", "eventId": NOWHERE, "at": "2026-09-20T10:00:00Z",
                "by": "packager example", "kind": "package-superseded",
                "versionId": os.path.basename(old),
                "packageId": json.load(open(pkg(old)))["packageId"],
                "reason": "re-packaged with a higher rung"})


def rename_image(root):
    """Keep the bytes and the recorded hash, but give the file a name that is not its hash."""
    d = movie(root)
    doc = json.load(open(meta(d)))
    old = doc["images"][0]["file"]
    new = "a" * 64 + ".jpg"
    shutil.move(os.path.join(d, "metadata", old), os.path.join(d, "metadata", new))
    doc["images"][0]["file"] = new
    write_json(meta(d), doc)


def silent(root):
    """An original with no audio: the package has video only."""
    vp = canonical(root)
    edit(pkg(vp), lambda d: d["renditions"].update(audio=[]))
    shutil.rmtree(os.path.join(vp, "hls", "a0"))
    rechecksum(vp)


CASES = [
    # (name, expect_ok, change(root), reason phrase, extra args)
    ("examples are valid", True, lambda r: None, "OK", []),
    ("examples pass the media check", True, lambda r: None, "OK", ["--check-checksums"]),

    # ---- records are written once: nothing here is a living document
    ("a record carrying a revision", False, lambda r: edit(item(movie(r)), lambda d: d.update(rev=2)), "'rev' was unexpected", []),
    ("a record carrying updatedAt", False, lambda r: edit(item(movie(r)), lambda d: d.update(updatedAt="2026-09-20T12:00:00Z")), "'updatedAt' was unexpected", []),
    ("unknown field in a package", False, lambda r: edit(pkg(kept(r)), lambda d: d.update(extra=1)), "extra", []),
    ("createdAt not a timestamp", False, lambda r: edit(item(movie(r)), lambda d: d.update(createdAt="yesterday")), "$.createdAt: 'yesterday' is not a 'date-time'", []),
    ("a timestamp with a lower-case t", False, lambda r: edit(pkg(kept(r)), lambda d: d.update(createdAt="2026-09-18t11:30:00z")), "$.createdAt:", []),
    ("a timestamp with a trailing line break", False, lambda r: edit(item(movie(r)), lambda d: d.update(createdAt="2026-09-18T09:00:00Z\n")), "line break", []),
    ("a reference id with a line break", False, lambda r: edit(item(movie(r)), lambda d: d["externalIds"].update(tmdbMovie="133701\n")), "line break", []),
    ("integer beyond int64", False, lambda r: edit(pkg(kept(r)), lambda d: d["renditions"]["video"][0].update(bitrateBps=10 ** 20)), "bitrateBps", []),
    ("channels as 2.0", False, lambda r: edit(pkg(kept(r)), lambda d: d["renditions"]["audio"][0].update(channels=2.0)), "integer", []),
    ("a nested error names the field", False, lambda r: edit(pkg(kept(r)), lambda d: d.update(state="done")), "'done' is not one of", []),

    # ---- identity
    ("itemId differs from its folder", False, lambda r: edit(item(movie(r)), lambda d: d.update(itemId=NOWHERE)), "does not match folder", []),
    ("item in the wrong shard", False, lambda r: shutil.move(movie(r), os.path.join(r, "movies", "ff", os.path.basename(movie(r)))) if os.makedirs(os.path.join(r, "movies", "ff")) is None else None, "is not in shard", []),
    ("a category that is not movies or series", False, lambda r: os.makedirs(os.path.join(r, "music", "aa")), "holds only movies/ and series/", []),
    ("a movie carrying episode numbering", False, lambda r: edit(item(movie(r)), lambda d: d.update(seasonNumber=1)), "must not have seasonNumber", []),
    ("an episode without its series", False, lambda r: edit(item(episode(r, 1)), lambda d: d.pop("seriesId")), "'seriesId' is a required property", []),
    ("a movie carrying an episode reference id", False, lambda r: edit(item(movie(r)), lambda d: d["externalIds"].update(tmdbEpisode="1")), "carries series or episode reference ids", []),

    # ---- the item folder holds no media
    ("media in the item folder", False, lambda r: open(os.path.join(movie(r), "Tears of Steel (2012).mkv"), "w").write("x"), "media belongs in versions/", []),
    ("a stray document in the item folder", False, lambda r: open(os.path.join(movie(r), "notes.txt"), "w").write("x"), "unexpected entry in a movie folder", []),
    ("versions in a series folder", False, lambda r: os.makedirs(os.path.join(series(r), "versions")), "unexpected entry in a series folder", []),

    # ---- every version is a folder under versions/
    ("a version that is a file", False, lambda r: open(os.path.join(movie(r), "versions", "loose.json"), "w").write("{}"), "every version is a folder under versions/", []),
    ("a version folder not named by its id", False, lambda r: shutil.move(kept(r), os.path.join(movie(r), "versions", "directors-cut")), "named by its versionId", []),
    ("a version stored under another version's id", False, lambda r: edit(ver(kept(r)), lambda d: d.update(versionId=NOWHERE)), "does not match folder", []),
    ("a stray entry in a version folder", False, lambda r: open(os.path.join(kept(r), "notes.txt"), "w").write("x"), "not a record, the package or the original", []),
    ("a version naming a source with no record", False, lambda r: edit(ver(kept(r)), lambda d: d.update(sourceIds=[NOWHERE])), "has no record under sources/", []),

    # ---- a package.json exists exactly when .complete does
    ("a package without its marker", False, lambda r: os.remove(os.path.join(kept(r), ".complete")), "package.json exists exactly when .complete does", []),
    ("a marker without its package", False, lambda r: os.remove(pkg(kept(r))), "package.json exists exactly when .complete does", []),

    # ---- what the package says about itself
    ("lossless with losses", False, lambda r: edit(pkg(kept(r)), lambda d: d["fidelity"].update(lossless=True)), "lossless must be true exactly when", []),
    ("canonical while the version keeps its original", False, lambda r: edit(pkg(kept(r)), lambda d: d.update(role="canonical")), "role must be 'canonical' exactly when", []),
    ("derived while the version keeps no original", False, lambda r: edit(pkg(canonical(r)), lambda d: d.update(role="derived")), "role must be 'canonical' exactly when", []),
    ("two default audio renditions", False, lambda r: edit(pkg(version_of(episode(r, 1), True)), lambda d: d["renditions"]["audio"][1].update(default=True)), "exactly one audio rendition must be default", []),
    ("a forced track flagged default", False, lambda r: edit(pkg(version_of(episode(r, 1), True)), lambda d: d["subtitles"][0].update(default=True)), "is a forced track flagged default", []),
    ("a forced flag on a dialogue track", False, lambda r: edit(pkg(version_of(episode(r, 1), True)), lambda d: d["subtitles"][1].update(forced=True)), "flagged forced but its purpose is dialogue", []),
    ("a purpose known without what it rests on", False, lambda r: edit(pkg(version_of(episode(r, 1), True)), lambda d: d["subtitles"][2].update(purposeFrom=None)), "needs purposeFrom exactly when", []),
    ("a forced purpose that is only assumed", False, lambda r: edit(pkg(version_of(episode(r, 1), True)), lambda d: d["subtitles"][1].update(purpose="forced")), "cannot be assumed", []),
    ("a rendition without a purpose", False, lambda r: edit(pkg(kept(r)), lambda d: [d["renditions"]["audio"][0].pop("purpose"), d["renditions"]["audio"][0].pop("purposeFrom")]), "'purpose' is a required property", []),
    ("a rendition language that is not a language", False, lambda r: edit(pkg(kept(r)), lambda d: d["renditions"]["audio"][0].update(language="English (5.1)")), "does not match", []),
    ("two renditions with one id", False, lambda r: edit(pkg(gone(r)), lambda d: d["renditions"]["video"][1].update(id="v0")), "rendition ids are not unique", []),

    # ---- the marks the version keeps
    ("chapters out of order", False, lambda r: edit(ver(gone(r)), lambda d: d["chapters"].reverse()), "chapters are not in timeline order", []),
    ("a chapter source without chapters", False, lambda r: edit(ver(kept(r)), lambda d: d.update(chaptersFrom="human")), "chaptersFrom must be set exactly when", []),
    ("the original's chapter marks not kept", False, lambda r: edit(ver(gone(r)), lambda d: d.update(chapters=[], chaptersFrom=None)), "the version keeps none", []),
    ("a detected range ending before it starts", False, lambda r: edit(ver(gone(r)), lambda d: d["segments"][0].update(endMs=0)), "ends before it starts", []),

    # ---- checksums cover the package and nothing else
    ("a checksums file edited", False, lambda r: open(os.path.join(kept(r), "checksums.sha256"), "a").write("0" * 64 + "  hls/extra\n"), "does not match its recorded sha256", []),
    ("a package file not in the checksums", False, lambda r: open(os.path.join(kept(r), "hls", "v0", "seg-0001.m4s"), "wb").write(b"x"), "hls/v0/seg-0001.m4s is not in checksums.sha256", ["--check-media"]),
    ("a checksums entry that is not a package file", False, lambda r: (os.remove(os.path.join(kept(r), "trickplay", "sprite-0000.jpg")), edit(pkg(kept(r)), lambda d: None)), "which is not a file of this package", ["--check-media"]),
    ("a package file changed after checksumming", False, lambda r: open(os.path.join(version_of(episode(r, 1), True), "subs", "1.vtt"), "ab").write(b"x"), "does not match its checksum", ["--check-checksums"]),

    # ---- the bytes the package names
    ("a rendition folder missing", False, lambda r: shutil.rmtree(os.path.join(gone(r), "hls", "v1")), "rendition dir hls/v1 missing", ["--check-media"]),
    ("a subtitle missing", False, lambda r: os.remove(os.path.join(version_of(episode(r, 1), True), "subs", "1.vtt")), "subtitle subs/1.vtt missing", ["--check-media"]),
    ("a trickplay sprite sheet missing", False, lambda r: os.remove(os.path.join(version_of(episode(r, 1), True), "trickplay", "sprite-0002.jpg")), "sprite sheet sprite-0002.jpg named by the VTT is missing", ["--check-media"]),
    ("trickplay that stops early", False, lambda r: open(os.path.join(gone(r), "trickplay", "thumbnails.vtt"), "w").write("WEBVTT\n\n00:00:00.000 --> 00:00:10.000\nsprite-0000.jpg#xywh=0,0,320,180\n"), "trickplay covers 1 of 73 thumbnails", ["--check-media"]),
    ("a hard-linked file", False, lambda r: os.link(os.path.join(kept(r), "hls", "v0", ".keep"), os.path.join(r, "..", "outside-link")), "hard links shared with another path", ["--check-media"]),

    # ---- the original, and the event that says it is gone
    ("an original missing with no event to explain it", False, lambda r: os.remove(os.path.join(kept(r), json.load(open(ver(kept(r))))["originalFile"])), "no original-deleted event says it was removed", ["--check-media"]),
    ("an original changed", False, lambda r: open(os.path.join(kept(r), json.load(open(ver(kept(r))))["originalFile"]), "ab").write(b"x"), "its source record says", ["--check-media"]),
    ("an original still there after its deletion", False, lambda r: open(os.path.join(gone(r), json.load(open(ver(gone(r))))["originalFile"]), "w").write("x"), "the original is still here", ["--check-media"]),
    ("an original that belongs to no source of this version", False, lambda r: edit(ver(kept(r)), lambda d: d.update(originalFile="something else.mkv")), "is not the file name of any source", ["--check-media"]),

    # ---- events
    ("a deletion without what it cost", False, lambda r: edit(deletion(r), lambda d: d.pop("accepted")), "'accepted' is a required property", []),
    ("a note claiming a loss", False, lambda r: note(r, accepted=["surround"]), "must not have accepted", []),
    ("an event naming a version that does not exist", False, lambda r: edit(deletion(r), lambda d: d.update(versionId=NOWHERE)), "names no version folder under versions/", []),
    ("an event naming a package that does not exist", False, lambda r: edit(deletion(r), lambda d: d.update(packageId=NOWHERE)), "names no package of any version", []),
    ("an event naming a source that does not exist", False, lambda r: edit(deletion(r), lambda d: d.update(sourceId=NOWHERE)), "names no source record under sources/", []),
    ("an event file named for another kind", False, lambda r: shutil.move(deletion(r), os.path.join(os.path.dirname(deletion(r)), "20260920T081500Z-note.json")), "file name says note but the record's kind", []),
    ("an event file named for another moment", False, lambda r: shutil.move(deletion(r), os.path.join(os.path.dirname(deletion(r)), "20261231T235959Z-original-deleted.json")), "the record happened at", []),
    ("an event file that is not a record name", False, lambda r: shutil.move(deletion(r), os.path.join(os.path.dirname(deletion(r)), "deleted.json")), "not an events/", []),
    ("a deletion of a version that never had an original", False, lambda r: write_json(
        os.path.join(episode(r, 2), "events", "20260920T110000Z-original-deleted.json"),
        {"schema": "zaentrum.library.event/2", "eventId": NOWHERE, "at": "2026-09-20T11:00:00Z", "by": "test",
         "kind": "original-deleted", "versionId": os.path.basename(canonical(r)), "reason": "space", "accepted": []}),
     "never named an original", []),

    # ---- the metadata projection and its images
    ("metadata of another item", False, lambda r: edit(meta(movie(r)), lambda d: d.update(itemId=NOWHERE)), "itemId differs from item.json", []),
    ("metadata of another type", False, lambda r: edit(meta(movie(r)), lambda d: d.update(type="episode")), "type differs from item.json", []),
    ("an image missing", False, lambda r: os.remove(os.path.join(movie(r), "metadata", json.load(open(meta(movie(r))))["images"][0]["file"])), "does not exist", []),
    ("an image whose hash is wrong", False, lambda r: open(os.path.join(movie(r), "metadata", json.load(open(meta(movie(r))))["images"][0]["file"]), "ab").write(b"x"), "sha256 does not match", []),
    ("an image not named by its hash", False, rename_image, "file name is not the hash of its own content", []),
    ("an image of another type", False, lambda r: edit(meta(movie(r)), lambda d: d["images"][0].update(contentType="image/png")), "recorded as image/png", []),
    ("an image of other dimensions", False, lambda r: edit(meta(movie(r)), lambda d: d["images"][0].update(width=3840)), "width is 1", []),
    ("an image of another size", False, lambda r: edit(meta(movie(r)), lambda d: d["images"][0].update(bytes=99)), "!= recorded 99", []),
    ("an image listed twice", False, lambda r: edit(meta(movie(r)), lambda d: d["images"].append(dict(d["images"][0]))), "listed twice", []),
    ("an unlisted file in metadata/", False, lambda r: open(os.path.join(movie(r), "metadata", "extra.jpg"), "wb").write(b"x"), "not listed in metadata.json", []),
    ("a season image on a movie", False, lambda r: edit(meta(movie(r)), lambda d: d["images"][0].update(season=1)), "season-specific image on a non-series item", []),
    ("a season image of a season nothing describes", False, lambda r: edit(meta(series(r)), lambda d: d["images"][1].update(season=3)), "which metadata.json does not describe", []),

    # ---- sources
    ("a probe whose hash is wrong", False, lambda r: open(probe(r), "a").write(" "), "probe sha256 does not match", []),
    ("a probe missing", False, lambda r: os.remove(probe(r)), "probe file missing", []),
    ("a source folder no record names", False, lambda r: os.makedirs(os.path.join(movie(r), "sources", "22222222-2222-4222-8222-222222222222")), "no sources/<sourceId>.json record names this folder", []),
    ("a stray file in a source folder", False, lambda r: open(os.path.join(os.path.dirname(probe(r)), "notes.txt"), "w").write("x"), "not the probe or a sidecar of this source", []),
    ("a source record under another name", False, lambda r: shutil.move(
        sorted(glob.glob(os.path.join(movie(r), "sources", "*.json")))[0],
        os.path.join(movie(r), "sources", NOWHERE + ".json")), "does not match the file name", []),

    # ---- the series and its episodes
    ("an episode naming another series", False, lambda r: edit(item(episode(r, 1)), lambda d: d.update(seriesId=NOWHERE)), "is not the enclosing series", []),
    ("an episode in a season the series does not list", False, lambda r: edit(item(episode(r, 1)), lambda d: d.update(seasonNumber=4, episodeCode="S04E01")), "which the series' metadata.json does not list", []),
    ("two episodes with one numbering", False, lambda r: edit(item(episode(r, 2)), lambda d: d.update(episodeNumber=1, episodeCode="S01E01")), "is already the numbering of episode", []),
    ("an episodeCode against the numbering", False, lambda r: edit(item(episode(r, 1)), lambda d: d.update(episodeCode="S04E01")), "differs from seasonNumber", []),
    ("an episode carrying another series' reference id", False, lambda r: (edit(item(series(r)), lambda d: d.update(externalIds={"tmdbTv": "1"})), edit(item(episode(r, 1)), lambda d: d.update(externalIds={"tmdbTv": "2"}))), "differs from the series'", []),
    ("a file among the episode folders", False, lambda r: open(os.path.join(series(r), "episodes", "notes.txt"), "w").write("x"), "unexpected file among the episode folders", []),

    # ---- valid variations
    ("operating-system files in shared folders", True, lambda r: [open(os.path.join(x, ".DS_Store"), "w").write("x") for x in
                                                                  (movie(r), os.path.join(movie(r), "metadata"), os.path.join(r, "movies"), kept(r), os.path.join(series(r), "episodes"))], "OK", ["--check-media"]),
    ("an item that has not been packaged yet", True, lambda r: (shutil.rmtree(os.path.join(movie(r), "versions")), shutil.rmtree(os.path.join(movie(r), "events"))), "OK", ["--check-media"]),
    ("a season listed with no episodes on storage", True, lambda r: edit(meta(series(r)), lambda d: d["series"]["seasons"].append(
        {"number": 2, "tmdbSeason": None, "name": "Season 2", "overview": None, "airDate": None, "episodeCountReference": 8})), "OK", []),
    ("a note with no subject", True, note, "OK", []),
    ("an event file named with its eventId", True, lambda r: shutil.move(deletion(r), os.path.join(
        os.path.dirname(deletion(r)), "20260920T081500Z-" + json.load(open(deletion(r)))["eventId"][:8] + "-original-deleted.json")), "OK", []),
    ("a re-package as a new version folder", True, repackage, "OK", ["--check-checksums"]),
    ("an original with no audio", True, silent, "OK", ["--check-checksums"]),
]


def main():
    failures = 0
    for name, expect_ok, change, phrase, extra in CASES:
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "library")
            shutil.copytree(EXAMPLES, root)
            change(root)
            run = subprocess.run([sys.executable, VALIDATOR, *extra, root], capture_output=True, text=True)
            out = run.stdout + run.stderr
            ok = run.returncode == 0
            good = ok == expect_ok and phrase in out and "Traceback" not in out and "checked:" in out
            print(f"{'pass' if good else 'FAIL'}  {'accepts' if expect_ok else 'rejects'}: {name}")
            if not good:
                failures += 1
                print("      " + "\n      ".join(out.strip().splitlines()[-6:]))
    print(f"{len(CASES) - failures}/{len(CASES)} cases behave as expected")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
