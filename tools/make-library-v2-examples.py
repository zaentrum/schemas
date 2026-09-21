#!/usr/bin/env python3
"""Regenerate library/v2/examples: a neutral sample library in the v2 record layout.

The database is the working copy; this tree is the record that can rebuild it. The fixtures show
every record the format has and the shapes a reader most needs to see:

  movies/  one movie in two versions. The first is package-only — its original was deleted, so
           version.json still names the file, the folder no longer holds it, and an
           events/<timestamp>-original-deleted.json record says what was accepted as lost. The
           second keeps its original next to its package, and carries a quality ladder.
  series/  one series with a season and two episodes. The first episode keeps its original and
           carries forced, full and SDH subtitles and a commentary track; the second was packaged
           from an original that was never kept here, so its package is canonical.

Images are named by the hash of their own content. Originals and images are small placeholders and
rendition folders are empty; hashes, sizes and checksums are computed, never typed. The tree passes
validate-library-v2.py --check-checksums.
"""
import base64, hashlib, json, os, shutil, uuid

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "library", "v2", "examples")
NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://zaentrum.github.io/schemas/library/v2/examples")

CREATED = "2026-09-18T09:00:00Z"      # the item entered the library
TAKEN = "2026-09-18T09:05:00Z"        # an original was taken in
VERSIONED = "2026-09-18T09:20:00Z"    # a version was established
PACKAGED = "2026-09-18T11:30:00Z"     # a package completed
REPACKAGED = "2026-09-19T13:00:00Z"   # a second package was made in a new version folder
SUPERSEDED = "2026-09-19T13:05:00Z"   # the package it replaces was recorded as superseded
DELETED = "2026-09-20T08:15:00Z"      # an original was deleted
REMOVED = "2026-09-20T09:30:00Z"      # a version was removed from the item
PROJECTED = "2026-09-20T12:00:00Z"    # the database was projected into metadata.json

# A fixed 1x1 JPEG and a fixed 1x1 transparent PNG, as bytes rather than generated, so the fixture
# hashes do not depend on the library build that happens to run the generator.
JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////"
    "////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA=")
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgAAIAAAUAAXpeqz8AAAAASUVORK5CYII=")


def jpeg(label):
    """The 1x1 JPEG with a comment segment, so two placeholder images are two different files.
    Images are named by their content hash, and identical bytes would be one file under two kinds."""
    payload = label.encode()
    com = b"\xff\xfe" + (len(payload) + 2).to_bytes(2, "big") + payload
    return JPEG[:2] + com + JPEG[2:]


def uid(*parts):
    return str(uuid.uuid5(NS, ":".join(parts)))


def sha(b):
    return "sha256:" + hashlib.sha256(b).hexdigest()


def qh1(data):
    return sha(data[:65536] + (data[-65536:] if len(data) > 65536 else b"") + len(data).to_bytes(8, "big"))


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not isinstance(data, bytes):
        data = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode()
    with open(path, "wb") as f:
        f.write(data)
    return data


def keep(path):
    """An empty rendition folder, kept in git by a .keep file."""
    write(os.path.join(path, ".keep"), b"")


# ---------------------------------------------------------------- metadata images
def image(item_dir, kind, data, ctype, **extra):
    """Write metadata/<content hash>.<ext> and return the entry that names it."""
    ext = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[ctype]
    digest = hashlib.sha256(data).hexdigest()
    name = f"{digest}.{ext}"
    write(os.path.join(item_dir, "metadata", name), data)
    entry = {"kind": kind, "file": name, "sha256": "sha256:" + digest, "contentType": ctype,
             "sizeBytes": len(data), "width": 1, "height": 1, "language": None,
             "sourceUrl": None, "fetchedAt": PROJECTED, "origin": "manual"}
    entry.update(extra)
    return entry


# ---------------------------------------------------------------- streams of an original
def essence(**kw):
    base = {"maxAudioChannels": 2, "surround": False, "losslessAudio": False, "objectAudio": False,
            "maxVideoHeight": 1080, "videoBitDepth": 8, "hdr10Metadata": False, "dolbyVision": False,
            "stereo3d": False, "interlaced": False, "audioLanguages": ["en"], "subtitleLanguages": [],
            "subtitleTracks": 0, "imageSubtitles": False, "styledSubtitles": False, "fonts": False,
            "chapters": False, "commentaryTracks": 0, "commentarySubtitles": 0, "audioDescriptionTracks": 0,
            "sdhSubtitleLanguages": [], "forcedSubtitleLanguages": [], "closedCaptions": False}
    base.update(kw)
    return base


