#!/usr/bin/env python3
"""Convert v1 item folders (manifest.json + metadata/) into v2 records.

Usage:
  library-v2-from-v1.py --in V1-LIBRARY (--out V2-LIBRARY | --in-place) [--dry-run]

v1 kept one living document per item: identity, the playback fields of the package in the item
folder itself, and every version in an array. v2 splits that into records that are written once —
the identity, one record per original, one folder per version with its package — and one projection
of the database, metadata.json. This tool does the split, and nothing else: every value comes from
the v1 documents, and what v1 did not hold stays empty.

  manifest.json .identity            -> item.json  (+ provenance: where the item came from)
                .versions[]          -> versions/<versionId>/version.json
                .versions[].sources[]-> sources/<sourceId>.json  (+ sources/<sourceId>/ffprobe.json)
                .versions[].package  -> versions/<versionId>/package.json
                top-level playback    - the package of the version whose path was "."
                .versions[].label,
                 .primary, .match,
                 .series.defaultOrdering,
                 .episode.coordinates -> metadata.json under `library`
                .versions[].sources[].state = deleted
                                     -> events/<at>-original-deleted.json
  metadata/metadata.json             -> metadata.json, its images renamed to their content hash

Media moves into the version folder: the package and the original of the version v1 kept in the
item folder itself, renamed when the target is on the same filesystem and copied when it is not.
--in-place rewrites the tree it reads; --out writes a new one and moves the bytes into it.

Safe to re-run: an item folder that already holds item.json and no manifest.json is left alone, and
a half-finished conversion is finished rather than repeated.

What v1 does not answer, and v2 therefore leaves empty: the naming scheme a file used (v1 recorded
the numbers, not the ordering they belong to), the language of a v1 image, and a series' episode
lists — in v2 each episode records its own place in each ordering, so the series repeats nothing.
Two v1 images with identical bytes become one file, because a v2 image is named by its content.
"""
import argparse, datetime, hashlib, json, os, re, shutil, sys, uuid

NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://zaentrum.github.io/schemas/library")
PACKAGE_DIRS = ("hls", "subs", "trickplay", "trailers")
OS_ARTEFACTS = re.compile(r"^(\.DS_Store|\._.*|Thumbs\.db|desktop\.ini|@eaDir|\.@__thumb|#recycle|\.AppleDouble)$")
ORDERINGS = ("aired", "dvd", "absolute", "production")
IMAGE_KINDS = {"poster", "backdrop", "logo", "still", "banner", "thumb"}
EXT_OF = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
ORIGINS = {"tmdb", "legacy-catalog", "filename", "folder-name", "file-tags", "manual"}
EVIDENCE_SIGNALS = {"runtime", "container-title", "stream-title", "folder-name", "filename", "commentary-track",
                    "content-analysis", "encoder-tag", "codec-vs-title", "tmdb", "catalog", "manual"}
LOSS_FLAGS = ("surround", "losslessAudio", "objectAudio", "hdr10Metadata", "dolbyVision", "stereo3d",
              "imageSubtitles", "styledSubtitles", "fonts", "closedCaptions")
LOSS_COUNTS = ("maxAudioChannels", "maxVideoHeight", "videoBitDepth", "subtitleTracks",
               "commentaryTracks", "commentarySubtitles", "audioDescriptionTracks")
LOSS_LANGUAGES = ("audioLanguages", "subtitleLanguages", "sdhSubtitleLanguages", "forcedSubtitleLanguages")
# 'S01E02', 'S01E02-03', 'S07E23-E24', '1x05' — the range must not swallow a quality token
# ('S01E02-1080p' numbers one episode), so a bare range end is at most three digits and is not
# followed by another digit or a 'p'.
EPISODE_TOKEN = re.compile(r"S(\d{1,3})[ ._-]?E(\d{1,4})(?:[ ._-]?E(\d{1,4})|-(\d{1,3})(?![0-9p]))?"
                           r"|(?<![0-9a-z])(\d{1,2})x(\d{2,3})(?![0-9])", re.I)


