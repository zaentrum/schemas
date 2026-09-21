#!/usr/bin/env python3
"""Write v2 library records for a catalog's items, from the catalog export, the package store and
the original files.

Usage:
  library-v2-from-catalog.py --export CATALOG.json --packages DIR --media DIR --out LIBRARY
                             [--items id,id,...] [--media-mode copy|move|none]
                             [--as-of TIMESTAMP] [--text-language LANG] [--dry-run]

The catalog is the working copy and this writes the record beside the bytes: one item folder per
row, holding the identity the row was created with, the texts and images the database held, the
original each row points at as the file itself reports it, and one version folder per packaged
asset with the package moved or copied in.

  <out>/movies/<aa>/<itemId>/          item.json  metadata.json  metadata/<sha256>.jpg
                                       sources/<sourceId>.json  sources/<sourceId>/ffprobe.json
                                       versions/<versionId>/version.json  package.json
                                                             hls/ subs/ trickplay/
                                                             checksums.sha256  .complete
  <out>/series/<aa>/<seriesId>/        item.json  metadata.json  metadata/
                                       episodes/<episodeId>/ (as above)

The export is produced on the client side (see the query beside this tool in the runbook); this
tool needs no database driver and no network, only the standard library, so it can be piped into
the pod that mounts the share:

  oc -n <ns> exec -i deploy/packager -- python3 - --export /tmp/catalog.json … < library-v2-from-catalog.py

What it can fill in, and what it cannot:
  * the identity, texts, images, people, chapters, segments and trailers come from the export;
  * the container, streams, fidelity and essence of an original come from `ffprobe`, which is used
    when it is on PATH — without it a source record still carries the file's size, mtime and qh1
    fingerprint, and says in `probe.note` that nothing was probed;
  * everything the export does not hold stays empty rather than guessed: no content rating, no
    localized titles beyond the one text language, no collection, no season texts, no edition
    beyond what a file name says, and no measured colour.

An item whose original file cannot be read gets its item.json and metadata.json and nothing else:
a version must name a source record, and a source record must carry the file's fixity, which
cannot be invented from a path. Those items are listed at the end.

Every generated id is a UUIDv5 of the item id and a stable name, and every "written at" stamp is
--as-of (the export's own exportedAt by default), so running this twice writes the same tree.
"""
import argparse, base64, datetime, hashlib, json, os, re, shutil, subprocess, sys, uuid

NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://zaentrum.github.io/schemas/library")
PACKAGE_DIRS = ("hls", "subs", "trickplay", "trailers")
OS_ARTEFACTS = re.compile(r"^(\.DS_Store|\._.*|Thumbs\.db|desktop\.ini|@eaDir|\.@__thumb|#recycle|\.AppleDouble)$")
IMAGE_KINDS = {"poster", "backdrop", "logo", "still", "banner", "thumb"}
SEGMENT_KINDS = {"intro", "recap", "credits", "preview", "commercial", "other"}
EXT_OF = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


# ---------------------------------------------------------------- small helpers
def did(*parts):
    """A generated id: the same inputs always give the same id, so a re-run writes the same tree."""
    return str(uuid.uuid5(NS, ":".join(str(p) for p in parts)))


def sha_bytes(b):
    return "sha256:" + hashlib.sha256(b).hexdigest()


def sha_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def qh1(p):
    """sha256(first 64 KiB || last 64 KiB || uint64be(size)): cheap proof a large file is the one
    recorded, two reads however big the file is."""
    size = os.path.getsize(p)
    h = hashlib.sha256()
    with open(p, "rb") as f:
        h.update(f.read(65536))
        if size > 65536:
            f.seek(max(size - 65536, 0))
            h.update(f.read(65536))
    h.update(size.to_bytes(8, "big"))
    return "sha256:" + h.hexdigest()


def ts(v):
    """RFC 3339 with an upper-case T and Z, or None."""
    if not v:
        return None
    s = str(v).strip().replace(" ", "T")
    if not re.search(r"(Z|[+-]\d\d:?\d\d)$", s):
        s += "Z"
    s = re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", s)
    return s.replace("+00:00", "Z")


