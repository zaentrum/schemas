#!/usr/bin/env python3
"""Prove that validate-library.py rejects broken libraries and accepts valid variations.

Each case copies library/v1/examples into a temporary folder, changes one thing, runs the
validator, and checks the exit code and a phrase of the reason. Run from anywhere; exits
non-zero when any case behaves unexpectedly.
"""
import glob, json, os, shutil, subprocess, sys, tempfile

TOOLS = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(TOOLS, "..", "library", "v1", "examples")
VALIDATOR = os.path.join(TOOLS, "validate-library.py")


def only(pattern, root):
    hits = glob.glob(os.path.join(root, pattern))
    assert len(hits) == 1, (pattern, hits)
    return hits[0]


def movie(root):
    return only("movies/*/*", root)


def series(root):
    return only("shows/*/*", root)


def episode(root):
    return only("shows/*/*/episodes/*", root)


def edit(path, change):
    with open(path) as f:
        doc = json.load(f)
    change(doc)
    with open(path, "w") as f:
        json.dump(doc, f, indent=2)


def man(d):
    return os.path.join(d, "manifest.json")


def meta(d):
    return os.path.join(d, "metadata", "metadata.json")


def set_path(doc, path, value):
    *parents, last = path
    for p in parents:
        doc = doc[p]
    doc[last] = value