def did(*parts):
    return str(uuid.uuid5(NS, ":".join(str(p) for p in parts)))


def sha_bytes(b):
    return "sha256:" + hashlib.sha256(b).hexdigest()


def sha_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def ts(v):
    if not v:
        return None
    s = str(v).strip().replace(" ", "T")
    if not re.search(r"(Z|[+-]\d\d:?\d\d)$", s):
        s += "Z"
    s = re.sub(r"([+-]\d\d)(\d\d)$", r"\1:\2", s)
    return s.replace("+00:00", "Z")


def stamp_of(timestamp):
    """The <YYYYMMDDTHHMMSSZ> an event file name carries, from the event's own 'at'."""
    t = datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return t.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def text(v):
    if v is None:
        return None
    s = str(v).replace("\r", " ").replace("\n", " ").strip()
    return s or None


def listdir(d):
    return sorted(n for n in os.listdir(d) if not OS_ARTEFACTS.match(n))


def walk_files(root):
    out = []
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if not OS_ARTEFACTS.match(x))
        out += [os.path.relpath(os.path.join(base, f), root) for f in sorted(files) if not OS_ARTEFACTS.match(f)]
    return sorted(out)


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


def deletion_gate(sources, package):
    """What deleting a version's originals costs: the essence of its sources minus that of its
    package. Nothing records it, so every reader computes it the same way."""
    lost = set()
    for key in LOSS_FLAGS:
        if any(s.get(key) for s in sources) and not package.get(key):
            lost.add(key)
    for key in LOSS_COUNTS:
        had = [s[key] for s in sources if s.get(key) is not None]
        kept = package.get(key)
        if had and (kept is None or kept < max(had)):
            lost.add(key)
    for key in LOSS_LANGUAGES:
        kept = set(package.get(key) or [])
        lost |= {f"{key}:{x}" for s in sources for x in s.get(key) or [] if x not in kept}
    return sorted(lost)


def drop(o, *keys):
    """A copy without the keys v2 has no place for."""
    return {k: v for k, v in (o or {}).items() if k not in keys}


def evidence(items):
    return [e for e in (items or []) if e.get("signal") in EVIDENCE_SIGNALS]


class Writer:
    def __init__(self, dry_run):
        self.dry_run = dry_run
        self.written = self.moved = self.copied = self.removed = 0

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

    def move(self, src, dst):
        """Rename when the target is on the same filesystem, copy when it is not. Re-running is
        safe: a file already at the target and gone from the source is left alone."""
        if os.path.abspath(src) == os.path.abspath(dst):
            return True
        if not os.path.isfile(src):
            return os.path.isfile(dst)
        if os.path.isfile(dst) and os.path.getsize(dst) == os.path.getsize(src):
            if not self.dry_run:
                os.unlink(src)
            return True
        if self.dry_run:
            self.moved += 1
            return True
        self.mkdir(os.path.dirname(dst))
        try:
            same = os.stat(src).st_dev == os.stat(os.path.dirname(dst) or ".").st_dev
        except OSError:
            same = False
        if same:
            os.replace(src, dst)
            self.moved += 1
        else:
            shutil.copy2(src, dst)
            os.unlink(src)
            self.copied += 1
        return True

    def unlink(self, p):
        if os.path.isfile(p):
            self.removed += 1
            if not self.dry_run:
                os.unlink(p)

    def rmdir_empty(self, d):
        if self.dry_run or not os.path.isdir(d):
            return
        for base, dirs, files in os.walk(d, topdown=False):
            if not os.listdir(base):
                os.rmdir(base)


