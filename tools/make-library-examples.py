#!/usr/bin/env python3
"""Regenerate library/v1/examples: one movie and one series with an episode in two versions.

The fixtures are neutral (an open movie and a fictional show) and exercise the parts of the
schema a reader most needs to see: a 5.1 original downmixed to stereo, a two-rung quality ladder,
chapters the package does not carry, a series folder with an episode sub-item, season artwork,
and a second version (black-and-white) stored in versions/<id>/ with its own playback block.
Images are 1x1 placeholders and rendition folders are empty; hashes and sizes are computed,
never typed. The tree passes validate-library.py --check-media.
"""
import base64, hashlib, json, os, shutil, uuid

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "library", "v1", "examples")
NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://zaentrum.github.io/schemas/library/examples")
AT = "2026-09-13T12:00:00Z"
JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////"
    "////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA=")


# A fixed 1x1 transparent PNG, as bytes rather than generated, so the fixture hashes do not
# depend on the zlib build that happens to run the generator.
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGNgAAIAAAUAAXpeqz8AAAAASUVORK5CYII=")


def uid(*parts):
    return str(uuid.uuid5(NS, ":".join(parts)))


def sha(b):
    return "sha256:" + hashlib.sha256(b).hexdigest()


def keep(path):
    """An empty rendition folder, kept in git by a .keep file."""
    write(os.path.join(path, ".keep"), b"")


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not isinstance(data, bytes):
        data = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode()
    with open(path, "wb") as f:
        f.write(data)
    return data


def audit():
    return {"rev": 1, "createdAt": AT, "createdBy": "make-library-examples", "updatedAt": AT, "updatedBy": "make-library-examples"}


def image(folder, kind, name, data, ctype, season=None, series=False, language=None):
    write(os.path.join(folder, name), data)
    entry = {"kind": kind, "file": name, "sha256": sha(data), "contentType": ctype, "sizeBytes": len(data),
             "width": 1, "height": 1, "language": language, "sourceUrl": None, "fetchedAt": None, "origin": "manual"}
    if series:
        entry["season"] = season
    return entry


def essence(**kw):
    base = {"maxAudioChannels": 2, "surround": False, "losslessAudio": False, "objectAudio": False, "maxVideoHeight": 1080,
            "videoBitDepth": 8, "hdr10Metadata": False, "dolbyVision": False, "stereo3d": False, "interlaced": False,
            "audioLanguages": ["en"], "subtitleLanguages": [], "subtitleTracks": 0, "imageSubtitles": False,
            "styledSubtitles": False, "fonts": False, "chapters": False, "commentaryTracks": 0, "commentarySubtitles": 0,
            "audioDescriptionTracks": 0, "sdhSubtitleLanguages": [], "forcedSubtitleLanguages": [], "closedCaptions": False}
    base.update(kw)
    return base


def probe_stream(st):
    """A minimal ffprobe stream carrying the same flags and title the stream record claims."""
    d = st.get("dispositions") or {}
    raw = {"default": int(bool(d.get("default"))), "forced": int(bool(d.get("forced"))), "comment": int(bool(d.get("commentary"))),
           "hearing_impaired": int(bool(d.get("hearingImpaired"))), "visual_impaired": int(bool(d.get("visualImpaired")))}
    tags = {k: v for k, v in (("language", st.get("languageRaw")), ("title", st.get("title"))) if v}
    if st.get("events") is not None:
        tags["NUMBER_OF_FRAMES"] = str(st["events"])
    out = {"index": st["index"], "codec_type": st["type"], "codec_name": st.get("codec"), "disposition": raw, "tags": tags}
    if st["type"] == "audio":
        out["channels"] = st["channels"]
    if st["type"] == "video":
        out.update(width=st.get("width"), height=st.get("height"))
    return out