def video(index, codec, w, h, depth=8, hdr="sdr"):
    return {"index": index, "type": "video", "codec": codec, "language": None, "languageRaw": None, "title": None,
            "dispositions": {"default": True}, "profile": "Main" if depth == 8 else "Main 10", "level": None,
            "bitDepth": depth, "pixelFormat": "yuv420p" if depth == 8 else "yuv420p10le", "width": w, "height": h,
            "sampleAspectRatio": "1:1", "displayAspectRatio": None, "frameRate": "24/1", "fieldOrder": "progressive",
            "bitrate": None,
            "colour": {"primaries": "bt709" if hdr == "sdr" else "bt2020",
                       "transfer": "bt709" if hdr == "sdr" else "smpte2084", "matrix": None, "range": "tv"},
            "hdr": {"format": hdr, "masteringDisplay": None, "contentLightLevel": None, "dolbyVision": None},
            "stereo3d": None, "closedCaptions": False, "encoder": None}


def audio(index, codec, channels, layout, title=None, commentary=False):
    return {"index": index, "type": "audio", "codec": codec, "language": "en", "languageRaw": "eng", "title": title,
            "dispositions": {"default": index == 1, "commentary": commentary}, "profile": None, "channels": channels,
            "channelLayout": layout, "sampleRate": 48000, "bitDepth": None, "bitrate": None, "lossless": False,
            "objectAudio": "none", "titleClaim": None, "encoder": None,
            "purpose": "commentary" if commentary else "main",
            "purposeFrom": "disposition" if commentary else "assumed", "variant": None}


def subtitle(index, title, forced=False, sdh=False, events=900):
    purpose = "forced" if forced else "sdh" if sdh else "dialogue"
    return {"index": index, "type": "subtitle", "codec": "subrip", "language": "en", "languageRaw": "eng",
            "title": title, "dispositions": {"default": False, "forced": forced, "hearingImpaired": sdh},
            "form": "text", "styled": False, "variant": None, "events": 40 if forced else events,
            "purpose": purpose, "purposeFrom": "disposition" if (forced or sdh) else "assumed"}


def probe_stream(st):
    """A minimal ffprobe stream carrying the same flags and title the stream record claims."""
    d = st.get("dispositions") or {}
    raw = {"default": int(bool(d.get("default"))), "forced": int(bool(d.get("forced"))),
           "comment": int(bool(d.get("commentary"))), "hearing_impaired": int(bool(d.get("hearingImpaired"))),
           "visual_impaired": int(bool(d.get("visualImpaired")))}
    tags = {k: v for k, v in (("language", st.get("languageRaw")), ("title", st.get("title"))) if v}
    if st.get("events") is not None:
        tags["NUMBER_OF_FRAMES"] = str(st["events"])
    out = {"index": st["index"], "codec_type": st["type"], "codec_name": st.get("codec"), "disposition": raw, "tags": tags}
    if st["type"] == "audio":
        out["channels"] = st["channels"]
    if st["type"] == "video":
        out.update(width=st.get("width"), height=st.get("height"))
    return out


# ---------------------------------------------------------------- the records
def source(item_dir, sid, name, library_path, streams, chapters, src_essence, duration_ms,
           quality="1080p", medium="disc", fingerprint="h264/high/8bit/sdr/1920x800", size=6300000000,
           part=None, naming=None):
    """sources/<sid>.json plus the verbatim probe beside it. Written once; it never says where the
    bytes are or whether they still exist — that is the version's record and the events."""
    probe = {"format": {"filename": name, "duration": str(duration_ms / 1000)},
             "streams": [probe_stream(x) for x in streams],
             "chapters": [{"start_time": str(c["startMs"] / 1000), "end_time": str(c["endMs"] / 1000),
                           "tags": {"title": c["title"]}} for c in chapters]}
    probe_bytes = write(os.path.join(item_dir, "sources", sid, "ffprobe.json"), probe)
    record = {
        "schema": "zaentrum.library.source/2", "sourceId": sid, "takenAt": TAKEN, "takenBy": "analyzer example",
        "file": {"name": name, "kind": "stream-container", "sizeBytes": size, "mtime": CREATED,
                 "fixity": {"qh1": sha(name.encode())}, "part": part},
        "origin": {"libraryPath": library_path, "folder": os.path.dirname(library_path), "takenBy": "move"},
        **({"naming": naming} if naming else {}),
        "labels": {"quality": quality, "medium": medium, "resolution": quality, "edition": None, "folderEdition": None},
        "container": {"format": "matroska,webm", "durationMs": duration_ms, "bitrate": None, "title": None,
                      "muxingApp": None, "writingApp": None, "creationTime": None, "tags": {}},
        "fidelity": {"class": "original", "fingerprint": fingerprint, "evidence": []},
        "streams": streams, "sidecars": [], "covers": [], "essence": src_essence,
        "probe": {"tool": "ffprobe", "version": None, "at": TAKEN, "file": f"sources/{sid}/ffprobe.json",
                  "sha256": sha(probe_bytes), "note": None},
    }
    write(os.path.join(item_dir, "sources", f"{sid}.json"), record)
    return record


