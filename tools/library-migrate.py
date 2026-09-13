#!/usr/bin/env python3
"""Build library documents (work / version / source / package) from a legacy catalog.

Reads extracted inputs from a directory and writes a staging tree that mirrors the
target layout, plus a plan of hard links for the large files:

  library/works/<aa>/<workId>/work.json
                              art/<kind>.<sha16>.jpg
                              versions/<versionId>/version.json
                                                   source.json
                                                   source/<original name>        (hard link)
                                                   packages/<packageId>/package.json
                                                   packages/<packageId>/...      (hard-linked package tree)
                              episodes/<episodeId>/...                           (series only)
  library/browse/{movies,series}/<Title (Year)>  ->  ../../works/<aa>/<id>      (derived view)

Nothing is invented. A field the legacy catalog never stored is left empty and its
absence is reported, so that a later re-sync can fill it and the gap stays visible.

Inputs (in --inputs):
  paths.tsv        itemId, type, parentId, season, episode, sourcePath, sourceSize, packageManifestPath
  catalog.json     rows from the legacy catalog tables
  probes.jsonl     ffprobe output and file stat per source
  fetched.jsonl    package manifests and sidecars per item
  artwork.jsonl    artwork bytes (base64) per item and kind
  colour.tsv       itemId, "sec:satmax,sec:satmax,..."  (peak chroma per sampled frame)
  tmdb.json        optional re-sync results keyed by item id
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
}

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
        "forced": bool(d.get("forced")) or "forced" in t,
        "original": bool(d.get("original")),
        "dub": bool(d.get("dub")),
        "commentary": bool(d.get("comment")) or "commentary" in t,
        "hearingImpaired": bool(d.get("hearing_impaired")) or bool(re.search(r"\bsdh\b|hearing", t)),
        "visualImpaired": bool(d.get("visual_impaired")) or "audio description" in t,
    }
    if d.get("attached_pic"):
        out["attachedPic"] = True
    return out

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
        return base
    if t == "subtitle":
        codec = (s.get("codec_name") or "").lower()
        title = tags.get("title") or ""
        m = re.search(r"\(([^)]+)\)", title)
        variant = None
        if m and not re.search(r"sdh|forced|commentary", m.group(1), re.I):
            variant = m.group(1)
        base.update({
            "type": "subtitle",
            "form": "image" if codec in IMAGE_SUBS else "text",
            "styled": codec in ("ass", "ssa"),
            "variant": variant,
        })
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

# ---------------------------------------------------------------- filename labels
QUALITY = re.compile(r"\b(Remux|Bluray|WEBDL|WEB-DL|WEBRip|HDTV|SDTV|DVD|TELESYNC|TS|CAM)[- ]?(\d{3,4}p)?\b", re.I)
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
    m = QUALITY.search(name)
    src = None
    if m:
        k = m.group(1).lower().replace("-", "")
        src = {"remux": "remux", "bluray": "bluray", "webdl": "webdl", "webrip": "webrip", "hdtv": "hdtv",
               "sdtv": "sdtv", "dvd": "dvd", "telesync": "telesync", "ts": "telesync", "cam": "cam"}.get(k, "unknown")
    rev = re.search(r"\b(v[2-9])\b", name)
    fold_ed = None
    fm = re.search(r" - ([^()]+?) \(\d{4}\)$", folder or "")
    if fm and edition_word(fm.group(1)):
        fold_ed = fm.group(1).strip()
    return {
        "quality": m.group(0) if m else None,
        "releaseSource": src,
        "resolution": (m.group(2) if m and m.group(2) else None),
        "proper": bool(re.search(r"\bproper\b", name, re.I)),
        "revision": rev.group(1) if rev else None,
        "edition": next((w for w in [edition_word(name)] if w), None),
        "folderEdition": fold_ed,
    }

def file_coords(name):
    m = re.search(r"S(\d{1,3})E(\d{1,4})(?:-?E(\d{1,4}))?", name, re.I)
    if not m:
        return None
    return {"scheme": "file", "season": int(m.group(1)), "episode": int(m.group(2)),
            "episodeEnd": int(m.group(3)) if m.group(3) else None}

# ---------------------------------------------------------------- build
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", required=True)
    ap.add_argument("--out", required=True, help="staging directory; 'library/' is created inside")
    ap.add_argument("--library-root", required=True, help="container path prefix of the source library, e.g. /var/lib/katalog/media")
    ap.add_argument("--packages-root", required=True, help="container path prefix of packages, e.g. /var/lib/katalog/packages")
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
    tmdb = json.load(open(os.path.join(I, "tmdb.json"))) if os.path.exists(os.path.join(I, "tmdb.json")) else {}

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
    playback = group("playback")
    subs = group("subtitles")
    steps = group("steps")

    lib = os.path.join(args.out, "library")
    plan = []           # hard links to create on the storage host
    report = collections.defaultdict(list)
    docs_written = collections.Counter()

    def write(rel, obj):
        p = os.path.join(lib, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)
            f.write("\n")
        docs_written[obj.get("schema", "other")] += 1

    def envelope(i, created, updated):
        return {"id": i, "rev": 1, "createdAt": created, "createdBy": "library-migrate", "updatedAt": updated, "updatedBy": "library-migrate"}

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
        return out

    def write_artwork(workdir, item):
        out = []
        meta = {a["kind"]: a for a in artmeta.get(item["id"], [])}
        for a in art_bytes.get(item["id"], []):
            b = base64.b64decode(a["b64"])
            digest = hashlib.sha256(b).hexdigest()
            rel = f"art/{a['kind']}.{digest[:16]}.jpg"
            p = os.path.join(lib, workdir, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(b)
            entry = {"kind": a["kind"], "file": rel, "sha256": "sha256:" + digest, "contentType": a["contenttype"] or "image/jpeg",
                     "sizeBytes": len(b), "inherit": "none"}
            if meta.get(a["kind"], {}).get("url"): entry["sourceUrl"] = meta[a["kind"]]["url"]
            if a.get("fetchedat"): entry["fetchedAt"] = ts(a["fetchedat"])
            out.append(entry)
        return out

    def processing(item):
        out = {}
        for s in steps.get(item["id"], []):
            st = {"done": "done", "failed": "failed", "skipped": "skipped", "not_applicable": "not-applicable",
                  "pending": "pending", "in_progress": "pending"}.get(s["status"], "pending")
            out[s["step"]] = {"status": st, "at": ts(s["finishedat"]), "attempts": s["attempts"], "error": s["error"]}
        return out

    def base_work(item, kind):
        created = ts(item["createdat"]) or NOW
        updated = ts(item["modifiedat"]) or created
        t = tmdb.get(item["id"]) or {}
        title = item["title"] or "(untitled)"
        doc = {"schema": "zaentrum.library.work/1", **envelope(item["id"], created, updated), "kind": kind}
        doc["titles"] = {
            "primary": title,
            "original": t.get("originalTitle"),
            "sort": item["sorttitle"] or title.lower(),
            "qualifier": None,
            "localized": {"en": {"title": title, "sortTitle": item["sorttitle"] or title.lower(),
                                 "tagline": item["tagline"], "overview": item["description"]}},
        }
        doc["year"] = item["year"]
        doc["releaseDate"] = t.get("releaseDate")
        doc["reference"] = {"runtimeMs": item["durationms"],
                            "runtimeSource": ("tmdb" if ext_ids(item) else "legacy-catalog") if item["durationms"] else None}
        doc["provenance"] = {"migratedFrom": "legacy-catalog", "migratedAt": NOW, "legacyItemId": item["id"],
                             "fieldOrigins": {"titles": "legacy-catalog", "reference.runtimeMs": "legacy-catalog"}}
        doc["externalIds"] = ext_ids(item)
        matched = bool(doc["externalIds"].get("tmdbMovie") or doc["externalIds"].get("tmdbTv"))
        override = t.get("matchOverride")
        if override:
            doc["match"] = {"status": "manual", "decidedBy": "human", "decidedAt": NOW, "confidence": 1.0,
                            "evidence": [{"signal": "manual", "value": {override["key"]: override["value"]}, "weight": 1.0, "note": override["note"]}]}
            doc["provenance"]["fieldOrigins"]["externalIds." + override["key"]] = "manual"
        elif kind == "episode" and (tmdb.get(item["parent_id"] or "") or {}).get("matchOverride"):
            doc["match"] = {"status": "manual", "decidedBy": "human", "decidedAt": NOW, "confidence": 1.0,
                            "evidence": [{"signal": "manual", "value": "inherited from the series match", "weight": 1.0}]}
        else:
            doc["match"] = {"status": "matched" if matched else "unmatched", "decidedBy": "legacy-catalog"}
        doc["genres"] = sorted({g["name"] for g in genres.get(item["id"], [])})
        doc["tags"] = sorted({g["tag"] for g in tags.get(item["id"], [])})
        doc["rating"] = float(item["rating"]) if item["rating"] is not None else None
        doc["contentRating"] = t.get("contentRating")
        chars = t.get("characters") or {}
        doc["credits"] = [{"personId": p["person_id"], "name": p["name"], "role": p["role"] or "actor",
                           "character": chars.get(p["name"]), "order": n, "tmdbPerson": None}
                          for n, p in enumerate(people.get(item["id"], []))]
        doc["curation"] = {"metadataLocked": False, "lockedFields": [], "notes": None}
        if t:
            for f in ("releaseDate", "titles.original", "collection", "contentRating", "series", "episode.airDate", "externalIds.tmdbEpisode"):
                doc["provenance"]["fieldOrigins"][f] = "tmdb"
        doc["processing"] = processing(item)
        for missing in ("releaseDate", "contentRating"):
            if not doc.get(missing):
                report["empty-in-legacy-catalog"].append(f"{kind}:{title}: {missing}")
        return doc

    def build_version(item, workdir):
        """One version per item today (the legacy model has no second slot)."""
        vid = did(item["id"], "version", "primary")
        sid = did(vid, "source")
        vdir = f"{workdir}/versions/{vid}"
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
        ed_ev, ed_kind, ed_label, conf = [], "unknown", None, 0.0
        for sig, text in (("folder-name", folder), ("filename", name),
                          ("container-title", (fmt.get("tags") or {}).get("title")),
                          ("stream-title", v0.get("title"))):
            w = edition_word(text)
            if w:
                ed_ev.append({"signal": sig, "value": text, "weight": 0.9})
                ed_kind, ed_label, conf = w, None, 0.9
        commentaries = [a["title"] for a in astreams if a["dispositions"].get("commentary")]
        if commentaries:
            ed_ev.append({"signal": "commentary-track", "value": commentaries[:3], "weight": 0.2,
                          "note": "commentary tracks identify the disc release, which usually names the cut"})
        delta = (measured - reference) if (measured and reference) else None
        if delta is not None:
            ed_ev.append({"signal": "runtime", "value": {"measuredMs": measured, "referenceMs": reference, "deltaMs": delta}, "weight": 0.6})
            if ed_kind == "unknown" and item["type"] == "movie":
                pct = delta / reference
                if pct >= 0.08:
                    ed_label, conf = f"Longer than the reference cut by {round(delta / 60000)} min", 0.6
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
        if not peaks:
            col = "unknown"
        elif max(peaks) <= 3:
            col = "black-and-white"
        elif max(peaks) >= 10:
            col = "colour"
        else:
            col = "unknown"
        col_decision = {"decidedBy": "inferred", "confidence": 0.8 if col != "unknown" else 0.0,
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
        elif ed_label:
            label_parts.append(ed_label)
        if col == "black-and-white":
            label_parts.append("Black & White")
        if dr in ("hdr10", "dolby-vision", "hlg"):
            label_parts.append({"hdr10": "HDR10", "dolby-vision": "Dolby Vision", "hlg": "HLG"}[dr])
        version_label = " · ".join(label_parts) or "Standard"

        # --- source document
        side = []
        for s in fe.get("sidecars") or []:
            rel = f"sidecars/{s['name']}"
            slang, _ = lang((re.search(r"\.([a-z]{2,3})(?:\.(?:sdh|forced|cc))*\.[a-z0-9]+$", s["name"], re.I) or [None, None])[1])
            side.append({"file": rel, "originalName": s["name"], "kind": s["kind"], "format": os.path.splitext(s["name"])[1][1:].lower(),
                         "language": slang, "forced": ".forced." in s["name"].lower(), "hearingImpaired": bool(re.search(r"\.(sdh|cc)\.", s["name"], re.I)),
                         "sizeBytes": s["size"], "fixity": {"qh1": s["qh1"]}})
            plan.append(("link", s["path"], f"{vdir}/{rel}"))
        default_sub = next((x for x in subs.get(item["id"], []) if x.get("isdefault")), None)
        source_doc = {
            "schema": "zaentrum.library.source/1", **envelope(sid, ts(item["createdat"]) or NOW, NOW),
            "versionId": vid, "workId": item["id"],
            "file": {
                "path": f"source/{name}", "name": name,
                "kind": "disc-image" if is_iso else "stream-container",
                "sizeBytes": stat.get("size") or 0,
                "mtime": datetime.datetime.fromtimestamp(stat["mtime"], datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z") if stat.get("mtime") else NOW,
                "fixity": {"qh1": stat.get("qh1")},
                "origin": {"libraryPath": src_path[len(args.library_root):].lstrip("/") if src_path.startswith(args.library_root) else src_path,
                           "folder": folder, "migratedAt": NOW, "method": "hardlink"},
            },
            "labels": lab,
            "container": {
                "format": fmt.get("format_name") or ("iso9660/udf" if is_iso else "unknown"),
                "durationMs": measured,
                "bitrate": num(fmt.get("bit_rate")),
                "title": (fmt.get("tags") or {}).get("title"),
                "muxingApp": (fmt.get("tags") or {}).get("encoder"),
                "writingApp": (fmt.get("tags") or {}).get("writing_application") or (fmt.get("tags") or {}).get("WRITING_APPLICATION"),
                "creationTime": (fmt.get("tags") or {}).get("creation_time"),
                "tags": {k: str(v) for k, v in (fmt.get("tags") or {}).items()},
            },
            "fidelity": {"class": fid_class, "evidence": fid_ev},
            "streams": streams,
            "chapters": [{"startMs": int(float(c["start_time"]) * 1000), "endMs": int(float(c["end_time"]) * 1000),
                          "title": (c.get("tags") or {}).get("title")} for c in probe.get("chapters") or []],
            "segments": [{"kind": s["kind"] if s["kind"] in ("intro", "recap", "credits", "preview", "commercial") else "other",
                          "startMs": s["startms"], "endMs": s["endms"], "detector": s["source"],
                          "confidence": float(s["confidence"]) if s["confidence"] is not None else None, "label": s["label"]}
                         for s in segments.get(item["id"], [])],
            "sidecars": side,
            "subtitleDecisions": {"defaultStreamIndex": None, "decidedBy": "legacy-catalog" if default_sub else None},
            "probe": {"tool": "ffprobe", "version": None, "at": NOW, "raw": probe or None,
                      "note": "disc image: streams were not demuxed; mount the image to inventory its playlists" if is_iso else None},
        }
        if not source_doc["file"]["fixity"]["qh1"]:
            raise SystemExit(f"no fixity for {src_path}")
        if default_sub:
            report["curated-subtitle-default"].append(f"{item['title']}: {default_sub.get('label') or default_sub.get('lang')}")
        if not probe.get("chapters") and chapters.get(item["id"]):
            source_doc["chapters"] = [{"startMs": c["startms"], "endMs": c["endms"], "title": c["title"]} for c in chapters[item["id"]]]
        write(f"{vdir}/source.json", source_doc)
        plan.append(("link", src_path, f"{vdir}/source/{name}"))

        # --- package document
        pkgs = []
        man = fe.get("manifest")
        if man:
            pid = did(vid, "package", fe["manifestSha256"])
            pdir = f"{vdir}/packages/{pid}"
            ren = man.get("renditions") or {}
            losses, dropped = [], []
            src_audio = {a["index"]: a for a in astreams}
            src_audio_order = [a["index"] for a in astreams]
            p_audio = []
            for n, a in enumerate(ren.get("audio") or []):
                sidx = src_audio_order[n] if n < len(src_audio_order) else None
                sch = src_audio[sidx]["channels"] if sidx is not None else None
                la, _ = lang(a.get("language"))
                p_audio.append({"id": a["id"], "codec": a.get("codec"), "channels": a.get("channels") or 2, "bitrate": a.get("bitrateBps"),
                                "language": la, "label": a.get("title") or None, "default": bool(a.get("default")),
                                "sourceStreamIndex": sidx, "sourceChannels": sch})
                if sch and a.get("channels") and a["channels"] < sch:
                    losses.append({"kind": "audio-downmix", "detail": f"{sch}ch -> {a['channels']}ch ({a.get('title') or a['id']})", "sourceStreamIndex": sidx})
                src_codec = src_audio[sidx]["codec"] if sidx is not None else None
                if src_codec and src_codec != "aac" and (a.get("codec") or "").startswith("mp4a"):
                    losses.append({"kind": "audio-codec", "detail": f"{src_codec} -> {a['codec']}", "sourceStreamIndex": sidx})
            if len(ren.get("audio") or []) < len(astreams):
                extra = src_audio_order[len(ren.get("audio") or []):]
                dropped += extra
                losses.append({"kind": "audio-dropped", "detail": f"{len(extra)} source audio track(s) not packaged", "sourceStreamIndex": None})
            p_video = []
            for vv in ren.get("video") or []:
                pdr = "hdr10" if vv.get("hdr") else "sdr"
                p_video.append({"id": vv["id"], "codec": vv.get("codec"), "width": vv.get("width"), "height": vv.get("height"),
                                "bitrate": vv.get("bitrateBps") or None, "dynamicRange": pdr, "sourceStreamIndex": v0.get("index")})
                if v0 and vv.get("height") and v0.get("height") and vv["height"] < v0["height"]:
                    losses.append({"kind": "video-resolution", "detail": f"{v0['width']}x{v0['height']} -> {vv['width']}x{vv['height']}", "sourceStreamIndex": v0.get("index")})
                if dr == "dolby-vision":
                    losses.append({"kind": "dolby-vision", "detail": "Dolby Vision metadata is not carried; manifest records only an hdr flag", "sourceStreamIndex": v0.get("index")})
                if dr in ("hdr10", "dolby-vision", "hlg") and not vv.get("hdr"):
                    losses.append({"kind": "dynamic-range", "detail": f"{dr} -> sdr", "sourceStreamIndex": v0.get("index")})
            s_subs = [s for s in streams if s["type"] == "subtitle"]
            p_subs = []
            for s in man.get("subtitles") or []:
                la, _ = lang(s.get("language"))
                p_subs.append({"id": s["id"], "format": s.get("format"), "language": la, "forced": bool(s.get("forced")),
                               "default": bool(s.get("default")), "sourceStreamIndex": None})
            if len(p_subs) < len(s_subs):
                losses.append({"kind": "subtitle-dropped", "detail": f"{len(s_subs) - len(p_subs)} of {len(s_subs)} source subtitle track(s) not packaged", "sourceStreamIndex": None})
            if any(s.get("styled") for s in s_subs):
                losses.append({"kind": "subtitle-styling", "detail": "ASS/SSA styling flattened to WebVTT", "sourceStreamIndex": None})
            if any(s["type"] == "attachment" for s in streams):
                losses.append({"kind": "attachments-dropped", "detail": "embedded fonts/images are not carried into the package", "sourceStreamIndex": None})
            if (probe.get("chapters") or chapters.get(item["id"])):
                losses.append({"kind": "chapters-dropped", "detail": "chapters are not in the package manifest (kept in source.json)", "sourceStreamIndex": None})
            pkg_doc = {
                "schema": "zaentrum.library.package/1", **envelope(pid, ts(man.get("packagedAt")) or NOW, NOW),
                "versionId": vid, "workId": item["id"], "sourceId": sid,
                "state": "complete" if fe.get("packageComplete") else "failed",
                "manifest": {"file": "manifest.json", "version": man.get("version") or 0, "sha256": fe["manifestSha256"]},
                "recipe": {"packager": man.get("packager"), "packagedAt": ts(man.get("packagedAt")),
                           "video": "copy or re-encode (not recorded in manifest v2)",
                           "audio": "aac-lc 2ch 192k" if all((a.get("channels") == 2 and a.get("bitrateBps") == 192000) for a in ren.get("audio") or []) else None,
                           "subtitles": "text -> webvtt, image -> sidecar", "segmentSeconds": (ren.get("video") or [{}])[0].get("targetDuration")},
                "renditions": {"video": p_video, "audio": p_audio, "subtitles": p_subs},
                "fidelity": {"lossless": not losses, "losses": losses, "droppedSourceStreams": dropped},
            }
            write(f"{pdir}/package.json", pkg_doc)
            plan.append(("linktree", fe["packageDir"], pdir))
            pkgs.append({"id": pid, "document": f"packages/{pid}/package.json", "state": pkg_doc["state"], "lossless": not losses})

        version_doc = {
            "schema": "zaentrum.library.version/1", **envelope(vid, ts(item["createdat"]) or NOW, NOW),
            "workId": item["id"], "primary": True,
            "edition": {"kind": ed_kind, "label": ed_label, "decidedBy": "inferred", "decidedAt": NOW, "confidence": conf, "evidence": ed_ev},
            "presentation": presentation,
            "runtime": {"measuredMs": measured or 0, "referenceMs": reference, "deltaMs": delta},
            "completeness": completeness,
            "master": {"fingerprint": fp, "fidelity": fid_class},
            "source": {"document": "source.json", "file": f"source/{name}"},
            "packages": pkgs,
        }
        write(f"{vdir}/version.json", version_doc)
        summary = {"id": vid, "path": f"versions/{vid}/", "label": version_label, "primary": True,
                   "runtimeMs": measured, "playable": bool(pkgs)}
        return summary, fp, col, dr

    browse = []
    for item in [items[i] for i in items if items[i]["type"] in ("movie", "series")]:
        workdir = f"works/{shard(item['id'])}/{item['id']}"
        if item["type"] == "movie":
            doc = base_work(item, "movie")
            doc["artwork"] = write_artwork(workdir, item)
            t = tmdb.get(item["id"]) or {}
            doc["collection"] = t.get("collection")
            summary, fp, col, dr = build_version(item, workdir)
            doc["versions"] = [summary]
            write(f"{workdir}/work.json", doc)
            browse.append(("movies", f"{item['title']} ({item['year']})" if item["year"] else item["title"], workdir))
            continue

        # ---- series
        doc = base_work(item, "series")
        doc["artwork"] = write_artwork(workdir, item)
        folder = None
        eps = sorted([items[i] for i in items if items[i]["parent_id"] == item["id"]],
                     key=lambda e: (e["seasonnumber"] if e["seasonnumber"] is not None else 9999, e["episodenumber"] or 0))
        t = tmdb.get(item["id"]) or {}
        seasons = collections.OrderedDict()
        masters = collections.defaultdict(list)
        for e in eps:
            edir = f"{workdir}/episodes/{e['id']}"
            ed = base_work(e, "episode")
            own_art = write_artwork(edir, e)
            # An episode without its own image shows its series' image at read time;
            # nothing is copied, so the list stays empty rather than duplicating bytes.
            ed["artwork"] = own_art
            fe = fetched.get(e["id"]) or {}
            folder = folder or fe.get("parentFolder")
            coords = [{"scheme": "aired", "season": e["seasonnumber"], "episode": e["episodenumber"] or 0, "episodeEnd": None}]
            fc = file_coords(os.path.basename(paths[e["id"]]["source"] or ""))
            if fc:
                coords.append(fc)
            et = tmdb.get(e["id"]) or {}
            ed["episode"] = {"seriesId": item["id"], "coordinates": coords, "airDate": et.get("airDate"),
                             "special": e["seasonnumber"] == 0}
            if et.get("airDate"):
                ed["provenance"]["fieldOrigins"]["episode.airDate"] = "tmdb"
            summary, fp, col, dr = build_version(e, edir)
            ed["versions"] = [summary]
            write(f"{edir}/work.json", ed)
            seasons.setdefault(e["seasonnumber"], []).append(e["id"])
            masters[(fp, f"{col} {dr}")].append(e["id"])
        tseasons = {s["number"]: s for s in t.get("seasons") or []}
        doc["series"] = {
            "status": t.get("status") or "unknown",
            "firstAirDate": t.get("firstAirDate"), "lastAirDate": t.get("lastAirDate"), "network": t.get("network"),
            "defaultOrdering": "aired",
            "seasons": [{"number": n if n is not None else 0, "name": (tseasons.get(n) or {}).get("name"),
                         "overview": (tseasons.get(n) or {}).get("overview"), "airDate": (tseasons.get(n) or {}).get("airDate"),
                         "artwork": [], "episodes": ids,
                         "episodeCountReference": (tseasons.get(n) or {}).get("episodeCount")}
                        for n, ids in seasons.items()],
            "masters": [{"fingerprint": fp, "presentation": pres, "episodes": ids} for (fp, pres), ids in masters.items()],
        }
        if folder:
            m = re.search(r"\(([A-Z]{2,3}|\d{4})\)$", folder)
            if m:
                doc["titles"]["qualifier"] = m.group(1)
        doc["versions"] = []
        if len(masters) > 1:
            report["mixed-masters"].append(f"{item['title']}: {len(masters)} masters across {len(eps)} episodes")
        for n in seasons:
            if not (tseasons.get(n) or {}).get("name"):
                report["empty-in-legacy-catalog"].append(f"series:{item['title']}: season {n} name/overview/airDate")
        write(f"{workdir}/work.json", doc)
        browse.append(("series", item["title"] + (f" ({doc['titles']['qualifier']})" if doc["titles"]["qualifier"] else ""), workdir))

    with open(os.path.join(args.out, "links.tsv"), "w") as f:
        for op, src, dst in plan:
            f.write(f"{op}\t{src}\t{dst}\n")
    with open(os.path.join(args.out, "browse.tsv"), "w") as f:
        for kind, name, workdir in browse:
            f.write(f"{kind}\t{name}\t{workdir}\n")
    json.dump({k: v for k, v in report.items()}, open(os.path.join(args.out, "report.json"), "w"), indent=2)
    print("documents:", dict(docs_written))
    print("hard links planned:", collections.Counter(p[0] for p in plan))
    print("browse entries:", len(browse))
    for k, v in report.items():
        print(f"report {k}: {len(v)}")

if __name__ == "__main__":
    main()