def source(item_dir, sid, name, library_path, streams, chapters, src_essence, duration_ms, quality="1080p", medium="disc"):
    probe = {"format": {"filename": name, "duration": str(duration_ms / 1000)}, "streams": [probe_stream(x) for x in streams], "chapters": []}
    probe_bytes = write(os.path.join(item_dir, "source", sid, "ffprobe.json"), probe)
    return {
        "id": sid, "state": "present",
        "file": {"name": name, "kind": "stream-container", "sizeBytes": 6300000000, "mtime": AT,
                 "fixity": {"qh1": sha(name.encode())}, "origin": {"libraryPath": library_path, "folder": os.path.dirname(library_path)},
                 "part": None},
        "labels": {"quality": quality, "medium": medium, "resolution": quality, "edition": None, "folderEdition": None},
        "container": {"format": "matroska,webm", "durationMs": duration_ms, "bitrate": None, "title": None,
                      "muxingApp": None, "writingApp": None, "creationTime": None, "tags": {}},
        "fidelity": {"class": "original", "evidence": []},
        "streams": streams, "chapters": chapters, "segments": [], "sidecars": [],
        "covers": [],
        "essence": src_essence,
        "probe": {"tool": "ffprobe", "version": None, "at": AT, "file": f"source/{sid}/ffprobe.json", "sha256": sha(probe_bytes), "note": None},
    }


def video(index, codec, w, h, depth=8, hdr="sdr"):
    return {"index": index, "type": "video", "codec": codec, "language": None, "languageRaw": None, "title": None,
            "dispositions": {"default": True}, "profile": "Main" if depth == 8 else "Main 10", "level": None, "bitDepth": depth,
            "pixelFormat": "yuv420p" if depth == 8 else "yuv420p10le", "width": w, "height": h, "sampleAspectRatio": "1:1",
            "displayAspectRatio": None, "frameRate": "24/1", "fieldOrder": "progressive", "bitrate": None,
            "colour": {"primaries": "bt709" if hdr == "sdr" else "bt2020", "transfer": "bt709" if hdr == "sdr" else "smpte2084",
                       "matrix": None, "range": "tv"},
            "hdr": {"format": hdr, "masteringDisplay": None, "contentLightLevel": None, "dolbyVision": None},
            "stereo3d": None, "closedCaptions": False, "encoder": None}


def audio(index, codec, channels, layout, title=None, commentary=False):
    return {"index": index, "type": "audio", "codec": codec, "language": "en", "languageRaw": "eng", "title": title,
            "dispositions": {"default": index == 1, "commentary": commentary}, "profile": None, "channels": channels,
            "channelLayout": layout, "sampleRate": 48000, "bitDepth": None, "bitrate": None, "lossless": False,
            "objectAudio": "none", "titleClaim": None, "encoder": None,
            "purpose": "commentary" if commentary else "main", "purposeFrom": "disposition" if commentary else "assumed", "variant": None}


def subtitle(index, title, forced=False, sdh=False, events=900):
    purpose = "forced" if forced else "sdh" if sdh else "dialogue"
    return {"index": index, "type": "subtitle", "codec": "subrip", "language": "en", "languageRaw": "eng", "title": title,
            "dispositions": {"default": False, "forced": forced, "hearingImpaired": sdh}, "form": "text", "styled": False,
            "variant": None, "events": 40 if forced else events, "purpose": purpose,
            "purposeFrom": "disposition" if (forced or sdh) else "assumed"}