def place_original(vdir, record):
    """Put a placeholder for the original in the version folder and correct the source record's size
    and fixity to the bytes that are actually there."""
    name = record["file"]["name"]
    data = write(os.path.join(vdir, name), b"placeholder for the original file of " + name.encode() + b"\n")
    record["file"]["sizeBytes"], record["file"]["fixity"] = len(data), {"qh1": qh1(data)}
    return name


def trickplay_files(folder, duration_ms):
    """A VTT with one cue per 10 s, pointing into 10x10 sprite sheets, and the sheets it names."""
    thumbs = duration_ms // 10000
    stamp = lambda ms: f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"
    lines = ["WEBVTT", ""]
    for i in range(thumbs):
        start, end = i * 10000, min((i + 1) * 10000, duration_ms)
        lines += [f"{stamp(start)} --> {stamp(end)}",
                  f"sprite-{i // 100:04d}.jpg#xywh={i % 100 % 10 * 320},{i % 100 // 10 * 180},320,180", ""]
    write(os.path.join(folder, "trickplay", "thumbnails.vtt"), ("\n".join(lines)).encode())
    for sheet in range((thumbs + 99) // 100):
        write(os.path.join(folder, "trickplay", f"sprite-{sheet:04d}.jpg"), JPEG)


TRICKPLAY = {"vttPath": "trickplay/thumbnails.vtt", "spritePattern": "trickplay/sprite-%04d.jpg",
             "intervalSec": 10, "thumbWidth": 320, "thumbHeight": 180, "gridCols": 10, "gridRows": 10}
PACKAGE_DIRS = ("hls", "subs", "trickplay", "trailers")


def checksums(vdir):
    """checksums.sha256 over the package files of a version folder, and the record for it. The
    records themselves, the original and this file are not package files."""
    files = []
    for d in PACKAGE_DIRS:
        for root, dirs, names in os.walk(os.path.join(vdir, d)):
            dirs.sort()
            files += [os.path.relpath(os.path.join(root, n), vdir) for n in sorted(names)]
    if os.path.isfile(os.path.join(vdir, ".complete")):
        files.append(".complete")
    files.sort()
    lines = [f"{hashlib.sha256(open(os.path.join(vdir, rel), 'rb').read()).hexdigest()}  {rel}" for rel in files]
    data = write(os.path.join(vdir, "checksums.sha256"), ("\n".join(lines) + "\n").encode())
    return {"file": "checksums.sha256", "algorithm": "sha256", "sha256": sha(data), "files": len(files),
            "bytes": sum(os.path.getsize(os.path.join(vdir, rel)) for rel in files)}


def renditions(width, height, hdr, source_channels, ladder=False):
    rungs = [(width, height, 8000000)] + ([(width * 2 // 3, height * 2 // 3, 3000000)] if ladder else [])
    return {
        "video": [{"id": f"v{i}", "dir": f"hls/v{i}", "codec": "hev1.1.6.L120.B0" if i == 0 else "hev1.1.6.L93.B0",
                   "width": w, "height": h, "bitrateBps": br, "hdr": hdr, "frameRate": "24/1", "segments": 10,
                   "targetDuration": 6, "dynamicRange": "hdr10" if hdr else "sdr", "sourceStreamIndex": 0}
                  for i, (w, h, br) in enumerate(rungs)],
        "audio": [{"id": "a0", "dir": "hls/a0", "codec": "mp4a.40.2", "language": "eng", "title": "", "default": True,
                   "channels": 2, "bitrateBps": 192000, "segments": 15, "visible": True, "sourceStreamIndex": 1,
                   "sourceChannels": source_channels, "purpose": "main", "purposeFrom": "assumed",
                   "variant": None, "original": True}],
    }


def version_record(vdir, vid, edition, presentation, runtime_ms, source_ids, original_files,
                   chapters=(), chapters_from=None, segments=(), completeness="complete", measured=None,
                   created=VERSIONED):
    record = {
        "schema": "zaentrum.library.version/2", "versionId": vid, "createdAt": created,
        "createdBy": "analyzer example", "edition": edition, "presentation": presentation,
        "runtimeMs": runtime_ms, "chapters": list(chapters), "chaptersFrom": chapters_from,
        "segments": list(segments),
        "completeness": {"status": completeness, "evidence": list(measured or [])},
        "sourceIds": list(source_ids), "originalFiles": list(original_files),
    }
    write(os.path.join(vdir, "version.json"), record)
    return record


def package_record(vdir, pid, role, duration_ms, ren, pkg_essence, losses, size_bytes,
                   subtitles=(), recipe=None, created=PACKAGED):
    """The package files must already be written: the checksums cover them."""
    record = {
        "schema": "zaentrum.library.package/2", "packageId": pid, "createdAt": created,
        "packagedBy": "packager example", "state": "complete", "role": role, "durationMs": duration_ms,
        "recipe": recipe or {"video": "hevc re-encode", "audio": "aac-lc 2ch 192k", "subtitles": None},
        "renditions": ren, "subtitles": list(subtitles), "trickplay": dict(TRICKPLAY), "trailers": [],
        "sizeBytes": size_bytes, "peakBandwidthBps": 8192000,
        "fidelity": {"lossless": not losses, "losses": list(losses),
                     "droppedSourceStreams": sorted({l["sourceStreamIndex"] for l in losses
                                                     if l["kind"].endswith("-dropped") and l["sourceStreamIndex"] is not None})},
        "essence": pkg_essence, "checksums": checksums(vdir),
    }
    write(os.path.join(vdir, "package.json"), record)
    return record


def event(item_dir, at, kind, by="librarian example", **fields):
    record = {"schema": "zaentrum.library.event/2", "eventId": uid("event", at, kind), "at": at,
              "by": by, "kind": kind, **fields}
    stamp = at.replace("-", "").replace(":", "")
    write(os.path.join(item_dir, "events", f"{stamp}-{kind}.json"), record)
    return record


# ---------------------------------------------------------------- the sample library
def movie():
    """Tears of Steel, in two versions: a theatrical cut whose original was deleted, and a
    director's cut split across two files that still keeps both next to its package. A third
    version was removed from the item altogether, and only the event that says so is left."""
    mid = uid("movie", "tears-of-steel")
    mdir = os.path.join(ROOT, "movies", mid[:2], mid)
    write(os.path.join(mdir, "item.json"), {
        "schema": "zaentrum.library.item/2", "itemId": mid, "type": "movie", "title": "Tears of Steel",
        "externalIds": {"tmdbMovie": "133701", "imdb": "tt2285752"},
        "createdAt": CREATED, "createdBy": "ingest example",
        "provenance": {"migratedFrom": "legacy-catalog", "legacyItemId": uid("legacy", "tears-of-steel"),
                       "migratedAt": CREATED, "legacyCreatedBy": "catalog import"},
    })

    chapters = [{"startMs": 0, "endMs": 300000, "title": "Chapter 1"},
                {"startMs": 300000, "endMs": 734000, "title": "Chapter 2"}]
    credits_seg = [{"kind": "credits", "startMs": 690000, "endMs": 734000, "detector": "blackframe",
                    "confidence": 0.9, "label": None}]

    # --- version one: theatrical. Its original was deleted, so only the package is left on disk.
    v1, s1, p1 = uid(mid, "version", "theatrical"), uid(mid, "source", "theatrical"), uid(mid, "package", "theatrical")
    v1dir = os.path.join(mdir, "versions", v1)
    src1 = source(mdir, s1, "Tears of Steel (2012).mkv",
                  "movies/Tears of Steel (2012)/Tears of Steel (2012).mkv",
                  [video(0, "h264", 1920, 800), audio(1, "ac3", 6, "5.1(side)"), subtitle(2, None)],
                  chapters, essence(maxAudioChannels=6, surround=True, chapters=True, maxVideoHeight=800,
                                    subtitleLanguages=["en"], subtitleTracks=1), 734000)
    version_record(v1dir, v1,
                   {"kind": "theatrical", "label": None, "decidedBy": "inferred", "decidedAt": VERSIONED,
                    "confidence": 0.6,
                    "evidence": [{"signal": "runtime", "value": {"measuredMs": 734000, "referenceMs": 720000},
                                  "weight": 0.6}]},
                   {"colour": "colour", "dynamicRange": "sdr", "stereo3d": "none", "aspectRatio": "12:5"},
                   734000, [s1], [src1["file"]["name"]], chapters, "original-file", credits_seg,
                   measured=[{"signal": "runtime", "value": {"measuredMs": 734000, "referenceMs": 720000},
                              "note": "runs past the reference runtime, so nothing is missing"}])
    write(os.path.join(v1dir, ".complete"), b"packager example 2026-09-18\n")
    ren1 = renditions(1920, 800, False, 6, ladder=True)
    for r in ren1["video"] + ren1["audio"]:
        keep(os.path.join(v1dir, r["dir"]))
    trickplay_files(v1dir, 734000)
    package_record(v1dir, p1, "derived", 734000, ren1, essence(maxVideoHeight=800),
                   [{"kind": "audio-downmix", "detail": "6ch -> 2ch (a0)", "sourceStreamIndex": 1},
                    {"kind": "subtitle-dropped", "detail": "1 of 1 source subtitle track(s) not packaged",
                     "sourceStreamIndex": 2}],
                   734000000,
                   recipe={"video": "hevc re-encode, 800p and 533p", "audio": "aac-lc 2ch 192k", "subtitles": None})
    # The original is gone. What the package failed to carry stays computable from the records
    # beside it; what a person agreed to lose is only ever here.
    event(mdir, DELETED, "original-deleted", sourceId=s1, versionId=v1, packageId=p1,
          reason="space on the archive volume",
          accepted=["surround", "maxAudioChannels", "subtitleLanguages:en", "subtitleTracks"])

    # --- version two: the director's cut, in two files, both kept next to the package.
    v2, p2 = uid(mid, "version", "directors-cut"), uid(mid, "package", "directors-cut")
    v2dir = os.path.join(mdir, "versions", v2)
    parts = []
    for index in (1, 2):
        sid = uid(mid, "source", "directors-cut", str(index))
        name = f"Tears of Steel (2012) - Director's Cut - part{index}.mkv"
        src = source(mdir, sid, name, f"movies/Tears of Steel (2012) - Director's Cut/{name}",
                     [video(0, "h264", 1920, 800), audio(1, "ac3", 6, "5.1(side)")],
                     [], essence(maxAudioChannels=6, surround=True, maxVideoHeight=800), 406000,
                     medium="web", part={"index": index, "of": 2})
        parts.append((sid, place_original(v2dir, src)))
        write(os.path.join(mdir, "sources", f"{sid}.json"), src)
    version_record(v2dir, v2,
                   {"kind": "directors-cut", "label": "Director's Cut", "decidedBy": "human", "decidedAt": VERSIONED,
                    "evidence": [{"signal": "folder-name", "value": "Tears of Steel (2012) - Director's Cut"}]},
                   {"colour": "colour", "dynamicRange": "sdr", "stereo3d": "none", "aspectRatio": "12:5"},
                   812000, [s for s, _ in parts], [f for _, f in parts])
    write(os.path.join(v2dir, ".complete"), b"packager example 2026-09-18\n")
    ren2 = renditions(1920, 800, False, 6)
    for r in ren2["video"] + ren2["audio"]:
        keep(os.path.join(v2dir, r["dir"]))
    trickplay_files(v2dir, 812000)
    package_record(v2dir, p2, "derived", 812000, ren2, essence(maxVideoHeight=800),
                   [{"kind": "audio-downmix", "detail": "6ch -> 2ch (a0)", "sourceStreamIndex": 1}],
                   812000000)

    # --- a version that was removed from the item: its folder is gone, this is the only trace.
    event(mdir, REMOVED, "version-removed", versionId=uid(mid, "version", "sdr-duplicate"),
          reason="an SDR duplicate of the theatrical cut, kept by mistake")

    write(os.path.join(mdir, "metadata.json"), {
        "schema": "zaentrum.library.metadata/2", "itemId": mid, "type": "movie",
        "asOf": PROJECTED, "projectedBy": "catalog example",
        "titles": {"primary": "Tears of Steel", "original": "Tears of Steel", "sort": "tears of steel",
                   "qualifier": None,
                   "localized": {"en": {"title": "Tears of Steel", "sortTitle": "tears of steel", "tagline": None,
                                        "overview": "A group of warriors and scientists gather at the Oude Kerk in "
                                                    "Amsterdam to stage a crucial event from the past."}}},
        "releaseDate": "2012-09-26", "genres": ["Science Fiction"], "tags": [], "rating": None, "contentRating": None,
        "credits": [{"personId": uid("person", "ian-hubert"), "name": "Ian Hubert", "role": "director",
                     "character": None, "order": 0, "tmdbPerson": None}],
        "collection": None,
        "library": {
            "primaryVersionId": v2,
            "versionLabels": {v2: "Director's Cut"},
            "match": {"status": "matched", "decidedBy": "tmdb", "decidedAt": PROJECTED, "confidence": 1.0,
                      "evidence": [{"signal": "tmdb", "value": "133701"}]},
            "reference": {"runtimeMs": 720000, "runtimeSource": "tmdb"},
        },
        "images": [image(mdir, "poster", jpeg("poster"), "image/jpeg"),
                   image(mdir, "backdrop", jpeg("backdrop"), "image/jpeg"),
                   image(mdir, "logo", PNG, "image/png", language="en")],
        "videos": [{"site": "example.org", "key": "tears-of-steel-trailer", "url": None, "name": "Trailer",
                    "kind": "trailer", "language": "en", "durationMs": 60000,
                    "publishedAt": "2012-09-26T00:00:00Z", "origin": "manual"}],
        "curation": {"metadataLocked": False, "lockedFields": [], "notes": None},
        "fieldOrigins": {"titles.primary": "tmdb", "releaseDate": "tmdb", "genres": "tmdb", "credits": "tmdb",
                         "images": "manual"},
    })


def episode_version(edir, vid, pid, sid, original, tracks, ladder, created_at, packaged_at, role):
    """One version folder of an episode, with its package."""
    vdir = os.path.join(edir, "versions", vid)
    version_record(vdir, vid,
                   {"kind": "unknown", "label": None, "decidedBy": "inferred", "decidedAt": created_at,
                    "evidence": []},
                   {"colour": "colour", "dynamicRange": "hdr10", "stereo3d": "none", "aspectRatio": "16:9"},
                   2700000, [sid], [original] if original else [],
                   segments=[{"kind": "intro", "startMs": 60000, "endMs": 120000, "detector": "chromaprint",
                              "confidence": 0.85, "label": None}],
                   created=created_at)
    write(os.path.join(vdir, ".complete"), b"packager example\n")
    ren = renditions(3840, 2160, True, 6, ladder=ladder)
    subs = []
    if tracks:
        # Characters speak an invented language in a few scenes: the forced track translates only
        # those lines; the full and SDH tracks are there to pick. Which one a viewer sees is up to
        # the player and the viewer's settings, so nothing here says it.
        ren["audio"].append({"id": "a1", "dir": "hls/a1", "codec": "mp4a.40.2", "language": "eng",
                             "title": "Commentary", "default": False, "channels": 2, "bitrateBps": 192000,
                             "segments": 15, "visible": True, "sourceStreamIndex": 2, "sourceChannels": 2,
                             "purpose": "commentary", "purposeFrom": "disposition", "variant": None, "original": None})
        subs = [{"id": "sub0", "path": "subs/0.vtt", "language": "eng", "title": "Forced", "default": False,
                 "forced": True, "format": "webvtt", "visible": True, "sourceStreamIndex": 3, "purpose": "forced",
                 "purposeFrom": "disposition", "variant": None},
                {"id": "sub1", "path": "subs/1.vtt", "language": "eng", "title": "", "default": False,
                 "forced": False, "format": "webvtt", "visible": True, "sourceStreamIndex": 4, "purpose": "dialogue",
                 "purposeFrom": "assumed", "variant": None},
                {"id": "sub2", "path": "subs/2.vtt", "language": "eng", "title": "SDH", "default": False,
                 "forced": False, "format": "webvtt", "visible": True, "sourceStreamIndex": 5, "purpose": "sdh",
                 "purposeFrom": "disposition", "variant": None}]
        for i in range(3):
            write(os.path.join(vdir, "subs", f"{i}.vtt"), b"WEBVTT\n")
    for r in ren["video"] + ren["audio"]:
        keep(os.path.join(vdir, r["dir"]))
    trickplay_files(vdir, 2700000)
    package_record(vdir, pid, role, 2700000, ren, essence(maxVideoHeight=2160, videoBitDepth=10, **tracks),
                   [{"kind": "audio-downmix", "detail": "6ch -> 2ch (a0)", "sourceStreamIndex": 1},
                    {"kind": "audio-codec", "detail": "eac3 -> mp4a.40.2", "sourceStreamIndex": 1}],
                   2700000000, subtitles=subs, created=packaged_at,
                   recipe={"video": "hevc re-encode", "audio": "aac-lc 2ch 192k",
                           "subtitles": "text -> webvtt" if tracks else None})


def episode_item(sdir, series_id, eid, number, title, overview, numbering, keeps_original):
    """One episode folder: identity, projection, one original and the versions made from it."""
    edir = os.path.join(sdir, "episodes", eid)
    write(os.path.join(edir, "item.json"), {
        "schema": "zaentrum.library.item/2", "itemId": eid, "type": "episode", "title": title,
        "externalIds": {}, "createdAt": CREATED, "createdBy": "ingest example",
        "seriesId": series_id, "seasonNumber": 1, "episodeNumber": number, "episodeCode": f"S01E{number:02d}",
    })
    sid = uid(eid, "source")
    streams = [video(0, "hevc", 3840, 2160, 10, "hdr10"), audio(1, "eac3", 6, "5.1")]
    tracks = {}
    if keeps_original:
        streams += [audio(2, "aac", 2, "stereo", title="Commentary", commentary=True),
                    subtitle(3, "Forced", forced=True), subtitle(4, None), subtitle(5, "SDH", sdh=True)]
        tracks = {"commentaryTracks": 1, "subtitleLanguages": ["en"], "subtitleTracks": 3,
                  "sdhSubtitleLanguages": ["en"], "forcedSubtitleLanguages": ["en"]}
    name = f"Example Show (US) - S01E{number:02d}{'-03' if number == 2 else ''} - {title}.mkv"
    src = source(edir, sid, name, f"tv/Example Show (US)/Season 01/{name}",
                 streams, [], essence(maxAudioChannels=6, surround=True, maxVideoHeight=2160, videoBitDepth=10,
                                      hdr10Metadata=True, **tracks),
                 2700000, "2160p", "web", "hevc/main10/10bit/hdr10/3840x2160",
                 naming={"scheme": "unknown", "seasonNumber": 1, "episodeNumber": number,
                         "episodeEnd": 3 if number == 2 else None,
                         "raw": f"S01E{number:02d}{'-03' if number == 2 else ''}"})

    if keeps_original:
        # one version, with the original beside its package
        primary = uid(eid, "version")
        original = place_original(os.path.join(edir, "versions", primary), src)
        write(os.path.join(edir, "sources", f"{sid}.json"), src)
        episode_version(edir, primary, uid(eid, "package"), sid, original, tracks, False,
                        VERSIONED, PACKAGED, "derived")
    else:
        # the original was never kept here, so each package is the only copy; the first was
        # re-packaged into a new folder and an event says which one took over.
        old, old_pkg = uid(eid, "version", "first"), uid(eid, "package", "first")
        primary, new_pkg = uid(eid, "version", "repackaged"), uid(eid, "package", "repackaged")
        episode_version(edir, old, old_pkg, sid, None, tracks, False, VERSIONED, PACKAGED, "canonical")
        episode_version(edir, primary, new_pkg, sid, None, tracks, True, REPACKAGED, REPACKAGED, "canonical")
        event(edir, SUPERSEDED, "package-superseded", by="packager example", versionId=old,
              packageId=old_pkg, supersededBy={"versionId": primary, "packageId": new_pkg},
              reason="re-packaged with a second rung")

    write(os.path.join(edir, "metadata.json"), {
        "schema": "zaentrum.library.metadata/2", "itemId": eid, "type": "episode",
        "asOf": PROJECTED, "projectedBy": "catalog example",
        "titles": {"primary": title, "original": None, "sort": title.lower(), "qualifier": None,
                   "localized": {"en": {"title": title, "sortTitle": title.lower(), "tagline": None,
                                        "overview": overview}}},
        "genres": [], "tags": [], "rating": None, "credits": [],
        "episode": {"airDate": f"2024-01-{9 + number * 7:02d}"},
        "library": {
            "primaryVersionId": primary,
            "match": {"status": "unmatched", "decidedBy": "inferred", "decidedAt": PROJECTED,
                      "confidence": 0.2, "evidence": [{"signal": "filename", "value": f"S01E{number:02d}"}]},
            "reference": {"runtimeMs": 2700000, "runtimeSource": "manual"},
            "numbering": numbering,
        },
        "images": [image(edir, "still", jpeg(f"still s01e{number:02d}"), "image/jpeg")],
        "curation": {"metadataLocked": False, "lockedFields": [], "notes": None},
        "fieldOrigins": {"titles.primary": "manual", "episode.airDate": "manual", "images": "manual"},
    })


def series():
    """Example Show: one season, two episodes, in two orderings — aired, and a disc ordering that
    runs the two the other way round. Nothing lists the episodes: the folders are the list, and each
    episode records its own place in each ordering."""
    sid = uid("series", "example-show-us")
    sdir = os.path.join(ROOT, "series", sid[:2], sid)
    ep1, ep2 = uid(sid, "S01E01"), uid(sid, "S01E02")
    write(os.path.join(sdir, "item.json"), {
        "schema": "zaentrum.library.item/2", "itemId": sid, "type": "series", "title": "Example Show",
        "externalIds": {}, "createdAt": CREATED, "createdBy": "ingest example",
    })
    write(os.path.join(sdir, "metadata.json"), {
        "schema": "zaentrum.library.metadata/2", "itemId": sid, "type": "series",
        "asOf": PROJECTED, "projectedBy": "catalog example",
        "titles": {"primary": "Example Show", "original": None, "sort": "example show", "qualifier": "US",
                   "localized": {"en": {"title": "Example Show", "sortTitle": "example show", "tagline": None,
                                        "overview": "A fictional series used to illustrate the library layout."}}},
        "genres": ["Drama"], "tags": [], "rating": None, "contentRating": None, "credits": [],
        "series": {"status": "returning", "firstAirDate": "2024-01-10", "lastAirDate": "2024-03-06",
                   "network": None,
                   "seasons": [{"number": 1, "tmdbSeason": None, "name": "Season 1",
                                "overview": "The first season.", "airDate": "2024-01-10",
                                "episodeCountReference": 8}]},
        "library": {
            "match": {"status": "unmatched", "decidedBy": "inferred", "decidedAt": PROJECTED,
                      "confidence": 0.2,
                      "evidence": [{"signal": "folder-name", "value": "Example Show (US)"}]},
            # The decision is which ordering a viewer sees; the orderings themselves are the
            # episodes' own numbering, so adding an episode never rewrites this file's list of them.
            "defaultOrdering": "aired",
        },
        "images": [image(sdir, "poster", jpeg("series poster"), "image/jpeg", season=None),
                   image(sdir, "poster", jpeg("season 1 poster"), "image/jpeg", season=1)],
        "curation": {"metadataLocked": False, "lockedFields": [], "notes": None},
        "fieldOrigins": {"titles.primary": "manual", "titles.qualifier": "filename", "series": "manual",
                         "images": "manual"},
    })
    episode_item(sdir, sid, ep1, 1, "Pilot", "The first episode.",
                 {"aired": {"season": 1, "episode": 1, "episodeEnd": None},
                  "dvd": {"season": 1, "episode": 2, "episodeEnd": None}}, keeps_original=True)
    episode_item(sdir, sid, ep2, 2, "Crosswind", "The second episode, and the third on one file.",
                 {"aired": {"season": 1, "episode": 2, "episodeEnd": 3},
                  "dvd": {"season": 1, "episode": 1, "episodeEnd": None}}, keeps_original=False)


def main():
    if os.path.isdir(ROOT):
        shutil.rmtree(ROOT)
    movie()
    series()
    print("examples written to", os.path.normpath(ROOT))


if __name__ == "__main__":
    main()