class Convert:
    def __init__(self, args):
        self.a = args
        self.w = Writer(args.dry_run)
        self.notes = []
        self.counts = {"items": 0, "sources": 0, "versions": 0, "packages": 0, "images": 0, "events": 0,
                       "already": 0}

    def note(self, item_id, msg):
        self.notes.append(f"{item_id}: {msg}")

    # -------------------------------------------------- one item
    def item(self, src, dst, expect_type, series=None):
        mp = os.path.join(src, "manifest.json")
        if not os.path.isfile(mp):
            if os.path.isfile(os.path.join(src, "item.json")) or os.path.isfile(os.path.join(dst, "item.json")):
                self.counts["already"] += 1
                return
            self.note(os.path.basename(src), "no manifest.json and no item.json: not a v1 item folder")
            return
        man = json.load(open(mp, encoding="utf-8"))
        iid = man["itemId"]
        if man.get("version") != 3:
            self.note(iid, f"manifest version {man.get('version')}, not 3; converted as far as it reads")
        meta_v1 = {}
        mpath = os.path.join(src, (man.get("metadata") or {}).get("file") or "metadata/metadata.json")
        if os.path.isfile(mpath):
            meta_v1 = json.load(open(mpath, encoding="utf-8"))
        self.w.mkdir(dst)

        versions, labels, primary = [], {}, None
        naming_of = {}
        if man.get("type") == "episode":
            for c in (man.get("episode") or {}).get("coordinates") or []:
                if c.get("scheme") == "file":
                    naming_of["*"] = {"scheme": "unknown", "seasonNumber": c.get("season"),
                                      "episodeNumber": c["episode"], "episodeEnd": c.get("episodeEnd"),
                                      "raw": None}
        for v in man.get("versions") or []:
            got = self.version(man, v, src, dst, naming_of)
            if got:
                versions.append(got)
                if v.get("label"):
                    labels[v["id"]] = v["label"]
                if v.get("primary"):
                    primary = v["id"]
        if primary is None and len(versions) == 1:
            primary = versions[0]

        self.w.write_json(os.path.join(dst, "item.json"), self.item_json(man, meta_v1))
        self.w.write_json(os.path.join(dst, "metadata.json"),
                          self.metadata_json(man, meta_v1, src, dst, primary, labels))
        self.counts["items"] += 1
        # the v1 documents, last: until they are gone the folder can be converted again
        if not self.a.dry_run:
            self.w.unlink(mp)
            if os.path.isfile(mpath):
                self.w.unlink(mpath)
            self.w.rmdir_empty(os.path.join(src, "source"))
            for name in ("checksums.sha256", ".complete"):
                p = os.path.join(src, name)
                if os.path.isfile(p) and src == dst and not os.path.isdir(os.path.join(dst, "versions")):
                    self.w.unlink(p)

    # -------------------------------------------------- item.json
    def item_json(self, man, meta_v1):
        ids = dict(man.get("externalIds") or {})
        t = man["type"]
        forbidden = {"movie": ("tmdbTv", "tmdbSeason", "tmdbEpisode"),
                     "series": ("tmdbMovie", "tmdbSeason", "tmdbEpisode", "tmdbCollection"),
                     "episode": ("tmdbMovie", "tmdbCollection")}[t]
        for key in forbidden:
            if ids.pop(key, None) is not None:
                self.note(man["itemId"], f"externalIds.{key} is not a reference id a {t} may carry, dropped")
        doc = {"schema": "zaentrum.library.item/2", "itemId": man["itemId"], "type": t,
               "title": text(man.get("title")) or "", "externalIds": ids,
               "createdAt": ts(man.get("createdAt"))}
        if text(man.get("createdBy")):
            doc["createdBy"] = text(man["createdBy"])
        prov = man.get("provenance") or {}
        if prov:
            migrated = prov.get("migratedFrom")
            doc["provenance"] = {"migratedFrom": migrated if migrated in ("legacy-catalog", "scan", "manual") else "manual",
                                 "legacyItemId": man["itemId"], "migratedAt": ts(prov.get("migratedAt")) or doc["createdAt"],
                                 "legacyCreatedBy": text(man.get("createdBy"))}
        if t == "episode":
            doc["seriesId"] = (man.get("episode") or {}).get("seriesId")
            doc["seasonNumber"] = man.get("seasonNumber")
            doc["episodeNumber"] = man.get("episodeNumber")
            if man.get("episodeCode"):
                doc["episodeCode"] = man["episodeCode"]
        return doc

    # -------------------------------------------------- metadata.json
    def metadata_json(self, man, meta, src, dst, primary, labels):
        t = man["type"]
        doc = {"schema": "zaentrum.library.metadata/2", "itemId": man["itemId"], "type": t,
               "asOf": ts(meta.get("updatedAt")) or ts(man.get("updatedAt")) or ts(man.get("createdAt")),
               "projectedBy": text(meta.get("updatedBy")) or "library-v2-from-v1",
               "titles": meta.get("titles") or {"primary": text(man.get("title")) or "", "original": None,
                                                "sort": None, "qualifier": None, "localized": {}}}
        for key in ("releaseDate", "genres", "tags", "rating", "contentRating", "credits", "videos", "curation"):
            if key in meta:
                doc[key] = meta[key]
        if t == "movie" and "collection" in meta:
            doc["collection"] = meta["collection"]
        if t == "series" and "series" in meta:
            doc["series"] = meta["series"]
        if t == "episode" and "episode" in meta:
            doc["episode"] = meta["episode"]
        library = {}
        if primary:
            library["primaryVersionId"] = primary
        if labels:
            library["versionLabels"] = labels
        match = man.get("match") or {}
        if match.get("status"):
            library["match"] = {"status": match["status"], "decidedBy": match.get("decidedBy") or "inferred"}
            for key in ("decidedAt", "confidence"):
                if match.get(key) is not None:
                    library["match"][key] = match[key]
            if match.get("evidence"):
                library["match"]["evidence"] = evidence(match["evidence"])
        reference = meta.get("reference") or {}
        if not reference.get("runtimeMs"):
            ref = next((v.get("runtime", {}).get("referenceMs") for v in man.get("versions") or []
                        if (v.get("runtime") or {}).get("referenceMs")), None)
            if ref:
                reference = {"runtimeMs": ref, "runtimeSource": reference.get("runtimeSource") or "legacy-catalog"}
        if reference:
            library["reference"] = reference
        if t == "series":
            ordering = (man.get("series") or {}).get("defaultOrdering")
            if ordering in ORDERINGS:
                library["defaultOrdering"] = ordering
            if (man.get("series") or {}).get("seasons"):
                self.note(man["itemId"], "the series' episode lists are not carried: in v2 each episode records "
                                         "its own place in each ordering")
            if (man.get("series") or {}).get("masters"):
                self.note(man["itemId"], "series.masters is not carried: each source's fidelity.fingerprint "
                                         "holds the master it came from")
        if t == "episode":
            numbering = {}
            for c in (man.get("episode") or {}).get("coordinates") or []:
                if c.get("scheme") in ORDERINGS:
                    numbering[c["scheme"]] = {"season": c.get("season"), "episode": c["episode"],
                                              "episodeEnd": c.get("episodeEnd")}
            if numbering:
                library["numbering"] = numbering
        if library:
            doc["library"] = library
        doc["images"] = self.images(meta, src, dst, man)
        origins = {k: v for k, v in (meta.get("fieldOrigins") or {}).items() if v in ORIGINS}
        if origins:
            doc["fieldOrigins"] = origins
        return doc

    def images(self, meta, src, dst, man):
        """An image is named by the hash of its own bytes, so it is written once and a replacement is
        a new file. Two v1 images with the same bytes are one file, and are listed once."""
        out, seen = [], {}
        md_src, md_dst = os.path.join(src, "metadata"), os.path.join(dst, "metadata")
        for img in meta.get("images") or []:
            p = os.path.join(md_src, img["file"])
            if not os.path.isfile(p):
                p = os.path.join(md_dst, img["file"])
            if not os.path.isfile(p):
                self.note(man["itemId"], f"metadata image {img['file']} is gone, dropped")
                continue
            if img.get("kind") not in IMAGE_KINDS:
                self.note(man["itemId"], f"image kind {img.get('kind')!r} is not one a v2 record holds, dropped")
                continue
            raw = open(p, "rb").read()
            ctype, w, h = sniff_image(raw)
            if ctype not in EXT_OF:
                self.note(man["itemId"], f"{img['file']} is not a JPEG, PNG or WebP, dropped")
                continue
            digest = hashlib.sha256(raw).hexdigest()
            name = f"{digest}.{EXT_OF[ctype]}"
            if name in seen:
                self.note(man["itemId"], f"the {img['kind']} is byte-identical to the {seen[name]}; one file, "
                                         f"listed once as the {seen[name]}")
                self.w.unlink(p)
                continue
            seen[name] = img["kind"]
            self.w.move(p, os.path.join(md_dst, name))
            self.counts["images"] += 1
            entry = {"kind": img["kind"], "file": name, "sha256": "sha256:" + digest, "contentType": ctype,
                     "sizeBytes": len(raw), "width": w, "height": h, "language": img.get("language"),
                     "sourceUrl": img.get("sourceUrl"), "fetchedAt": ts(img.get("fetchedAt")),
                     "origin": img.get("origin") if img.get("origin") in ORIGINS else "manual"}
            if man["type"] == "series":
                entry["season"] = img.get("season")
            out.append(entry)
        return out

    # -------------------------------------------------- one version
    def version(self, man, v, src, dst, naming_of):
        vid = v["id"]
        here = src if v.get("path", ".") == "." else os.path.join(src, v["path"].rstrip("/"))
        vp = os.path.join(dst, "versions", vid)
        pkg = v.get("package") or {}
        state = pkg.get("state")
        sources = v.get("sources") or []
        if not sources:
            self.note(man["itemId"], f"version {vid} names no source; a v2 version must, so it is not converted")
            return None
        self.w.mkdir(vp)

        originals, deleted = [], []
        for s in sources:
            rec, was_deleted = self.source(man, s, src, dst, naming_of)
            if s.get("file", {}).get("path") or was_deleted:
                originals.append(s["file"]["name"])
            if was_deleted:
                deleted.append(s)
            if s.get("file", {}).get("path"):
                self.w.move(os.path.join(here, s["file"]["path"]), os.path.join(vp, s["file"]["name"]))
            self.w.write_json(os.path.join(dst, "sources", s["id"] + ".json"), rec)
            self.counts["sources"] += 1

        version = {"schema": "zaentrum.library.version/2", "versionId": vid,
                   "createdAt": ts(man.get("createdAt")), "createdBy": text(man.get("createdBy")) or "library-v2-from-v1",
                   "edition": self.edition(v.get("edition") or {}),
                   "presentation": self.presentation(v.get("presentation") or {})}
        runtime = v.get("runtime") or {}
        version["runtimeMs"] = runtime.get("measuredMs")
        version["chapters"] = v.get("chapters") or []
        version["chaptersFrom"] = v.get("chaptersFrom") if version["chapters"] else None
        version["segments"] = v.get("segments") or []
        if v.get("completeness"):
            version["completeness"] = {"status": v["completeness"]["status"],
                                       "evidence": evidence(v["completeness"].get("evidence"))}
        version["sourceIds"] = [s["id"] for s in sources]
        version["originalFiles"] = originals

        wrote_package = False
        if state in ("complete", "stale"):
            entries = self.package_files(here, vp)
            package = self.package(man, v, pkg, state, bool(originals), entries, vp, here)
            if package is not None:
                self.w.write_json(os.path.join(vp, "version.json"), version)
                self.w.write_json(os.path.join(vp, "package.json"), package)
                self.w.move(os.path.join(here, ".complete"), os.path.join(vp, ".complete"))
                self.counts["packages"] += 1
                wrote_package = True
        else:
            self.note(man["itemId"], f"version {vid} carried a {state!r} package: a v2 record is written only "
                                     f"when a package completes, so the version keeps its records and no package")
            self.w.write_json(os.path.join(vp, "version.json"), version)
        if not wrote_package and state not in ("complete", "stale"):
            self.w.unlink(os.path.join(vp, ".complete"))
        for s in deleted:
            self.event(man, v, s, dst, pkg)
        self.counts["versions"] += 1
        if here != src and here != vp:
            self.w.rmdir_empty(here)
        return vid

    def source(self, man, s, src, dst, naming_of):
        f = s["file"]
        rec = {"schema": "zaentrum.library.source/2", "sourceId": s["id"],
               "takenAt": ts(man.get("createdAt")), "takenBy": text(man.get("createdBy")) or "library-v2-from-v1",
               "file": {"name": f["name"], "sizeBytes": f["sizeBytes"], "fixity": f["fixity"]}}
        for key in ("kind", "mtime", "ownership", "part"):
            if f.get(key) is not None:
                rec["file"][key] = f[key]
        if f.get("origin"):
            rec["origin"] = dict(f["origin"])
            rec["origin"]["takenBy"] = "unknown"
        named = self.naming(f["name"]) or naming_of.get("*")
        if named:
            rec["naming"] = named
        if s.get("labels"):
            rec["labels"] = s["labels"]
        rec["container"] = s["container"]
        fidelity = dict(s.get("fidelity") or {"class": "unknown"})
        fidelity["evidence"] = evidence(fidelity.get("evidence"))
        for v in man.get("versions") or []:
            if any(x["id"] == s["id"] for x in v.get("sources") or []) and (v.get("master") or {}).get("fingerprint"):
                fidelity.setdefault("fingerprint", v["master"]["fingerprint"])
        rec["fidelity"] = fidelity
        rec["streams"] = s.get("streams") or []
        rec["sidecars"] = []
        for sc in s.get("sidecars") or []:
            entry = dict(sc)
            entry["file"] = f"sources/{s['id']}/" + os.path.basename(sc["file"])
            self.w.move(os.path.join(src, sc["file"]), os.path.join(dst, entry["file"]))
            rec["sidecars"].append(entry)
        rec["covers"] = s.get("covers") or []
        rec["essence"] = s.get("essence") or {}
        probe = dict(s.get("probe") or {"tool": "ffprobe", "at": None, "file": None})
        if probe.get("file"):
            target = f"sources/{s['id']}/" + os.path.basename(probe["file"])
            self.w.move(os.path.join(src, probe["file"]), os.path.join(dst, target))
            probe["file"] = target
        rec["probe"] = probe
        return rec, s.get("state") == "deleted"

    def naming(self, name):
        m = EPISODE_TOKEN.search(os.path.splitext(name)[0])
        if not m:
            return None
        if m.group(1) is not None:
            season, episode, end = int(m.group(1)), int(m.group(2)), m.group(3) or m.group(4)
        else:
            season, episode, end = int(m.group(5)), int(m.group(6)), None
        return {"scheme": "unknown", "seasonNumber": season, "episodeNumber": episode,
                "episodeEnd": int(end) if end else None, "raw": m.group(0)}

    def edition(self, e):
        out = drop(e, "review")
        out.setdefault("kind", "unknown")
        if out.get("evidence"):
            out["evidence"] = evidence(out["evidence"])
        return out

    def presentation(self, p):
        out = drop(p, "colourDecision")
        out.setdefault("colour", "unknown")
        out.setdefault("dynamicRange", "unknown")
        out.setdefault("stereo3d", "unknown")
        if p.get("colourDecision"):
            cd = drop(p["colourDecision"], "review")
            cd["evidence"] = evidence(cd.get("evidence"))
            out["colourDecision"] = cd
        return out

    def package_files(self, here, vp):
        """Every file of the package, moved into the version folder. .complete comes last, so it is
        listed by its bytes here and written after the records."""
        entries = []
        for sub in PACKAGE_DIRS:
            base = os.path.join(here, sub)
            target = os.path.join(vp, sub)
            for root, rel in ((base, r) for r in (walk_files(base) if os.path.isdir(base) else [])):
                self.w.move(os.path.join(root, rel), os.path.join(target, rel))
            if os.path.abspath(base) != os.path.abspath(target):
                self.w.rmdir_empty(base)
            for rel in walk_files(target) if os.path.isdir(target) else []:
                p = os.path.join(target, rel)
                entries.append((os.path.join(sub, rel).replace(os.sep, "/"), sha_file(p).split(":", 1)[1],
                                os.path.getsize(p)))
        marker = os.path.join(here, ".complete")
        if not os.path.isfile(marker):
            marker = os.path.join(vp, ".complete")
        if os.path.isfile(marker):
            raw = open(marker, "rb").read()
            entries.append((".complete", hashlib.sha256(raw).hexdigest(), len(raw)))
        return sorted(entries)

    def package(self, man, v, pkg, state, keep_original, entries, vp, here):
        playback = pkg.get("playback") or ({"durationMs": man.get("durationMs"), "packagedAt": man.get("packagedAt"),
                                            "packager": man.get("packager"), "renditions": man.get("renditions"),
                                            "subtitles": man.get("subtitles"), "trickplay": man.get("trickplay"),
                                            "trailers": man.get("trailers")} if v.get("path", ".") == "." else {})
        ren = playback.get("renditions") or {}
        video = [drop(x) for x in (ren.get("video") or [])]
        audio = [drop(x) for x in (ren.get("audio") or [])]
        subs = [drop(x) for x in (playback.get("subtitles") or [])]
        if not video:
            self.note(man["itemId"], f"version {v['id']} says it has a package but records no video rendition; "
                                     f"no package.json written")
            return None
        defaults = [a for a in audio if a.get("default")]
        for a in defaults[1:]:
            a["default"] = False
            self.note(man["itemId"], f"audio rendition {a['id']} was a second default; cleared")
        if audio and not defaults:
            audio[0]["default"] = True
            self.note(man["itemId"], "no audio rendition was default; the first one is")
        for s in subs:
            if s.get("default") and (s.get("forced") or s.get("purpose") in ("forced", "signs-songs")):
                s["default"] = False
                self.note(man["itemId"], f"subtitle {s['id']} was a forced track flagged default; cleared")
        checksums = self.checksums(pkg, entries, vp, here, man)
        role = "derived" if keep_original else "canonical"
        if pkg.get("role") and pkg["role"] != role:
            self.note(man["itemId"], f"version {v['id']} package role {pkg['role']!r} became {role!r}: a package is "
                                     f"canonical exactly when the version keeps no original")
        doc = {"schema": "zaentrum.library.package/2", "packageId": pkg["id"],
               "createdAt": ts(playback.get("packagedAt")) or ts(man.get("createdAt")),
               "packagedBy": text(playback.get("packager")) or "unknown", "state": state, "role": role,
               "durationMs": playback.get("durationMs") or 0,
               "renditions": {"video": video, "audio": audio}, "subtitles": subs,
               "trickplay": playback.get("trickplay"), "trailers": playback.get("trailers") or [],
               "sizeBytes": pkg.get("sizeBytes"), "peakBandwidthBps": pkg.get("peakBandwidthBps"),
               "fidelity": {"lossless": bool((pkg.get("fidelity") or {}).get("lossless")),
                            "losses": (pkg.get("fidelity") or {}).get("losses") or [],
                            "droppedSourceStreams": (pkg.get("fidelity") or {}).get("droppedSourceStreams") or []},
               "essence": pkg.get("essence") or {}, "checksums": checksums}
        if pkg.get("recipe"):
            doc["recipe"] = pkg["recipe"]
        if doc["fidelity"]["lossless"] != (not doc["fidelity"]["losses"]):
            doc["fidelity"]["lossless"] = not doc["fidelity"]["losses"]
            self.note(man["itemId"], f"version {v['id']} package called itself lossless with losses recorded; "
                                     f"lossless is true exactly when there are none")
        return doc

    def checksums(self, pkg, entries, vp, here, man):
        """The v1 file covers exactly the same set, so it is carried across when it does; when it
        does not, it is written again from the files themselves."""
        target = os.path.join(vp, "checksums.sha256")
        old = os.path.join(here, "checksums.sha256")
        listed = {}
        for p in (old, target):
            if os.path.isfile(p):
                for line in open(p, encoding="utf-8"):
                    digest, _, rel = line.rstrip("\n").partition("  ")
                    if rel:
                        listed[rel] = digest
                break
        want = {rel: digest for rel, digest, _ in entries}
        if listed == want and os.path.isfile(old):
            self.w.move(old, target)
            raw = open(target, "rb").read() if not self.a.dry_run and os.path.isfile(target) else \
                "".join(f"{d}  {r}\n" for r, d, _ in entries).encode()
        else:
            if listed and listed != want:
                self.note(man["itemId"], "checksums.sha256 did not cover exactly the package's files; written again")
            raw = "".join(f"{d}  {r}\n" for r, d, _ in entries).encode()
            self.w.write(target, raw)
            self.w.unlink(old)
        return {"file": "checksums.sha256", "algorithm": "sha256", "sha256": sha_bytes(raw),
                "files": len(entries), "bytes": sum(size for _, _, size in entries)}

    def event(self, man, v, s, dst, pkg):
        """A v1 source that said it was deleted is a fact that arose after the records: an event."""
        at = ts((s.get("file") or {}).get("deletedAt")) or ts((v.get("truth") or {}).get("since")) \
            or ts(man.get("updatedAt")) or ts(man.get("createdAt"))
        gate = deletion_gate([x.get("essence") or {} for x in v.get("sources") or []], pkg.get("essence") or {})
        eid = did(man["itemId"], "original-deleted", v["id"], s["id"])
        doc = {"schema": "zaentrum.library.event/2", "eventId": eid, "at": at,
               "by": text((s.get("file") or {}).get("deletedBy")) or text(man.get("updatedBy")) or "library-v2-from-v1",
               "kind": "original-deleted", "sourceId": s["id"], "versionId": v["id"]}
        if pkg.get("id"):
            doc["packageId"] = pkg["id"]
        doc["reason"] = text((s.get("file") or {}).get("deletionReason"))
        doc["accepted"] = gate
        self.w.write_json(os.path.join(dst, "events", f"{stamp_of(at)}-original-deleted.json"), doc)
        self.counts["events"] += 1

    # -------------------------------------------------- the tree
    def run(self):
        for category, kind in (("movies", "movie"), ("series", "series")):
            base = os.path.join(self.a.inp, category)
            if not os.path.isdir(base):
                continue
            for shard in listdir(base):
                sd = os.path.join(base, shard)
                if not os.path.isdir(sd):
                    continue
                for iid in listdir(sd):
                    src = os.path.join(sd, iid)
                    if not os.path.isdir(src):
                        continue
                    dst = src if self.a.in_place else os.path.join(self.a.out, category, shard, iid)
                    try:
                        self.item(src, dst, kind)
                    except Exception as e:
                        self.notes.append(f"{iid}: cannot convert: {type(e).__name__}: {e}")
                        continue
                    if kind == "series":
                        ed = os.path.join(src, "episodes")
                        for eid in (listdir(ed) if os.path.isdir(ed) else []):
                            ep_src = os.path.join(ed, eid)
                            if not os.path.isdir(ep_src):
                                continue
                            ep_dst = ep_src if self.a.in_place else os.path.join(dst, "episodes", eid)
                            try:
                                self.item(ep_src, ep_dst, "episode")
                            except Exception as e:
                                self.notes.append(f"{eid}: cannot convert: {type(e).__name__}: {e}")


def main():
    ap = argparse.ArgumentParser(prog="library-v2-from-v1.py",
                                 description="Convert v1 item folders into v2 records.")
    ap.add_argument("--in", dest="inp", required=True, help="the v1 library root holding movies/ and series/")
    ap.add_argument("--out", help="write a new v2 tree here and move the media into it")
    ap.add_argument("--in-place", action="store_true", help="rewrite the tree that is read")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if bool(args.out) == bool(args.in_place):
        ap.error("give either --out or --in-place")
    args.inp = os.path.abspath(args.inp)
    if args.out:
        args.out = os.path.abspath(args.out)

    c = Convert(args)
    c.run()
    print(f"{'would convert' if args.dry_run else 'converted'}: {c.counts}")
    print(f"records written: {c.w.written}, files renamed: {c.w.moved}, copied: {c.w.copied}, "
          f"v1 documents removed: {c.w.removed}")
    for n in c.notes:
        print("  note " + n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
