#!/usr/bin/env python3
"""Build library item folders (manifest.json + metadata/) from a legacy catalog.

Reads extracted inputs from a directory and writes a staging tree in the target layout,
plus a plan of storage operations for the large files:

  library/movies/<aa>/<movieId>/manifest.json            identity, reference ids, playback fields, versions
                               metadata/metadata.json   every text, and the image list
                               metadata/poster.jpg ...  the images themselves
                               source/<sourceId>/ffprobe.json   verbatim probe of the original
                               <original file>, hls/ subs/ trickplay/ .complete   (moved in by the plan)
  library/shows/<aa>/<seriesId>/manifest.json, metadata/ (series poster, logo, season-NN-poster.jpg)
                               episodes/<episodeId>/manifest.json, metadata/, source/, hls/ ...

The library is read by machines: nothing but <category>/<aa>/<id> item folders, no views, no links.
The plan (links.tsv → plan.tsv) moves each file into place — a rename on the same filesystem, a copy
otherwise — so every file in the library is an ordinary, independent file.

A value the export does not hold stays empty rather than guessed. Where automation cannot decide
(an unmatched item, an edition, a black-and-white call from few samples, a disputed episode match,
a disc image that was never probed) the document carries a `review` and report.json lists the item.
probe.at and TMDB image fetchedAt are the modification times of probes.jsonl and tmdb_images.json:
keep those times when copying an export (cp -p, rsync -t).

Inputs (in --inputs):
  paths.tsv          itemId, type, parentId, season, episode, sourcePath, sourceSize, packageManifestPath
  catalog.json       rows from the legacy catalog tables
  probes.jsonl       ffprobe output and file stat per source
  fetched.jsonl      package manifests and sidecars per item
  artwork.jsonl      artwork bytes (base64) per item and kind
  colour.tsv         itemId, "sec:satmax,sec:satmax,..."  (peak chroma per sampled frame)
  tmdb.json          optional re-sync results keyed by item id
  tmdb_images.json   optional: season posters, episode stills, logos, person ids (bytes in images/)
"""
import argparse, base64, collections, datetime, hashlib, json, os, re, sys, uuid

NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://zaentrum.github.io/schemas/library")
NOW = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

# ---------------------------------------------------------------- helpers
def ts(v):
    if not v:
        return None
    s = str(v).replace(" ", "T")
    if not re.search(r"(Z|[+-]\d\d:?\d\d)$", s):
        s += "Z"
    return s

def did(*parts):
    return str(uuid.uuid5(NS, ":".join(parts)))

def shard(i):
    return i[:2]

def sha(b):
    return "sha256:" + hashlib.sha256(b).hexdigest()

ISO639 = {
    "eng": "en", "ger": "de", "deu": "de", "fre": "fr", "fra": "fr", "ita": "it", "spa": "es", "jpn": "ja",
    "chi": "zh", "zho": "zh", "dut": "nl", "nld": "nl", "por": "pt", "rus": "ru", "pol": "pl", "cze": "cs",
    "ces": "cs", "ukr": "uk", "hin": "hi", "kor": "ko", "swe": "sv", "nor": "no", "nob": "nb", "dan": "da",
    "fin": "fi", "tur": "tr", "ara": "ar", "heb": "he", "hun": "hu", "gre": "el", "ell": "el", "rum": "ro",
    "ron": "ro", "tha": "th", "vie": "vi", "ind": "id", "may": "ms", "msa": "ms", "cat": "ca", "hrv": "hr",
    "slv": "sl", "slo": "sk", "slk": "sk", "srp": "sr", "bul": "bg", "est": "et", "lav": "lv", "lit": "lt",
    "ice": "is", "isl": "is", "fil": "fil", "tel": "te", "tam": "ta", "und": "und", "zxx": "zxx", "mul": "mul",
    "baq": "eu", "eus": "eu", "glg": "gl", "kan": "kn", "mal": "ml", "ben": "bn", "mar": "mr", "urd": "ur", "per": "fa",
    "fas": "fa", "wel": "cy", "cym": "cy", "arm": "hy", "hye": "hy", "geo": "ka", "kat": "ka", "mac": "mk", "mkd": "mk",
    "alb": "sq", "sqi": "sq", "tgl": "tl", "lao": "lo", "khm": "km", "bur": "my", "mya": "my", "sin": "si", "nep": "ne",
    "pan": "pa", "guj": "gu", "tib": "bo", "bod": "bo", "mon": "mn", "kaz": "kk", "uzb": "uz", "aze": "az", "bel": "be",
    "bos": "bs", "gle": "ga", "bre": "br", "ltz": "lb", "afr": "af", "swa": "sw", "zul": "zu", "xho": "xh", "amh": "am",
    "som": "so", "hau": "ha", "yor": "yo", "ibo": "ig", "epo": "eo", "lat": "la", "yid": "yi", "jav": "jv", "sun": "su",
    "mao": "mi", "mri": "mi", "grn": "gn", "que": "qu", "nno": "nn", "fao": "fo", "kal": "kl", "iku": "iu", "tat": "tt",
    "kur": "ku", "pus": "ps", "tuk": "tk", "kir": "ky", "tgk": "tg", "mlt": "mt", "roh": "rm", "sco": "sco", "haw": "haw",
}

def primary_language(raw):
    """The primary language subtag, for matching audio and subtitles (Norwegian Bokmål and Nynorsk count as Norwegian)."""
    code = (lang(raw)[0] or "und").split("-")[0]
    return {"nb": "no", "nn": "no"}.get(code, code)

VARIANT_WORDS = re.compile(r"\b(Latin[ -]?American|LatAm|European|Castilian|Canadian|Brazilian|Portugal|Simplified|Traditional|"
                           r"Cantonese|Mandarin|Flemish|Swiss|Austrian|Mexican|Hong Kong|Taiwanese|Belgian|Qu[eé]b[eé]cois)\b", re.I)

def title_variant(title):
    m = VARIANT_WORDS.search(title or "")
    return m.group(1) if m else None

def lang(raw):
    if not raw:
        return None, None
    r = str(raw).strip().lower()
    if re.fullmatch(r"[a-z]{2}(-[a-z0-9]{2,8})?", r):
        return r, None
    if r in ISO639:
        return ISO639[r], r
    if re.fullmatch(r"[a-z]{3}", r):
        return r, None
    return "und", r

def num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None

def ratio(v):
    return v if v and v not in ("0:1", "N/A") else None