def playback(duration_ms, width, height, hdr, audio_src, ladder=False):
    rungs = [(width, height, 8000000)] + ([(width * 2 // 3, height * 2 // 3, 3000000)] if ladder else [])
    return {
        "durationMs": duration_ms, "packagedAt": "2026-09-01T10:00:00+00:00", "packager": "packager example",
        "renditions": {
            "video": [{"id": f"v{i}", "dir": f"hls/v{i}", "codec": "hev1.1.6.L120.B0" if i == 0 else "hev1.1.6.L93.B0", "width": w, "height": h,
                       "bitrateBps": br, "hdr": hdr, "frameRate": "24/1", "segments": 10, "targetDuration": 6,
                       "dynamicRange": "hdr10" if hdr else "sdr", "sourceStreamIndex": 0} for i, (w, h, br) in enumerate(rungs)],
            "audio": [{"id": "a0", "dir": "hls/a0", "codec": "mp4a.40.2", "language": "eng", "title": "", "default": True,
                       "channels": 2, "bitrateBps": 192000, "segments": 15, "visible": True,
                       "sourceStreamIndex": 1, "sourceChannels": audio_src, "purpose": "main", "purposeFrom": "assumed",
                       "variant": None, "original": True}],
        },
        "subtitles": [], "trickplay": None,
    }


def main():
    if os.path.isdir(ROOT):
        shutil.rmtree(ROOT)

    # ---------------------------------------------------------------- movie: Tears of Steel
    mid = uid("movie", "tears-of-steel")
    mdir = os.path.join(ROOT, "movies", mid[:2], mid)
    vid, sid, pid = uid(mid, "version"), uid(mid, "source"), uid(mid, "package")
    chapters = [{"startMs": 0, "endMs": 300000, "title": "Chapter 1"}, {"startMs": 300000, "endMs": 734000, "title": "Chapter 2"}]
    src_es = essence(maxAudioChannels=6, surround=True, chapters=True, subtitleLanguages=["en"], subtitleTracks=1)
    pb = playback(734000, 1920, 800, False, 6, ladder=True)
    write(os.path.join(mdir, ".complete"), b"packager example 2026-09-01\n")
    for r in pb["renditions"]["video"] + pb["renditions"]["audio"]:
        keep(os.path.join(mdir, r["dir"]))
    manifest = {
        "schema": "zaentrum.library.manifest/1", "version": 3, "itemId": mid, **audit(),
        "type": "movie", "title": "Tears of Steel", "year": 2012, "tmdbId": "133701",
        "externalIds": {"tmdbMovie": "133701", "imdb": "tt2285752"},
        "match": {"status": "matched", "decidedBy": "tmdb"},
        "metadata": {"file": "metadata/metadata.json"},
        **pb,
        "versions": [{
            "id": vid, "path": ".", "label": None, "primary": True,
            "edition": {"kind": "theatrical", "label": None, "decidedBy": "inferred", "confidence": 0.6,
                        "evidence": [{"signal": "runtime", "value": {"measuredMs": 734000, "referenceMs": 720000, "deltaMs": 14000}, "weight": 0.6}],
                        "review": None},
            "presentation": {"colour": "colour", "dynamicRange": "sdr", "stereo3d": "none", "aspectRatio": "12:5"},
            "runtime": {"measuredMs": 734000, "referenceMs": 720000, "deltaMs": 14000},
            "completeness": {"status": "complete", "evidence": []},
            "master": {"fingerprint": "h264/high/8bit/sdr/1920x800", "fidelity": "original"},
            "truth": {"kind": "source", "since": AT, "note": "the original still exists"},
            "sources": [source(mdir, sid, "Tears of Steel (2012).mkv", "movies/Tears of Steel (2012)/Tears of Steel (2012).mkv",
                               [video(0, "h264", 1920, 800), audio(1, "ac3", 6, "5.1(side)"),
                                subtitle(2, None)],
                               chapters, src_es, 734000)],
            "package": {
                "id": pid, "state": "complete", "role": "derived", "sizeBytes": 734000000, "peakBandwidthBps": 8192000,
                "recipe": {"video": "hevc re-encode, 800p and 533p", "audio": "aac-lc 2ch 192k", "subtitles": "text -> webvtt"},
                "fidelity": {"lossless": False, "droppedSourceStreams": [2], "losses": [
                    {"kind": "audio-downmix", "detail": "6ch -> 2ch (a0)", "sourceStreamIndex": 1},
                    {"kind": "subtitle-dropped", "detail": "1 of 1 source subtitle track(s) not packaged", "sourceStreamIndex": 2},
                    {"kind": "chapters-dropped", "detail": "chapters are not in the package", "sourceStreamIndex": None}]},
                "essence": essence(maxVideoHeight=800), "chapters": [],
            },
            "lostIfOriginalDeleted": ["surround", "chapters", "audioChannels 6->2", "subtitleLanguages en", "subtitleTracks 1->0"],
        }],
        "processing": {"package": {"status": "done", "at": AT, "attempts": 1, "error": None}},
        "provenance": {"migratedFrom": "scan", "migratedAt": AT},
    }
    meta_dir = os.path.join(mdir, "metadata")
    metadata = {
        "schema": "zaentrum.library.metadata/1", "itemId": mid, **audit(), "type": "movie",
        "titles": {"primary": "Tears of Steel", "original": "Tears of Steel", "sort": "tears of steel", "qualifier": None,
                   "localized": {"en": {"title": "Tears of Steel", "sortTitle": "tears of steel", "tagline": None,
                                        "overview": "A group of warriors and scientists gather at the Oude Kerk in Amsterdam to stage a crucial event from the past."}}},
        "releaseDate": "2012-09-26", "genres": ["Science Fiction"], "tags": [], "rating": None, "contentRating": None,
        "reference": {"runtimeMs": 720000, "runtimeSource": "tmdb"},
        "credits": [{"personId": uid("person", "ian-hubert"), "name": "Ian Hubert", "role": "director", "character": None, "order": 0, "tmdbPerson": None}],
        "collection": None,
        "images": [image(meta_dir, "poster", "poster.jpg", JPEG, "image/jpeg"),
                   image(meta_dir, "backdrop", "backdrop.jpg", JPEG, "image/jpeg"),
                   image(meta_dir, "logo", "logo.png", PNG, "image/png", language="en")],
        "videos": [{"site": "example.org", "key": "tears-of-steel-trailer", "url": None, "name": "Trailer", "kind": "trailer",
                    "language": "en", "durationMs": 60000, "publishedAt": "2012-09-26T00:00:00Z", "origin": "manual"}],
        "curation": {"metadataLocked": False, "lockedFields": [], "notes": None},
        "fieldOrigins": {"titles.primary": "tmdb", "releaseDate": "tmdb", "genres": "tmdb", "credits": "tmdb", "images": "manual"},
    }
    write(os.path.join(meta_dir, "metadata.json"), metadata)
    write(os.path.join(mdir, "manifest.json"), manifest)

    # ---------------------------------------------------------------- series: Example Show (US), one episode in two versions
    sid_series = uid("series", "example-show-us")
    sdir = os.path.join(ROOT, "shows", sid_series[:2], sid_series)
    eid = uid(sid_series, "S01E01")
    edir = os.path.join(sdir, "episodes", eid)
    v_colour, v_bw = uid(eid, "version", "colour"), uid(eid, "version", "black-and-white")
    s_colour, s_bw = uid(eid, "source", "colour"), uid(eid, "source", "black-and-white")
    series_manifest = {
        "schema": "zaentrum.library.manifest/1", "version": 3, "itemId": sid_series, **audit(),
        "type": "series", "title": "Example Show", "year": 2024,
        "externalIds": {}, "match": {"status": "unmatched", "decidedBy": "inferred"},
        "metadata": {"file": "metadata/metadata.json"},
        "series": {"defaultOrdering": "aired",
                   "seasons": [{"number": 1, "tmdbSeason": None, "episodes": [{"itemId": eid, "path": f"episodes/{eid}/", "episode": 1, "episodeEnd": None}]}],
                   "masters": [{"fingerprint": "hevc/main10/10bit/hdr10/3840x2160", "presentation": "colour hdr10", "episodes": [eid]}]},
        "processing": {}, "provenance": {"migratedFrom": "scan", "migratedAt": AT},
    }
    smeta_dir = os.path.join(sdir, "metadata")
    series_meta = {
        "schema": "zaentrum.library.metadata/1", "itemId": sid_series, **audit(), "type": "series",
        "titles": {"primary": "Example Show", "original": None, "sort": "example show", "qualifier": "US",
                   "localized": {"en": {"title": "Example Show", "sortTitle": "example show", "tagline": None,
                                        "overview": "A fictional series used to illustrate the library layout."}}},
        "genres": ["Drama"], "tags": [], "rating": None, "contentRating": None, "credits": [],
        "series": {"status": "returning", "firstAirDate": "2024-01-10", "lastAirDate": "2024-03-06", "network": None,
                   "seasons": [{"number": 1, "tmdbSeason": None, "name": "Season 1", "overview": "The first season.", "airDate": "2024-01-10", "episodeCountReference": 8}]},
        "images": [image(smeta_dir, "poster", "poster.jpg", JPEG, "image/jpeg", series=True),
                   image(smeta_dir, "poster", "season-01-poster.jpg", JPEG, "image/jpeg", season=1, series=True)],
        "curation": {"metadataLocked": False, "lockedFields": [], "notes": None},
        "fieldOrigins": {"titles.primary": "manual", "titles.qualifier": "filename", "series": "manual", "images": "manual"},
    }
    write(os.path.join(smeta_dir, "metadata.json"), series_meta)
    write(os.path.join(sdir, "manifest.json"), series_manifest)

    write(os.path.join(edir, ".complete"), b"packager example 2026-09-01\n")
    write(os.path.join(edir, "versions", v_bw, ".complete"), b"packager example 2026-09-02\n")
    for base in (edir, os.path.join(edir, "versions", v_bw)):
        for r in ("hls/v0", "hls/a0"):
            keep(os.path.join(base, r))
    keep(os.path.join(edir, "hls/a1"))
    for sub in ("0", "1", "2"):
        write(os.path.join(edir, "subs", f"{sub}.vtt"), b"WEBVTT\n")
    hdr_es = essence(maxAudioChannels=6, surround=True, maxVideoHeight=2160, videoBitDepth=10, hdr10Metadata=True)
    episode_tracks = {"commentaryTracks": 1, "subtitleLanguages": ["en"], "subtitleTracks": 3, "sdhSubtitleLanguages": ["en"], "forcedSubtitleLanguages": ["en"]}
    bw_es = essence(maxAudioChannels=6, surround=True, maxVideoHeight=2160, videoBitDepth=10, hdr10Metadata=True)
    ep_pb = playback(2700000, 3840, 2160, True, 6)
    # Characters speak an invented language in a few scenes: the forced track translates only those lines; the full
    # and SDH tracks are there to pick. Which one a viewer sees is up to the player and the viewer's settings.
    ep_pb["renditions"]["audio"].append({"id": "a1", "dir": "hls/a1", "codec": "mp4a.40.2", "language": "eng", "title": "Commentary",
                                         "default": False, "channels": 2, "bitrateBps": 192000, "segments": 15, "visible": True,
                                         "sourceStreamIndex": 2, "sourceChannels": 2, "purpose": "commentary", "purposeFrom": "disposition",
                                         "variant": None, "original": None})
    ep_pb["subtitles"] = [
        {"id": "sub0", "path": "subs/0.vtt", "language": "eng", "title": "Forced", "default": False, "forced": True, "format": "webvtt",
         "visible": True, "sourceStreamIndex": 3, "purpose": "forced", "purposeFrom": "disposition", "variant": None},
        {"id": "sub1", "path": "subs/1.vtt", "language": "eng", "title": "", "default": False, "forced": False, "format": "webvtt",
         "visible": True, "sourceStreamIndex": 4, "purpose": "dialogue", "purposeFrom": "assumed", "variant": None},
        {"id": "sub2", "path": "subs/2.vtt", "language": "eng", "title": "SDH", "default": False, "forced": False, "format": "webvtt",
         "visible": True, "sourceStreamIndex": 5, "purpose": "sdh", "purposeFrom": "disposition", "variant": None},
    ]
    bw_pb = playback(2700000, 3840, 2160, True, 6)
    episode_manifest = {
        "schema": "zaentrum.library.manifest/1", "version": 3, "itemId": eid, **audit(),
        "type": "episode", "title": "Pilot", "year": 2024,
        "externalIds": {}, "match": {"status": "unmatched", "decidedBy": "inferred"},
        "metadata": {"file": "metadata/metadata.json"},
        "seriesTitle": "Example Show", "seasonNumber": 1, "episodeNumber": 1, "episodeCode": "S01E01",
        "episode": {"seriesId": sid_series, "coordinates": [{"scheme": "aired", "season": 1, "episode": 1, "episodeEnd": None},
                                                             {"scheme": "file", "season": 1, "episode": 1, "episodeEnd": None}]},
        **ep_pb,
        "versions": [
            {"id": v_colour, "path": ".", "label": "HDR10", "primary": True,
             "edition": {"kind": "unknown", "label": None, "decidedBy": "inferred", "evidence": [], "review": None},
             "presentation": {"colour": "colour", "dynamicRange": "hdr10", "stereo3d": "none", "aspectRatio": "16:9"},
             "runtime": {"measuredMs": 2700000, "referenceMs": None, "deltaMs": None},
             "master": {"fingerprint": "hevc/main10/10bit/hdr10/3840x2160", "fidelity": "original"},
             "truth": {"kind": "source", "since": AT, "note": None},
             "sources": [source(edir, s_colour, "Example Show (US) - S01E01 - Pilot.mkv",
                                "tv/Example Show (US)/Season 01/Example Show (US) - S01E01 - Pilot.mkv",
                                [video(0, "hevc", 3840, 2160, 10, "hdr10"), audio(1, "eac3", 6, "5.1"),
                                 audio(2, "aac", 2, "stereo", title="Commentary", commentary=True),
                                 subtitle(3, "Forced", forced=True), subtitle(4, None), subtitle(5, "SDH", sdh=True)],
                                [], {**hdr_es, **episode_tracks}, 2700000, "2160p", "web")],
             "package": {"id": uid(eid, "package", "colour"), "state": "complete", "role": "derived", "sizeBytes": 2700000000, "peakBandwidthBps": 8192000,
                         "fidelity": {"lossless": False, "droppedSourceStreams": [],
                                      "losses": [{"kind": "audio-downmix", "detail": "6ch -> 2ch (a0)", "sourceStreamIndex": 1},
                                                 {"kind": "audio-codec", "detail": "eac3 -> mp4a.40.2", "sourceStreamIndex": 1}]},
                         "essence": {**essence(maxVideoHeight=2160, videoBitDepth=10, hdr10Metadata=False), **episode_tracks}, "chapters": []},
             "lostIfOriginalDeleted": ["surround", "hdr10Metadata", "audioChannels 6->2"]},
            {"id": v_bw, "path": f"versions/{v_bw}/", "label": "Black & White", "primary": False,
             "edition": {"kind": "other", "label": "Black & White", "decidedBy": "human", "decidedAt": AT, "evidence": [], "review": None},
             "presentation": {"colour": "black-and-white",
                              "colourDecision": {"decidedBy": "inferred", "confidence": 0.8,
                                                 "evidence": [{"signal": "content-analysis", "value": {"method": "signalstats SATMAX", "samples": [{"atSec": 600, "satMax": 1.0}]}, "weight": 0.8}]},
                              "dynamicRange": "hdr10", "stereo3d": "none", "aspectRatio": "16:9"},
             "runtime": {"measuredMs": 2700000, "referenceMs": None, "deltaMs": None},
             "master": {"fingerprint": "hevc/main10/10bit/hdr10/3840x2160", "fidelity": "original"},
             "truth": {"kind": "source", "since": AT, "note": None},
             "sources": [source(edir, s_bw, "Example Show (US) - S01E01 - Pilot (Black and White).mkv",
                                "tv/Example Show (US)/Season 01/Example Show (US) - S01E01 - Pilot (Black and White).mkv",
                                [video(0, "hevc", 3840, 2160, 10, "hdr10"), audio(1, "eac3", 6, "5.1")], [], bw_es, 2700000, "2160p", "web")],
             "package": {"id": uid(eid, "package", "black-and-white"), "state": "complete", "role": "derived", "sizeBytes": 2700000000, "peakBandwidthBps": 8192000,
                         "fidelity": {"lossless": False, "droppedSourceStreams": [],
                                      "losses": [{"kind": "audio-downmix", "detail": "6ch -> 2ch (a0)", "sourceStreamIndex": 1}]},
                         "essence": essence(maxVideoHeight=2160, videoBitDepth=10), "chapters": [],
                                  "playback": bw_pb},
             "lostIfOriginalDeleted": ["surround", "hdr10Metadata", "audioChannels 6->2"]},
        ],
        "processing": {}, "provenance": {"migratedFrom": "scan", "migratedAt": AT},
    }
    emeta_dir = os.path.join(edir, "metadata")
    episode_meta = {
        "schema": "zaentrum.library.metadata/1", "itemId": eid, **audit(), "type": "episode",
        "titles": {"primary": "Pilot", "original": None, "sort": "pilot", "qualifier": None,
                   "localized": {"en": {"title": "Pilot", "sortTitle": "pilot", "tagline": None, "overview": "The first episode."}}},
        "genres": [], "tags": [], "rating": None, "credits": [],
        "episode": {"airDate": "2024-01-10"},
        "images": [image(emeta_dir, "still", "still.jpg", JPEG, "image/jpeg")],
        "curation": {"metadataLocked": False, "lockedFields": [], "notes": None},
        "fieldOrigins": {"titles.primary": "manual", "episode.airDate": "manual", "images": "manual"},
    }
    write(os.path.join(emeta_dir, "metadata.json"), episode_meta)
    write(os.path.join(edir, "manifest.json"), episode_manifest)
    print("examples written to", os.path.normpath(ROOT))


if __name__ == "__main__":
    main()