def ts_of_mtime(p):
    t = datetime.datetime.fromtimestamp(os.path.getmtime(p), datetime.timezone.utc)
    return t.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def num(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None


def text(v):
    """A one-line string, or None: a record's fields never carry a line break."""
    if v is None:
        return None
    s = str(v).replace("\r", " ").replace("\n", " ").strip()
    return s or None


def ratio(v):
    return v if v and v not in ("0:1", "N/A") else None


def listdir(d):
    return sorted(n for n in os.listdir(d) if not OS_ARTEFACTS.match(n))


ISO639 = {
    "eng": "en", "ger": "de", "deu": "de", "fre": "fr", "fra": "fr", "ita": "it", "spa": "es", "jpn": "ja",
    "chi": "zh", "zho": "zh", "dut": "nl", "nld": "nl", "por": "pt", "rus": "ru", "pol": "pl", "cze": "cs",
    "ces": "cs", "ukr": "uk", "hin": "hi", "kor": "ko", "swe": "sv", "nor": "no", "nob": "nb", "dan": "da",
    "fin": "fi", "tur": "tr", "ara": "ar", "heb": "he", "hun": "hu", "gre": "el", "ell": "el", "rum": "ro",
    "ron": "ro", "tha": "th", "vie": "vi", "ind": "id", "may": "ms", "msa": "ms", "cat": "ca", "hrv": "hr",
    "slv": "sl", "slo": "sk", "slk": "sk", "srp": "sr", "bul": "bg", "est": "et", "lav": "lv", "lit": "lt",
    "ice": "is", "isl": "is", "fil": "fil", "tel": "te", "tam": "ta", "und": "und", "zxx": "zxx", "mul": "mul",
    "baq": "eu", "eus": "eu", "glg": "gl", "kan": "kn", "mal": "ml", "ben": "bn", "mar": "mr", "urd": "ur",
    "per": "fa", "fas": "fa", "wel": "cy", "cym": "cy", "arm": "hy", "hye": "hy", "geo": "ka", "kat": "ka",
    "mac": "mk", "mkd": "mk", "alb": "sq", "sqi": "sq", "tgl": "tl", "lao": "lo", "khm": "km", "bur": "my",
    "mya": "my", "sin": "si", "nep": "ne", "pan": "pa", "guj": "gu", "tib": "bo", "bod": "bo", "mon": "mn",
    "kaz": "kk", "uzb": "uz", "aze": "az", "bel": "be", "bos": "bs", "gle": "ga", "bre": "br", "ltz": "lb",
    "afr": "af", "swa": "sw", "zul": "zu", "xho": "xh", "amh": "am", "som": "so", "hau": "ha", "yor": "yo",
    "ibo": "ig", "epo": "eo", "lat": "la", "yid": "yi", "jav": "jv", "sun": "su", "mao": "mi", "mri": "mi",
    "grn": "gn", "que": "qu", "nno": "nn", "fao": "fo", "kal": "kl", "iku": "iu", "tat": "tt", "kur": "ku",
    "pus": "ps", "tuk": "tk", "kir": "ky", "tgk": "tg", "mlt": "mt", "roh": "rm", "sco": "sco", "haw": "haw",
}


def lang(raw):
    """(BCP 47 code, the code the file carried) — 'eng' becomes 'en' and keeps 'eng' beside it."""
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


def primary_language(raw):
    code = (lang(raw)[0] or "und").split("-")[0]
    return {"nb": "no", "nn": "no"}.get(code, code)


LANGUAGE_RE = re.compile(r"^([a-z]{2,3}(-[A-Za-z0-9]{2,8})*|und|zxx|mul)$")


def rendition_language(raw):
    """A package rendition keeps the code the media file carried ('eng'), not a normalised one: it
    describes the playlist as it was written. Only a code the format cannot hold is normalised."""
    r = str(raw or "").strip()
    return r if LANGUAGE_RE.match(r) else (lang(r)[0] or "und")


# ---------------------------------------------------------------- what a file name claims
QUALITY = re.compile(r"\b(Remux|Blu-?ray|DVD|WEB[A-Za-z-]*|HDTV|SDTV)[- ](\d{3,4}p)\b|\b(\d{3,4}p)\b", re.I)
EDITION_WORDS = [
    ("directors-cut", r"director'?s?[ ._-]?cut"), ("extended", r"\bextended\b"), ("unrated", r"\bunrated\b"),
    ("theatrical", r"\btheatrical\b"), ("special-edition", r"special[ ._-]?edition"),
    ("remastered", r"\bremaster(ed)?\b"), ("restored", r"\brestor(ed|ation)\b"), ("imax", r"\bimax\b"),
    ("final-cut", r"final[ ._-]?cut"),
]
# 'S01E02', 'S01E02-03', 'S07E23-E24', '1x05' — the range must not swallow a quality token
# ('S01E02-1080p' numbers one episode), so a bare range end is at most three digits and is not
# followed by another digit or a 'p'.
EPISODE_TOKEN = re.compile(r"S(\d{1,3})[ ._-]?E(\d{1,4})(?:[ ._-]?E(\d{1,4})|-(\d{1,3})(?![0-9p]))?"
                           r"|(?<![0-9a-z])(\d{1,2})x(\d{2,3})(?![0-9])", re.I)


def medium_of(token):
    t = token.lower()
    return "web" if t.startswith("web") else "disc" if t in ("remux", "bluray", "blu-ray", "dvd") else \
        "broadcast" if t in ("hdtv", "sdtv") else "unknown"


def edition_word(t):
    if not t:
        return None
    for kind, pat in EDITION_WORDS:
        if re.search(pat, t, re.I):
            return kind
    return None


def labels(name, folder):
    hits = list(QUALITY.finditer(os.path.splitext(name)[0]))
    m = hits[-1] if hits else None
    fold_ed = None
    fm = re.search(r" - ([^()]+?) \(\d{4}\)$", folder or "")
    if fm and edition_word(fm.group(1)):
        fold_ed = fm.group(1).strip()
    return {
        "quality": m.group(0) if m else None,
        "medium": medium_of(m.group(1)) if m and m.group(1) else None,
        "resolution": ((m.group(2) or m.group(3)) if m else None),
        "edition": edition_word(name),
        "folderEdition": fold_ed,
    }


def naming(name):
    """How the file's own name numbered what it holds. The name never says which ordering it used,
    so the scheme stays 'unknown' and only the numbers and the token itself are recorded."""
    m = EPISODE_TOKEN.search(os.path.splitext(name)[0])
    if not m:
        return None
    if m.group(1) is not None:
        season, episode, end = int(m.group(1)), int(m.group(2)), m.group(3) or m.group(4)
    else:
        season, episode, end = int(m.group(5)), int(m.group(6)), None
    return {"scheme": "unknown", "seasonNumber": season, "episodeNumber": episode,
            "episodeEnd": int(end) if end else None, "raw": m.group(0)}


# ---------------------------------------------------------------- streams, as ffprobe reported them
TEXT_SUBS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text", "arib_caption"}
IMAGE_SUBS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle", "xsub"}
LOSSLESS_AUDIO = {"truehd", "mlp", "flac", "alac", "pcm_s16le", "pcm_s24le", "pcm_s32le", "pcm_bluray",
                  "pcm_dvd", "wavpack", "ape", "tta"}
CLAIM_CODECS = [("truehd", r"truehd|true-hd|true hd"), ("dts-hd ma", r"dts[- .]?hd[- .]?(ma|master)"),
                ("dts", r"\bdts\b"), ("e-ac-3", r"e-?ac-?3|dd\+|ddp|dolby digital plus"),
                ("ac-3", r"\bac-?3\b|dolby digital(?! plus)"), ("flac", r"\bflac\b"), ("opus", r"\bopus\b"),
                ("aac", r"\baac\b")]
VARIANT_WORDS = re.compile(r"\b(Latin[ -]?American|LatAm|European|Castilian|Canadian|Brazilian|Portugal|"
                           r"Simplified|Traditional|Cantonese|Mandarin|Flemish|Swiss|Austrian|Mexican|"
                           r"Hong Kong|Taiwanese|Belgian|Qu[eé]b[eé]cois)\b", re.I)
RE_ENCODE = re.compile(r"handbrake|x264|x265|lavf|lavc|ffmpeg|nvenc|vaapi|qsv|mencoder|staxrip|shutter", re.I)


def title_variant(t):
    m = VARIANT_WORDS.search(t or "")
    return m.group(1) if m else None


def claim(title):
    if not title:
        return None
    t = title.lower()
    codec = next((c for c, pat in CLAIM_CODECS if re.search(pat, t)), None)
    m = re.search(r"\b([1-9])[.,]([0-2])\b", t)
    channels = f"{m.group(1)}.{m.group(2)}" if m else None
    return None if not codec and not channels else {"codec": codec, "channels": channels}


def claim_contradicts(c, codec, channels):
    if not c:
        return False
    actual = (codec or "").lower()
    family = {"truehd": ("truehd", "mlp"), "dts-hd ma": ("dts",), "dts": ("dts",), "e-ac-3": ("eac3",),
              "ac-3": ("ac3",), "flac": ("flac",), "opus": ("opus",), "aac": ("aac",)}
    bad = bool(c["codec"] and not any(actual.startswith(x) for x in family.get(c["codec"], ())))
    if c["channels"]:
        a, b = c["channels"].split(".")
        bad = bad or int(a) + int(b) != channels
    return bad


def dispositions(s):
    d = s.get("disposition") or {}
    out = {"default": bool(d.get("default")), "forced": bool(d.get("forced")), "original": bool(d.get("original")),
           "dub": bool(d.get("dub")), "commentary": bool(d.get("comment")),
           "hearingImpaired": bool(d.get("hearing_impaired")), "visualImpaired": bool(d.get("visual_impaired"))}
    if d.get("attached_pic"):
        out["attachedPic"] = True
    for raw, key in (("lyrics", "lyrics"), ("captions", "captions"), ("descriptions", "descriptions")):
        if d.get(raw):
            out[key] = True
    return out


def subtitle_purpose(s):
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
    fmt = "dolby-vision" if dovi else "hdr10" if trc == "smpte2084" else "hlg" if trc == "arib-std-b67" else "sdr"
    out = {"format": fmt, "masteringDisplay": None, "contentLightLevel": None, "dolbyVision": None}
    if mastering:
        out["masteringDisplay"] = {k: v for k, v in mastering.items() if k != "side_data_type"}
    if cll:
        out["contentLightLevel"] = {k: v for k, v in cll.items() if k != "side_data_type"}
    if dovi:
        out["dolbyVision"] = {"profile": num(dovi.get("dv_profile")) or 0, "level": num(dovi.get("dv_level")) or 0,
                              "rpuPresent": bool(dovi.get("rpu_present_flag")),
                              "elPresent": bool(dovi.get("el_present_flag")),
                              "blPresent": bool(dovi.get("bl_present_flag")),
                              "blCompatibilityId": num(dovi.get("dv_bl_signal_compatibility_id")) or 0}
    return out


def stereo3d(s):
    tags = s.get("tags") or {}
    mode = (tags.get("stereo_mode") or tags.get("STEREO_MODE") or "").lower()
    for x in s.get("side_data_list") or []:
        if "Stereo 3D" in (x.get("side_data_type") or ""):
            mode = (x.get("type") or mode or "").lower()
    return None if not mode or mode in ("mono", "2d") else mode


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
    base = {"index": s["index"], "codec": s.get("codec_name"), "language": language, "languageRaw": raw,
            "title": text(tags.get("title")), "dispositions": dispositions(s)}
    if t == "video":
        base.update({
            "type": "video", "profile": s.get("profile"),
            "level": s.get("level") if isinstance(s.get("level"), (int, float)) and s.get("level") >= 0 else None,
            "bitDepth": bit_depth(s), "pixelFormat": s.get("pix_fmt"), "width": s.get("width"),
            "height": s.get("height"), "sampleAspectRatio": ratio(s.get("sample_aspect_ratio")),
            "displayAspectRatio": ratio(s.get("display_aspect_ratio")),
            "frameRate": s.get("avg_frame_rate") if s.get("avg_frame_rate") not in (None, "0/0") else s.get("r_frame_rate"),
            "fieldOrder": s.get("field_order"), "bitrate": num(s.get("bit_rate")),
            "colour": {"primaries": s.get("color_primaries"), "transfer": s.get("color_transfer"),
                       "matrix": s.get("color_space"), "range": s.get("color_range")},
            "hdr": hdr_info(s), "stereo3d": stereo3d(s), "closedCaptions": bool(s.get("closed_captions")),
            "encoder": text(tags.get("ENCODER") or tags.get("encoder")),
        })
        return base
    if t == "audio":
        codec = (s.get("codec_name") or "").lower()
        profile = s.get("profile") or ""
        channels = num(s.get("channels")) or 1
        c = claim(tags.get("title"))
        if c is not None:
            c["contradictsActual"] = claim_contradicts(c, codec, channels)
        base.update({
            "type": "audio", "profile": profile or None, "channels": channels,
            "channelLayout": s.get("channel_layout"), "sampleRate": num(s.get("sample_rate")),
            "bitDepth": num(s.get("bits_per_raw_sample")) or None,
            "bitrate": num(s.get("bit_rate")) or num(tags.get("BPS")),
            "lossless": codec in LOSSLESS_AUDIO or (codec == "dts" and "MA" in profile),
            "objectAudio": "atmos" if "atmos" in profile.lower() else "dts-x" if "dts:x" in profile.lower() else "none",
            "titleClaim": c, "encoder": text(tags.get("ENCODER") or tags.get("encoder")),
        })
        base["purpose"], base["purposeFrom"] = audio_purpose(s)
        base["variant"] = title_variant(tags.get("title"))
        return base
    if t == "subtitle":
        codec = (s.get("codec_name") or "").lower()
        events = next((num(v) for k, v in tags.items() if k.upper().startswith("NUMBER_OF_FRAMES")), None)
        base.update({"type": "subtitle", "form": "image" if codec in IMAGE_SUBS else "text",
                     "styled": codec in ("ass", "ssa"), "variant": title_variant(tags.get("title")),
                     "events": events})
        base["purpose"], base["purposeFrom"] = subtitle_purpose(s)
        return base
    if t == "attachment":
        fname, mime = tags.get("filename"), tags.get("mimetype")
        role = "font" if (mime and "font" in mime) or (fname and re.search(r"\.(ttf|otf|ttc)$", fname, re.I)) \
            else "cover" if (mime and mime.startswith("image/")) else "other"
        base.update({"type": "attachment", "filename": text(fname), "mimetype": text(mime), "role": role})
        return base
    base.update({"type": "data"})
    return base


def infer_forced_by_size(streams):
    """An untitled, unflagged subtitle with a small fraction of the events of the full track in its
    language translates a few lines, not the film: it is a forced track."""
    subs = [x for x in streams if x["type"] == "subtitle"]
    for x in subs:
        if x["purposeFrom"] != "assumed" or not x.get("events"):
            continue
        peers = [y["events"] for y in subs if y is not x and y.get("events")
                 and primary_language(y.get("language")) == primary_language(x.get("language"))]
        if peers and max(peers) >= 300 and x["events"] < 0.1 * max(peers):
            x["purpose"], x["purposeFrom"] = "forced", "content"


# ---------------------------------------------------------------- essence
def track_essence(audio, subs, closed_captions):
    def langs(items, purposes):
        return sorted({lang(x.get("language"))[0] for x in items if x.get("purpose") in purposes and x.get("language")})
    return {"commentarySubtitles": sum(1 for x in subs if x.get("purpose") == "commentary"),
            "audioDescriptionTracks": sum(1 for x in audio if x.get("purpose") == "description"),
            "sdhSubtitleLanguages": langs(subs, ("sdh",)),
            "forcedSubtitleLanguages": langs(subs, ("forced", "signs-songs")),
            "closedCaptions": bool(closed_captions)}


def source_essence(streams, chapters):
    v = [x for x in streams if x["type"] == "video" and not x["dispositions"].get("attachedPic")]
    a = [x for x in streams if x["type"] == "audio"]
    sub = [x for x in streams if x["type"] == "subtitle"]
    v0 = v[0] if v else {}
    hdr = v0.get("hdr") or {}
    return {
        "maxAudioChannels": max([x["channels"] for x in a], default=0),
        "surround": any(x["channels"] > 2 for x in a),
        "losslessAudio": any(x.get("lossless") for x in a),
        "objectAudio": any(x.get("objectAudio") in ("atmos", "dts-x") for x in a),
        "maxVideoHeight": v0.get("height"), "videoBitDepth": v0.get("bitDepth"),
        "hdr10Metadata": bool(hdr.get("masteringDisplay") or hdr.get("contentLightLevel")),
        "dolbyVision": hdr.get("format") == "dolby-vision", "stereo3d": bool(v0.get("stereo3d")),
        "interlaced": (v0.get("fieldOrder") or "progressive") not in ("progressive", "unknown", None),
        "audioLanguages": sorted({x["language"] for x in a if x.get("language")}),
        "subtitleLanguages": sorted({x["language"] for x in sub if x.get("language")}),
        "subtitleTracks": len(sub), "imageSubtitles": any(x.get("form") == "image" for x in sub),
        "styledSubtitles": any(x.get("styled") for x in sub),
        "fonts": any(x["type"] == "attachment" and x.get("role") == "font" for x in streams),
        "chapters": bool(chapters),
        "commentaryTracks": sum(1 for x in a if x.get("purpose") == "commentary"),
        **track_essence(a, sub, v0.get("closedCaptions", False)),
    }


def package_essence(man):
    ren = man.get("renditions") or {}
    vids, auds = ren.get("video") or [], ren.get("audio") or []
    subs = man.get("subtitles") or []
    v0 = vids[0] if vids else {}
    codec = v0.get("codec") or ""
    return {
        "maxAudioChannels": max([x.get("channels") or 0 for x in auds], default=0),
        "surround": any((x.get("channels") or 0) > 2 for x in auds),
        "losslessAudio": False, "objectAudio": False, "maxVideoHeight": v0.get("height"),
        "videoBitDepth": 10 if codec.startswith(("hev1.2", "hvc1.2")) else (8 if codec else None),
        "hdr10Metadata": False, "dolbyVision": False, "stereo3d": False, "interlaced": False,
        "audioLanguages": sorted({lang(x.get("language"))[0] for x in auds if x.get("language")}),
        "subtitleLanguages": sorted({lang(x.get("language"))[0] for x in subs if x.get("language")}),
        "subtitleTracks": len(subs),
        "imageSubtitles": any((x.get("format") or "") in ("pgs", "sup", "vobsub", "dvb") for x in subs),
        "styledSubtitles": False, "fonts": False, "chapters": False,
        "commentaryTracks": sum(1 for x in auds if x.get("purpose") == "commentary"),
        **track_essence(auds, subs, False),
    }


CODEC_FAMILY = [("aac", r"^(mp4a\.40|aac)"), ("ac3", r"^(ac-3|ac3)"), ("eac3", r"^(ec-3|eac3)"),
                ("opus", r"^opus"), ("flac", r"^(fLaC|flac)"), ("alac", r"^alac"), ("truehd", r"^(truehd|mlp)"),
                ("dts", r"^dts"), ("hevc", r"^(hev1|hvc1|hevc)"), ("h264", r"^(avc1|avc3|h264)"),
                ("av1", r"^av0?1"), ("vp9", r"^vp0?9")]


def codec_family(codec):
    """'mp4a.40.2' and 'aac' are the same codec under two names; a package advertises the MP4 name
    and a probe reports the library name, so they are compared as families."""
    c = (codec or "").strip().lower()
    for family, pat in CODEC_FAMILY:
        if re.match(pat, c):
            return family
    return c


def losses_against(src, pkg_essence, streams, renditions, subs):
    """Every way the package is poorer than the original, in the package record's own terms."""
    out = []
    a = [x for x in streams if x["type"] == "audio"]
    v = [x for x in streams if x["type"] == "video" and not x["dispositions"].get("attachedPic")]
    sub = [x for x in streams if x["type"] == "subtitle"]
    auds = renditions.get("audio") or []
    vids = renditions.get("video") or []
    if src.get("maxAudioChannels", 0) > pkg_essence.get("maxAudioChannels", 0):
        out.append({"kind": "audio-downmix",
                    "detail": f"{src['maxAudioChannels']}ch -> {pkg_essence.get('maxAudioChannels', 0)}ch",
                    "sourceStreamIndex": a[0]["index"] if a else None})
    codecs = sorted({codec_family(x.get("codec")) for x in a})
    packaged = sorted({codec_family(x.get("codec")) for x in auds})
    if codecs and any(c not in codecs for c in packaged):
        out.append({"kind": "audio-codec", "detail": f"{'/'.join(codecs)} -> {'/'.join(packaged)}",
                    "sourceStreamIndex": a[0]["index"] if a else None})
    if len(a) > len(auds):
        out.append({"kind": "audio-dropped", "detail": f"{len(a) - len(auds)} of {len(a)} audio track(s) not packaged",
                    "sourceStreamIndex": None})
    if v and vids and (v[0].get("height") or 0) > (vids[0].get("height") or 0):
        out.append({"kind": "video-resolution", "detail": f"{v[0]['height']}p -> {vids[0]['height']}p",
                    "sourceStreamIndex": v[0]["index"]})
    if (src.get("videoBitDepth") or 0) > (pkg_essence.get("videoBitDepth") or 0):
        out.append({"kind": "video-bitdepth",
                    "detail": f"{src['videoBitDepth']}bit -> {pkg_essence.get('videoBitDepth')}bit",
                    "sourceStreamIndex": v[0]["index"] if v else None})
    if src.get("hdr10Metadata") and not pkg_essence.get("hdr10Metadata"):
        out.append({"kind": "dynamic-range", "detail": "HDR10 metadata not carried into the package",
                    "sourceStreamIndex": v[0]["index"] if v else None})
    if src.get("dolbyVision") and not pkg_essence.get("dolbyVision"):
        out.append({"kind": "dolby-vision", "detail": "Dolby Vision layer not carried into the package",
                    "sourceStreamIndex": v[0]["index"] if v else None})
    if src.get("stereo3d") and not pkg_essence.get("stereo3d"):
        out.append({"kind": "stereo3d", "detail": "3D presentation not carried into the package",
                    "sourceStreamIndex": v[0]["index"] if v else None})
    if len(sub) > len(subs):
        out.append({"kind": "subtitle-dropped",
                    "detail": f"{len(sub) - len(subs)} of {len(sub)} source subtitle track(s) not packaged",
                    "sourceStreamIndex": None})
    if src.get("styledSubtitles") and not pkg_essence.get("styledSubtitles"):
        out.append({"kind": "subtitle-styling", "detail": "ASS/SSA typesetting flattened", "sourceStreamIndex": None})
    if src.get("closedCaptions") and not pkg_essence.get("closedCaptions"):
        out.append({"kind": "closed-captions-dropped", "detail": "embedded CEA-608/708 captions not carried",
                    "sourceStreamIndex": v[0]["index"] if v else None})
    if src.get("fonts") and not pkg_essence.get("fonts"):
        out.append({"kind": "attachments-dropped", "detail": "attached fonts not carried", "sourceStreamIndex": None})
    return out


# ---------------------------------------------------------------- images
def sniff_image(b):
    """(content type, width, height) from the header alone; (None, None, None) when unrecognised."""
    if b[:8] == b"\x89PNG\r\n\x1a\n" and len(b) >= 24:
        return "image/png", int.from_bytes(b[16:20], "big"), int.from_bytes(b[20:24], "big")
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        chunk = b[12:16]
        if chunk == b"VP8X" and len(b) >= 30:
            return "image/webp", 1 + int.from_bytes(b[24:27], "little"), 1 + int.from_bytes(b[27:30], "little")
        if chunk == b"VP8 " and len(b) >= 30:
            return "image/webp", int.from_bytes(b[26:28], "little") & 0x3FFF, int.from_bytes(b[28:30], "little") & 0x3FFF
        if chunk == b"VP8L" and len(b) >= 25:
            v = int.from_bytes(b[21:25], "little")
            return "image/webp", (v & 0x3FFF) + 1, ((v >> 14) & 0x3FFF) + 1
        return "image/webp", None, None
    if b[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(b):
            if b[i] != 0xFF:
                i += 1
                continue
            m = b[i + 1]
            if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                return "image/jpeg", int.from_bytes(b[i + 7:i + 9], "big"), int.from_bytes(b[i + 5:i + 7], "big")
            if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
                i += 2
                continue
            i += 2 + int.from_bytes(b[i + 2:i + 4], "big")
        return "image/jpeg", None, None
    return None, None, None


# ---------------------------------------------------------------- writing
class Writer:
    """Every record is written to a temporary file in its own folder and renamed into place, so a
    reader never sees half a file. --dry-run reports the same work and touches nothing."""

    def __init__(self, dry_run):
        self.dry_run = dry_run
        self.written = self.placed = self.bytes_placed = 0

    def mkdir(self, d):
        if not self.dry_run:
            os.makedirs(d, exist_ok=True)

    def write(self, path, data):
        self.written += 1
        if self.dry_run:
            return
        self.mkdir(os.path.dirname(path))
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)

    def write_json(self, path, doc):
        self.write(path, (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))

    def place(self, src, dst, mode):
        """Move or copy one file into the library. Re-running is safe: a file already in place with
        the same size is left alone."""
        if os.path.isfile(dst) and (not os.path.isfile(src) or os.path.getsize(dst) == os.path.getsize(src)):
            return True
        if not os.path.isfile(src):
            return False
        self.placed += 1
        self.bytes_placed += os.path.getsize(src)
        if self.dry_run:
            return True
        self.mkdir(os.path.dirname(dst))
        if mode == "move":
            shutil.move(src, dst)
        else:
            shutil.copy2(src, dst)
        return True

    def prune(self, d, keep):
        """Drop files in a folder the record no longer names — a projection replaces the whole set."""
        if self.dry_run or not os.path.isdir(d):
            return
        for name in listdir(d):
            if name not in keep and os.path.isfile(os.path.join(d, name)):
                os.unlink(os.path.join(d, name))


def walk_files(root):
    """Every file under root, relative to it, in a stable order and without OS artefacts."""
    out = []
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if not OS_ARTEFACTS.match(x))
        out += [os.path.relpath(os.path.join(base, f), root) for f in sorted(files) if not OS_ARTEFACTS.match(f)]
    return sorted(out)