# ---------------------------------------------------------------- stream normalisation
TEXT_SUBS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text", "arib_caption"}
IMAGE_SUBS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub"}
LOSSLESS_AUDIO = {"truehd", "mlp", "flac", "alac", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_bluray", "pcm_dvd", "wavpack", "ape", "tta"}

CLAIM_CODECS = [
    ("truehd", r"truehd|true-hd|true hd"),
    ("dts-hd ma", r"dts[- .]?hd[- .]?(ma|master)"),
    ("dts", r"\bdts\b"),
    ("e-ac-3", r"e-?ac-?3|dd\+|ddp|dolby digital plus"),
    ("ac-3", r"\bac-?3\b|dolby digital(?! plus)"),
    ("flac", r"\bflac\b"),
    ("opus", r"\bopus\b"),
    ("aac", r"\baac\b"),
]

def claim(title):
    if not title:
        return None
    t = title.lower()
    codec = next((c for c, pat in CLAIM_CODECS if re.search(pat, t)), None)
    m = re.search(r"\b([1-9])[.,]([0-2])\b", t)
    channels = f"{m.group(1)}.{m.group(2)}" if m else None
    if not codec and not channels:
        return None
    return {"codec": codec, "channels": channels}

def claim_contradicts(c, codec, channels):
    if not c:
        return False
    actual = (codec or "").lower()
    family = {"truehd": ("truehd", "mlp"), "dts-hd ma": ("dts",), "dts": ("dts",), "e-ac-3": ("eac3",),
              "ac-3": ("ac3",), "flac": ("flac",), "opus": ("opus",), "aac": ("aac",)}
    bad = False
    if c["codec"] and not any(actual.startswith(x) for x in family.get(c["codec"], ())):
        bad = True
    if c["channels"]:
        a, b = c["channels"].split(".")
        if int(a) + int(b) != channels:
            bad = True
    return bad

def dispositions(s):
    d = s.get("disposition") or {}
    t = ((s.get("tags") or {}).get("title") or "").lower()
    out = {
        "default": bool(d.get("default")),
        "forced": bool(d.get("forced")),
        "original": bool(d.get("original")),
        "dub": bool(d.get("dub")),
        "commentary": bool(d.get("comment")),
        "hearingImpaired": bool(d.get("hearing_impaired")),
        "visualImpaired": bool(d.get("visual_impaired")),
    }
    if d.get("attached_pic"):
        out["attachedPic"] = True
    for raw, key in (("lyrics", "lyrics"), ("captions", "captions"), ("descriptions", "descriptions")):
        if d.get(raw):
            out[key] = True
    return out

def subtitle_purpose(s):
    """What a subtitle track is for, and what that rests on: the stream flags first, then the title."""
    d = s.get("disposition") or {}
    t = ((s.get("tags") or {}).get("title") or "").lower()
    if d.get("forced"):
        return "forced", "disposition"
    if d.get("hearing_impaired") or d.get("captions"):
        return "sdh", "disposition"
    if d.get("comment"):
        return "commentary", "disposition"
    if d.get("lyrics"):
        return "lyrics", "disposition"
    if re.search(r"\bforced\b", t) and not re.search(r"\b(non|not|no|un)[- ]?forced\b", t):
        return "forced", "title"
    if re.search(r"\bsigns?\b.{0,12}\bsongs?\b|\bsigns only\b", t):
        return "signs-songs", "title"
    if re.search(r"\bsdh\b|\bcc\b|hearing|closed caption", t):
        return "sdh", "title"
    if re.search(r"commentary|kommentar|commentaire|comentario|commento|commentaar", t):
        return "commentary", "title"
    if re.search(r"\blyrics\b|karaoke", t):
        return "lyrics", "title"
    return "dialogue", "assumed"

def audio_purpose(s):
    d = s.get("disposition") or {}
    t = ((s.get("tags") or {}).get("title") or "").lower()
    if d.get("comment"):
        return "commentary", "disposition"
    if d.get("visual_impaired") or d.get("descriptions"):
        return "description", "disposition"
    if re.search(r"commentary|kommentar|commentaire|comentario|commento|commentaar", t):
        return "commentary", "title"
    if re.search(r"audio description|descriptive (video|audio)|audiodeskription|audiodescription", t):
        return "description", "title"
    return "main", "assumed"

def hdr_info(s):
    trc = s.get("color_transfer")
    side = s.get("side_data_list") or []
    dovi = next((x for x in side if "DOVI" in (x.get("side_data_type") or "")), None)
    mastering = next((x for x in side if "Mastering display" in (x.get("side_data_type") or "")), None)
    cll = next((x for x in side if "Content light" in (x.get("side_data_type") or "")), None)
    if dovi:
        fmt = "dolby-vision"
    elif trc == "smpte2084":
        fmt = "hdr10"
    elif trc == "arib-std-b67":
        fmt = "hlg"
    else:
        fmt = "sdr"
    out = {"format": fmt, "masteringDisplay": None, "contentLightLevel": None, "dolbyVision": None}
    if mastering:
        out["masteringDisplay"] = {k: v for k, v in mastering.items() if k != "side_data_type"}
    if cll:
        out["contentLightLevel"] = {k: v for k, v in cll.items() if k != "side_data_type"}
    if dovi:
        out["dolbyVision"] = {
            "profile": num(dovi.get("dv_profile")) or 0,
            "level": num(dovi.get("dv_level")) or 0,
            "rpuPresent": bool(dovi.get("rpu_present_flag")),
            "elPresent": bool(dovi.get("el_present_flag")),
            "blPresent": bool(dovi.get("bl_present_flag")),
            "blCompatibilityId": num(dovi.get("dv_bl_signal_compatibility_id")) or 0,
        }
    return out

def stereo3d(s):
    tags = s.get("tags") or {}
    mode = (tags.get("stereo_mode") or tags.get("STEREO_MODE") or "").lower()
    for x in s.get("side_data_list") or []:
        if "Stereo 3D" in (x.get("side_data_type") or ""):
            mode = (x.get("type") or mode or "").lower()
    if not mode or mode in ("mono", "2d"):
        return None
    return mode

def bit_depth(s):
    b = num(s.get("bits_per_raw_sample"))
    if b:
        return b
    pf = s.get("pix_fmt") or ""
    m = re.search(r"p(\d{2})(le|be)?$", pf)
    return int(m.group(1)) if m else (8 if pf else None)

def norm_stream(s):
    t = s.get("codec_type")
    tags = s.get("tags") or {}
    language, raw = lang(tags.get("language"))
    base = {
        "index": s["index"],
        "codec": s.get("codec_name"),
        "language": language,
        "languageRaw": raw,
        "title": tags.get("title"),
        "dispositions": dispositions(s),
    }
    if t == "video":
        encoder = tags.get("ENCODER") or tags.get("encoder")
        base.update({
            "type": "video",
            "profile": s.get("profile"),
            "level": s.get("level") if isinstance(s.get("level"), (int, float)) and s.get("level") >= 0 else None,
            "bitDepth": bit_depth(s),
            "pixelFormat": s.get("pix_fmt"),
            "width": s.get("width"),
            "height": s.get("height"),
            "sampleAspectRatio": ratio(s.get("sample_aspect_ratio")),
            "displayAspectRatio": ratio(s.get("display_aspect_ratio")),
            "frameRate": s.get("avg_frame_rate") if s.get("avg_frame_rate") not in (None, "0/0") else s.get("r_frame_rate"),
            "fieldOrder": s.get("field_order"),
            "bitrate": num(s.get("bit_rate")),
            "colour": {
                "primaries": s.get("color_primaries"),
                "transfer": s.get("color_transfer"),
                "matrix": s.get("color_space"),
                "range": s.get("color_range"),
            },
            "hdr": hdr_info(s),
            "stereo3d": stereo3d(s),
            "closedCaptions": bool(s.get("closed_captions")),
            "encoder": encoder,
        })
        return base
    if t == "audio":
        codec = (s.get("codec_name") or "").lower()
        profile = s.get("profile") or ""
        channels = num(s.get("channels")) or 1
        c = claim(tags.get("title"))
        lossless = codec in LOSSLESS_AUDIO or (codec == "dts" and "MA" in profile)
        obj = "atmos" if "atmos" in profile.lower() else "dts-x" if "dts:x" in profile.lower() else "none"
        if c is not None:
            c["contradictsActual"] = claim_contradicts(c, codec, channels)
        base.update({
            "type": "audio",
            "profile": profile or None,
            "channels": channels,
            "channelLayout": s.get("channel_layout"),
            "sampleRate": num(s.get("sample_rate")),
            "bitDepth": num(s.get("bits_per_raw_sample")) or None,
            "bitrate": num(s.get("bit_rate")) or num(tags.get("BPS")),
            "lossless": lossless,
            "objectAudio": obj,
            "titleClaim": c,
            "encoder": tags.get("ENCODER") or tags.get("encoder"),
        })
        base["purpose"], base["purposeFrom"] = audio_purpose(s)
        base["variant"] = title_variant(tags.get("title"))
        return base
    if t == "subtitle":
        codec = (s.get("codec_name") or "").lower()
        title = tags.get("title") or ""
        variant = title_variant(title)
        events = next((num(v) for k, v in tags.items() if k.upper().startswith("NUMBER_OF_FRAMES")), None)
        base.update({
            "type": "subtitle",
            "form": "image" if codec in IMAGE_SUBS else "text",
            "styled": codec in ("ass", "ssa"),
            "variant": variant,
            "events": events,
        })
        base["purpose"], base["purposeFrom"] = subtitle_purpose(s)
        return base
    if t == "attachment":
        fname = tags.get("filename")
        mime = tags.get("mimetype")
        role = "font" if (mime and "font" in mime) or (fname and re.search(r"\.(ttf|otf|ttc)$", fname, re.I)) \
            else "cover" if (mime and mime.startswith("image/")) else "other"
        base.update({"type": "attachment", "filename": fname, "mimetype": mime, "role": role})
        return base
    base.update({"type": "data"})
    return base

def infer_forced_by_size(streams):
    """An untitled, unflagged subtitle with a small fraction of the events of the full track in its language is a
    forced track: it translates a few lines, not the film."""
    subs = [x for x in streams if x["type"] == "subtitle"]
    for x in subs:
        if x["purposeFrom"] != "assumed" or not x.get("events"):
            continue
        peers = [y["events"] for y in subs if y is not x and y.get("events") and primary_language(y.get("language")) == primary_language(x.get("language"))]
        if peers and max(peers) >= 300 and x["events"] < 0.1 * max(peers):
            x["purpose"], x["purposeFrom"] = "forced", "content"

# ---------------------------------------------------------------- essence
def source_essence(streams, chapters):
    v = [x for x in streams if x["type"] == "video" and not x["dispositions"].get("attachedPic")]
    a = [x for x in streams if x["type"] == "audio"]
    sub = [x for x in streams if x["type"] == "subtitle"]
    v0 = v[0] if v else {}
    hdr = (v0.get("hdr") or {}) if v0 else {}
    return {
        "maxAudioChannels": max([x["channels"] for x in a], default=0),
        "surround": any(x["channels"] > 2 for x in a),
        "losslessAudio": any(x.get("lossless") for x in a),
        "objectAudio": any(x.get("objectAudio") in ("atmos", "dts-x") for x in a),
        "maxVideoHeight": v0.get("height"),
        "videoBitDepth": v0.get("bitDepth"),
        "hdr10Metadata": bool(hdr.get("masteringDisplay") or hdr.get("contentLightLevel")),
        "dolbyVision": hdr.get("format") == "dolby-vision",
        "stereo3d": bool(v0.get("stereo3d")),
        "interlaced": (v0.get("fieldOrder") or "progressive") not in ("progressive", "unknown", None),
        "audioLanguages": sorted({x["language"] for x in a if x.get("language")}),
        "subtitleLanguages": sorted({x["language"] for x in sub if x.get("language")}),
        "subtitleTracks": len(sub),
        "imageSubtitles": any(x.get("form") == "image" for x in sub),
        "styledSubtitles": any(x.get("styled") for x in sub),
        "fonts": any(x["type"] == "attachment" and x.get("role") == "font" for x in streams),
        "chapters": bool(chapters),
        "commentaryTracks": sum(1 for x in a if x.get("purpose") == "commentary"),
        **track_essence(a, sub, v0.get("closedCaptions", False)),
    }

def track_essence(audio, subs, closed_captions):
    """The accessibility and translation properties shared by originals and packages."""
    def langs(items, purposes):
        return sorted({lang(x.get("language"))[0] for x in items if x.get("purpose") in purposes and x.get("language")})
    return {
        "commentarySubtitles": sum(1 for x in subs if x.get("purpose") == "commentary"),
        "audioDescriptionTracks": sum(1 for x in audio if x.get("purpose") == "description"),
        "sdhSubtitleLanguages": langs(subs, ("sdh",)),
        "forcedSubtitleLanguages": langs(subs, ("forced", "signs-songs")),
        "closedCaptions": bool(closed_captions),
    }

def package_essence(man):
    ren = man.get("renditions") or {}
    vids = ren.get("video") or []; auds = ren.get("audio") or []; subs = man.get("subtitles") or []
    v0 = vids[0] if vids else {}
    codec = v0.get("codec") or ""
    depth = 10 if codec.startswith(("hev1.2", "hvc1.2")) else (8 if codec else None)
    return {
        "maxAudioChannels": max([x.get("channels") or 0 for x in auds], default=0),
        "surround": any((x.get("channels") or 0) > 2 for x in auds),
        "losslessAudio": False,
        "objectAudio": False,
        "maxVideoHeight": v0.get("height"),
        "videoBitDepth": depth,
        "hdr10Metadata": False,
        "dolbyVision": False,
        "stereo3d": False,
        "interlaced": False,
        "audioLanguages": sorted({lang(x.get("language"))[0] for x in auds if x.get("language")}),
        "subtitleLanguages": sorted({lang(x.get("language"))[0] for x in subs if x.get("language")}),
        "subtitleTracks": len(subs),
        "imageSubtitles": any((x.get("format") or "") in ("pgs", "sup", "vobsub", "dvb") for x in subs),
        "styledSubtitles": False,
        "fonts": False,
        "chapters": False,
        "commentaryTracks": sum(1 for x in auds if x.get("purpose") == "commentary"),
        **track_essence(auds, subs, False),
    }

def essence_gap(src, pkg, chapters_kept=False):
    """What deleting the original would lose: every property the source has that the package lacks. Chapter marks
    are not lost when the version keeps them."""
    gap = []
    for k in ("surround", "losslessAudio", "objectAudio", "hdr10Metadata", "dolbyVision", "stereo3d", "imageSubtitles", "styledSubtitles", "fonts"):
        if src.get(k) and not pkg.get(k):
            gap.append(k)
    if src.get("chapters") and not chapters_kept:
        gap.append("chapters")
    if (src.get("maxAudioChannels") or 0) > (pkg.get("maxAudioChannels") or 0):
        gap.append(f"audioChannels {src['maxAudioChannels']}->{pkg['maxAudioChannels']}")
    if (src.get("maxVideoHeight") or 0) > (pkg.get("maxVideoHeight") or 0):
        gap.append(f"videoHeight {src['maxVideoHeight']}->{pkg['maxVideoHeight']}")
    if (src.get("videoBitDepth") or 0) > (pkg.get("videoBitDepth") or 0):
        gap.append(f"bitDepth {src['videoBitDepth']}->{pkg['videoBitDepth']}")
    for k in ("audioLanguages", "subtitleLanguages"):
        missing = sorted(set(src.get(k) or []) - set(pkg.get(k) or []))
        if missing:
            gap.append(f"{k} {','.join(missing)}")
    if (src.get("subtitleTracks") or 0) > (pkg.get("subtitleTracks") or 0):
        gap.append(f"subtitleTracks {src['subtitleTracks']}->{pkg['subtitleTracks']}")
    if (src.get("commentaryTracks") or 0) > (pkg.get("commentaryTracks") or 0):
        gap.append(f"commentaryTracks {src['commentaryTracks']}->{pkg['commentaryTracks']}")
    for k in ("sdhSubtitleLanguages", "forcedSubtitleLanguages"):
        missing = sorted(set(src.get(k) or []) - set(pkg.get(k) or []))
        if missing:
            gap.append(f"{k} {','.join(missing)}")
    for k in ("commentarySubtitles", "audioDescriptionTracks"):
        if (src.get(k) or 0) > (pkg.get(k) or 0):
            gap.append(f"{k} {src[k]}->{pkg[k]}")
    if src.get("closedCaptions") and not pkg.get("closedCaptions"):
        gap.append("closedCaptions")
    return gap

# ---------------------------------------------------------------- filename labels
# A medium token only counts when it is joined to a resolution ("<medium>-1080p"), so a title word such as
# "Web" never becomes a label. The last match wins: quality tokens end a filename.
QUALITY = re.compile(r"\b(Remux|Blu-?ray|DVD|WEB[A-Za-z-]*|HDTV|SDTV)[- ](\d{3,4}p)\b|\b(\d{3,4}p)\b", re.I)

def medium_of(token):
    t = token.lower()
    return "web" if t.startswith("web") else "disc" if t in ("remux", "bluray", "blu-ray", "dvd") else \
        "broadcast" if t in ("hdtv", "sdtv") else "unknown"

def quality_match(text):
    hits = list(QUALITY.finditer(text or ""))
    return hits[-1] if hits else None
EDITION_WORDS = [
    ("directors-cut", r"director'?s?[ ._-]?cut"),
    ("extended", r"\bextended\b"),
    ("unrated", r"\bunrated\b"),
    ("theatrical", r"\btheatrical\b"),
    ("special-edition", r"special[ ._-]?edition"),
    ("remastered", r"\bremaster(ed)?\b"),
    ("restored", r"\brestor(ed|ation)\b"),
    ("imax", r"\bimax\b"),
    ("final-cut", r"final[ ._-]?cut"),
]

def edition_word(text):
    if not text:
        return None
    for kind, pat in EDITION_WORDS:
        if re.search(pat, text, re.I):
            return kind
    return None

def labels(name, folder):
    m = quality_match(os.path.splitext(name)[0])
    medium = medium_of(m.group(1)) if m and m.group(1) else None
    fold_ed = None
    fm = re.search(r" - ([^()]+?) \(\d{4}\)$", folder or "")
    if fm and edition_word(fm.group(1)):
        fold_ed = fm.group(1).strip()
    return {
        "quality": m.group(0) if m else None,
        "medium": medium,
        "resolution": ((m.group(2) or m.group(3)) if m else None),
        "edition": edition_word(name),
        "folderEdition": fold_ed,
    }

def file_title(name):
    """The episode title an original filename carries after its SxxEyy code, without the quality token."""
    stem = os.path.splitext(name)[0]
    m = re.search(r"S\d{1,3}E\d{1,4}(?:-?E\d{1,4})?\s*-\s*(.+)$", stem, re.I)
    if not m:
        return None
    title = m.group(1)
    q = quality_match(title)
    if q:
        title = title[:q.start()]
    return title.strip(" -._") or None

def norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()

def same_title(a, b):
    """Equal after normalisation, or one contains the other as whole words when the shorter is specific enough."""
    a, b = norm_title(a), norm_title(b)
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    return len(short) >= 12 and f" {short} " in f" {long_} "

def file_coords(name):
    m = re.search(r"S(\d{1,3})E(\d{1,4})(?:-?E(\d{1,4}))?", name, re.I)
    if not m:
        return None
    return {"scheme": "file", "season": int(m.group(1)), "episode": int(m.group(2)),
            "episodeEnd": int(m.group(3)) if m.group(3) else None}

# ---------------------------------------------------------------- images
def image_size(b):
    """Width and height from a JPEG or PNG header, without decoding the image."""
    if b[:8] == b"\x89PNG\r\n\x1a\n" and len(b) >= 24:
        return int.from_bytes(b[16:20], "big"), int.from_bytes(b[20:24], "big")
    if b[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(b):
            if b[i] != 0xFF:
                i += 1
                continue
            marker = b[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                return int.from_bytes(b[i + 7:i + 9], "big"), int.from_bytes(b[i + 5:i + 7], "big")
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            i += 2 + int.from_bytes(b[i + 2:i + 4], "big")
    return None, None

EXT = {"image/png": "png", "image/webp": "webp", "image/jpeg": "jpg"}

# ---------------------------------------------------------------- build
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", required=True)
    ap.add_argument("--out", required=True, help="staging directory; 'library/' is created inside")
    ap.add_argument("--library-root", required=True, help="container path prefix of the source library, e.g. /var/lib/katalog/media")
    ap.add_argument("--packages-root", required=True, help="container path prefix of packages, e.g. /var/lib/katalog/packages")
    ap.add_argument("--items", help="comma-separated movie or series ids to build; default: every item")
    ap.add_argument("--originals", choices=("place", "leave"), default="place",
                    help="place: plan each original into its version folder; leave: keep originals in the source library "
                         "(file.path null), so a version folder holds only its package")
    args = ap.parse_args()
    I = args.inputs

    rows = [l.rstrip("\n").split("\t") for l in open(os.path.join(I, "paths.tsv")) if l.strip()]
    paths = {r[0]: {"type": r[1], "parent": r[2] or None, "source": r[5] or None, "pkg": (r[7] if len(r) > 7 else "") or None} for r in rows}
    cat = json.load(open(os.path.join(I, "catalog.json")))
    probes = {j["itemId"]: j for j in map(json.loads, open(os.path.join(I, "probes.jsonl"))) if j}
    fetched = {j["itemId"]: j for j in map(json.loads, open(os.path.join(I, "fetched.jsonl"))) if j}
    art_bytes = collections.defaultdict(list)
    for l in open(os.path.join(I, "artwork.jsonl")):
        if l.strip():
            a = json.loads(l)
            art_bytes[a["item_id"]].append(a)
    colour = {}
    cpath = os.path.join(I, "colour.tsv")
    if os.path.exists(cpath):
        for l in open(cpath):
            p = l.rstrip("\n").split("\t")
            if len(p) == 2:
                samples = []
                for part in p[1].split(","):
                    sec, _, val = part.partition(":")
                    samples.append({"atSec": num(sec), "satMax": (float(val) if val not in ("", "null") else None)})
                colour[p[0]] = samples
    def optional_json(name):
        p = os.path.join(I, name)
        return json.load(open(p)) if os.path.exists(p) else {}
    tmdb = optional_json("tmdb.json")
    extra = optional_json("tmdb_images.json")     # season posters, episode stills, logos, person ids
    blobs = os.path.join(I, "images")

    def mtime_of(name):
        p = os.path.join(I, name)
        if not os.path.exists(p):
            return None
        return datetime.datetime.fromtimestamp(int(os.path.getmtime(p)), datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    PROBED_AT = mtime_of("probes.jsonl") or NOW          # when the originals were probed, not when this tool ran
    FETCHED_AT = mtime_of("tmdb_images.json") or NOW

    def group(name, key="item_id"):
        g = collections.defaultdict(list)
        for r in cat.get(name) or []:
            g[r[key]].append(r)
        return g

    items = {r["id"]: r for r in cat["items"]}
    ext = group("externalids")
    people = group("people")
    genres = group("genres")
    tags = group("tags")
    artmeta = group("artwork")
    chapters = group("chapters")
    segments = group("segments")
    steps = group("steps")
    trailers = group("trailers")
    packaged_rows = {r["item_id"]: r for r in cat.get("playback") or [] if r.get("kind") == "packaged"}

    lib = os.path.join(args.out, "library")
    plan = []           # files to move into the library: rename on one filesystem, copy otherwise
    report = collections.defaultdict(list)
    written = collections.Counter()

    def write_json(rel, obj):
        p = os.path.join(lib, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        data = (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode()
        with open(p, "wb") as f:
            f.write(data)
        written[obj.get("schema", "raw") if isinstance(obj, dict) else "raw"] += 1
        return sha(data)

    def write_bytes(rel, data):
        p = os.path.join(lib, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
        written["image"] += 1

    def audit(created, updated):
        if created == NOW:
            report["created-at-missing"].append("an item without a catalog creation time got the migration time")
        return {"rev": 1, "createdAt": created, "createdBy": "library-migrate", "updatedAt": updated, "updatedBy": "library-migrate"}

    def ext_ids(item):
        e = {x["source"]: x["externalid"] for x in ext.get(item["id"], [])}
        out = {}
        if item["type"] == "movie":
            if e.get("tmdb"): out["tmdbMovie"] = e["tmdb"]
        elif item["type"] == "series":
            if e.get("tmdb"): out["tmdbTv"] = e["tmdb"]
        elif item["type"] == "episode":
            if e.get("tmdb-episode"): out["tmdbEpisode"] = e["tmdb-episode"]
            parent = {x["source"]: x["externalid"] for x in ext.get(item["parent_id"] or "", [])}
            if parent.get("tmdb"): out["tmdbTv"] = parent["tmdb"]
        if e.get("imdb") and re.fullmatch(r"tt\d+", e["imdb"]): out["imdb"] = e["imdb"]
        if e.get("tvdb") and e["tvdb"].isdigit(): out["tvdb"] = e["tvdb"]
        t = tmdb.get(item["id"]) or {}
        for k in ("tmdbMovie", "tmdbTv", "tmdbCollection", "imdb", "tvdb", "tmdbEpisode"):
            if t.get(k) and k not in out: out[k] = t[k]
        if item["type"] == "episode":
            season = ((extra.get(item["parent_id"]) or {}).get("seasons") or {}).get(str(item["seasonnumber"])) or {}
            if season.get("tmdbSeason"): out["tmdbSeason"] = season["tmdbSeason"]
            if (extra.get(item["id"]) or {}).get("tmdbEpisode") and "tmdbEpisode" not in out:
                out["tmdbEpisode"] = extra[item["id"]]["tmdbEpisode"]
        if item["type"] == "movie" and out.get("tmdbCollection") is None:
            out.pop("tmdbCollection", None)
        return out

    def match(item, ids):
        t = tmdb.get(item["id"]) or {}
        override = t.get("matchOverride")
        if override:
            return {"status": "manual", "decidedBy": "human", "decidedAt": NOW, "confidence": 1.0,
                    "evidence": [{"signal": "manual", "value": {override["key"]: override["value"]}, "weight": 1.0, "note": override["note"]}]}
        if item["type"] == "episode" and (tmdb.get(item["parent_id"] or "") or {}).get("matchOverride"):
            return {"status": "manual", "decidedBy": "human", "decidedAt": NOW, "confidence": 1.0,
                    "evidence": [{"signal": "manual", "value": "inherited from the series match", "weight": 1.0}]}
        matched = bool(ids.get("tmdbMovie") or ids.get("tmdbTv"))
        if item["type"] == "episode" and matched:
            disputed = episode_dispute(item, ids)
            if disputed:
                return disputed
        if not matched:
            report["unmatched-needs-review"].append(f"{item['type']}:{item['title']}")
            return {"status": "unmatched", "decidedBy": "legacy-catalog",
                    "review": "No reference id: find the item in the reference database and set externalIds, then re-sync its metadata."}
        return {"status": "matched", "decidedBy": "legacy-catalog"}

    def episode_dispute(item, ids):
        """The catalog matched an episode whose title the original filename contradicts, while the reference database
        lists the filename's title under another episode of the same season."""
        name = os.path.basename(paths[item["id"]]["source"] or "")
        claimed = file_title(name)
        if not claimed or same_title(claimed, item["title"]):
            return None
        season = ((extra.get(item["parent_id"]) or {}).get("seasons") or {}).get(str(item["seasonnumber"])) or {}
        hits = [(int(n), e) for n, e in (season.get("episodes") or {}).items() if same_title(claimed, e.get("name"))]
        if len(hits) != 1:
            return None
        n, e = hits[0]
        if e["tmdbEpisode"] == ids.get("tmdbEpisode"):
            return None
        review = (f"The original filename names '{claimed}', which the reference database lists as S{item['seasonnumber']:02d}E{n:02d} "
                  f"(tmdbEpisode {e['tmdbEpisode']}), but the catalog matched S{item['seasonnumber']:02d}E{item['episodenumber']:02d} "
                  f"'{item['title']}' (tmdbEpisode {ids.get('tmdbEpisode')}). Confirm, re-number and re-sync.")
        report["episode-match-disputed"].append(f"{item['title']}: {review}")
        return {"status": "disputed", "decidedBy": "inferred", "decidedAt": NOW, "confidence": 0.8,
                "evidence": [{"signal": "filename", "value": name, "weight": 0.8},
                             {"signal": "tmdb", "value": {"season": item["seasonnumber"], "episode": n, "tmdbEpisode": e["tmdbEpisode"], "name": e.get("name")}, "weight": 0.8},
                             {"signal": "catalog", "value": {"season": item["seasonnumber"], "episode": item["episodenumber"], "tmdbEpisode": ids.get("tmdbEpisode"), "title": item["title"]}, "weight": 0.5}],
                "review": review}

    def processing(item):
        out = {}
        for s in steps.get(item["id"], []):
            st = {"done": "done", "failed": "failed", "skipped": "skipped", "not_applicable": "not-applicable",
                  "pending": "pending", "in_progress": "pending"}.get(s["status"], "pending")
            out[s["step"]] = {"status": st, "at": ts(s["finishedat"]), "attempts": s["attempts"], "error": s["error"]}
        return out

    # ------------------------------------------------------------ metadata/ (texts + images)
    def credits(item, origins):
        tmdb_people = collections.defaultdict(list)
        owner = item["id"] if item["type"] != "episode" else item["parent_id"]
        for p in (extra.get(owner) or {}).get("people") or []:
            tmdb_people[p["name"].casefold()].append(p)
        chars = (tmdb.get(item["id"]) or {}).get("characters") or {}
        out, unmatched = [], 0
        for n, p in enumerate(people.get(item["id"], [])):
            role = p["role"] or "actor"
            cands = tmdb_people.get(p["name"].casefold(), [])
            hit = next((c for c in cands if c["role"] == role), None) or (cands[0] if len(cands) == 1 else None)
            if not hit:
                unmatched += 1
            character = chars.get(p["name"]) or (hit or {}).get("character") if role == "actor" else None
            out.append({"personId": p["person_id"], "name": p["name"], "role": role, "character": character or None,
                        "order": n, "tmdbPerson": hit["tmdbPerson"] if hit else None})
        if out:
            origins["credits"] = "legacy-catalog"
            if any(c["tmdbPerson"] for c in out):
                origins["credits.tmdbPerson"] = "tmdb"
        if unmatched:
            report["credits-without-tmdb-person"].append(f"{item['type']}:{item['title']}: {unmatched} of {len(out)}")
        return out

    def images(item, mdir, origins):
        """Legacy artwork bytes, TMDB logo/season posters/episode still. Returns the image list."""
        out = []
        meta = {a["kind"]: a for a in artmeta.get(item["id"], [])}
        legacy = {a["kind"]: base64.b64decode(a["b64"]) for a in art_bytes.get(item["id"], [])}
        fetched_at = {a["kind"]: ts(a.get("fetchedat")) for a in art_bytes.get(item["id"], [])}
        ctype = {a["kind"]: a.get("contenttype") or "image/jpeg" for a in art_bytes.get(item["id"], [])}

        def add(kind, data, content_type, name, origin, source_url=None, fetched=None, season=None, language=None):
            w, h = image_size(data)
            write_bytes(f"{mdir}/{name}", data)
            entry = {"kind": kind, "file": name, "sha256": sha(data), "contentType": content_type, "sizeBytes": len(data),
                     "width": w, "height": h, "language": language, "sourceUrl": source_url, "fetchedAt": fetched, "origin": origin}
            if item["type"] == "series":
                entry["season"] = season
            out.append(entry)

        def blob(desc):
            return open(os.path.join(blobs, desc["blob"]), "rb").read()

        x = extra.get(item["id"]) or {}
        if item["type"] == "episode":
            # The legacy catalog stored an episode's still as both 'poster' and 'backdrop' (same bytes).
            still_legacy = legacy.get("poster") or legacy.get("backdrop")
            legacy_url = (meta.get("poster") or meta.get("backdrop") or {}).get("url")
            if legacy.get("poster") and legacy.get("backdrop") and legacy["poster"] != legacy["backdrop"]:
                report["episode-art-kept-as-two-images"].append(item["title"])
            t = x.get("still")
            # The catalog's own image is kept as found, even when the reference database has a larger copy of
            # the same picture: a re-sync may upgrade it, a migration never replaces bytes.
            if still_legacy:
                add("still", still_legacy, ctype.get("poster", "image/jpeg"), "still.jpg", "legacy-catalog", legacy_url, fetched_at.get("poster"))
            elif t:
                add("still", blob(t), t["contentType"], f"still.{EXT.get(t['contentType'], 'jpg')}", "tmdb", t["sourceUrl"], FETCHED_AT)
            if legacy.get("backdrop") and legacy.get("poster") and legacy["poster"] != legacy["backdrop"]:
                add("backdrop", legacy["backdrop"], ctype.get("backdrop", "image/jpeg"), "backdrop.jpg", "legacy-catalog",
                    (meta.get("backdrop") or {}).get("url"), fetched_at.get("backdrop"))
        else:
            for kind in ("poster", "backdrop"):
                if legacy.get(kind):
                    add(kind, legacy[kind], ctype[kind], f"{kind}.{EXT.get(ctype[kind], 'jpg')}", "legacy-catalog",
                        (meta.get(kind) or {}).get("url"), fetched_at.get(kind))
            if x.get("logo"):
                l = x["logo"]
                add("logo", blob(l), l["contentType"], f"logo.{EXT.get(l['contentType'], 'png')}", "tmdb", l["sourceUrl"], FETCHED_AT,
                    language=x.get("logoLanguage"))
            for n, s in sorted(((int(k), v) for k, v in (x.get("seasons") or {}).items())):
                if s.get("poster"):
                    p = s["poster"]
                    add("poster", blob(p), p["contentType"], f"season-{n:02d}-poster.{EXT.get(p['contentType'], 'jpg')}", "tmdb", p["sourceUrl"], FETCHED_AT, season=n)
        return out

    def metadata_doc(item, kind, mdir, qualifier=None):
        created = ts(item["createdat"]) or NOW
        updated = ts(item["modifiedat"]) or created
        t = tmdb.get(item["id"]) or {}
        title = item["title"] or "(untitled)"
        origins = {}
        doc = {"schema": "zaentrum.library.metadata/1", "itemId": item["id"], **audit(created, updated), "type": kind}
        overview = item["description"]
        if item["title"]: origins["titles.primary"] = "legacy-catalog"
        if overview: origins["titles.localized.en.overview"] = "legacy-catalog"
        if item["tagline"]: origins["titles.localized.en.tagline"] = "legacy-catalog"
        if kind == "episode" and not overview:
            s = ((extra.get(item["parent_id"]) or {}).get("seasons") or {}).get(str(item["seasonnumber"])) or {}
            e = (s.get("episodes") or {}).get(str(item["episodenumber"])) or {}
            if e.get("overview"):
                overview = e["overview"]
                origins["titles.localized.en.overview"] = "tmdb"
        doc["titles"] = {
            "primary": title,
            "original": t.get("originalTitle"),
            "sort": item["sorttitle"] or title.lower(),
            "qualifier": qualifier,
            "localized": {"en": {"title": title, "sortTitle": item["sorttitle"] or title.lower(),
                                 "tagline": item["tagline"], "overview": overview}},
        }
        if t.get("originalTitle"): origins["titles.original"] = "tmdb"
        if qualifier: origins["titles.qualifier"] = "folder-name"
        if kind == "movie":
            doc["releaseDate"] = t.get("releaseDate")
            if t.get("releaseDate"): origins["releaseDate"] = "tmdb"
        doc["genres"] = sorted({g["name"] for g in genres.get(item["id"], [])})
        doc["tags"] = sorted({g["tag"] for g in tags.get(item["id"], [])})
        if doc["genres"]: origins["genres"] = "legacy-catalog"
        if doc["tags"]: origins["tags"] = "legacy-catalog"
        doc["rating"] = float(item["rating"]) if item["rating"] is not None else None
        if doc["rating"] is not None: origins["rating"] = "legacy-catalog"
        if kind != "episode":
            doc["contentRating"] = t.get("contentRating")
            if t.get("contentRating"): origins["contentRating"] = "tmdb"
        if item["durationms"]:
            doc["reference"] = {"runtimeMs": item["durationms"], "runtimeSource": "legacy-catalog"}
            origins["reference.runtimeMs"] = "legacy-catalog"
        doc["credits"] = credits(item, origins)
        if kind == "movie":
            doc["collection"] = t.get("collection")
            if t.get("collection"): origins["collection"] = "tmdb"
        if kind == "series":
            x = extra.get(item["id"]) or {}
            doc["series"] = {
                "status": t.get("status") or "unknown", "firstAirDate": t.get("firstAirDate"), "lastAirDate": t.get("lastAirDate"),
                "network": t.get("network"),
                "seasons": [{"number": int(n), "tmdbSeason": s.get("tmdbSeason"), "name": s.get("name"), "overview": s.get("overview"),
                             "airDate": s.get("airDate"), "episodeCountReference": s.get("episodeCount")}
                            for n, s in sorted((x.get("seasons") or {}).items(), key=lambda kv: int(kv[0]))],
            }
            if t: origins["series"] = "tmdb"
        if kind == "episode":
            et = tmdb.get(item["id"]) or {}
            doc["episode"] = {"airDate": et.get("airDate")}
            if et.get("airDate"): origins["episode.airDate"] = "tmdb"
        doc["images"] = images(item, mdir, origins)
        vids = [{"site": v["site"], "key": v["externalid"], "url": v.get("url"), "name": v.get("title"), "kind": None, "language": None,
                 "durationMs": v["durationsec"] * 1000 if v.get("durationsec") else None, "publishedAt": ts(v.get("publishedat")),
                 "origin": v.get("source") if v.get("source") in ("tmdb", "manual") else "legacy-catalog"}
                for v in trailers.get(item["id"], []) if v.get("site") and v.get("externalid")]
        if vids:
            doc["videos"] = vids
            origins["videos"] = "legacy-catalog"   # read from the catalog; each entry names its upstream origin
        doc["curation"] = {"metadataLocked": False, "lockedFields": [], "notes": None}
        doc["fieldOrigins"] = origins
        for missing in (("releaseDate", "contentRating") if kind == "movie" else ()):
            if not doc.get(missing):
                report["empty-in-legacy-catalog"].append(f"{kind}:{title}: {missing}")
        return doc

    # ------------------------------------------------------------ one version (the legacy model has one per item)
    def build_version(item, idir):
        vid = did(item["id"], "version", "primary")
        sid = did(vid, "source")
        pr = probes.get(item["id"]) or {}
        probe = pr.get("probe") or {}
        fmt = probe.get("format") or {}
        stat = pr.get("stat") or {}
        fe = fetched.get(item["id"]) or {}
        src_path = paths[item["id"]]["source"]
        name = os.path.basename(src_path)
        folder = fe.get("folder") or os.path.basename(os.path.dirname(src_path))
        is_iso = name.lower().endswith(".iso")
        streams = [norm_stream(s) for s in probe.get("streams") or []]
        infer_forced_by_size(streams)
        vstreams = [s for s in streams if s["type"] == "video" and not s["dispositions"].get("attachedPic")]
        v0 = vstreams[0] if vstreams else {}
        astreams = [s for s in streams if s["type"] == "audio"]
        measured = int(float(fmt["duration"]) * 1000) if fmt.get("duration") else None
        reference = item["durationms"]

        # --- fidelity: original or already a re-encode?
        fid_ev = []
        enc_v = (v0.get("encoder") or "")
        if re.search(r"nvenc|libx26[45]|libsvtav1|lavc", enc_v, re.I):
            fid_ev.append({"signal": "encoder-tag", "value": enc_v, "weight": 0.9, "note": "video stream carries an encoder tag"})
        for a in astreams:
            if a.get("titleClaim") and a["titleClaim"].get("contradictsActual"):
                fid_ev.append({"signal": "codec-vs-title", "value": f"stream {a['index']}: title '{a['title']}' vs actual {a['codec']} {a['channels']}ch",
                               "weight": 0.8, "note": "title names an original format the stream no longer contains"})
            if re.search(r"lavc", a.get("encoder") or "", re.I):
                fid_ev.append({"signal": "encoder-tag", "value": f"stream {a['index']}: {a['encoder']}", "weight": 0.9})
        if is_iso:
            fid_class = "original"
            fid_ev.append({"signal": "filename", "value": name, "weight": 0.6, "note": "disc image"})
        elif fid_ev:
            fid_class = "derivative"
        elif fmt.get("tags", {}).get("encoder", "").lower().startswith("libebml") and not enc_v:
            fid_class = "original"
            fid_ev.append({"signal": "encoder-tag", "value": fmt["tags"]["encoder"], "weight": 0.5, "note": "muxed, no stream encoder tags"})
        else:
            fid_class = "unknown"

        # --- edition
        lab = labels(name, folder)
        ed_ev, ed_kind, conf, review = [], "unknown", None, None
        for sig, text in (("folder-name", folder), ("filename", name),
                          ("container-title", (fmt.get("tags") or {}).get("title")),
                          ("stream-title", v0.get("title"))):
            w = edition_word(text)
            if w:
                ed_ev.append({"signal": sig, "value": text, "weight": 0.9})
                ed_kind, conf = w, 0.9
        commentaries = [a["title"] for a in astreams if a.get("purpose") == "commentary"]
        if commentaries:
            ed_ev.append({"signal": "commentary-track", "value": commentaries[:3], "weight": 0.2,
                          "note": "commentary tracks identify the disc release, which usually names the cut"})
        delta = (measured - reference) if (measured and reference) else None
        if delta is not None:
            ed_ev.append({"signal": "runtime", "value": {"measuredMs": measured, "referenceMs": reference, "deltaMs": delta}, "weight": 0.6})
            if ed_kind == "unknown" and item["type"] == "movie":
                pct = delta / reference
                if pct >= 0.08:
                    review = (f"Runs {round(delta / 60000)} min longer than the reference runtime "
                              f"({round(measured / 60000)} vs {round(reference / 60000)} min): likely an extended or director's cut. Confirm the edition.")
                    report["edition-needs-review"].append(f"{item['title']}: {review}")
                elif abs(pct) <= 0.03:
                    ed_kind, conf = "theatrical", 0.6
        completeness = {"status": "unknown", "evidence": []}
        if delta is not None:
            if reference and delta < -0.08 * reference:
                completeness = {"status": "suspect", "evidence": [{"signal": "runtime", "value": delta, "weight": 0.6,
                                "note": "much shorter than reference: truncated file or a shorter cut"}]}
            else:
                completeness = {"status": "complete", "evidence": [{"signal": "runtime", "value": delta, "weight": 0.5}]}

        # --- presentation
        samples = colour.get(item["id"]) or []
        peaks = [s["satMax"] for s in samples if s["satMax"] is not None]
        col = "unknown" if not peaks else "black-and-white" if max(peaks) <= 3 else "colour" if max(peaks) >= 10 else "unknown"
        col_review = None
        if is_iso and not probe:
            col_review = "Disc image: mount it and probe its main title to measure runtime, streams and colour."
            report["disc-image-not-probed"].append(item["title"])
        elif col == "black-and-white":
            col_review = (f"Black-and-white from {len(peaks)} sampled frames. Check the opening and closing minutes for colour "
                          "sequences (partial-colour) before showing the label.")
            report["colour-needs-review"].append(f"{item['title']}: black-and-white from {len(peaks)} samples")
        col_decision = {"decidedBy": "inferred", "confidence": 0.8 if col != "unknown" else 0.0, "review": col_review,
                        "evidence": [{"signal": "content-analysis", "value": {"method": "signalstats SATMAX, peak over sampled frames", "samples": samples},
                                      "weight": 0.8, "note": "a few sampled frames cannot detect brief colour accents; partial-colour needs dense sampling or a person"}]}
        dr = (v0.get("hdr") or {}).get("format", "unknown") if v0 else "unknown"
        s3d = v0.get("stereo3d")
        presentation = {
            "colour": col, "colourDecision": col_decision, "dynamicRange": dr,
            "stereo3d": ("side-by-side" if s3d and "left_right" in s3d or s3d == "block_lr" else "mvc" if s3d == "mvc" else "none" if not s3d else "unknown"),
            "aspectRatio": v0.get("displayAspectRatio"),
        }
        fp = "/".join(str(x) for x in [v0.get("codec"), (v0.get("profile") or "").lower().replace(" ", ""), f"{v0.get('bitDepth')}bit",
                                        dr if dr != "dolby-vision" else f"dv{((v0.get('hdr') or {}).get('dolbyVision') or {}).get('profile')}",
                                        f"{v0.get('width')}x{v0.get('height')}"]) if v0 else ("disc-image" if is_iso else "unknown")

        label_parts = []
        if ed_kind not in ("unknown", "theatrical"):
            label_parts.append({"directors-cut": "Director's Cut", "unrated": "Unrated", "extended": "Extended"}.get(ed_kind, ed_kind.replace("-", " ").title()))
        if col == "black-and-white":
            label_parts.append("Black & White")
        if dr in ("hdr10", "dolby-vision", "hlg"):
            label_parts.append({"hdr10": "HDR10", "dolby-vision": "Dolby Vision", "hlg": "HLG"}[dr])
        version_label = " · ".join(label_parts) or None

        # --- the original: record + verbatim probe (kept after the file is deleted)
        probe_rel = f"source/{sid}/ffprobe.json"
        probe_sha = write_json(f"{idir}/{probe_rel}", probe) if probe else None
        side = []
        for s in fe.get("sidecars") or []:
            rel = f"source/{sid}/{s['name']}"
            slang, _ = lang((re.search(r"\.([a-z]{2,3})(?:\.(?:sdh|forced|cc))*\.[a-z0-9]+$", s["name"], re.I) or [None, None])[1])
            forced, sdh = ".forced." in s["name"].lower(), bool(re.search(r"\.(sdh|cc)\.", s["name"], re.I))
            side.append({"file": rel, "originalName": s["name"], "kind": s["kind"], "format": os.path.splitext(s["name"])[1][1:].lower(),
                         "language": slang, "forced": forced, "hearingImpaired": sdh,
                         **({"purpose": "forced" if forced else "sdh" if sdh else "dialogue"} if s["kind"] == "subtitle" else {}),
                         "sizeBytes": s["size"]})
            plan.append(("copy", s["path"], f"{idir}/{rel}"))
        source = {
            "id": sid, "state": "present",
            "file": {
                "name": name, "path": name if args.originals == "place" else None, "kind": "disc-image" if is_iso else "stream-container",
                "sizeBytes": stat.get("size") or 0,
                "mtime": datetime.datetime.fromtimestamp(stat["mtime"], datetime.timezone.utc).isoformat().replace("+00:00", "Z") if stat.get("mtime") else NOW,
                "fixity": {"qh1": stat.get("qh1")},
                "origin": {"libraryPath": src_path[len(args.library_root):].lstrip("/") if src_path.startswith(args.library_root) else src_path,
                           "folder": folder},
                "ownership": {"uid": stat.get("uid"), "gid": stat.get("gid"),
                              "mode": (stat.get("mode") or "0o0")[-4:].lstrip("o").rjust(4, "0")[-4:], "acl": None, "selinux": None},
                "part": None,
            },
            "labels": lab,
            "container": {
                "format": fmt.get("format_name") or ("iso9660/udf" if is_iso else "unknown"),
                "durationMs": measured, "bitrate": num(fmt.get("bit_rate")),
                "title": (fmt.get("tags") or {}).get("title"),
                "muxingApp": (fmt.get("tags") or {}).get("encoder"),
                "writingApp": (fmt.get("tags") or {}).get("writing_application") or (fmt.get("tags") or {}).get("WRITING_APPLICATION"),
                "creationTime": (fmt.get("tags") or {}).get("creation_time"),
                "tags": {k: str(v) for k, v in (fmt.get("tags") or {}).items()},
            },
            "fidelity": {"class": fid_class, "evidence": fid_ev},
            "streams": streams,
            "sidecars": side,
            "covers": [],
            "probe": {"tool": "ffprobe", "version": None, "at": PROBED_AT if probe else None, "file": probe_rel if probe else None, "sha256": probe_sha,
                      "note": "disc image: streams were not demuxed; mount the image to inventory its playlists" if is_iso else None},
        }
        # Timeline marks belong to the version: the file's own chapters, else the catalog's; detected ranges from the catalog.
        if probe.get("chapters"):
            version_chapters = [{"startMs": int(float(c["start_time"]) * 1000), "endMs": int(float(c["end_time"]) * 1000),
                                 "title": (c.get("tags") or {}).get("title")} for c in probe["chapters"]]
            chapters_from = "original-file"
        elif chapters.get(item["id"]):
            version_chapters = [{"startMs": c["startms"], "endMs": c["endms"], "title": c["title"]} for c in chapters[item["id"]]]
            chapters_from = "legacy-catalog"
        else:
            version_chapters, chapters_from = [], None
        version_segments = [{"kind": g["kind"] if g["kind"] in ("intro", "recap", "credits", "preview", "commercial") else "other",
                             "startMs": g["startms"], "endMs": g["endms"], "detector": g["source"],
                             "confidence": float(g["confidence"]) if g["confidence"] is not None else None, "label": g["label"]}
                            for g in segments.get(item["id"], [])]
        if measured and any(g["endMs"] > measured for g in version_segments):
            report["segments-beyond-runtime"].append(
                f"{item['title']}: a detected range ends {round((max(g['endMs'] for g in version_segments) - measured) / 1000)} s after the measured runtime")
        source["essence"] = source_essence(streams, probe.get("chapters"))
        fc = file_coords(name)
        if item["type"] == "episode" and fc and fc.get("episodeEnd"):
            siblings = sorted((x for x in items.values() if x["parent_id"] == item["parent_id"] and x["seasonnumber"] == fc["season"]
                               and fc["episode"] <= (x["episodenumber"] or -1) <= fc["episodeEnd"]), key=lambda x: x["episodenumber"])
            source["covers"] = [x["id"] for x in siblings]
        if not source["file"]["fixity"]["qh1"]:
            raise SystemExit(f"no fixity for {src_path}")
        # The original joins its version's folder: one destination for the version's media, package or not.
        if args.originals == "place":
            plan.append(("original", src_path, f"{idir}/{name}"))

        # --- the package in this folder: playback fields (verbatim + source mapping) and its account of losses
        playback, package, lost = None, None, []
        man = fe.get("manifest")
        if man:
            pid = did(vid, "package", fe["manifestSha256"])
            ren = man.get("renditions") or {}
            losses, dropped = [], []
            src_audio = {a["index"]: a for a in astreams}
            src_audio_order = [a["index"] for a in astreams]
            audio = []
            for n, a in enumerate(ren.get("audio") or []):
                sidx = src_audio_order[n] if n < len(src_audio_order) else None
                sch = src_audio[sidx]["channels"] if sidx is not None else None
                sa = src_audio.get(sidx) or {}
                audio.append({**a, "sourceStreamIndex": sidx, "sourceChannels": sch,
                              "purpose": sa.get("purpose", "unknown"), "purposeFrom": sa.get("purposeFrom"),
                              "variant": sa.get("variant"),
                              "original": True if (sa.get("dispositions") or {}).get("original") else None})
                if sch and a.get("channels") and a["channels"] < sch:
                    losses.append({"kind": "audio-downmix", "detail": f"{sch}ch -> {a['channels']}ch ({a.get('title') or a['id']})", "sourceStreamIndex": sidx})
                src_codec = src_audio[sidx]["codec"] if sidx is not None else None
                if src_codec and src_codec != "aac" and (a.get("codec") or "").startswith("mp4a"):
                    losses.append({"kind": "audio-codec", "detail": f"{src_codec} -> {a['codec']}", "sourceStreamIndex": sidx})
            # Menus built from legacy titles mislead: a title can name a format the package no longer carries,
            # and several renditions can be indistinguishable once downmixed.
            for a in audio:
                c = claim(a.get("title"))
                carried = "aac" if (a.get("codec") or "").startswith("mp4a") else (a.get("codec") or "")
                if c and claim_contradicts(c, carried, a.get("channels") or 0):
                    report["rendition-title-overclaims"].append(
                        f"{item['title']}: {a['id']} titled '{a['title']}' carries {a.get('channels')}ch {a.get('codec')}")
            groups = collections.defaultdict(list)
            for a in audio:
                if a.get("visible", True):
                    # commentaries differ by what is said, so their titles keep them apart
                    title_key = a.get("title") if a.get("purpose") == "commentary" else None
                    groups[(primary_language(a.get("language")), a.get("variant"), a.get("purpose"), a.get("channels"), a.get("codec"), title_key)].append(a)
            for key, members in groups.items():
                if len(members) < 2:
                    continue
                if key[2] != "commentary" and all(claim(m.get("title")) for m in members):
                    report["interchangeable-audio-renditions"].append(
                        f"{item['title']}: {', '.join(m['id'] for m in members)} ({key[0]} {key[2]}) are all {key[3]}ch {key[4]} and titled "
                        f"only by their former format; a menu offers them once")
                else:
                    report["indistinguishable-audio-renditions"].append(
                        f"{item['title']}: {', '.join(m['id'] for m in members)} ({key[0]} {key[2]}, {key[3]}ch) cannot be told apart and may differ "
                        f"in content (an untitled commentary); set their purpose or title by hand")
            if len(ren.get("audio") or []) < len(astreams):
                more = src_audio_order[len(ren.get("audio") or []):]
                dropped += more
                losses.append({"kind": "audio-dropped", "detail": f"{len(more)} source audio track(s) not packaged", "sourceStreamIndex": None})
            video = []
            for vv in ren.get("video") or []:
                video.append({**vv, "dynamicRange": "hdr10" if vv.get("hdr") else "sdr", "sourceStreamIndex": v0.get("index")})
                if v0 and vv.get("height") and v0.get("height") and vv["height"] < v0["height"]:
                    losses.append({"kind": "video-resolution", "detail": f"{v0['width']}x{v0['height']} -> {vv['width']}x{vv['height']}", "sourceStreamIndex": v0.get("index")})
                if dr == "dolby-vision":
                    losses.append({"kind": "dolby-vision", "detail": "Dolby Vision metadata is not carried; the playback fields record only an hdr flag", "sourceStreamIndex": v0.get("index")})
                if dr in ("hdr10", "dolby-vision", "hlg") and not vv.get("hdr"):
                    losses.append({"kind": "dynamic-range", "detail": f"{dr} -> sdr", "sourceStreamIndex": v0.get("index")})
            s_subs = [s for s in streams if s["type"] == "subtitle"]
            # The packager writes the i-th subtitle stream of the original to subs/<i>.<ext>.
            p_subs = []
            for sub in man.get("subtitles") or []:
                m_i = re.match(r"subs/(\d+)\.", sub.get("path") or "")
                ss = s_subs[int(m_i.group(1))] if m_i and int(m_i.group(1)) < len(s_subs) else None
                if ss:
                    purpose, purpose_from = ss["purpose"], ss["purposeFrom"]
                elif sub.get("forced"):
                    purpose, purpose_from = "forced", "disposition"
                else:
                    purpose, purpose_from = "unknown", None
                p_subs.append({**sub, "sourceStreamIndex": ss["index"] if ss else None, "purpose": purpose, "purposeFrom": purpose_from,
                               "variant": ss.get("variant") if ss else None})
                if purpose in ("forced", "signs-songs") and not sub.get("forced"):
                    report["package-forced-flag-missing"].append(
                        f"{item['title']}: {sub['id']} '{sub.get('title') or ''}' is a {purpose} track ({purpose_from}) the package does not flag forced")
                if purpose in ("forced", "signs-songs") and sub.get("default"):
                    report["forced-track-flagged-default"].append(f"{item['title']}: {sub['id']} '{sub.get('title') or ''}' is {purpose} but flagged default")
            if any(x.get("purpose") == "commentary" for x in s_subs) and not any(x.get("purpose") == "commentary" for x in astreams):
                report["commentary-subtitles-without-commentary-audio"].append(f"{item['title']}: set the purpose of the commentary audio tracks by hand")
            src_forced = {primary_language(x.get("language")) for x in s_subs if x.get("purpose") in ("forced", "signs-songs")}
            pkg_forced = {primary_language(x.get("language")) for x in p_subs if x["purpose"] in ("forced", "signs-songs")}
            if src_forced - pkg_forced:
                losses.append({"kind": "subtitle-dropped", "detail": f"forced subtitles not packaged: {','.join(sorted(src_forced - pkg_forced))}", "sourceStreamIndex": None})
            if v0.get("closedCaptions"):
                losses.append({"kind": "closed-captions-dropped", "detail": "embedded closed captions are not carried into the package", "sourceStreamIndex": v0.get("index")})
            if len(p_subs) < len(s_subs):
                losses.append({"kind": "subtitle-dropped", "detail": f"{len(s_subs) - len(p_subs)} of {len(s_subs)} source subtitle track(s) not packaged", "sourceStreamIndex": None})
            if any(s.get("styled") for s in s_subs):
                losses.append({"kind": "subtitle-styling", "detail": "ASS/SSA styling flattened to WebVTT", "sourceStreamIndex": None})
            if any(s["type"] == "attachment" for s in streams):
                losses.append({"kind": "attachments-dropped", "detail": "embedded fonts/images are not carried into the package", "sourceStreamIndex": None})
            playback = {"durationMs": man.get("durationMs"), "packagedAt": man.get("packagedAt"), "packager": man.get("packager"),
                        "renditions": {"video": video, "audio": audio}, "subtitles": p_subs, "trickplay": man.get("trickplay")}
            if man.get("trailers"):
                playback["trailers"] = man["trailers"]
            prow = packaged_rows.get(item["id"]) or {}
            package = {
                "id": pid, "state": "complete" if fe.get("packageComplete") else "failed", "role": "derived",
                "sizeBytes": prow.get("sizebytes"),
                "peakBandwidthBps": prow["bitratekbps"] * 1000 if prow.get("bitratekbps") else None,
                "recipe": {"video": "copy or re-encode (not recorded by the packager)",
                           "audio": "aac-lc 2ch 192k" if all((a.get("channels") == 2 and a.get("bitrateBps") == 192000) for a in ren.get("audio") or []) else None,
                           "subtitles": "text -> webvtt, image -> sidecar"},
                "fidelity": {"lossless": not losses, "losses": losses, "droppedSourceStreams": dropped},
                "essence": package_essence({"renditions": {"video": video, "audio": audio}, "subtitles": p_subs}),
                "checksums": None,
            }
            report["package-checksums-to-compute"].append(f"{item['title']}: run tools/package-checksums.py on storage")
            lost = essence_gap(source["essence"], package["essence"], chapters_kept=bool(version_chapters))
            if lost:
                report["lost-if-original-deleted"].append(f"{item['title']}: {'; '.join(lost)}")
            plan.append(("package", fe["packageDir"], idir))
        else:
            lost = ["everything: no package exists"]
            report["lost-if-original-deleted"].append(f"{item['title']}: EVERYTHING - no package exists")

        version = {
            "id": vid, "path": ".", "label": version_label, "primary": True,
            "edition": {"kind": ed_kind, "label": None, "decidedBy": "inferred", "decidedAt": NOW,
                        **({"confidence": conf} if conf is not None else {}), "evidence": ed_ev,
                        "review": review or (col_review if is_iso and not probe else None)},
            "presentation": presentation,
            "runtime": {"measuredMs": measured, "referenceMs": reference, "deltaMs": delta},
            "chapters": version_chapters,
            "chaptersFrom": chapters_from,
            "segments": version_segments,
            "completeness": completeness,
            "master": {"fingerprint": fp, "fidelity": fid_class},
            "truth": {"kind": "source", "since": NOW, "note": "the original still exists; the package becomes the truth once it is deleted"},
            "sources": [source],
            "package": package,
            "lostIfOriginalDeleted": lost,
        }
        return version, playback, fp, col, dr, man

    def manifest_doc(item, kind, ids):
        created = ts(item["createdat"]) or NOW
        doc = {"schema": "zaentrum.library.manifest/1", "version": 3, "itemId": item["id"], **audit(created, NOW),
               "type": kind, "title": item["title"] or "(untitled)", "year": item["year"]}
        if kind == "movie" and ids.get("tmdbMovie"):
            doc["tmdbId"] = ids["tmdbMovie"]
        if kind == "episode" and ids.get("tmdbTv"):
            doc["tmdbId"] = ids["tmdbTv"]
        doc["externalIds"] = ids
        doc["match"] = match(item, ids)
        doc["metadata"] = {"file": "metadata/metadata.json"}
        return doc

    def finish_playable(doc, item, idir):
        version, playback, fp, col, dr, man = build_version(item, idir)
        if playback:
            for k in ("durationMs", "packagedAt", "packager", "renditions", "subtitles", "trickplay", "trailers"):
                if k in playback:
                    doc[k] = playback[k]
        doc["versions"] = [version]
        doc["processing"] = processing(item)
        doc["provenance"] = {"migratedFrom": "legacy-catalog", "migratedAt": NOW, "legacyItemId": item["id"],
                             "legacyCreatedBy": item.get("createdby"), "legacyModifiedBy": item.get("modifiedby")}
        return fp, col, dr, man

    wanted = set(args.items.split(",")) if args.items else None
    for item in [items[i] for i in items if items[i]["type"] in ("movie", "series") and (wanted is None or i in wanted)]:
        if item["type"] == "movie":
            idir = f"movies/{shard(item['id'])}/{item['id']}"
            ids = ext_ids(item)
            doc = manifest_doc(item, "movie", ids)
            finish_playable(doc, item, idir)
            write_json(f"{idir}/metadata/metadata.json", metadata_doc(item, "movie", f"{idir}/metadata"))
            write_json(f"{idir}/manifest.json", doc)
            continue

        # ---- series: a real folder, episodes as sub-items
        sdir = f"shows/{shard(item['id'])}/{item['id']}"
        ids = ext_ids(item)
        sdoc = manifest_doc(item, "series", ids)
        eps = sorted([items[i] for i in items if items[i]["parent_id"] == item["id"]],
                     key=lambda e: (e["seasonnumber"] if e["seasonnumber"] is not None else 9999, e["episodenumber"] or 0))
        seasons = collections.OrderedDict()
        masters = collections.defaultdict(list)
        folder = None
        tseasons = (extra.get(item["id"]) or {}).get("seasons") or {}
        for e in eps:
            edir = f"{sdir}/episodes/{e['id']}"
            eids = ext_ids(e)
            ed = manifest_doc(e, "episode", eids)
            fe = fetched.get(e["id"]) or {}
            folder = folder or fe.get("parentFolder")
            code = f"S{e['seasonnumber']:02d}E{(e['episodenumber'] or 0):02d}" if e["seasonnumber"] is not None else None
            ed["seriesTitle"] = item["title"]
            ed["seasonNumber"] = e["seasonnumber"]
            ed["episodeNumber"] = e["episodenumber"]
            if code:
                ed["episodeCode"] = code
            coords = [{"scheme": "aired", "season": e["seasonnumber"], "episode": e["episodenumber"] or 0, "episodeEnd": None}]
            fc = file_coords(os.path.basename(paths[e["id"]]["source"] or ""))
            if fc:
                coords.append(fc)
            ed["episode"] = {"seriesId": item["id"], "coordinates": coords}
            fp, col, dr, man = finish_playable(ed, e, edir)
            if man:
                for k in ("seriesTitle", "episodeCode"):
                    if man.get(k) and man[k] != ed.get(k):
                        report["episode-identity-differs-from-package"].append(f"{e['title']}: {k} manifest={man[k]!r} catalog={ed.get(k)!r}")
                        ed[k] = man[k]
            write_json(f"{edir}/metadata/metadata.json", metadata_doc(e, "episode", f"{edir}/metadata"))
            write_json(f"{edir}/manifest.json", ed)
            n = e["seasonnumber"] if e["seasonnumber"] is not None else 0
            seasons.setdefault(n, []).append({"itemId": e["id"], "path": f"episodes/{e['id']}/",
                                              "episode": e["episodenumber"], "episodeEnd": (fc or {}).get("episodeEnd")})
            masters[(fp, f"{col} {dr}")].append(e["id"])
        qualifier = None
        if folder:
            m = re.search(r"\(([A-Z]{2,3}|\d{4})\)$", folder)
            if m:
                qualifier = m.group(1)
        sdoc["series"] = {
            "defaultOrdering": "aired",
            "seasons": [{"number": n, "tmdbSeason": (tseasons.get(str(n)) or {}).get("tmdbSeason"), "episodes": eplist}
                        for n, eplist in seasons.items()],
            "masters": [{"fingerprint": fp, "presentation": pres, "episodes": ids_} for (fp, pres), ids_ in masters.items()],
        }
        sdoc["processing"] = processing(item)
        sdoc["provenance"] = {"migratedFrom": "legacy-catalog", "migratedAt": NOW, "legacyItemId": item["id"],
                             "legacyCreatedBy": item.get("createdby"), "legacyModifiedBy": item.get("modifiedby")}
        if len(masters) > 1:
            report["mixed-masters"].append(f"{item['title']}: {len(masters)} masters across {len(eps)} episodes")
        write_json(f"{sdir}/metadata/metadata.json", metadata_doc(item, "series", f"{sdir}/metadata", qualifier))
        write_json(f"{sdir}/manifest.json", sdoc)

    with open(os.path.join(args.out, "plan.tsv"), "w") as f:
        for op, src, dst in plan:
            f.write(f"{op}\t{src}\t{dst}\n")
    json.dump({k: v for k, v in report.items()}, open(os.path.join(args.out, "report.json"), "w"), indent=2)
    print("files:", dict(written))
    print("storage operations planned:", dict(collections.Counter(p[0] for p in plan)))
    for k, v in report.items():
        print(f"report {k}: {len(v)}")

if __name__ == "__main__":
    main()
