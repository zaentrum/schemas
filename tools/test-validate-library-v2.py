#!/usr/bin/env python3
"""Prove that validate-library-v2.py rejects broken libraries and accepts valid variations.

Each case copies library/v2/examples into a temporary folder, changes one thing, runs the
validator, and checks the exit code and a phrase of the reason. Every rule the validator carries
has a case here that fails without it. Run from anywhere; exits non-zero when any case behaves
unexpectedly. Words on the command line run only the cases whose names contain one of them.
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
    """The version folder that does (or does not) hold its originals on disk."""
    for vp in version_dirs(d):
        v = json.load(open(os.path.join(vp, "version.json")))
        here = bool(v["originalFiles"]) and all(os.path.isfile(os.path.join(vp, n)) for n in v["originalFiles"])
        if here == keeps_original:
            return vp
    raise AssertionError(f"no version with keeps_original={keeps_original} in {d}")


def kept(root):
    """The movie version that still has its originals — the director's cut, split across two files."""
    return version_of(movie(root), True)


def gone(root):
    """The movie version whose original was deleted: only the package is left."""
    return version_of(movie(root), False)


def primary(d):
    """The version the projection says plays when the viewer does not choose."""
    return os.path.join(d, "versions", json.load(open(meta(d)))["library"]["primaryVersionId"])


def superseded(root):
    """The episode version whose package was replaced by one in a newer folder."""
    ev = json.load(open(supersede(root)))
    return os.path.join(episode(root, 2), "versions", ev["versionId"])


def originals(vp):
    return json.load(open(ver(vp)))["originalFiles"]


def item(d):
    return os.path.join(d, "item.json")


def meta(d):
    return os.path.join(d, "metadata.json")


def ver(vp):
    return os.path.join(vp, "version.json")


def pkg(vp):
    return os.path.join(vp, "package.json")


def deletion(root):
    return only("movies/*/*/events/*-original-deleted/event.json", root)


def removal(root):
    return only("movies/*/*/events/*-version-removed/event.json", root)


def supersede(root):
    return only("series/*/*/episodes/*/events/*-package-superseded/event.json", root)


def episode_source(root, number):
    return sorted(glob.glob(os.path.join(episode(root, number), "sources", "*", "source.json")))[0]


def movie_source(root):
    """The source record of the movie's deleted theatrical original: the one with a probe."""
    return sorted(glob.glob(os.path.join(movie(root), "sources", "*", "source.json")))[0]


def sums(folder):
    return os.path.join(folder, "checksums.sha256")


def probe(root):
    return sorted(glob.glob(os.path.join(movie(root), "sources", "*", "ffprobe.json")))[0]


def person(root, name):
    """The folder of the person the examples call name."""
    for p in sorted(glob.glob(os.path.join(root, "people", "*", "*"))):
        if json.load(open(os.path.join(p, "person.json")))["name"] == name:
            return p
    raise AssertionError(f"no person {name}")


def director(root):
    return person(root, "Ian Hubert")


def lead(root):
    return person(root, "Mara Example")


def pjson(p):
    return os.path.join(p, "person.json")


def profile(p):
    return os.path.join(p, json.load(open(pjson(p)))["images"][0]["file"])


def move_person(root, p, new_id, shard=None):
    """Put a person folder under another id, or into another shard."""
    target = os.path.join(root, "people", shard or new_id[:2], new_id)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.move(p, target)


def extra_of(root, kind):
    """The folder of the one example extra of this kind."""
    hits = [os.path.dirname(p) for p in glob.glob(os.path.join(root, "*", "*", "*", "extras", "*", "extra.json"))
            if json.load(open(p))["kind"] == kind]
    assert len(hits) == 1, (kind, hits)
    return hits[0]


def featurette(root):
    """The movie's featurette: an extra kept as its original and as a package of its own."""
    return extra_of(root, "featurette")


def trailer(root):
    """The movie's trailer, downloaded from the link its videos[] lists: kept with a package too."""
    return extra_of(root, "trailer")


def bts(root):
    """The series' behind-the-scenes extra of season 1: kept only as its original."""
    return extra_of(root, "behind-the-scenes")


def xjson(xp):
    return os.path.join(xp, "extra.json")


def x_original(xp):
    return os.path.join(xp, json.load(open(xjson(xp)))["originalFiles"][0])


def retirement(root):
    """The movie's extra-removed event: it retired the extra that kept the trailer as its original alone."""
    return only("movies/*/*/events/*-extra-removed/event.json", root)


def retired(root):
    return json.load(open(retirement(root)))["extraId"]


def resurrect_extra(root):
    """Put the removed extra's folder back, full of junk: an extra-removed event says to ignore the
    folder even when it is still on storage."""
    xp = os.path.join(movie(root), "extras", retired(root))
    os.makedirs(xp)
    with open(os.path.join(xp, "extra.json"), "w") as f:
        f.write("this is not even JSON")
    with open(os.path.join(xp, "notes.txt"), "w") as f:
        f.write("x")


def retire(item_dir, extra_id):
    """An extra-removed event in item_dir, naming extra_id when it is not None."""
    new_event(item_dir, {"schema": "zaentrum.library.event/2", "eventId": NOWHERE, "at": "2026-09-20T11:00:00Z",
                         "by": "test", "kind": "extra-removed", **({"extraId": extra_id} if extra_id else {})})


def x_decision(root, xid, decision):
    """The movie's projection deciding something about the extra xid."""
    edit(meta(movie(root)), lambda d: d["library"]["extras"].update({xid: decision}))


def package_only(root):
    """The featurette with its original gone and its package the only copy, written that way."""
    xp = featurette(root)
    os.remove(x_original(xp))
    edit(xjson(xp), lambda d: d.update(originalFiles=[], originals=[]))
    edit(pkg(xp), lambda d: d.update(role="canonical"))


def nothing_kept(root):
    """The series' extra with its original gone and its checksums written again over its record alone."""
    xp = bts(root)
    os.remove(x_original(xp))
    edit_raw(xjson(xp), lambda d: d.update(originalFiles=[], originals=[]))
    write_sums(xp, ["extra.json"])


def packaged_from(root):
    """The featurette as the platform writes an extra: only its package, and the original it was made
    from described, deleted once the package was recorded."""
    xp = featurette(root)
    original = x_original(xp)
    described = {"name": os.path.basename(original), "sizeBytes": os.path.getsize(original),
                 "fixity": json.load(open(xjson(xp)))["originals"][0]["fixity"]}
    package_only(root)
    edit(xjson(xp), lambda d: d.update(packagedFrom=[described]))


def unfinished_extra_package(root):
    """The featurette as a packager that died left it: its record, its original and part of a
    package, and no package.json, checksums or .complete."""
    for name in (".complete", "package.json", "checksums.sha256"):
        os.remove(os.path.join(featurette(root), name))


def credit_as(doc, role):
    """The credit a projection gives in role: the examples credit each role once."""
    return next(c for c in doc["credits"] if c["role"] == role)


def bare_person(root):
    """A person the database knows nothing about but a name: the smallest record there is."""
    p = director(root)
    for name in os.listdir(p):
        if name != "person.json":
            os.remove(os.path.join(p, name))
    doc = json.load(open(pjson(p)))
    write_json(pjson(p), {"schema": doc["schema"], "personId": doc["personId"], "asOf": doc["asOf"],
                          "name": doc["name"], "images": []})


# ---------------------------------------------------------------- editing the fixture
def edit(path, change):
    """Change a record and write the checksums that cover it again, so a case breaks only the rule
    it is about. edit_raw leaves them as they were, for the cases about the checksums themselves."""
    edit_raw(path, change)
    recover(path)


def edit_raw(path, change):
    with open(path) as f:
        doc = json.load(f)
    change(doc)
    with open(path, "w") as f:
        json.dump(doc, f, indent=2)


def write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(doc, f, indent=2)