# ---------------------------------------------------------------- ffprobe
def have_ffprobe():
    return shutil.which("ffprobe") is not None


def ffprobe(path):
    """The verbatim probe, or None. Reads headers only, so it is cheap even over NFS."""
    try:
        r = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format",
                            "-show_streams", "-show_chapters", path],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=300)
        return json.loads(r.stdout) if r.returncode == 0 and r.stdout else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def ffprobe_version():
    try:
        r = subprocess.run(["ffprobe", "-version"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30)
        return text(r.stdout.decode("utf-8", "replace").splitlines()[0]) if r.returncode == 0 else None
    except (OSError, IndexError, subprocess.SubprocessError):
        return None


# ---------------------------------------------------------------- the build
class Build:
    def __init__(self, args, export):
        self.a = args
        self.export = export
        self.w = Writer(args.dry_run)
        self.as_of = ts(args.as_of or export.get("exportedAt")) or ts(datetime.datetime.now(
            datetime.timezone.utc).replace(microsecond=0).isoformat())
        self.probe_version = ffprobe_version() if have_ffprobe() else None
        self.notes = []
        self.skipped = []
        self.counts = {"items": 0, "sources": 0, "versions": 0, "packages": 0, "images": 0, "probed": 0}

    def note(self, item_id, msg):
        self.notes.append(f"{item_id}: {msg}")

    # -------------------------------------------------- paths
    def under(self, base, path, marker):
        """The catalog holds the path as the services see it; the share may be mounted elsewhere."""
        if os.path.exists(path):
            return path
        p = str(path).replace("\\", "/")
        i = p.rfind("/" + marker + "/")
        if i >= 0:
            return os.path.join(base, p[i + len(marker) + 2:])
        return os.path.join(base, os.path.basename(p))

    def item_dir(self, row, by_id):
        iid = row["id"]
        if row["type"] == "movie":
            return os.path.join(self.a.out, "movies", iid[:2], iid)
        if row["type"] == "series":
            return os.path.join(self.a.out, "series", iid[:2], iid)
        parent = row.get("parentId")
        return os.path.join(self.a.out, "series", parent[:2], parent, "episodes", iid)

    # -------------------------------------------------- item.json
    def item_json(self, row):
        ids = {}
        for e in row.get("externalIds") or []:
            src, val = (e.get("source") or "").lower(), text(e.get("externalId"))
            if not val:
                continue
            if src in ("tmdb", "themoviedb"):
                ids["tmdbMovie" if row["type"] == "movie" else "tmdbTv"] = val
            elif src in ("tmdb-episode", "tmdbepisode"):
                if row["type"] == "episode":
                    ids["tmdbEpisode"] = val
            elif src in ("tmdb-season", "tmdbseason"):
                if row["type"] != "movie":
                    ids["tmdbSeason"] = val
            elif src == "imdb" and re.fullmatch(r"tt[0-9]+", val):
                ids["imdb"] = val
            elif src == "tvdb" and re.fullmatch(r"[0-9]+", val):
                ids["tvdb"] = val
            else:
                self.note(row["id"], f"external id source {src!r} has no v2 field, dropped")
        for key in ("tmdbMovie", "tmdbTv", "tmdbSeason", "tmdbEpisode"):
            if key in ids and not re.fullmatch(r"[0-9]+", ids[key]):
                self.note(row["id"], f"{key} {ids[key]!r} is not a number, dropped")
                del ids[key]
        doc = {"schema": "zaentrum.library.item/2", "itemId": row["id"], "type": row["type"],
               "title": text(row.get("title")) or "", "externalIds": ids,
               "createdAt": ts(row.get("createdAt")) or self.as_of}
        if text(row.get("createdBy")):
            doc["createdBy"] = text(row["createdBy"])
        doc["provenance"] = {"migratedFrom": "legacy-catalog", "legacyItemId": row["id"], "migratedAt": self.as_of,
                             "legacyCreatedBy": text(row.get("createdBy"))}
        if row["type"] == "episode":
            doc["seriesId"] = row["parentId"]
            doc["seasonNumber"] = int(row["seasonNumber"])
            doc["episodeNumber"] = int(row["episodeNumber"])
            doc["episodeCode"] = f"S{int(row['seasonNumber']):02d}E{int(row['episodeNumber']):02d}"
        return doc

    # -------------------------------------------------- metadata.json and the images
    def metadata_json(self, row, d, version_ids, episodes):
        lg = self.a.text_language
        localized = {}
        body = {}
        if text(row.get("title")):
            body["title"] = text(row["title"])
        if text(row.get("sortTitle")):
            body["sortTitle"] = text(row["sortTitle"])
        if row.get("tagline"):
            body["tagline"] = str(row["tagline"]).strip()
        if row.get("description"):
            body["overview"] = str(row["description"]).strip()
        if body:
            localized[lg] = body
        doc = {"schema": "zaentrum.library.metadata/2", "itemId": row["id"], "type": row["type"],
               "asOf": self.as_of, "projectedBy": "library-v2-from-catalog",
               "titles": {"primary": text(row.get("title")) or "", "original": None,
                          "sort": text(row.get("sortTitle")), "qualifier": None, "localized": localized},
               "releaseDate": str(row["year"]) if row.get("year") else None,
               "genres": sorted({g for g in (row.get("genres") or []) if g}),
               "tags": sorted({t for t in (row.get("tags") or []) if t}),
               "rating": row["rating"] if isinstance(row.get("rating"), (int, float)) and 0 <= row["rating"] <= 10 else None,
               "contentRating": None}
        credits = []
        for p in row.get("people") or []:
            pid = text(p.get("personId"))
            if not pid or not re.fullmatch(r"[0-9a-f-]{36}", pid):
                continue
            credits.append({"personId": pid, "name": text(p.get("name")) or "", "role": text(p.get("role")) or "actor",
                            "character": None, "order": None, "tmdbPerson": None})
        doc["credits"] = credits
        if row["type"] == "movie":
            doc["collection"] = None
        if row["type"] == "series":
            seasons = sorted({int(e["seasonNumber"]) for e in episodes if e.get("seasonNumber") is not None})
            doc["series"] = {"seasons": [{"number": n, "tmdbSeason": None, "name": None, "overview": None,
                                          "airDate": None, "episodeCountReference": None} for n in seasons]}
        library = {"match": {"status": "matched" if any(k.startswith("tmdb") for k in
                                                        (self.item_json(row)["externalIds"])) else "unmatched",
                             "decidedBy": "legacy-catalog", "decidedAt": self.as_of}}
        if version_ids:
            library["primaryVersionId"] = version_ids[0]
        if row.get("durationMs"):
            library["reference"] = {"runtimeMs": int(row["durationMs"]), "runtimeSource": "legacy-catalog"}
        if row["type"] == "series":
            library["defaultOrdering"] = "aired"
        if row["type"] == "episode":
            library["numbering"] = {"aired": {"season": int(row["seasonNumber"]),
                                              "episode": int(row["episodeNumber"]), "episodeEnd": None}}
        doc["library"] = library
        doc["images"] = self.images(row, d)
        doc["videos"] = self.videos(row)
        doc["curation"] = {"metadataLocked": bool(row.get("metadataLocked")), "lockedFields": [], "notes": None}
        origins = {}
        for field, value in (("titles.primary", doc["titles"]["primary"]), ("titles.sort", doc["titles"]["sort"]),
                             ("releaseDate", doc["releaseDate"]), ("genres", doc["genres"]), ("tags", doc["tags"]),
                             ("rating", doc["rating"]), ("credits", doc["credits"]), ("images", doc["images"]),
                             ("videos", doc["videos"])):
            if value:
                origins[field] = "legacy-catalog"
        doc["fieldOrigins"] = origins
        return doc

    def images(self, row, d):
        md = os.path.join(d, "metadata")
        out, seen = [], {}
        for art in row.get("artwork") or []:
            kind = (art.get("kind") or "").lower()
            if kind not in IMAGE_KINDS:
                self.note(row["id"], f"artwork kind {kind!r} is not one a v2 record holds, dropped")
                continue
            try:
                raw = base64.b64decode(art.get("base64") or "", validate=False)
            except (ValueError, TypeError):
                raw = b""
            if not raw:
                self.note(row["id"], f"{kind} artwork has no bytes, dropped")
                continue
            ctype, w, h = sniff_image(raw)
            if ctype not in EXT_OF:
                self.note(row["id"], f"{kind} artwork is not a JPEG, PNG or WebP, dropped")
                continue
            digest = hashlib.sha256(raw).hexdigest()
            name = f"{digest}.{EXT_OF[ctype]}"
            if name in seen:
                self.note(row["id"], f"{kind} artwork is byte-identical to the {seen[name]}, listed once")
                continue
            seen[name] = kind
            self.w.write(os.path.join(md, name), raw)
            self.counts["images"] += 1
            entry = {"kind": kind, "file": name, "sha256": "sha256:" + digest, "contentType": ctype,
                     "sizeBytes": len(raw), "width": w, "height": h, "language": None, "sourceUrl": None,
                     "fetchedAt": ts(art.get("fetchedAt")), "origin": "legacy-catalog"}
            if row["type"] == "series":
                entry["season"] = None
            out.append(entry)
        self.w.prune(md, set(seen))
        return out

    def videos(self, row):
        out = []
        for t in row.get("trailers") or []:
            site = text(t.get("site")) or text(t.get("source"))
            key = text(t.get("externalId"))
            if not key and t.get("url"):
                key = text(str(t["url"]).rsplit("/", 1)[-1])
            if not site or not key:
                self.note(row["id"], "a trailer names neither a site nor an id, dropped")
                continue
            source = (text(t.get("source")) or "").lower()
            out.append({"site": site, "key": key, "url": text(t.get("url")), "name": text(t.get("title")),
                        "kind": "trailer", "language": None,
                        "durationMs": int(t["durationSec"]) * 1000 if num(t.get("durationSec")) else None,
                        "publishedAt": None,
                        "origin": source if source in ("tmdb", "manual") else "legacy-catalog"})
        return out

    # -------------------------------------------------- sources
    def source(self, row, d, asset):
        """One original as the file itself reports it. Returns (record, path on disk) or (None, why)."""
        path = self.under(self.a.media, asset["path"], "media")
        if not os.path.isfile(path):
            return None, f"original {asset['path']} is not on this share"
        sid = did(row["id"], "source", os.path.basename(path))
        rel = os.path.relpath(path, self.a.media) if path.startswith(os.path.abspath(self.a.media) + os.sep) \
            else str(asset["path"]).lstrip("/")
        folder = os.path.dirname(rel)
        name = os.path.basename(path)
        rec = {"schema": "zaentrum.library.source/2", "sourceId": sid, "takenAt": self.as_of,
               "takenBy": "library-v2-from-catalog",
               "file": {"name": name, "kind": "stream-container", "sizeBytes": os.path.getsize(path),
                        "mtime": ts_of_mtime(path), "fixity": {"qh1": qh1(path)}},
               "origin": {"libraryPath": rel, "takenBy": {"copy": "copy", "move": "move"}.get(self.a.media_mode, "unknown")}}
        if folder:
            rec["origin"]["folder"] = folder
        if self.a.media_mode == "none":
            del rec["origin"]["takenBy"]
        named = naming(name)
        if named:
            rec["naming"] = named
        rec["labels"] = labels(name, folder)
        probe = ffprobe(path) if self.probe_version else None
        if probe:
            self.counts["probed"] += 1
            fmt = probe.get("format") or {}
            tags = {k: text(v) for k, v in (fmt.get("tags") or {}).items() if text(v)}
            streams = [norm_stream(s) for s in probe.get("streams") or []]
            infer_forced_by_size(streams)
            chapters = probe.get("chapters") or []
            duration = fmt.get("duration")
            rec["container"] = {"format": fmt.get("format_name") or "",
                                "durationMs": int(float(duration) * 1000) if duration else None,
                                "bitrate": num(fmt.get("bit_rate")), "title": tags.get("title"),
                                "muxingApp": tags.get("muxing_application") or tags.get("encoder"),
                                "writingApp": tags.get("writing_application"),
                                "creationTime": tags.get("creation_time"), "tags": tags}
            rec["fidelity"] = self.fidelity(streams, rec["container"])
            rec["streams"] = streams
            rec["sidecars"] = []
            rec["covers"] = []
            rec["essence"] = source_essence(streams, chapters)
            pf = f"sources/{sid}/ffprobe.json"
            raw = (json.dumps(probe, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
            self.w.write(os.path.join(d, pf), raw)
            rec["probe"] = {"tool": "ffprobe", "version": self.probe_version, "at": self.as_of, "file": pf,
                            "sha256": sha_bytes(raw), "note": None}
            rec["_chapters"] = [{"startMs": int(float(c.get("start_time", 0)) * 1000),
                                 "endMs": int(float(c.get("end_time", 0)) * 1000),
                                 "title": text((c.get("tags") or {}).get("title"))} for c in chapters]
        else:
            rec["container"] = {"format": "", "durationMs": None, "bitrate": None, "title": None,
                                "muxingApp": None, "writingApp": None, "creationTime": None, "tags": {}}
            rec["fidelity"] = {"class": "unknown", "fingerprint": None, "evidence": []}
            rec["streams"] = []
            rec["sidecars"] = []
            rec["covers"] = []
            rec["essence"] = {}
            rec["probe"] = {"tool": "ffprobe", "version": None, "at": None, "file": None, "sha256": None,
                            "note": "ffprobe was not available where this record was written; the original's "
                                    "streams, fidelity and essence are unknown"}
            rec["_chapters"] = []
            self.note(row["id"], f"{name} was not probed: streams, fidelity and essence stay empty")
        return rec, path

    def fidelity(self, streams, container):
        v = next((x for x in streams if x["type"] == "video" and not x["dispositions"].get("attachedPic")), None)
        evidence = []
        if v and v.get("encoder"):
            evidence.append({"signal": "encoder-tag", "value": v["encoder"], "weight": 0.9})
        for key in ("writingApp", "muxingApp"):
            if container.get(key) and RE_ENCODE.search(container[key]):
                evidence.append({"signal": "container-title", "value": container[key], "weight": 0.5,
                                 "note": key})
        for a in [x for x in streams if x["type"] == "audio"]:
            if (a.get("titleClaim") or {}).get("contradictsActual"):
                evidence.append({"signal": "codec-vs-title", "value": a.get("title"), "weight": 0.8})
        fingerprint = None
        if v:
            hdr = (v.get("hdr") or {}).get("format") or "sdr"
            fingerprint = "/".join(str(x) for x in [v.get("codec") or "?", (v.get("profile") or "?").lower(),
                                                    f"{v.get('bitDepth') or '?'}bit", hdr,
                                                    f"{v.get('width') or '?'}x{v.get('height') or '?'}"])
        return {"class": "derivative" if evidence else "unknown", "fingerprint": fingerprint, "evidence": evidence}

    # -------------------------------------------------- versions and packages
    def version(self, row, d, asset, source, source_path, index):
        pkg_manifest_path = self.under(self.a.packages, asset["path"], "packages")
        pkg_dir = os.path.dirname(pkg_manifest_path)
        if not os.path.isdir(pkg_dir):
            return None, f"package folder {asset['path']} is not on this share"
        if not os.path.isfile(os.path.join(pkg_dir, ".complete")):
            return None, f"package {os.path.relpath(pkg_dir, self.a.packages)} has no .complete marker"
        man = {}
        if os.path.isfile(pkg_manifest_path):
            try:
                man = json.load(open(pkg_manifest_path, encoding="utf-8"))
            except (OSError, ValueError) as e:
                return None, f"package manifest could not be read: {e}"
        vid = did(row["id"], "version", os.path.relpath(pkg_dir, self.a.packages))
        pid = did(row["id"], "package", os.path.relpath(pkg_dir, self.a.packages))
        vp = os.path.join(d, "versions", vid)

        keep_original = self.a.media_mode in ("copy", "move") and source_path is not None
        original_files = [source["file"]["name"]] if keep_original else []
        streams = source.get("streams") or []
        chapters = [c for c in (row.get("chapters") or []) if c.get("startMs") is not None]
        marks = [{"startMs": int(c["startMs"]), "endMs": int(c.get("endMs") or c["startMs"]),
                  "title": text(c.get("title"))} for c in sorted(chapters, key=lambda c: c.get("ordinal") or 0)]
        marks_from = "legacy-catalog" if marks else None
        if not marks and source.get("_chapters"):
            marks = source["_chapters"]
            marks_from = "original-file" if marks else None
        segments = []
        for s in row.get("segments") or []:
            kind = (s.get("kind") or "other").lower()
            segments.append({"kind": kind if kind in SEGMENT_KINDS else "other", "startMs": int(s["startMs"]),
                             "endMs": int(s.get("endMs") or s["startMs"]), "detector": text(s.get("source")),
                             "confidence": s["confidence"] if isinstance(s.get("confidence"), (int, float)) else None,
                             "label": text(s.get("label"))})
        v0 = next((x for x in streams if x["type"] == "video" and not x["dispositions"].get("attachedPic")), None)
        name = source["file"]["name"]
        kind = edition_word(name) or edition_word(source["labels"].get("folderEdition"))
        edition = {"kind": kind or "unknown", "label": text(source["labels"].get("folderEdition")),
                   "decidedBy": "inferred", "decidedAt": self.as_of,
                   "evidence": [{"signal": "filename", "value": name, "weight": 0.6}] if kind else []}
        dynamic = (v0.get("hdr") or {}).get("format") if v0 else None
        three_d = (v0 or {}).get("stereo3d")
        presentation = {
            "colour": "unknown",
            "dynamicRange": dynamic if dynamic in ("sdr", "hdr10", "hdr10plus", "hlg", "dolby-vision") else "unknown",
            "stereo3d": "none" if v0 is not None and not three_d else
                        {"side_by_side": "side-by-side", "sbs": "side-by-side", "top_and_bottom": "top-and-bottom",
                         "tb": "top-and-bottom", "mvc": "mvc"}.get(three_d or "", "unknown"),
            "aspectRatio": (v0 or {}).get("displayAspectRatio"),
        }
        version = {"schema": "zaentrum.library.version/2", "versionId": vid,
                   "createdAt": ts(man.get("packagedAt")) or self.as_of, "createdBy": "library-v2-from-catalog",
                   "edition": edition, "presentation": presentation,
                   "runtimeMs": source["container"].get("durationMs"), "chapters": marks, "chaptersFrom": marks_from,
                   "segments": segments, "sourceIds": [source["sourceId"]], "originalFiles": original_files}

        # the package's own files, moved or copied in; .complete is written last, so its bytes are
        # read here and the checksums file can already cover it.
        complete = open(os.path.join(pkg_dir, ".complete"), "rb").read()
        entries, missing = [], []
        for sub in PACKAGE_DIRS:
            base = os.path.join(pkg_dir, sub)
            if not os.path.isdir(base):
                continue
            for rel in walk_files(base):
                src = os.path.join(base, rel)
                entries.append((os.path.join(sub, rel).replace(os.sep, "/"), src, sha_file(src).split(":", 1)[1],
                                os.path.getsize(src)))
        entries.append((".complete", None, hashlib.sha256(complete).hexdigest(), len(complete)))
        for entry in listdir(pkg_dir):
            if entry not in PACKAGE_DIRS and entry not in (".complete", "manifest.json", ".packaging"):
                self.note(row["id"], f"package holds {entry!r}, which is not part of a v2 version folder; left behind")
        entries.sort()
        self.w.mkdir(vp)
        for rel, src, _, _ in entries:
            if src is None:
                continue
            if not self.w.place(src, os.path.join(vp, rel), self.a.media_mode if self.a.media_mode != "none" else "copy"):
                missing.append(rel)
        if missing:
            return None, f"package files could not be placed: {', '.join(missing[:3])}"
        if keep_original:
            if not self.w.place(source_path, os.path.join(vp, name), self.a.media_mode):
                return None, f"original {name} could not be placed"

        checksums = "".join(f"{digest}  {rel}\n" for rel, _, digest, _ in entries).encode()
        self.w.write(os.path.join(vp, "checksums.sha256"), checksums)
        package = self.package(row, pid, man, asset, entries, checksums, source, keep_original, pkg_dir)
        self.w.write_json(os.path.join(vp, "version.json"), version)
        self.w.write_json(os.path.join(vp, "package.json"), package)
        self.w.write(os.path.join(vp, ".complete"), complete)
        if self.a.media_mode == "move" and not self.a.dry_run and os.path.isfile(os.path.join(pkg_dir, ".complete")):
            os.unlink(os.path.join(pkg_dir, ".complete"))
        self.counts["versions"] += 1
        self.counts["packages"] += 1
        return vid, None

    def package(self, row, pid, man, asset, entries, checksums, source, keep_original, pkg_dir):
        ren = man.get("renditions") or {}
        video = [self.video_rendition(v) for v in (ren.get("video") or [])]
        audio = [self.audio_rendition(a, i) for i, a in enumerate(ren.get("audio") or [])]
        subs = [self.subtitle_rendition(s, i) for i, s in enumerate(man.get("subtitles") or [])]
        defaults = [a for a in audio if a["default"]]
        for a in defaults[1:]:
            a["default"] = False
            self.note(row["id"], f"audio rendition {a['id']} was a second default in the package manifest; cleared")
        if audio and not defaults:
            audio[0]["default"] = True
            self.note(row["id"], "no audio rendition was default in the package manifest; the first one is")
        for s in subs:
            if s["default"] and (s["forced"] or s["purpose"] in ("forced", "signs-songs")):
                s["default"] = False
                self.note(row["id"], f"subtitle {s['id']} was a forced track flagged default; cleared")
        non_forced_default = [s for s in subs if s["default"] and not s["forced"]]
        for s in non_forced_default[1:]:
            s["default"] = False
            self.note(row["id"], f"subtitle {s['id']} was a second default; cleared")
        pkg_essence = package_essence({"renditions": {"video": video, "audio": audio}, "subtitles": subs})
        streams = source.get("streams") or []
        if streams:
            losses = losses_against(source["essence"], pkg_essence, streams,
                                    {"video": video, "audio": audio}, subs)
        else:
            losses = [{"kind": "other", "detail": "the original was not probed, so what the package failed to "
                                                  "carry was never measured", "sourceStreamIndex": None}]
        doc = {"schema": "zaentrum.library.package/2", "packageId": pid,
               "createdAt": ts(man.get("packagedAt")) or self.as_of,
               "packagedBy": text(man.get("packager")) or "unknown", "state": "complete",
               "role": "derived" if keep_original else "canonical",
               "durationMs": num(man.get("durationMs")) or num(asset.get("durationMs")) or 0,
               "renditions": {"video": video, "audio": audio}, "subtitles": subs,
               "trickplay": self.trickplay(man.get("trickplay")), "trailers": [],
               "sizeBytes": sum(size for _, _, _, size in entries),
               "peakBandwidthBps": self.peak_bandwidth(pkg_dir, asset),
               "fidelity": {"lossless": not losses, "losses": losses, "droppedSourceStreams": []},
               "essence": pkg_essence,
               "checksums": {"file": "checksums.sha256", "algorithm": "sha256", "sha256": sha_bytes(checksums),
                             "files": len(entries), "bytes": sum(size for _, _, _, size in entries)}}
        return doc

    def video_rendition(self, v):
        out = {"id": text(v.get("id")) or "v0", "dir": v.get("dir") or "hls/v0", "codec": v.get("codec") or "",
               "width": num(v.get("width")) or 0, "height": num(v.get("height")) or 0,
               "bitrateBps": num(v.get("bitrateBps")) or 0, "hdr": bool(v.get("hdr")),
               "frameRate": str(v.get("frameRate") or ""), "segments": num(v.get("segments")) or 0,
               "targetDuration": num(v.get("targetDuration")) or 0}
        if v.get("dynamicRange"):
            out["dynamicRange"] = v["dynamicRange"]
        if v.get("sourceStreamIndex") is not None:
            out["sourceStreamIndex"] = num(v["sourceStreamIndex"])
        return out

    def audio_rendition(self, a, i):
        title = text(a.get("title")) or ""
        purpose, purpose_from = audio_purpose({"disposition": {}, "tags": {"title": title}})
        out = {"id": text(a.get("id")) or f"a{i}", "dir": a.get("dir") or f"hls/a{i}", "codec": a.get("codec") or "",
               "language": rendition_language(a.get("language")), "title": title, "default": bool(a.get("default")),
               "channels": num(a.get("channels")) or 0, "bitrateBps": num(a.get("bitrateBps")) or 0,
               "segments": num(a.get("segments")) or 0, "visible": bool(a.get("visible", True)),
               "purpose": purpose, "purposeFrom": purpose_from, "variant": title_variant(title)}
        for key in ("sourceStreamIndex", "sourceChannels"):
            if a.get(key) is not None:
                out[key] = num(a[key])
        if a.get("original") is not None:
            out["original"] = bool(a["original"])
        return out

    def subtitle_rendition(self, s, i):
        title = text(s.get("title")) or ""
        purpose, purpose_from = subtitle_purpose({"disposition": {"forced": bool(s.get("forced"))},
                                                  "tags": {"title": title}})
        out = {"id": text(s.get("id")) or f"sub{i}", "path": s.get("path") or f"subs/{i}.vtt",
               "language": rendition_language(s.get("language")), "title": title, "default": bool(s.get("default")),
               "forced": bool(s.get("forced")), "format": s.get("format") or "webvtt",
               "visible": bool(s.get("visible", True)), "purpose": purpose, "purposeFrom": purpose_from,
               "variant": title_variant(title)}
        if s.get("sourceStreamIndex") is not None:
            out["sourceStreamIndex"] = num(s["sourceStreamIndex"])
        return out

    def trickplay(self, tp):
        if not tp:
            return None
        return {"vttPath": tp.get("vttPath") or "trickplay/thumbnails.vtt",
                "spritePattern": tp.get("spritePattern") or "trickplay/sprite-%04d.jpg",
                "intervalSec": num(tp.get("intervalSec")) or 10, "thumbWidth": num(tp.get("thumbWidth")) or 0,
                "thumbHeight": num(tp.get("thumbHeight")) or 0, "gridCols": num(tp.get("gridCols")) or 1,
                "gridRows": num(tp.get("gridRows")) or 1}

    def peak_bandwidth(self, pkg_dir, asset):
        """The highest BANDWIDTH the master playlist advertises; the catalog's kbps when it is gone."""
        master = os.path.join(pkg_dir, "hls", "master.m3u8")
        if os.path.isfile(master):
            peaks = [int(m) for m in re.findall(r"BANDWIDTH=(\d+)", open(master, encoding="utf-8", errors="replace").read())]
            if peaks:
                return max(peaks)
        return (num(asset.get("bitrateKbps")) or 0) * 1000 or None

    # -------------------------------------------------- one item
    def build(self, row, by_id, episodes):
        d = self.item_dir(row, by_id)
        item = self.item_json(row)
        if not item["title"]:
            self.skipped.append((row["id"], "the row has no title, and a record must carry the one it was created with"))
            return
        if row["type"] == "episode" and (row.get("seasonNumber") is None or row.get("episodeNumber") is None
                                         or not row.get("parentId")):
            self.skipped.append((row["id"], "an episode row without a series, a season or an episode number "
                                            "cannot be an episode record"))
            return
        self.w.mkdir(d)
        version_ids = []
        if row["type"] != "series":
            primary = next((a for a in row.get("playbackAssets") or [] if a.get("kind") == "primary"), None)
            packaged = [a for a in row.get("playbackAssets") or [] if a.get("kind") == "packaged"]
            source, source_path = (None, None)
            if primary:
                source, source_path = self.source(row, d, primary)
                if source is None:
                    self.note(row["id"], source_path)
                    source, source_path = None, None
            if source is None and packaged:
                self.note(row["id"], "no original could be read, so its versions and packages are not recorded: "
                                     "a version names a source record, and a source record carries the file's fixity")
            elif source is not None:
                rec = {k: v for k, v in source.items() if not k.startswith("_")}
                self.w.write_json(os.path.join(d, "sources", source["sourceId"] + ".json"), rec)
                self.counts["sources"] += 1
                for i, asset in enumerate(sorted(packaged, key=lambda a: str(a.get("path")))):
                    vid, why = self.version(row, d, asset, source, source_path, i)
                    if vid:
                        version_ids.append(vid)
                    else:
                        self.note(row["id"], why)
        self.w.write_json(os.path.join(d, "item.json"), item)
        self.w.write_json(os.path.join(d, "metadata.json"),
                          self.metadata_json(row, d, version_ids, episodes))
        self.counts["items"] += 1


def main():
    ap = argparse.ArgumentParser(prog="library-v2-from-catalog.py",
                                 description="Write v2 library records from a catalog export.")
    ap.add_argument("--export", required=True, help="the catalog export produced on the client side")
    ap.add_argument("--packages", required=True, help="the package store the catalog's packaged assets point into")
    ap.add_argument("--media", required=True, help="the folder the catalog's primary assets point into")
    ap.add_argument("--out", required=True, help="the library root to write: movies/ and series/ go here")
    ap.add_argument("--items", default="", help="comma-separated item ids; a selected episode brings its series, "
                                                "a selected series brings its episodes")
    ap.add_argument("--media-mode", choices=("copy", "move", "none"), default="copy",
                    help="copy (default) or move the bytes into the version folder, or leave them where they are")
    ap.add_argument("--as-of", default="", help="the moment every record says it was written; the export's "
                                                "exportedAt by default, which is what makes a re-run identical")
    ap.add_argument("--text-language", default="und",
                    help="the language key the row's description and tagline are written under; 'und' by default, "
                         "because the catalog does not record what language its texts are in")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    args.out = os.path.abspath(args.out)
    args.media = os.path.abspath(args.media)
    args.packages = os.path.abspath(args.packages)

    with open(args.export, encoding="utf-8") as f:
        export = json.load(f)
    rows = export.get("items") or []
    by_id = {r["id"]: r for r in rows}
    wanted = [x.strip() for x in args.items.split(",") if x.strip()]
    if wanted:
        chosen = {x for x in wanted if x in by_id}
        for x in wanted:
            if x not in by_id:
                print(f"note: --items names {x}, which the export does not hold")
        for x in list(chosen):
            row = by_id[x]
            if row["type"] == "episode" and row.get("parentId") in by_id:
                chosen.add(row["parentId"])
            if row["type"] == "series":
                chosen |= {r["id"] for r in rows if r.get("parentId") == x}
        rows = [r for r in rows if r["id"] in chosen]

    b = Build(args, export)
    if not b.probe_version:
        print("note: ffprobe is not on PATH; source records will carry size, mtime and qh1 only")
    order = {"series": 0, "movie": 1, "episode": 2}
    for row in sorted(rows, key=lambda r: (order.get(r["type"], 3), r["id"])):
        episodes = [r for r in (export.get("items") or []) if r.get("parentId") == row["id"]] \
            if row["type"] == "series" else []
        try:
            b.build(row, by_id, episodes)
        except Exception as e:  # one unreadable item must not stop the run
            b.skipped.append((row["id"], f"{type(e).__name__}: {e}"))

    print(f"{'would write' if args.dry_run else 'wrote'}: {b.counts}")
    print(f"files placed: {b.w.placed} ({b.w.bytes_placed} bytes), records written: {b.w.written}")
    for n in b.notes:
        print("  note " + n)
    for iid, why in b.skipped:
        print(f"  skipped {iid}: {why}")
    return 1 if b.skipped else 0


if __name__ == "__main__":
    sys.exit(main())