CASES = [
    # (name, expect_ok, change(root), reason phrase, extra args)
    ("examples are valid", True, lambda r: None, "OK", []),
    ("examples pass the media check", True, lambda r: None, "OK", ["--check-media"]),
    ("series carrying versions", False, lambda r: edit(man(series(r)), lambda d: d.update(versions=[])), "must not have versions", []),
    ("series carrying playback fields", False, lambda r: edit(man(series(r)), lambda d: d.update(durationMs=1)), "must not have durationMs", []),
    ("movie with a series block", False, lambda r: edit(man(movie(r)), lambda d: d.update(series={"defaultOrdering": "aired", "seasons": []})), "must not have series", []),
    ("episode naming another series", False, lambda r: edit(man(episode(r)), lambda d: set_path(d, ["episode", "seriesId"], "00000000-0000-4000-8000-000000000000")), "is not the enclosing series", []),
    ("itemId differs from its folder", False, lambda r: edit(man(movie(r)), lambda d: d.update(itemId="00000000-0000-4000-8000-000000000000")), "does not match folder", []),
    ("metadata of another type", False, lambda r: edit(meta(movie(r)), lambda d: d.update(type="episode")), "type differs", []),
    ("unknown top-level field", False, lambda r: edit(man(movie(r)), lambda d: d.update(extra=1)), "extra", []),
    ("createdAt not a timestamp", False, lambda r: edit(man(movie(r)), lambda d: d.update(createdAt="yesterday")), "$.createdAt: 'yesterday' is not a 'date-time'", []),
    ("packagedAt not a timestamp", False, lambda r: edit(man(movie(r)), lambda d: d.update(packagedAt="2026-06-01 20:46:17")), "$.packagedAt:", []),
    ("packagedAt with a lower-case t", False, lambda r: edit(man(movie(r)), lambda d: d.update(packagedAt="2026-09-01t10:00:00z")), "$.packagedAt:", []),
    ("createdAt with a trailing line break", False, lambda r: edit(man(movie(r)), lambda d: d.update(createdAt="2026-09-13T12:00:00Z\n")), "line break", []),
    ("version 3.0", False, lambda r: edit(man(movie(r)), lambda d: d.update(version=3.0)), "$.version:", []),
    ("integer beyond int64", False, lambda r: edit(man(movie(r)), lambda d: d["renditions"]["video"][0].update(bitrateBps=10 ** 20)), "bitrateBps", []),
    ("nested error names the field", False, lambda r: edit(man(movie(r)), lambda d: d["versions"][0]["package"].update(state="done")), "'done' is not one of", []),
    ("episode listing path without the trailing slash", False, lambda r: edit(man(series(r)), lambda d: d["series"]["seasons"][0]["episodes"][0].update(path=d["series"]["seasons"][0]["episodes"][0]["path"].rstrip("/"))), "episodes[0].path", []),
    ("channels as 2.0", False, lambda r: edit(man(movie(r)), lambda d: d["renditions"]["audio"][0].update(channels=2.0)), "integer", []),
    ("renditions without durationMs", False, lambda r: edit(man(movie(r)), lambda d: d.pop("durationMs")), "durationMs", []),
    ("rendition language not a language", False, lambda r: edit(man(movie(r)), lambda d: d["renditions"]["audio"][0].update(language="English (5.1)")), "does not match", []),
    ("reference id with a line break", False, lambda r: edit(man(movie(r)), lambda d: d["externalIds"].update(tmdbMovie="133701\n")), "line break", []),
    ("version path './'", False, lambda r: edit(man(movie(r)), lambda d: d["versions"][0].update(path="./")), "does not match", []),
    ("version stored under another version's id", False, lambda r: edit(man(episode(r)), lambda d: d["versions"][1].update(path="versions/00000000-0000-4000-8000-000000000000/")), "is not versions/", []),
    ("two primary versions", False, lambda r: edit(man(episode(r)), lambda d: d["versions"][1].update(primary=True)), "exactly one version must be primary", []),
    ("root package carrying a playback block", False, lambda r: edit(man(movie(r)), lambda d: d["versions"][0]["package"].update(playback={})), "$.versions[0].package", []),
    ("playback fields without a package", False, lambda r: edit(man(movie(r)), lambda d: d["versions"][0].update(package=None, lostIfOriginalDeleted=["everything"])), "no version in the item folder has a package", []),
    ("two default audio renditions", False, lambda r: edit(man(movie(r)), lambda d: d["renditions"]["audio"].append({**d["renditions"]["audio"][0], "id": "a1", "dir": "hls/a0"})), "exactly one audio rendition must be default", []),
    ("default audio decision contradicting the flag", False, lambda r: edit(man(movie(r)), lambda d: [d["renditions"]["audio"].append({**d["renditions"]["audio"][0], "id": "a1", "default": False}), d["versions"][0]["package"]["decisions"].update(defaultAudio="a1")]), "is not the rendition flagged default", []),
    ("default audio decision naming no rendition", False, lambda r: edit(man(movie(r)), lambda d: d["versions"][0]["package"]["decisions"].update(defaultAudio="a9")), "is not an audio rendition", []),
    ("lossless with losses", False, lambda r: edit(man(movie(r)), lambda d: d["versions"][0]["package"]["fidelity"].update(lossless=True)), "lossless must be true exactly when", []),
    ("package truth while the original exists", False, lambda r: edit(man(movie(r)), lambda d: d["versions"][0]["truth"].update(kind="package")), "truth.kind must be 'package' exactly when", []),
    ("movie carrying an episode id", False, lambda r: edit(man(movie(r)), lambda d: d["externalIds"].update(tmdbEpisode="1")), "carries series or episode reference ids", []),
    ("episode numbering against the listing", False, lambda r: edit(man(episode(r)), lambda d: d["episode"]["coordinates"][0].update(episode=5)), "differ from the series listing", []),
    ("episodeCode against the coordinates", False, lambda r: edit(man(episode(r)), lambda d: d.update(episodeCode="S04E01")), "episodeCode", []),
    ("series listing a missing episode", False, lambda r: shutil.rmtree(episode(r)), "does not exist", []),
    ("unlisted episode folder", False, lambda r: shutil.copytree(episode(r), os.path.join(series(r), "episodes", "11111111-1111-4111-8111-111111111111")), "not listed in the series manifest", []),
    ("image missing", False, lambda r: os.remove(os.path.join(movie(r), "metadata", "poster.jpg")), "does not exist", []),
    ("image hash wrong", False, lambda r: open(os.path.join(movie(r), "metadata", "poster.jpg"), "ab").write(b"x"), "sha256", []),
    ("image type wrong", False, lambda r: edit(meta(movie(r)), lambda d: d["images"][0].update(contentType="image/png")), "recorded as image/png", []),
    ("image dimensions wrong", False, lambda r: edit(meta(movie(r)), lambda d: d["images"][0].update(width=3840)), "width is 1", []),
    ("image listed twice", False, lambda r: edit(meta(movie(r)), lambda d: d["images"].append(dict(d["images"][0]))), "listed twice", []),
    ("unlisted file in metadata/", False, lambda r: open(os.path.join(movie(r), "metadata", "extra.jpg"), "wb").write(b"x"), "not listed in metadata.json", []),
    ("season image on a movie", False, lambda r: edit(meta(movie(r)), lambda d: d["images"][0].update(season=1)), "season-specific image on a non-series item", []),
    ("probe hash wrong", False, lambda r: open(only("movies/*/*/source/*/ffprobe.json", r), "a").write(" "), "probe sha256", []),
    ("unreferenced source folder", False, lambda r: os.makedirs(os.path.join(movie(r), "source", "22222222-2222-4222-8222-222222222222")), "not referenced by the manifest", []),
    ("stray entry in an item folder", False, lambda r: open(os.path.join(movie(r), "notes.txt"), "w").write("x"), "unexpected entry", []),
    ("item in the wrong shard", False, lambda r: shutil.move(movie(r), os.path.join(r, "movies", "ff", os.path.basename(movie(r)))) if os.makedirs(os.path.join(r, "movies", "ff")) is None else None, "is not in shard", []),
    ("rendition folder missing", False, lambda r: shutil.rmtree(os.path.join(movie(r), "hls", "v1")), "rendition dir hls/v1 missing", ["--check-media"]),
    ("complete marker without a package", False, lambda r: edit(man(movie(r)), lambda d: [d["versions"][0].update(package=None, lostIfOriginalDeleted=["everything"])] + [d.pop(k) for k in ("durationMs", "packagedAt", "packager", "renditions", "subtitles", "trickplay")]), "marker present but the package is neither complete nor stale", ["--check-media"]),
    ("item-folder marker with no version stored there", False, lambda r: (
        shutil.copytree(os.path.join(movie(r), "hls"), os.path.join(movie(r), "versions", json.load(open(man(movie(r))))["versions"][0]["id"], "hls")),
        open(os.path.join(movie(r), "versions", json.load(open(man(movie(r))))["versions"][0]["id"], ".complete"), "w").write("x"),
        edit(man(movie(r)), lambda d: [d["versions"][0].update(path=f"versions/{d['versions'][0]['id']}/"),
                                     d["versions"][0]["package"].update(playback={k: d.pop(k) for k in ("durationMs", "packagedAt", "packager", "renditions", "subtitles", "trickplay")})])),
     "marker in the item folder but no version is stored there", ["--check-media"]),
    ("stale package still on disk", True, lambda r: edit(man(movie(r)), lambda d: d["versions"][0]["package"].update(state="stale")), "OK", ["--check-media"]),
    ("silent film without audio", True, lambda r: (edit(man(movie(r)), lambda d: [d["renditions"].update(audio=[]), d["versions"][0]["package"]["decisions"].update(defaultAudio=None),
                                                                            d["versions"][0]["sources"][0].update(streams=[x for x in d["versions"][0]["sources"][0]["streams"] if x["type"] != "audio"])]),
                                                   shutil.rmtree(os.path.join(movie(r), "hls", "a0"))), "OK", ["--check-media"]),
    ("absolute ordering without season numbers", True, lambda r: (edit(man(series(r)), lambda d: d["series"].update(defaultOrdering="absolute")),
                                                                  edit(man(episode(r)), lambda d: d["episode"]["coordinates"].append({"scheme": "absolute", "season": None, "episode": 1, "episodeEnd": None}))), "OK", []),
    ("operating-system files in shared folders", True, lambda r: [open(os.path.join(x, ".DS_Store"), "w").write("x") for x in (movie(r), os.path.join(movie(r), "metadata"), os.path.join(r, "movies"), os.path.join(series(r), "episodes"))], "OK", ["--check-media"]),
    ("forced subtitle pairing names a full track", False, lambda r: edit(man(episode(r)), lambda d: d["renditions"]["audio"][0].update(forcedSubtitle="sub1")), "is not a forced or signs-and-songs track", []),
    ("forced subtitle pairing names no subtitle", False, lambda r: edit(man(episode(r)), lambda d: d["renditions"]["audio"][0].update(forcedSubtitle="sub9")), "forcedSubtitle sub9 is not a subtitle", []),
    ("forced track as the default subtitle", False, lambda r: edit(man(episode(r)), lambda d: d["versions"][0]["package"]["decisions"].update(defaultSubtitle="sub0")), "is a forced track", []),
    ("forced flag on a dialogue track", False, lambda r: edit(man(episode(r)), lambda d: d["subtitles"][1].update(forced=True)), "flagged forced but its purpose is dialogue", []),
    ("unknown subtitle purpose", False, lambda r: edit(man(episode(r)), lambda d: d["subtitles"][2].update(purpose="deaf")), "'deaf' is not one of", []),
    ("forced subtitles only, full subtitles off", True, lambda r: None, "OK", ["--check-media"]),
    ("manifest that is not an object", False, lambda r: open(man(movie(r)), "w").write("[1, 2]"), "not a JSON object", []),
    ("movie with no package", True, lambda r: (edit(man(movie(r)), lambda d: [d["versions"][0].update(package=None, lostIfOriginalDeleted=["everything: no package exists"])] + [d.pop(k) for k in ("durationMs", "packagedAt", "packager", "renditions", "subtitles", "trickplay")]),
                                                os.remove(os.path.join(movie(r), ".complete")), shutil.rmtree(os.path.join(movie(r), "hls"))), "OK", ["--check-media"]),
    ("original deleted, canonical package", True, lambda r: edit(man(movie(r)), lambda d: [
        d["versions"][0]["sources"][0].update(state="deleted"),
        d["versions"][0]["sources"][0]["file"].update(deletedAt="2026-10-01T00:00:00Z", deletionReason="space"),
        d["versions"][0]["truth"].update(kind="package"),
        d["versions"][0]["package"].update(role="canonical", chapters=d["versions"][0]["sources"][0]["chapters"])]), "OK", []),
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