def digest(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def write_sums(folder, names):
    with open(sums(folder), "w") as f:
        f.write("".join(f"{digest(os.path.join(folder, n))}  {n}\n" for n in sorted(names)))


def recover(path):
    """The checksums over a record that a case changed, written again from the bottom of the chain
    up: an item, source or event folder's over the names it lists, and for a version the checksums,
    package.json's record of them and .complete."""
    folder, name = os.path.split(path)
    if name == "version.json":
        if os.path.isfile(pkg(folder)):
            rechecksum(folder)
    elif name == "extra.json" and os.path.isfile(pkg(folder)):
        rechecksum_extra(folder)
    elif name == "package.json":
        close(folder)
    elif os.path.isfile(sums(folder)) and name not in ("metadata.json", "person.json"):
        listed = [line.split("  ", 1)[1].rstrip("\n") for line in open(sums(folder)) if "  " in line]
        write_sums(folder, listed)


def close(vp):
    """.complete, the last link: the hash of package.json as it is now."""
    if os.path.isfile(os.path.join(vp, ".complete")):
        with open(os.path.join(vp, ".complete"), "w") as f:
            f.write("sha256:" + digest(pkg(vp)) + "\n")


def rechecksum(vp):
    """Rewrite checksums.sha256 over version.json and the package files, the package record's hash of
    it, and .complete, after the package files or version.json changed."""
    files = ["version.json"]
    for d in ("hls", "subs", "trickplay", "trailers"):
        for root, dirs, names in os.walk(os.path.join(vp, d)):
            dirs.sort()
            files += [os.path.relpath(os.path.join(root, n), vp) for n in sorted(names)]
    write_sums(vp, files)
    edit_raw(pkg(vp), lambda d: d["checksums"].update(
        sha256="sha256:" + digest(sums(vp)), files=len(files),
        bytes=sum(os.path.getsize(os.path.join(vp, r)) for r in files)))
    close(vp)


def rechecksum_extra(xp):
    """Rewrite an extra's checksums over extra.json, the originals that are there and its package
    files, the package record's hash of them, and .complete, after one of them changed."""
    files = ["extra.json"] + [n for n in json.load(open(xjson(xp)))["originalFiles"] if os.path.isfile(os.path.join(xp, n))]
    for d in ("hls", "subs", "trickplay"):
        for root, dirs, names in os.walk(os.path.join(xp, d)):
            dirs.sort()
            files += [os.path.relpath(os.path.join(root, n), xp) for n in sorted(names)]
    relist(xp, files)


def new_event(item_dir, doc, name=None):
    """An event folder, events/<stamp>-<eventId8>-<kind>/, with the checksums written with it."""
    stamp = doc["at"].replace("-", "").replace(":", "")
    folder = os.path.join(item_dir, "events", name or f"{stamp}-{doc['eventId'][:8]}-{doc['kind']}")
    write_json(os.path.join(folder, "event.json"), doc)
    write_sums(folder, ["event.json"])
    return folder


def event_name(root):
    """The folder name of the movie's original-deleted event."""
    return os.path.basename(os.path.dirname(deletion(root)))


def rename_event(path, name):
    """Move an event folder to another name, its checksums with it."""
    shutil.move(os.path.dirname(path), os.path.join(os.path.dirname(os.path.dirname(path)), name))


def listing(folder):
    """The names a checksums file lists."""
    return [line.split("  ", 1)[1].rstrip("\n") for line in open(sums(folder)) if "  " in line]


def relist(vp, names):
    """A version's checksums over exactly names, with the package record and .complete closed over
    them, so only what is or is not listed can be wrong."""
    write_sums(vp, names)
    edit_raw(pkg(vp), lambda d: d["checksums"].update(
        sha256="sha256:" + digest(sums(vp)), files=len(names),
        bytes=sum(os.path.getsize(os.path.join(vp, n)) for n in names)))
    close(vp)


def old_source(root):
    """Put a source back the way it was before each source was a folder: sources/<id>.json."""
    sp = movie_source(root)
    shutil.move(sp, os.path.dirname(os.path.dirname(sp)) + "/" + os.path.basename(os.path.dirname(sp)) + ".json")


def old_event(root):
    """Put an event back the way it was before each event was a folder: events/<stamp>-<kind>.json."""
    ep = deletion(root)
    shutil.move(ep, os.path.join(os.path.dirname(os.path.dirname(ep)), "20260920T081500Z-original-deleted.json"))
    shutil.rmtree(os.path.dirname(ep))


def dropped_image(folder):
    """An image a projection listed once and a later one dropped: named by the hash of its bytes."""
    data = b"\xff\xd8\xff\xfe\x00\x0bdropped\xff\xd9"
    with open(os.path.join(folder, hashlib.sha256(data).hexdigest() + ".jpg"), "wb") as f:
        f.write(data)


def unfinished_package(root):
    """The director's cut as a packager that died left it: its originals and record, part of a
    package, and no package.json or .complete."""
    vp = kept(root)
    for name in (".complete", "package.json", "checksums.sha256"):
        os.remove(os.path.join(vp, name))


def note(root, at="2026-09-20T09:45:00Z", **fields):
    """Add a note event to the movie."""
    new_event(movie(root), {"schema": "zaentrum.library.event/2", "eventId": NOWHERE, "at": at, "by": "test",
                            "kind": "note", "reason": "a fact no other record holds", **fields})


IMAGE_FACTS = ("file", "sha256", "contentType", "sizeBytes", "width", "height")


def as_poster(root, *kinds, **fields):
    """The movie's images of kinds made the poster's bytes, as the catalog makes an episode's backdrop:
    entries of their own kind sharing the poster's one file — the files they named before left for the
    sweep — and fields set on each."""
    def change(d):
        poster = next(i for i in d["images"] if i["kind"] == "poster")
        d["images"] = [{**i, **{k: poster[k] for k in IMAGE_FACTS}, **fields} if i["kind"] in kinds else i
                       for i in d["images"]]
    edit(meta(movie(root)), change)


def season_as_series(root):
    """The series' season 1 poster made the series' own poster's bytes: one file, a poster of the series
    itself and of the season."""
    def change(d):
        own = next(i for i in d["images"] if i["kind"] == "poster" and i.get("season") is None)
        d["images"] = [dict(i, **{k: own[k] for k in IMAGE_FACTS}) if i.get("season") == 1 else i for i in d["images"]]
    edit(meta(series(root)), change)


def note_in(item_dir, **fields):
    """Add a note event to any item."""
    new_event(item_dir, {"schema": "zaentrum.library.event/2", "eventId": "0e000000-0000-4000-8000-0000000000d1",
                         "at": "2026-09-23T09:00:00Z", "by": "test", "kind": "note", "reason": "a fact", **fields})


def remove_version(item_dir, vp, at="2026-09-22T10:00:00Z", events_in=None):
    """What the retire job does once a superseded version's grace has passed: the version-removed
    event — among item_dir's events, or another item's — and the version's folder deleted."""
    new_event(events_in or item_dir, {"schema": "zaentrum.library.event/2", "eventId": "0e000000-0000-4000-8000-" + at[8:10] * 6,
                                      "at": at, "by": "test", "kind": "version-removed",
                                      "versionId": os.path.basename(vp), "reason": "superseded, after the grace"})
    shutil.rmtree(vp)


def superseded_removed(root):
    """A version replaced as the platform replaces one: the movie's version whose original was deleted,
    superseded by the director's cut, then removed after the grace and its folder deleted. The deletion
    of its original and the supersession of its package stay, as its history. Gives the removed
    version's packageId."""
    old, new = gone(root), kept(root)
    old_pkg, new_pkg = json.load(open(pkg(old)))["packageId"], json.load(open(pkg(new)))["packageId"]
    new_event(movie(root), {"schema": "zaentrum.library.event/2", "eventId": "0e000000-0000-4000-8000-0000000000c1",
                            "at": "2026-09-21T10:00:00Z", "by": "test", "kind": "package-superseded",
                            "versionId": os.path.basename(old), "packageId": old_pkg,
                            "supersededBy": {"versionId": os.path.basename(new), "packageId": new_pkg},
                            "reason": "replaced by the director's cut"})
    remove_version(movie(root), old)
    return old_pkg


def all_removed(root):
    """Every version of the movie removed — the second after superseding the first, so only the
    supersession, as its successor, names the second's package — and the projection deciding nothing."""
    superseded_removed(root)
    remove_version(movie(root), kept(root), at="2026-09-23T10:00:00Z")
    edit(meta(movie(root)), lambda d: d["library"].update(primaryVersionId=None, versionLabels={}))


def resurrect(root):
    """Put the removed version's folder back, full of junk: a version-removed event says to ignore
    the folder even when it is still on storage."""
    vp = os.path.join(movie(root), "versions", json.load(open(removal(root)))["versionId"])
    os.makedirs(vp)
    with open(os.path.join(vp, "version.json"), "w") as f:
        f.write("this is not even JSON")


def rename_image(root):
    """Keep the bytes and the recorded hash, but give the file a name that is not its hash."""
    d = movie(root)
    doc = json.load(open(meta(d)))
    old = doc["images"][0]["file"]
    new = "a" * 64 + ".jpg"
    shutil.move(os.path.join(d, "metadata", old), os.path.join(d, "metadata", new))
    doc["images"][0]["file"] = new
    write_json(meta(d), doc)


def with_surround(vp, **fields):
    """The package in vp with a 5.1 companion of its first audio track, in a folder of its own."""
    os.makedirs(os.path.join(vp, "hls", "a9"), exist_ok=True)
    open(os.path.join(vp, "hls", "a9", ".keep"), "w").close()
    edit_raw(pkg(vp), lambda d: d["renditions"].update(audioSurround=[dict(
        d["renditions"]["audio"][0], **{"id": "a9", "dir": "hls/a9", "codec": "ec-3", "channels": 6, "default": True,
                                        **fields})]))
    rechecksum(vp)


def hls_layout(vp, **fields):
    """The package in vp as the packager writes one now: a master playlist and the HLS layout that names
    it, every audio rendition named in its group, every rung with its peak, range, name and encoder."""
    with open(os.path.join(vp, "hls", "master.m3u8"), "w") as f:
        f.write("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=9192000\nv0/playlist.m3u8\n")

    def change(d):
        d["hls"] = {"master": "hls/master.m3u8", "segmentSeconds": 6, "audioGroups": ["audio", "audio-surround"],
                    "subtitleGroup": None, **fields}
        for a in d["renditions"]["audio"]:
            a.update(group="audio", name="English")
        for a in d["renditions"].get("audioSurround") or []:
            a.update(group="audio-surround", name="English 5.1")
        for v in d["renditions"]["video"]:
            v.update(peakBitrateBps=9000000, videoRange="SDR", label="source", encoder="copy")
    edit_raw(pkg(vp), change)
    rechecksum(vp)


def subtitle_hls(root, made=True):
    """Episode 1's full English subtitle segmented into an HLS rendition — whose folder is there, or not."""
    vp = version_of(episode(root, 1), True)
    if made:
        os.makedirs(os.path.join(vp, "hls", "s1"))
        open(os.path.join(vp, "hls", "s1", "playlist.m3u8"), "w").write("#EXTM3U\n")
    edit_raw(pkg(vp), lambda d: d["subtitles"][1].update(hls="hls/s1", name="English"))
    rechecksum(vp)


def from_sidecar(root, listed=True, path=None, name="subtitle-1.de.srt", original_name=None):
    """Episode 1 as the packager leaves one that came with a German subtitle file: its copy in the source
    folder, under the name the library gives it — or name — listed in source.json when listed, and a
    subtitle of the package made from it — or from path. original_name, the name the file came with, the
    entry names as a record written before 2026-10-08 did."""
    sp = os.path.dirname(episode_source(root, 1))
    sid = os.path.basename(sp)
    with open(os.path.join(sp, name), "w") as f:
        f.write("1\n00:00:01,000 --> 00:00:02,000\nHallo\n")
    if listed:
        edit_raw(os.path.join(sp, "source.json"), lambda d: d["sidecars"].append({
            "file": f"sources/{sid}/{name}", **({"originalName": original_name} if original_name else {}),
            "kind": "subtitle", "format": "srt", "language": "de",
            "forced": False, "hearingImpaired": False, "purpose": "dialogue",
            "sizeBytes": os.path.getsize(os.path.join(sp, name)), "sha256": "sha256:" + digest(os.path.join(sp, name))}))
        write_sums(sp, ["source.json", "ffprobe.json", name])
    else:
        os.remove(os.path.join(sp, name))
    vp = version_of(episode(root, 1), True)
    with open(os.path.join(vp, "subs", "3.vtt"), "w") as f:
        f.write("WEBVTT\n")
    edit_raw(pkg(vp), lambda d: d["subtitles"].append({
        "id": "sub3", "path": "subs/3.vtt", "language": "ger", "title": "Deutsch", "default": False, "forced": False,
        "format": "webvtt", "visible": True, "purpose": "dialogue", "purposeFrom": "assumed", "variant": None,
        "fromSidecar": path or f"sources/{sid}/{name}"}))
    rechecksum(vp)


def deleted_outside(root, source="its own", accepted=()):
    """Episode 2's current version, packaged by a packager that never kept the original in its folder, and
    the event of the original's deletion once its package was recorded — naming the version's source,
    another one, or none."""
    vp = primary(episode(root, 2))
    sid = json.load(open(ver(vp)))["sourceIds"][0] if source == "its own" else source
    new_event(episode(root, 2), {"schema": "zaentrum.library.event/2", "eventId": NOWHERE, "at": "2026-09-20T11:00:00Z",
                                 "by": "test", "kind": "original-deleted", "versionId": os.path.basename(vp),
                                 **({"sourceId": sid} if sid else {}), "reason": "originals are not kept",
                                 "accepted": list(accepted)})


def work_tree(root):
    """The share's root as the platform keeps it: beside movies/, series/ and people/, .work/ holds the
    arrivals, the workers' handoffs, the trash and a sweep's quarantine — nothing of the record."""
    for rel, body in (("incoming/Example Film (2024)/Example Film (2024).mkv", "an original waiting"),
                      ("inbox/" + NOWHERE + "/renditions.json", "{}"), ("staging/" + NOWHERE + "/.packaging", "{}"),
                      ("trash/20261006/" + NOWHERE + "/old.mkv", "x"), ("quarantine/20261006T120000Z/sweep.json", "{}"),
                      ("migration/2026-10-07a/units/" + NOWHERE + ".json", "{}")):
        os.makedirs(os.path.dirname(os.path.join(root, ".work", rel)), exist_ok=True)
        with open(os.path.join(root, ".work", rel), "w") as f:
            f.write(body)


def silent(root):
    """An original with no audio: the package has video only."""
    vp = primary(episode(root, 2))
    edit(pkg(vp), lambda d: d["renditions"].update(audio=[]))
    shutil.rmtree(os.path.join(vp, "hls", "a0"))
    rechecksum(vp)


def kept_nfo(root):
    """Episode 1's source keeping a copy of the .nfo that came with its original, as a source recorded
    before 2026-10-08 could."""
    sp = os.path.dirname(episode_source(root, 1))
    sid = os.path.basename(sp)
    with open(os.path.join(sp, "notes.nfo"), "w") as f:
        f.write("<episodedetails/>\n")
    edit_raw(os.path.join(sp, "source.json"), lambda d: d["sidecars"].append({
        "file": f"sources/{sid}/notes.nfo", "kind": "nfo", "format": "nfo",
        "sizeBytes": os.path.getsize(os.path.join(sp, "notes.nfo")), "sha256": "sha256:" + digest(os.path.join(sp, "notes.nfo"))}))
    write_sums(sp, ["source.json", "ffprobe.json", "notes.nfo"])


def probe_says(root, change):
    """The movie's first source with its probe changed, and its record and checksums following it, so only
    what the probe says can be wrong."""
    pp = probe(root)
    edit_raw(pp, change)
    edit_raw(os.path.join(os.path.dirname(pp), "source.json"), lambda d: d["probe"].update(sha256="sha256:" + digest(pp)))
    recover(os.path.join(os.path.dirname(pp), "source.json"))


def parts_as_they_arrived(root):
    """The director's cut keeping its two originals under the names they arrived with, its sources naming
    them so, as a version written before 2026-10-08 did."""
    vp = kept(root)
    names = originals(vp)
    for i, name in enumerate(names, 1):
        arrived = f"Example Film - Part {i}.mkv"
        shutil.move(os.path.join(vp, name), os.path.join(vp, arrived))
        for sp in glob.glob(os.path.join(movie(root), "sources", "*", "source.json")):
            if json.load(open(sp))["file"]["name"] == name and os.path.basename(os.path.dirname(sp)) in \
                    json.load(open(ver(vp)))["sourceIds"]:
                edit(sp, lambda d: d["file"].update(name=arrived))
    edit(ver(vp), lambda d: d.update(originalFiles=[f"Example Film - Part {i}.mkv" for i in range(1, len(names) + 1)]))


def extra_as_it_arrived(root):
    """The series' extra keeping its original under the name it arrived with."""
    xp = bts(root)
    arrived = "Example Show - Behind the Scenes.mkv"
    shutil.move(x_original(xp), os.path.join(xp, arrived))
    edit_raw(xjson(xp), lambda d: (d.update(originalFiles=[arrived]), d["originals"][0].update(name=arrived)))
    write_sums(xp, ["extra.json", arrived])


def taken_in(root):
    """Episode 1's version as the platform takes one in before anything packaged it: its record and its
    original alone."""
    vp = version_of(episode(root, 1), True)
    for name in ("hls", "subs", "trickplay"):
        shutil.rmtree(os.path.join(vp, name))
    for name in ("package.json", "checksums.sha256", ".complete"):
        os.remove(os.path.join(vp, name))


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
    ("a category that is not movies, series or people", False, lambda r: os.makedirs(os.path.join(r, "music", "aa")), "holds only movies/, series/ and people/", []),
    ("the work tree beside the record", True, work_tree, "OK", ["--check-checksums"]),
    ("a file whose name begins with a dot at the root", True, lambda r: open(os.path.join(r, ".seed-done"), "w").write("x"), "OK", []),
    ("the store before the library beside it, until the migration's cleanup", True, lambda r: [os.makedirs(os.path.join(r, n, "left")) for n in ("media", "packages", "extras", "incoming")], "a folder of the store before the library", []),
    ("a file named like a folder of the store before the library", False, lambda r: open(os.path.join(r, "media"), "w").write("x"), "a library root holds only movies/, series/ and people/", []),
    ("a movie carrying episode numbering", False, lambda r: edit(item(movie(r)), lambda d: d.update(seasonNumber=1)), "must not have seasonNumber", []),
    ("an episode without its series", False, lambda r: edit(item(episode(r, 1)), lambda d: d.pop("seriesId")), "'seriesId' is a required property", []),
    ("a movie carrying an episode reference id", False, lambda r: edit(item(movie(r)), lambda d: d["externalIds"].update(tmdbEpisode="1")), "carries series or episode reference ids", []),

    # ---- the item folder holds no media
    ("media in the item folder", False, lambda r: open(os.path.join(movie(r), "original.mkv"), "w").write("x"), "media belongs in versions/", []),
    ("a stray document in the item folder", False, lambda r: open(os.path.join(movie(r), "notes.txt"), "w").write("x"), "unexpected entry in a movie folder", []),
    ("versions in a series folder", False, lambda r: os.makedirs(os.path.join(series(r), "versions")), "unexpected entry in a series folder", []),

    # ---- every version is a folder under versions/
    ("a version that is a file", False, lambda r: open(os.path.join(movie(r), "versions", "loose.json"), "w").write("{}"), "every version is a folder under versions/", []),
    ("a version folder not named by its id", False, lambda r: shutil.move(kept(r), os.path.join(movie(r), "versions", "directors-cut")), "named by its versionId", []),
    ("a version stored under another version's id", False, lambda r: edit(ver(kept(r)), lambda d: d.update(versionId=NOWHERE)), "does not match folder", []),
    ("a stray entry in a version folder", False, lambda r: open(os.path.join(kept(r), "notes.txt"), "w").write("x"), "not a record, the package or an original", []),
    ("a version naming a source with no record", False, lambda r: edit(ver(kept(r)), lambda d: d.update(sourceIds=[NOWHERE])), "has no record under sources/", []),

    # ---- a package.json exists exactly when .complete does
    ("a package without its marker", False, lambda r: os.remove(os.path.join(kept(r), ".complete")), "package.json exists exactly when .complete does", []),
    ("a marker without its package", False, lambda r: os.remove(pkg(kept(r))), "package.json exists exactly when .complete does", []),

    # ---- what the package says about itself
    ("lossless with losses", False, lambda r: edit(pkg(kept(r)), lambda d: d["fidelity"].update(lossless=True)), "lossless must be true exactly when", []),
    ("canonical while the version keeps its original", False, lambda r: edit(pkg(kept(r)), lambda d: d.update(role="canonical")), "role must be 'canonical' exactly when", []),
    ("derived while the version keeps no original", False, lambda r: edit(pkg(primary(episode(r, 2))), lambda d: d.update(role="derived")), "role must be 'canonical' exactly when", []),
    ("a package recording a gate nothing records", False, lambda r: edit(pkg(kept(r)), lambda d: d.update(lostIfOriginalDeleted=[])), "'lostIfOriginalDeleted' was unexpected", []),
    ("a version recording a gate nothing records", False, lambda r: edit(ver(kept(r)), lambda d: d.update(lostIfOriginalDeleted=[])), "'lostIfOriginalDeleted' was unexpected", []),
    ("completeness the format does not measure", False, lambda r: edit(ver(gone(r)), lambda d: d["completeness"].update(status="unknown")), "'unknown' is not one of", []),
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
    ("one part of a split original missing", False, lambda r: os.remove(os.path.join(kept(r), originals(kept(r))[1])), "no original-deleted event says it was removed", ["--check-media"]),
    ("an original changed", False, lambda r: open(os.path.join(kept(r), originals(kept(r))[0]), "ab").write(b"x"), "its source record says", ["--check-media"]),
    ("an original still there after its deletion", False, lambda r: open(os.path.join(gone(r), originals(gone(r))[0]), "w").write("x"), "but it is still here", ["--check-media"]),
    ("an original that belongs to no source of this version", False, lambda r: edit(ver(kept(r)), lambda d: d.update(originalFiles=["original-9.mkv"])), "is not the file name of any source", []),

    # ---- events
    ("a deletion without what it cost", False, lambda r: edit(deletion(r), lambda d: d.pop("accepted")), "'accepted' is a required property", []),
    ("a note claiming a loss", False, lambda r: note(r, accepted=["surround"]), "must not have accepted", []),
    ("an event naming a version that does not exist", False, lambda r: edit(deletion(r), lambda d: d.update(versionId=NOWHERE)), "names no version folder under versions/", []),
    ("an event naming a package that does not exist", False, lambda r: edit(deletion(r), lambda d: d.update(packageId=NOWHERE)), "names no package of any version", []),
    ("an event naming a source that does not exist", False, lambda r: edit(deletion(r), lambda d: d.update(sourceId=NOWHERE)), "names no source record under sources/", []),
    ("an event folder named for another kind", False, lambda r: rename_event(deletion(r), event_name(r).replace("original-deleted", "note")), "folder name says note but the record's kind", []),
    ("an event folder named for another moment", False, lambda r: rename_event(deletion(r), event_name(r).replace("20260920T081500Z", "20261231T235959Z")), "the record happened at", []),
    ("an event folder that is not a record name", False, lambda r: rename_event(deletion(r), "deleted"), "not an events/", []),
    ("an event folder without its eventId", False, lambda r: rename_event(deletion(r), "20260920T081500Z-original-deleted"), "not an events/", []),
    ("an event folder naming another eventId", False, lambda r: rename_event(deletion(r), "20260920T081500Z-00000000-original-deleted"), "which does not start the eventId", []),
    ("a deletion of an original a version never kept, that does not say whose it was", False, lambda r: deleted_outside(r, source=None),
     "keeps no original in its folder, so an original-deleted event for it records one deleted outside the record, and names its source", []),
    ("an original deleted outside the record once its package was recorded", True, deleted_outside, "OK", ["--check-checksums"]),
    ("an original deleted outside the record, accepting more than the gate", False, lambda r: deleted_outside(r, accepted=["dolbyVision"]),
     "which the records do not say the package failed to carry", []),
    ("an original deleted outside the record, of a source the version was not made from", False, lambda r: deleted_outside(r, source=NOWHERE),
     "names no source record under sources/", []),

    # ---- the metadata projection and its images
    ("metadata of another item", False, lambda r: edit(meta(movie(r)), lambda d: d.update(itemId=NOWHERE)), "itemId differs from item.json", []),
    ("metadata of another type", False, lambda r: edit(meta(movie(r)), lambda d: d.update(type="episode")), "type differs from item.json", []),
    ("an image missing", False, lambda r: os.remove(os.path.join(movie(r), "metadata", json.load(open(meta(movie(r))))["images"][0]["file"])), "does not exist", []),
    ("an image whose hash is wrong", False, lambda r: open(os.path.join(movie(r), "metadata", json.load(open(meta(movie(r))))["images"][0]["file"]), "ab").write(b"x"), "sha256 does not match", []),
    ("an image not named by its hash", False, rename_image, "file name is not the hash of its own content", []),
    ("an image of another type", False, lambda r: edit(meta(movie(r)), lambda d: d["images"][0].update(contentType="image/png")), "recorded as image/png", []),
    ("an image of other dimensions", False, lambda r: edit(meta(movie(r)), lambda d: d["images"][0].update(width=3840)), "width is 1", []),
    ("an image of another size", False, lambda r: edit(meta(movie(r)), lambda d: d["images"][0].update(sizeBytes=99)), "!= recorded 99", []),
    ("an image sized in v1's words", False, lambda r: edit(meta(movie(r)), lambda d: d["images"][0].update(bytes=d["images"][0].pop("sizeBytes"))), "'bytes' was unexpected", []),
    ("one kind listing an image twice", False, lambda r: edit(meta(movie(r)), lambda d: d["images"].append(dict(d["images"][0]))), "listed twice in metadata.json as a poster", []),
    ("a backdrop that is the poster: two kinds sharing one file", True, lambda r: as_poster(r, "backdrop"), "OK", ["--check-media"]),
    ("three kinds sharing one file", True, lambda r: as_poster(r, "backdrop", "logo"), "OK", ["--check-media"]),
    ("a file shared by two kinds, primary for each", True, lambda r: as_poster(r, "backdrop", primary=True), "OK", ["--check-media"]),
    ("a kind of a shared file recording another size", False, lambda r: as_poster(r, "backdrop", sizeBytes=1), "!= recorded 1", ["--check-media"]),
    ("a kind of a shared file that names no file there", False, lambda r: edit(meta(movie(r)), lambda d: d["images"].append(
        dict(d["images"][0], kind="thumb", file="0" * 64 + ".jpg", sha256="sha256:" + "0" * 64))), "listed in metadata.json but does not exist", []),
    ("a series' poster that is its season's: one file for the series itself and for the season", True, season_as_series, "OK", ["--check-media"]),
    ("one file twice as a season's poster", False, lambda r: edit(meta(series(r)), lambda d: d["images"].append(
        dict(next(i for i in d["images"] if i.get("season") == 1), primary=False))), "listed twice in metadata.json as a poster of season 1", []),
    ("an unlisted file in metadata/", False, lambda r: open(os.path.join(movie(r), "metadata", "extra.jpg"), "wb").write(b"x"), "not listed in metadata.json", []),
    ("a season image on a movie", False, lambda r: edit(meta(movie(r)), lambda d: d["images"][0].update(season=1)), "season-specific image on a non-series item", []),
    ("a season image of a season nothing describes", False, lambda r: edit(meta(series(r)), lambda d: d["images"][1].update(season=3)), "which metadata.json does not describe", []),

    # ---- the reference ids an item has now, beside the ones item.json was created with
    ("a projection naming the reference ids the item has now", True, lambda r: edit(meta(movie(r)), lambda d: d.update(externalIds={"tmdbMovie": "133702", "imdb": "tt2285752"})), "OK", []),
    ("a projection naming a reference id the format does not know", False, lambda r: edit(meta(movie(r)), lambda d: d.update(externalIds={"tmdb": "1"})), "'tmdb' was unexpected", []),
    ("a movie's projection naming a series' reference id", False, lambda r: edit(meta(movie(r)), lambda d: d.update(externalIds={"tmdbTv": "1"})), "metadata.json: a movie carries series or episode reference ids", []),
    ("an episode's projection naming a movie's reference id", False, lambda r: edit(meta(episode(r, 1)), lambda d: d.update(externalIds={"tmdbMovie": "1"})), "metadata.json: an episode carries movie reference ids", []),

    # ---- a projection says which state of its database row it reflects, and how fresh its reference data is
    ("a projection's database state that is not a moment", False, lambda r: edit(meta(movie(r)), lambda d: d.update(databaseUpdatedAt="last week")), "$.databaseUpdatedAt", []),
    ("a projection that does not say which state it reflects", True, lambda r: edit(meta(movie(r)), lambda d: d.pop("databaseUpdatedAt")), "OK", []),
    ("a reference source the format does not know", False, lambda r: edit(meta(movie(r)), lambda d: d["sources"].update(tvdb={"fetchedAt": "2026-09-20T11:50:00Z"})), "'tvdb' was unexpected", []),
    ("a source that does not say when it was fetched", False, lambda r: edit(meta(movie(r)), lambda d: d["sources"]["tmdb"].pop("fetchedAt")), "'fetchedAt' is a required property", []),
    ("a source fetched at something that is not a moment", False, lambda r: edit(meta(movie(r)), lambda d: d["sources"]["tmdb"].update(fetchedAt="yesterday")), "$.sources.tmdb.fetchedAt", []),
    ("a source's last change that is not a date", False, lambda r: edit(meta(movie(r)), lambda d: d["sources"]["tmdb"].update(changedAt="16 September")), "$.sources.tmdb.changedAt", []),
    ("a source's freshness the format does not model", False, lambda r: edit(meta(movie(r)), lambda d: d["sources"]["tmdb"].update(etag="abc")), "'etag' was unexpected", []),
    ("a source that never reported a change", True, lambda r: edit(meta(movie(r)), lambda d: d["sources"]["tmdb"].pop("changedAt")), "OK", []),
    ("a projection never taken from a reference source", True, lambda r: edit(meta(movie(r)), lambda d: d.pop("sources")), "OK", []),

    # ---- the image a reader shows for its kind, and where an image's bytes came from
    ("two primary posters", False, lambda r: edit(meta(movie(r)), lambda d: d["images"][1].update(kind="poster", primary=True)), "2 poster images are primary", []),
    ("a primary poster and a primary backdrop", True, lambda r: edit(meta(movie(r)), lambda d: d["images"][1].update(primary=True)), "OK", []),
    ("two primary posters for one season", False, lambda r: edit(meta(series(r)), lambda d: d["images"][0].update(season=1)), "2 poster images for season 1 are primary", []),
    ("two primary posters for the series itself", False, lambda r: edit(meta(series(r)), lambda d: d["images"][1].update(season=None)), "2 poster images for the series itself are primary", []),
    ("a primary poster for the series and another for its season", True, lambda r: edit(meta(series(r)), lambda d: [i.update(primary=True) for i in d["images"]]), "OK", []),
    ("two primary portraits", False, lambda r: edit(pjson(lead(r)), lambda d: [i.update(primary=True) for i in d["images"]]), "2 profile images are primary", []),
    ("a primary that is not a yes or a no", False, lambda r: edit(meta(movie(r)), lambda d: d["images"][0].update(primary="yes")), "is not of type 'boolean'", []),
    ("a projection that marks no image primary", True, lambda r: edit(pjson(lead(r)), lambda d: [i.pop("primary", None) for i in d["images"]]), "OK", []),
    ("an image origin that does not name its source", False, lambda r: edit(meta(movie(r)), lambda d: d["images"][0]["origin"].pop("source")), "'source' is a required property", []),
    ("an image origin of a source the format does not know", False, lambda r: edit(meta(movie(r)), lambda d: d["images"][0]["origin"].update(source="scan")), "'scan' is not one of", []),
    ("an image origin the format does not model", False, lambda r: edit(pjson(lead(r)), lambda d: d["images"][1]["origin"].update(by="someone")), "'by' was unexpected", []),
    ("an image origin fetched at something that is not a moment", False, lambda r: edit(pjson(lead(r)), lambda d: d["images"][1]["origin"].update(fetchedAt="yesterday")), "origin.fetchedAt", []),
    ("an image origin with an empty ref", False, lambda r: edit(pjson(lead(r)), lambda d: d["images"][1]["origin"].update(ref="")), "should be non-empty", []),
    ("an image origin whose ref holds a line break", False, lambda r: edit(pjson(lead(r)), lambda d: d["images"][1]["origin"].update(ref="/a\n.jpg")), "ref contains a line break", []),
    ("an image origin that knows only its source", True, lambda r: edit(meta(movie(r)), lambda d: d["images"][0].update(origin={"source": "manual"})), "OK", []),
    ("an image origin in the form before 2026-10-02 (f), the source alone", True, lambda r: edit(meta(movie(r)), lambda d: [i.update(origin="legacy-catalog") for i in d["images"]]), "OK", ["--check-media"]),
    ("an image origin in that form naming a source it never had", False, lambda r: edit(meta(movie(r)), lambda d: d["images"][0].update(origin="file")), "'file' is not one of", []),

    # ---- a source record of an original gone before the library recorded it
    ("a source record of an original gone before the library recorded it", True, lambda r: edit(movie_source(r), lambda d: (d["file"].pop("fixity"), d["probe"].update(note="gone before the library was recorded"))), "OK", ["--check-checksums"]),
    ("a source record without the file's fixity that does not say why", False, lambda r: edit(movie_source(r), lambda d: d["file"].pop("fixity")), "$.probe.note: None is not of type 'string'", []),
    ("an original kept beside its package whose record has no fixity", False, lambda r: edit(os.path.join(movie(r), "sources", json.load(open(ver(kept(r))))["sourceIds"][0], "source.json"), lambda d: (d["file"].pop("fixity"), d["probe"].update(note="x"))), "which this version keeps, so its source record carries the file's fixity", []),

    # ---- sources
    ("a probe whose hash is wrong", False, lambda r: open(probe(r), "a").write(" "), "probe sha256 does not match", []),
    ("a probe missing", False, lambda r: os.remove(probe(r)), "probe file missing", []),
    ("a source folder without its record", False, lambda r: os.makedirs(os.path.join(movie(r), "sources", "22222222-2222-4222-8222-222222222222")), "a source folder without its source.json record", []),
    ("a stray file in a source folder", False, lambda r: open(os.path.join(os.path.dirname(probe(r)), "notes.txt"), "w").write("x"), "not the record, the probe, a sidecar or the checksums of this source", []),
    ("a source record under another name", False, lambda r: shutil.move(
        os.path.dirname(movie_source(r)), os.path.join(movie(r), "sources", NOWHERE)), "does not match the folder name", []),

    # ---- the series and its episodes
    ("an episode naming another series", False, lambda r: edit(item(episode(r, 1)), lambda d: d.update(seriesId=NOWHERE)), "is not the enclosing series", []),
    ("an episode in a season the series does not list", False, lambda r: edit(item(episode(r, 1)), lambda d: d.update(seasonNumber=4, episodeCode="S04E01")), "which the series' metadata.json does not list", []),
    ("two episodes with one numbering", False, lambda r: edit(item(episode(r, 2)), lambda d: d.update(episodeNumber=1, episodeCode="S01E01")), "is already the numbering of episode", []),
    ("an episodeCode against the numbering", False, lambda r: edit(item(episode(r, 1)), lambda d: d.update(episodeCode="S04E01")), "differs from seasonNumber", []),
    ("an episode carrying another series' reference id", False, lambda r: (edit(item(series(r)), lambda d: d.update(externalIds={"tmdbTv": "1"})), edit(item(episode(r, 1)), lambda d: d.update(externalIds={"tmdbTv": "2"}))), "differs from the series'", []),
    ("a file among the episode folders", False, lambda r: open(os.path.join(series(r), "episodes", "notes.txt"), "w").write("x"), "unexpected file among the episode folders", []),

    # ---- the deletion gate and the event that answers it
    ("a deletion accepting more than was lost", False, lambda r: edit(deletion(r), lambda d: d["accepted"].append("dolbyVision")), "which the records do not say the package failed to carry", []),
    ("a deletion accepting a language the package kept", False, lambda r: edit(deletion(r), lambda d: d["accepted"].append("audioLanguages:en")), "audioLanguages:en", []),
    ("an accepted loss that is not an essence property", False, lambda r: edit(deletion(r), lambda d: d["accepted"].append("audio downmixed 6->2")), "does not match", []),
    ("a deletion naming a source the version was not made from", False, lambda r: edit(deletion(r), lambda d: d.update(sourceId=json.load(open(ver(kept(r))))["sourceIds"][0])), "was not made from", []),

    # ---- a version removed from the item, and a package replaced by another
    ("a removal naming a version that is still counted", False, lambda r: edit(removal(r), lambda d: d.update(kind="note", reason="x")), "folder name says version-removed but the record's kind", []),
    ("a re-package with no successor", False, lambda r: edit(supersede(r), lambda d: d.pop("supersededBy")), "'supersededBy' is a required property", []),
    ("a successor that does not exist", False, lambda r: edit(supersede(r), lambda d: d["supersededBy"].update(packageId=NOWHERE)), "supersededBy.packageId", []),
    ("a package superseded by its own version", False, lambda r: edit(supersede(r), lambda d: d["supersededBy"].update(versionId=d["versionId"])), "supersededBy names the version it supersedes", []),
    ("a note claiming a successor", False, lambda r: note(r, supersededBy={"versionId": NOWHERE, "packageId": NOWHERE}), "must not have supersededBy", []),
    ("the projection pointing at a removed version", False, lambda r: edit(meta(movie(r)), lambda d: d["library"].update(primaryVersionId=json.load(open(removal(r)))["versionId"])), "names a version that was removed", []),
    ("a version superseded, then removed and its folder deleted", True, superseded_removed, "OK", ["--check-checksums"]),
    ("an original deleted, then its version removed and its folder deleted", True, lambda r: remove_version(movie(r), gone(r)), "OK", ["--check-checksums"]),
    ("a removed version named by its package alone", True, lambda r: note(r, packageId=superseded_removed(r)), "OK", []),
    ("every version of an item removed, the second after superseding the first", True, all_removed, "OK", ["--check-checksums"]),
    ("a version removed by another item's event", False, lambda r: remove_version(movie(r), gone(r), events_in=episode(r, 1)),
     "names no version folder under versions/, nor one a version-removed event of this item removed", []),
    ("a removed version's package named by another item", False, lambda r: note_in(episode(r, 1), packageId=superseded_removed(r)),
     "names no package of any version, nor the package of one a version-removed event of this item removed", []),

    # ---- what the database decided about this item's storage
    ("a primary version that does not exist", False, lambda r: edit(meta(movie(r)), lambda d: d["library"].update(primaryVersionId=NOWHERE)), "primaryVersionId", []),
    ("a label on a version that does not exist", False, lambda r: edit(meta(movie(r)), lambda d: d["library"]["versionLabels"].update({NOWHERE: "Extended"})), "library.versionLabels names", []),
    ("an unknown field among the decisions", False, lambda r: edit(meta(movie(r)), lambda d: d["library"].update(extra=1)), "'extra' was unexpected", []),
    ("a match status the format does not know", False, lambda r: edit(meta(movie(r)), lambda d: d["library"]["match"].update(status="maybe")), "'maybe' is not one of", []),
    ("an ordering decision on a movie", False, lambda r: edit(meta(movie(r)), lambda d: d["library"].update(defaultOrdering="aired")), "must not have defaultOrdering", []),
    ("a series listing its episodes' orderings", False, lambda r: edit(meta(series(r)), lambda d: d["library"].update(orderings={})), "'orderings' was unexpected", []),
    ("a place in an ordering on a movie", False, lambda r: edit(meta(movie(r)), lambda d: d["library"].update(numbering={})), "must not have numbering", []),

    # ---- where the episodes sit in each ordering
    ("a numbering against the item's own aired numbers", False, lambda r: edit(meta(episode(r, 1)), lambda d: d["library"]["numbering"]["aired"].update(episode=5)), "but item.json was created with", []),
    ("a numbering in an ordering the format does not know", False, lambda r: edit(meta(episode(r, 1)), lambda d: d["library"]["numbering"].update(broadcast={"season": 1, "episode": 1, "episodeEnd": None})), "$.library.numbering: 'broadcast' is not one of", []),
    ("two episodes in one place in one ordering", False, lambda r: edit(meta(episode(r, 2)), lambda d: d["library"]["numbering"]["dvd"].update(episode=2)), "puts this episode where", []),

    # ---- how the file itself was numbered, and where the item came from
    ("a naming scheme the format does not know", False, lambda r: edit(episode_source(r, 2), lambda d: d["naming"].update(scheme="broadcast")), "'broadcast' is not one of", []),
    ("a naming field the format does not model", False, lambda r: edit(episode_source(r, 2), lambda d: d["naming"].update(title="Crosswind")), "'title' was unexpected", []),
    ("provenance the format does not model", False, lambda r: edit(item(movie(r)), lambda d: d["provenance"].update(legacyPath="/old/library")), "'legacyPath' was unexpected", []),
    ("a legacy id that is not an id", False, lambda r: edit(item(movie(r)), lambda d: d["provenance"].update(legacyItemId="movie-12")), "does not match", []),

    # ---- every write-once folder proves its records: the item's identity
    ("an item record changed after it was written", False, lambda r: edit_raw(item(movie(r)), lambda d: d.update(title="Another Title")), "does not match the checksum checksums.sha256 recorded for it", []),
    ("an item folder without its checksums", False, lambda r: os.remove(sums(movie(r))), "missing: item.json is covered by a checksums file", []),
    ("a series folder without its checksums", False, lambda r: os.remove(sums(series(r))), "missing: item.json is covered by a checksums file", []),
    ("an episode folder without its checksums", False, lambda r: os.remove(sums(episode(r, 1))), "missing: item.json is covered by a checksums file", []),
    ("an item's checksums listing more than item.json", False, lambda r: write_sums(movie(r), ["item.json", "metadata.json"]), "lists metadata.json, which is not item.json", []),
    ("an item's checksums listing nothing", False, lambda r: open(sums(movie(r)), "w").write(""), "does not list item.json", []),
    ("an item's checksums that are not a sha256sum file", False, lambda r: open(sums(movie(r)), "a").write("item.json is fine\n"), "not a sha256sum line", []),

    # ---- a source: its record, probe and sidecars, one folder
    ("a source record changed after it was written", False, lambda r: edit_raw(movie_source(r), lambda d: d.update(takenBy="someone else")), "does not match the checksum checksums.sha256 recorded for it", []),
    ("a probe changed after it was written", False, lambda r: (edit_raw(probe(r), lambda d: d.update(note="x")), edit(movie_source(r), lambda d: d["probe"].update(sha256="sha256:" + digest(probe(r)))), write_sums(os.path.dirname(probe(r)), ["source.json"]), open(sums(os.path.dirname(probe(r))), "a").write(f"{'0' * 64}  ffprobe.json\n")), "ffprobe.json: does not match the checksum", []),
    ("a source's checksums without its probe", False, lambda r: write_sums(os.path.dirname(movie_source(r)), ["source.json"]), "does not list ffprobe.json", []),
    ("a source folder without its checksums", False, lambda r: os.remove(sums(os.path.dirname(movie_source(r)))), "missing: source.json, its probe or one of its sidecars is covered", []),
    ("a source in the layout before each source was a folder", False, old_source, "a source record in the layout before 2026-10-02 (b)", []),

    # ---- an event: one folder per fact
    ("an event changed after it was written", False, lambda r: edit_raw(deletion(r), lambda d: d.update(reason="changed afterwards")), "does not match the checksum checksums.sha256 recorded for it", []),
    ("an event folder without its checksums", False, lambda r: os.remove(sums(os.path.dirname(deletion(r)))), "missing: event.json is covered by a checksums file", []),
    ("a stray file in an event folder", False, lambda r: open(os.path.join(os.path.dirname(deletion(r)), "notes.txt"), "w").write("x"), "not event.json or the checksums written with it", []),
    ("an event in the layout before each event was a folder", False, old_event, "an event record in the layout before 2026-10-02 (b)", []),

    # ---- a version: .complete -> package.json -> checksums.sha256 -> version.json and the package
    ("a version record changed after its package completed", False, lambda r: edit_raw(ver(kept(r)), lambda d: d.update(runtimeMs=1)), "version.json: does not match the checksum checksums.sha256 recorded for it", []),
    ("a version's checksums that do not list version.json", False, lambda r: relist(kept(r), [n for n in listing(kept(r)) if n != "version.json"]), "does not list version.json", []),
    ("a version's checksums listing the marker above them", False, lambda r: relist(kept(r), listing(kept(r)) + [".complete"]), "lists .complete, which is written after it", []),
    ("a version's checksums listing the package record", False, lambda r: relist(kept(r), listing(kept(r)) + ["package.json"]), "lists package.json, which holds the hash of this file", []),
    ("a package record changed after it completed", False, lambda r: edit_raw(pkg(kept(r)), lambda d: d.update(packagedBy="someone else")), "does not name this package.json", []),
    ("a marker naming another package record", False, lambda r: open(os.path.join(kept(r), ".complete"), "w").write("sha256:" + "0" * 64 + "\n"), "does not name this package.json", []),
    ("a marker that is not a hash", False, lambda r: open(os.path.join(kept(r), ".complete"), "w").write("packager example 2026-09-18\n"), "not sha256:<hex> of package.json", []),
    ("a checksums total that leaves out version.json", False, lambda r: edit(pkg(kept(r)), lambda d: d["checksums"].update(bytes=d["checksums"]["bytes"] - os.path.getsize(ver(kept(r))))), "the record says", ["--check-media"]),
    ("a marker without a trailing line break", True, lambda r: open(os.path.join(kept(r), ".complete"), "w").write("sha256:" + digest(pkg(kept(r)))), "OK", ["--check-checksums"]),

    # ---- people: a category of their own, a projection like metadata.json
    ("a person folder not named by its id", False, lambda r: move_person(r, director(r), "a1" + NOWHERE[2:]), "does not match folder", []),
    ("a person in the wrong shard", False, lambda r: move_person(r, director(r), os.path.basename(director(r)), shard="ff"), "person folder is not in shard", []),
    ("a person folder without its record", False, lambda r: os.remove(pjson(director(r))), "person.json: missing", []),
    ("a person record listing credits", False, lambda r: edit(pjson(director(r)), lambda d: d.update(credits=[])), "'credits' was unexpected", []),
    ("a stray file in a person folder", False, lambda r: open(os.path.join(director(r), "notes.txt"), "w").write("x"), "not person.json and not an image person.json lists", []),
    ("a portrait whose hash is wrong", False, lambda r: open(profile(director(r)), "ab").write(b"x"), "sha256 does not match person.json", []),
    ("a portrait listed but missing", False, lambda r: os.remove(profile(director(r))), "listed in person.json but does not exist", []),
    ("a person image of an item's kind", False, lambda r: edit(pjson(director(r)), lambda d: d["images"][0].update(kind="poster")), "'poster' is not one of ['profile']", []),
    ("an item image of a person's kind", False, lambda r: edit(meta(movie(r)), lambda d: d["images"][0].update(kind="profile")), "'profile' is not one of", []),
    ("a person image belonging to a season", False, lambda r: edit(pjson(director(r)), lambda d: d["images"][0].update(season=1)), "must not have season here", []),
    ("a death before the birth", False, lambda r: edit(pjson(lead(r)), lambda d: d.update(deathDate="1984-12-31")), "is before birthDate", []),
    ("a biography in something that is not a language", False, lambda r: edit(pjson(lead(r)), lambda d: d["biography"].update(English="x")), "$.biography", []),
    ("a title id as a person's imdb id", False, lambda r: edit(pjson(lead(r)), lambda d: d["externalIds"].update(imdb="tt2285752")), "does not match", []),
    ("a person name that is empty", False, lambda r: edit(pjson(lead(r)), lambda d: d.update(name="")), "$.name", []),
    ("a department that is not a name", False, lambda r: edit(pjson(lead(r)), lambda d: d.update(knownForDepartment=["Acting"])), "$.knownForDepartment", []),
    ("a person known for nothing the reference database says", True, lambda r: edit(pjson(lead(r)), lambda d: d.update(knownForDepartment=None)), "OK", []),
    ("a person's database state that is not a moment", False, lambda r: edit(pjson(lead(r)), lambda d: d.update(databaseUpdatedAt="2026-09-20")), "$.databaseUpdatedAt", []),
    ("a person's reference source the format does not know", False, lambda r: edit(pjson(lead(r)), lambda d: d["sources"].update(imdb={"fetchedAt": "2026-09-20T11:50:00Z"})), "'imdb' was unexpected", []),
    ("a person's source fetched at something that is not a moment", False, lambda r: edit(pjson(lead(r)), lambda d: d["sources"]["tmdb"].update(fetchedAt="2026-09-20 11:50")), "$.sources.tmdb.fetchedAt", []),
    ("a credit to a person with no record is a note", True, lambda r: shutil.rmtree(director(r)), "who has no people/", []),
    ("a person nothing is known about but a name", True, bare_person, "OK", ["--check-media"]),
    ("a death known only to the year of the birth", True, lambda r: edit(pjson(lead(r)), lambda d: d.update(deathDate="1985")), "OK", []),

    # ---- a credit: one person in one role, a token of an open vocabulary, and the source's own words for the job
    ("a creator credited in the source's own words", True, lambda r: edit(meta(series(r)), lambda d: credit_as(d, "creator").update(job="Creator")), "OK", []),
    ("a writer's jobs joined into one credit", True, lambda r: edit(meta(series(r)), lambda d: credit_as(d, "writer").update(job="Screenplay, Story, Teleplay")), "OK", []),
    ("a role the well-known ones do not name", True, lambda r: edit(meta(series(r)), lambda d: credit_as(d, "creator").update(role="production-designer", job="Production Design")), "OK", []),
    ("a series credit counting no episodes", True, lambda r: edit(meta(series(r)), lambda d: credit_as(d, "writer").update(episodeCount=0)), "OK", []),
    ("a credit from before credits had a job or an episode count", True, lambda r: edit(meta(series(r)), lambda d: [[c.pop(k) for k in ("job", "episodeCount")] for c in d["credits"]]), "OK", []),
    ("a role in the source's words rather than a token", False, lambda r: edit(meta(movie(r)), lambda d: d["credits"][0].update(role="Director")), "$.credits[0].role: 'Director' does not match", []),
    ("a role written as words", False, lambda r: edit(meta(movie(r)), lambda d: d["credits"][0].update(role="production designer")), "$.credits[0].role: 'production designer' does not match", []),
    ("a role longer than the forty characters the catalog keeps", False, lambda r: edit(meta(movie(r)), lambda d: d["credits"][0].update(role="supervising-art-director-and-production-designer")), "$.credits[0].role: 'supervising-art-director-and-production-designer' does not match", []),
    ("a credit naming no role", False, lambda r: edit(meta(movie(r)), lambda d: d["credits"][0].update(role="")), "$.credits[0].role: '' does not match", []),
    ("several jobs listed rather than joined", False, lambda r: edit(meta(series(r)), lambda d: credit_as(d, "writer").update(job=["Story", "Teleplay"])), "is not of type 'string', 'null'", []),
    ("a negative episode count", False, lambda r: edit(meta(series(r)), lambda d: credit_as(d, "actor").update(episodeCount=-1)), "$.credits[0].episodeCount: -1 is less than the minimum of 0", []),
    ("an episode count that is not a whole number", False, lambda r: edit(meta(series(r)), lambda d: credit_as(d, "actor").update(episodeCount=2.5)), "$.credits[0].episodeCount: 2.5 is not of type 'integer', 'null'", []),
    ("a billing order that is not a whole number", False, lambda r: edit(meta(series(r)), lambda d: credit_as(d, "actor").update(order=1.5)), "$.credits[0].order: 1.5 is not of type 'integer', 'null'", []),

    # ---- garbage a writer left: a note for the sweep, not a broken record
    ("an image a later projection dropped is a note", True, lambda r: dropped_image(os.path.join(movie(r), "metadata")), "metadata.json no longer lists; library-v2-sweep.py", []),
    ("a portrait a later projection dropped is a note", True, lambda r: dropped_image(lead(r)), "person.json no longer lists; library-v2-sweep.py", ["--check-media"]),
    ("a file beside a projection that claims a hash it does not have", False, lambda r: open(os.path.join(movie(r), "metadata", "a" * 64 + ".jpg"), "wb").write(b"\xff\xd8 other bytes"), "not named by the hash of its own content", []),
    ("a package that never finished is a note", True, unfinished_package, "a package that never finished", []),
    ("a quarantine an interrupted sweep left, from before the work tree", False, lambda r: os.makedirs(os.path.join(r, "_swept", "20261002T120000Z")), "the quarantine of a library-v2-sweep.py --apply from before 2026-10-06 that did not finish", []),

    # ---- bonus material: inside its movie or series, never an item of its own and never an episode's
    ("an extra of a kind the format does not know", False, lambda r: edit(xjson(featurette(r)), lambda d: d.update(kind="bonus")), "'bonus' is not one of", []),
    ("an extra without a title", False, lambda r: edit(xjson(bts(r)), lambda d: d.pop("title")), "'title' is a required property", []),
    ("an extra record carrying updatedAt", False, lambda r: edit(xjson(bts(r)), lambda d: d.update(updatedAt="2026-09-20T12:00:00Z")), "'updatedAt' was unexpected", []),
    ("what the probe found, without the probe", False, lambda r: edit(xjson(bts(r)), lambda d: d.pop("probe")), "'probe' is a dependency of", []),
    ("a probe without what it found", False, lambda r: edit(xjson(bts(r)), lambda d: d.pop("essence")), "'essence' is a dependency of 'probe'", []),
    ("an extra's title in something that is not a language", False, lambda r: edit(xjson(featurette(r)), lambda d: d["localizedTitles"].update(Dutch="x")), "$.localizedTitles", []),
    ("an original named like a record of its folder", False, lambda r: edit(xjson(bts(r)), lambda d: d.update(originalFiles=["checksums.sha256"])), "should not be valid under", []),

    # ---- where an extra's file came from: a link it was downloaded from
    ("an extra's origin of a kind the format does not know", False, lambda r: edit(xjson(trailer(r)), lambda d: d["origin"].update(kind="scan")), "'scan' is not one of", []),
    ("an origin that does not say what kind it is", False, lambda r: edit(xjson(trailer(r)), lambda d: d["origin"].pop("kind")), "'kind' is a required property", []),
    ("an origin the format does not model", False, lambda r: edit(xjson(trailer(r)), lambda d: d["origin"].update(seenBy="someone")), "'seenBy' was unexpected", []),
    ("an origin fetched at something that is not a moment", False, lambda r: edit(xjson(trailer(r)), lambda d: d["origin"].update(fetchedAt="yesterday")), "$.origin.fetchedAt", []),
    ("an origin that knows only its kind", True, lambda r: edit(xjson(trailer(r)), lambda d: d.update(origin={"kind": "link"})), "OK", ["--check-checksums"]),

    # ---- an extra describes each original as a source record describes its file: size and fixity
    ("an extra without its originals' fixity", False, lambda r: edit(xjson(bts(r)), lambda d: d.pop("originals")), "'originals' is a required property", []),
    ("an original described without its fixity", False, lambda r: edit(xjson(bts(r)), lambda d: d["originals"][0].pop("fixity")), "'fixity' is a required property", []),
    ("originals describing another file than originalFiles names", False, lambda r: edit(xjson(bts(r)), lambda d: d["originals"][0].update(name="original-9.mkv")), "originals describes other files than originalFiles names", []),
    ("an extra's original of another size than recorded", False, lambda r: edit(xjson(bts(r)), lambda d: d["originals"][0].update(sizeBytes=d["originals"][0]["sizeBytes"] + 1)), "bytes, extra.json says", ["--check-media"]),
    ("an extra's original that does not match its recorded qh1", False, lambda r: edit(xjson(bts(r)), lambda d: d["originals"][0]["fixity"].update(qh1="sha256:" + "0" * 64)), "does not match the qh1 fixity extra.json records", ["--check-media"]),
    ("an extra's original that does not match its recorded sha256", False, lambda r: edit(xjson(featurette(r)), lambda d: d["originals"][0]["fixity"].update(sha256="sha256:" + "0" * 64)), "does not match the sha256 extra.json records", ["--check-checksums"]),
    ("an original whose sha256 nobody computed", True, lambda r: edit(xjson(featurette(r)), lambda d: [d["originals"][0]["fixity"].pop(k) for k in ("sha256", "sha256At")]), "OK", ["--check-checksums"]),
    ("extras in an episode", False, lambda r: os.makedirs(os.path.join(episode(r, 1), "extras")), "an episode has no extras", []),
    ("an extra that is a file", False, lambda r: open(os.path.join(series(r), "extras", "loose.json"), "w").write("{}"), "every extra is a folder under extras/", []),
    ("an extra folder not named by its id", False, lambda r: shutil.move(bts(r), os.path.join(series(r), "extras", "behind-the-scenes")), "an extra folder is named by its extraId", []),
    ("an extra stored under another extra's id", False, lambda r: edit(xjson(bts(r)), lambda d: d.update(extraId=NOWHERE)), "does not match folder", []),
    ("an extra folder without its record", False, lambda r: os.remove(xjson(bts(r))), "an extra folder without its extra.json record", []),
    ("a stray entry in an extra folder", False, lambda r: open(os.path.join(bts(r), "notes.txt"), "w").write("x"), "not a record, the package or an original this extra names", []),

    # ---- an extra proves itself: its checksums list extra.json and every file beside it, the originals included
    ("an extra record changed after it was written", False, lambda r: edit_raw(xjson(bts(r)), lambda d: d.update(title="Another Title")), "extra.json: does not match the checksum checksums.sha256 recorded for it", []),
    ("an extra's checksums leaving out its original", False, lambda r: write_sums(bts(r), ["extra.json"]), "does not list original.mkv", []),
    ("an extra's original missing", False, lambda r: os.remove(x_original(bts(r))), "an original extra.json names is not here", []),
    ("an extra's original changed after it was written", False, lambda r: open(x_original(featurette(r)), "ab").write(b"x"), "original.mkv: does not match its checksum", ["--check-checksums"]),
    ("a package file of an extra its checksums do not list", False, lambda r: open(os.path.join(featurette(r), "hls", "v0", "seg-0001.m4s"), "wb").write(b"x"), "does not list hls/v0/seg-0001.m4s", []),
    ("an extra's checksums listing a file that is not there", False, lambda r: os.remove(os.path.join(featurette(r), "subs", "0.vtt")), "lists subs/0.vtt, which is not a file of this extra", []),
    ("an extra's checksums listing its package record", False, lambda r: relist(featurette(r), listing(featurette(r)) + ["package.json"]), "lists package.json, which holds the hash of this file", []),
    ("an extra's checksums listing the marker above them", False, lambda r: relist(featurette(r), listing(featurette(r)) + [".complete"]), "lists .complete, which is written after it", []),
    ("an extra's package record changed after it completed", False, lambda r: edit_raw(pkg(featurette(r)), lambda d: d.update(packagedBy="someone else")), "does not name this package.json", []),
    ("an extra's marker that is not a hash", False, lambda r: open(os.path.join(featurette(r), ".complete"), "w").write("packager example\n"), "the head of the chain that covers this extra", []),
    ("an extra's package without its marker", False, lambda r: os.remove(os.path.join(featurette(r), ".complete")), "package.json exists exactly when .complete does", []),
    ("an extra's marker without its package", False, lambda r: os.remove(pkg(featurette(r))), "package.json exists exactly when .complete does", []),
    ("an extra with neither an original nor a package", False, nothing_kept, "holds neither an original nor a package", []),
    ("an extra that never finished is a note", True, lambda r: os.remove(sums(bts(r))), "an extra that never finished", []),
    ("an extra whose package never finished is a note", True, unfinished_extra_package, "an extra whose package never finished", []),

    # ---- an extra's package is a version's, and carries no trailers
    ("an extra's package canonical while it keeps its original", False, lambda r: edit(pkg(featurette(r)), lambda d: d.update(role="canonical")), "role must be 'canonical' exactly when the extra keeps no original", []),
    ("an extra's package listing trailers", False, lambda r: edit(pkg(featurette(r)), lambda d: d.update(trailers=[{"id": "t0", "source": "packager", "durationMs": 1000, "manifestPath": "hls/master.m3u8"}])), "an extra's package lists no trailers", []),
    ("an extra's rendition folder missing", False, lambda r: (shutil.rmtree(os.path.join(featurette(r), "hls", "a0")), rechecksum_extra(featurette(r))), "rendition dir hls/a0 missing", ["--check-media"]),
    ("an extra's checksums total that is not its files'", False, lambda r: edit(pkg(featurette(r)), lambda d: d["checksums"].update(bytes=d["checksums"]["bytes"] - 1)), "the record says", ["--check-media"]),

    # ---- a season is a series' to give
    ("a season on a movie's extra", False, lambda r: edit(xjson(featurette(r)), lambda d: d.update(seasonNumber=1)), "only a series' extra belongs to a season", []),
    ("an extra in a season the series does not list", False, lambda r: edit(xjson(bts(r)), lambda d: d.update(seasonNumber=3)), "season 3, which the series' metadata.json does not list", []),

    # ---- how a viewer sees the extras is the projection's
    ("a decision about an extra that does not exist", False, lambda r: x_decision(r, NOWHERE, {"hidden": True}), "library.extras names", []),
    ("a decision about another item's extra", False, lambda r: x_decision(r, os.path.basename(bts(r)), {"order": 2}), "library.extras names", []),
    ("a decision about an extra the format does not model", False, lambda r: x_decision(r, os.path.basename(featurette(r)), {"pinned": True}), "'pinned' was unexpected", []),
    ("a decision about an extra that decides nothing", False, lambda r: x_decision(r, os.path.basename(featurette(r)), {}), "should be non-empty", []),
    ("decisions about extras on an episode", False, lambda r: edit(meta(episode(r, 1)), lambda d: d["library"].update(extras={NOWHERE: {"hidden": True}})), "must not have extras", []),

    # ---- an extra retired by an event: its folder may be gone, and one still there is ignored
    ("an extra-removed event without the extra it removes", False, lambda r: retire(movie(r), None), "'extraId' is a required property", []),
    ("an extraId on an event about an original", False, lambda r: edit(deletion(r), lambda d: d.update(extraId=NOWHERE)), "must not have extraId", []),
    ("an extra-removed event in an episode", False, lambda r: retire(episode(r, 1), NOWHERE), "an episode has no extras, so none can be removed", []),
    ("a removed extra's folder still on storage is ignored, and noted for the sweep", True, resurrect_extra, "the folder of an extra an extra-removed event removed", ["--check-checksums"]),
    ("the projection deciding about a removed extra", False, lambda r: x_decision(r, retired(r), {"hidden": True}), "which is a removed extra", []),
    ("a note about an extra that is not there", False, lambda r: note(r, extraId=NOWHERE), f"extraId {NOWHERE} names no extra folder under extras/", []),
    ("a note about an extra that was removed", False, lambda r: note(r, extraId=retired(r)), "and a removed extra is not one", []),
    ("a note about an extra", True, lambda r: note(r, extraId=os.path.basename(featurette(r))), "OK", []),

    # ---- what an extra may also be
    ("an extra kept only as its package", True, package_only, "OK", ["--check-checksums"]),
    ("an extra kept only as its package, with the original it was made from", True, packaged_from, "OK", ["--check-checksums"]),
    ("what a package was made from, on an extra that keeps its original", False, lambda r: edit(xjson(featurette(r)), lambda d: d.update(packagedFrom=list(d["originals"]))), "and this one keeps its originals: originals describes them", []),
    ("what a package was made from, on an extra with no package", False, lambda r: (nothing_kept(r), edit(xjson(bts(r)), lambda d: d.update(packagedFrom=[{"name": "original.mkv", "sizeBytes": 1, "fixity": {"qh1": "sha256:" + "0" * 64}}]))), "packagedFrom says what the package was made from, but this extra has no package", []),
    ("what a package was made from, without its fixity", False, lambda r: (packaged_from(r), edit(xjson(featurette(r)), lambda d: d["packagedFrom"][0].pop("fixity"))), "'fixity' is a required property", []),
    ("what a package was made from, named with a folder", False, lambda r: (packaged_from(r), edit(xjson(featurette(r)), lambda d: d["packagedFrom"][0].update(name="extras/a.mkv"))), "$.packagedFrom[0].name: 'extras/a.mkv' does not match", []),
    ("an empty list of what a package was made from", False, lambda r: (package_only(r), edit(xjson(featurette(r)), lambda d: d.update(packagedFrom=[]))), "$.packagedFrom: [] should be non-empty", []),
    ("an extra nothing probed", True, lambda r: edit(xjson(featurette(r)), lambda d: [d.pop(k) for k in ("container", "streams", "fidelity", "essence", "probe")]), "OK", ["--check-checksums"]),
    ("a series' extra of no particular season", True, lambda r: edit(xjson(bts(r)), lambda d: d.pop("seasonNumber")), "OK", []),
    ("an item whose database decided nothing about its extras", True, lambda r: edit(meta(movie(r)), lambda d: d["library"].pop("extras")), "OK", []),
    ("operating-system files in extra folders", True, lambda r: [open(os.path.join(x, ".DS_Store"), "w").write("x") for x in
                                                               (os.path.join(movie(r), "extras"), featurette(r), os.path.join(featurette(r), "hls"), bts(r))], "OK", ["--check-checksums"]),

    # ---- the package as the packager writes it now: 5.1 companions, its HLS layout, subtitles made from sidecars
    ("a package with a 5.1 companion and its HLS layout", True, lambda r: (with_surround(kept(r)), hls_layout(kept(r))), "OK", ["--check-checksums"]),
    ("a 5.1 companion whose folder is missing", False, lambda r: (with_surround(kept(r)), shutil.rmtree(os.path.join(kept(r), "hls", "a9")), rechecksum(kept(r))), "rendition dir hls/a9 missing", ["--check-media"]),
    ("a 5.1 companion with the id of a stereo rendition", False, lambda r: with_surround(kept(r), id="a0", dir="hls/a0"), "audio rendition ids are not unique", []),
    ("two default 5.1 companions", False, lambda r: (with_surround(kept(r)), edit(pkg(kept(r)), lambda d: d["renditions"]["audioSurround"].append(dict(d["renditions"]["audioSurround"][0], id="a8")))), "more than one 5.1 companion is default", []),
    ("a 5.1 companion that does not say what it is for", False, lambda r: (with_surround(kept(r)), edit(pkg(kept(r)), lambda d: d["renditions"]["audioSurround"][0].pop("purpose"))), "'purpose' is a required property", []),
    ("a 5.1 companion whose purpose is only assumed commentary", False, lambda r: (with_surround(kept(r)), edit(pkg(kept(r)), lambda d: d["renditions"]["audioSurround"][0].update(purpose="commentary"))), "audio a9 purpose commentary cannot be assumed", []),
    ("a video range HLS does not name", False, lambda r: (hls_layout(kept(r)), edit(pkg(kept(r)), lambda d: d["renditions"]["video"][0].update(videoRange="HDR"))), "'HDR' is not one of ['SDR', 'PQ', 'HLG']", []),
    ("a peak bitrate below nothing", False, lambda r: (hls_layout(kept(r)), edit(pkg(kept(r)), lambda d: d["renditions"]["video"][0].update(peakBitrateBps=-1))), "-1 is less than the minimum of 0", []),
    ("an HLS layout without its master playlist", False, lambda r: (hls_layout(kept(r)), os.remove(os.path.join(kept(r), "hls", "master.m3u8")), rechecksum(kept(r))), "master playlist hls/master.m3u8 missing", ["--check-media"]),
    ("an HLS layout cut into segments of no length", False, lambda r: hls_layout(kept(r), segmentSeconds=0), "0 is less than the minimum of 1", []),
    ("an HLS layout that does not say its subtitle group", False, lambda r: (hls_layout(kept(r)), edit(pkg(kept(r)), lambda d: d["hls"].pop("subtitleGroup"))), "'subtitleGroup' is a required property", []),
    ("an audio group the HLS layout does not name", False, lambda r: (with_surround(kept(r)), hls_layout(kept(r), audioGroups=["audio"])), "a9 is in group audio-surround, which hls.audioGroups does not name", []),
    ("a subtitle group no subtitle is in", False, lambda r: hls_layout(kept(r), subtitleGroup="subs"), "hls.subtitleGroup names subs, but no subtitle has an HLS rendition", []),
    ("a subtitle with its HLS rendition", True, subtitle_hls, "OK", ["--check-checksums"]),
    ("a subtitle's HLS rendition missing", False, lambda r: subtitle_hls(r, made=False), "the HLS rendition hls/s1 of subtitle sub1 is missing", ["--check-media"]),
    ("a subtitle made from a sidecar its source keeps", True, from_sidecar, "OK", ["--check-checksums"]),
    ("a subtitle made from a sidecar its source does not list", False, lambda r: from_sidecar(r, listed=False), "does not list among its sidecars", []),
    ("a subtitle made from a sidecar of a source the version was not made from", False, lambda r: from_sidecar(r, path=f"sources/{NOWHERE}/x.srt"), "which is no sidecar of a source this version was made from", []),
    ("a subtitle made from a sidecar outside the item", False, lambda r: from_sidecar(r, path="../x.srt"), "$.subtitles[3].fromSidecar: '../x.srt' does not match", []),
    ("an extra's subtitle made from a sidecar", False, lambda r: edit(pkg(featurette(r)), lambda d: d["subtitles"][0].update(fromSidecar="sources/x/y.srt")), "but an extra has no source record", []),

    # ---- no record names a file as it arrived, and no file in the record is named so
    ("a version taken in before anything packaged it, its record and its original alone", True, taken_in, "OK", ["--check-checksums"]),
    ("a source naming its original as it arrived", False, lambda r: edit(movie_source(r), lambda d: d["file"].update(name="Example Film (2012).mkv")),
     "file.name 'Example Film (2012).mkv' is a name the original arrived under", []),
    ("a version and its sources naming their originals as they arrived", False, parts_as_they_arrived,
     "originalFiles names 'Example Film - Part 1.mkv', which is not a name the library gives an original", ["--check-media"]),
    ("and its sources naming them so", False, parts_as_they_arrived,
     "file.name 'Example Film - Part 2.mkv' is a name the original arrived under", []),
    ("a source saying where its original came from", False,
     lambda r: edit(movie_source(r), lambda d: d.update(origin={"libraryPath": "Example Film (2012)/Example Film (2012).mkv"})),
     "origin names where the original came from", []),
    ("a source keeping the container's title", False, lambda r: edit(movie_source(r), lambda d: d["container"].update(title="Example.Film.2012")),
     "keeps the container's title tag", []),
    ("a source keeping the container's title among its tags, in any case", False,
     lambda r: edit(movie_source(r), lambda d: d["container"]["tags"].update(TITLE="Example.Film.2012")), "keeps the container's title tag", []),
    ("a copy of a subtitle file under the name it came with", False, lambda r: from_sidecar(r, name="Example Show - S01E01.de.srt"),
     "is not the copy of a subtitle file under the name the library gives it", []),
    ("a copy of a subtitle file naming the file it was copied from", False, lambda r: from_sidecar(r, original_name="Example Show - S01E01.de.srt"),
     "names the file it was copied from (originalName)", []),
    ("a copy of the .nfo that came with an original", False, kept_nfo, "sidecar sources/", []),
    ("a probe naming the path it read", False, lambda r: probe_says(r, lambda d: d["format"].update(filename="/media/Example Film (2012).mkv")),
     "format.filename '/media/Example Film (2012).mkv' is not the name the library gives the original", []),
    ("a probe keeping the title tag", False, lambda r: probe_says(r, lambda d: d["format"].setdefault("tags", {}).update(Title="Example.Film.2012")),
     "keeps the container's title tag (format.tags.title)", []),
    ("an extra keeping its original under the name it arrived with", False, extra_as_it_arrived,
     "originalFiles names 'Example Show - Behind the Scenes.mkv'", ["--check-checksums"]),
    ("what a package was made from, named as it arrived", False,
     lambda r: (packaged_from(r), edit(xjson(featurette(r)), lambda d: d["packagedFrom"][0].update(name="On Location.mkv"))),
     "packagedFrom names 'On Location.mkv'", []),
    ("an extra keeping the container's title", False, lambda r: edit(xjson(featurette(r)), lambda d: d["container"].update(title="On.Location")),
     "keeps the container's title tag", []),

    # ---- valid variations
    ("operating-system files in shared folders", True, lambda r: [open(os.path.join(x, ".DS_Store"), "w").write("x") for x in
                                                                  (movie(r), os.path.join(movie(r), "metadata"), os.path.join(r, "movies"), kept(r), os.path.join(series(r), "episodes"))], "OK", ["--check-media"]),
    ("an item that has not been packaged yet", True, lambda r: (
        shutil.rmtree(os.path.join(movie(r), "versions")), shutil.rmtree(os.path.join(movie(r), "events")),
        edit(meta(movie(r)), lambda d: d["library"].update(primaryVersionId=None, versionLabels={}))), "OK", ["--check-media"]),
    ("a season listed with no episodes on storage", True, lambda r: edit(meta(series(r)), lambda d: d["series"]["seasons"].append(
        {"number": 2, "tmdbSeason": None, "name": "Season 2", "overview": None, "airDate": None, "episodeCountReference": 8})), "OK", []),
    ("a note with no subject", True, note, "OK", []),
    ("an original with no audio", True, silent, "OK", ["--check-checksums"]),
    ("a deletion accepting less than the gate", True, lambda r: edit(deletion(r), lambda d: d.update(accepted=["surround"])), "OK", []),
    ("a deletion that gave up nothing", True, lambda r: edit(deletion(r), lambda d: d.update(accepted=[])), "OK", []),
    ("a removed version whose folder is still on storage", True, resurrect, "OK", ["--check-checksums"]),
    ("an item whose database decided nothing about its storage", True, lambda r: edit(meta(movie(r)), lambda d: d.pop("library")), "OK", []),
]


def main():
    """Every case, or with arguments only the cases whose name contains one of them."""
    wanted = sys.argv[1:]
    cases = [c for c in CASES if not wanted or any(w in c[0] for w in wanted)]
    failures = 0
    for name, expect_ok, change, phrase, extra in cases:
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
    print(f"{len(cases) - failures}/{len(cases)} cases behave as expected")
    sys.exit(1 if failures or not cases else 0)


if __name__ == "__main__":
    main()
