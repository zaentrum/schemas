#!/usr/bin/env python3
"""Read a v2 library tree and produce the catalog contents it implies.

Usage:
  library-v2-rebuild.py LIBRARY [--out rows.json] [--compare CATALOG.json [--subset]]
                        [--text-language LANG] [--ignore-fields a,b,c]

The database is the working copy and this tree is the record that can rebuild it. This reads the
records, applies the item's events in the order they happened, and writes the rows a catalog would
hold: the items and their texts, the images, the people, the chapters and segments, one playback
asset per package and one per original that is still there, and the subtitles the package carries.
It needs no network and no other service, it can run against a copy, and running it twice gives the
same answer.

Applying events, earliest first:
  original-deleted    the originals it names are gone, so they are not playback assets any more,
                      and the version's package is the only copy of it — canonical whatever its
                      record says, with everything the package failed to carry permanently lost
  version-removed     the version is not part of the item: its folder is ignored altogether
  package-superseded  the package it names is not the one to use; the successor is
  note                nothing

--compare reports both directions against a catalog export: rows the tree has that the database
does not, rows the database has that the tree does not, and field by field where they disagree. It
exits non-zero when anything differs, so it can gate a migration.

Fields that cannot agree by construction are ignored by default (--ignore-fields):
  id     a database key, not a fact about the item
  path   the bytes moved into the version folder, so the database's old paths are stale
  hash   never filled by either side

--subset reports the rows only the database has without counting them, for a tree that was built
from part of a catalog.
"""
import argparse, base64, datetime, hashlib, json, os, re, sys, uuid

NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://zaentrum.github.io/schemas/library")
OS_ARTEFACTS = re.compile(r"^(\.DS_Store|\._.*|Thumbs\.db|desktop\.ini|@eaDir|\.@__thumb|#recycle|\.AppleDouble)$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
LOSS_FLAGS = ("surround", "losslessAudio", "objectAudio", "hdr10Metadata", "dolbyVision", "stereo3d",
              "imageSubtitles", "styledSubtitles", "fonts", "closedCaptions")
LOSS_COUNTS = ("maxAudioChannels", "maxVideoHeight", "videoBitDepth", "subtitleTracks",
               "commentaryTracks", "commentarySubtitles", "audioDescriptionTracks")
LOSS_LANGUAGES = ("audioLanguages", "subtitleLanguages", "sdhSubtitleLanguages", "forcedSubtitleLanguages")
EXTERNAL_ID_SOURCE = {"tmdbMovie": "tmdb", "tmdbTv": "tmdb", "tmdbSeason": "tmdb-season",
                      "tmdbEpisode": "tmdb-episode", "tmdbCollection": "tmdb-collection",
                      "imdb": "imdb", "tvdb": "tvdb"}


def did(*parts):
    return str(uuid.uuid5(NS, ":".join(str(p) for p in parts)))


def listdir(d):
    return sorted(n for n in os.listdir(d) if not OS_ARTEFACTS.match(n))


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


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


# ---------------------------------------------------------------- reading one item
class Rebuild:
    def __init__(self, root, text_language):
        self.root = root
        self.text_language = text_language
        self.items = []
        self.storage = []
        self.notes = []

    def note(self, msg):
        self.notes.append(msg)

    def run(self):
        for category, kind in (("movies", "movie"), ("series", "series")):
            base = os.path.join(self.root, category)
            if not os.path.isdir(base):
                continue
            for shard in listdir(base):
                sd = os.path.join(base, shard)
                if not os.path.isdir(sd):
                    continue
                for iid in listdir(sd):
                    d = os.path.join(sd, iid)
                    if not os.path.isdir(d):
                        continue
                    self.item(d)
                    ed = os.path.join(d, "episodes")
                    for eid in (listdir(ed) if os.path.isdir(ed) else []):
                        if os.path.isdir(os.path.join(ed, eid)):
                            self.item(os.path.join(ed, eid))
        self.items.sort(key=lambda r: r["id"])
        self.storage.sort(key=lambda r: r["itemId"])

    def item(self, d):
        try:
            item = load(os.path.join(d, "item.json"))
        except (OSError, ValueError) as e:
            self.note(f"{d}: no item.json to rebuild from ({e})")
            return
        meta = {}
        try:
            meta = load(os.path.join(d, "metadata.json"))
        except (OSError, ValueError):
            self.note(f"{item['itemId']}: no metadata.json, so the row keeps only what item.json records")
        events = self.events(d, item["itemId"])
        sources = self.sources(d)
        versions, storage = self.versions(d, item, sources, events)
        self.items.append(self.row(d, item, meta, versions, sources))
        self.storage.append({"itemId": item["itemId"], "versions": storage})

    def events(self, d, iid):
        base = os.path.join(d, "events")
        out = []
        for name in (listdir(base) if os.path.isdir(base) else []):
            try:
                ev = load(os.path.join(base, name))
            except (OSError, ValueError) as e:
                self.note(f"{iid}: event {name} could not be read ({e})")
                continue
            out.append((ev.get("at") or "", name, ev))
        return [ev for _, _, ev in sorted(out, key=lambda x: (x[0], x[1]))]

    def sources(self, d):
        base = os.path.join(d, "sources")
        out = {}
        for name in (listdir(base) if os.path.isdir(base) else []):
            if name.endswith(".json") and UUID_RE.match(name[:-5]):
                try:
                    rec = load(os.path.join(base, name))
                    out[rec["sourceId"]] = rec
                except (OSError, ValueError, KeyError):
                    continue
        return out

    def versions(self, d, item, sources, events):
        base = os.path.join(d, "versions")
        found = {}
        for name in (listdir(base) if os.path.isdir(base) else []):
            vp = os.path.join(base, name)
            if not os.path.isdir(vp) or not os.path.isfile(os.path.join(vp, "version.json")):
                continue
            try:
                version = load(os.path.join(vp, "version.json"))
            except (OSError, ValueError) as e:
                self.note(f"{item['itemId']}: version {name} could not be read ({e})")
                continue
            package = None
            if os.path.isfile(os.path.join(vp, "package.json")) and os.path.isfile(os.path.join(vp, ".complete")):
                try:
                    package = load(os.path.join(vp, "package.json"))
                except (OSError, ValueError) as e:
                    self.note(f"{item['itemId']}: package of version {name} could not be read ({e})")
            elif os.path.isfile(os.path.join(vp, "package.json")):
                self.note(f"{item['itemId']}: version {name} has a package record but no .complete marker, "
                          f"so it is not playable and is left out")
            found[name] = {"dir": vp, "version": version, "package": package, "gone": set(), "whole": False,
                           "removed": False, "superseded": False, "supersededBy": None}

        for ev in events:
            kind = ev.get("kind")
            vid = ev.get("versionId")
            if kind == "version-removed" and vid in found:
                found[vid]["removed"] = True
            elif kind == "original-deleted" and vid in found:
                if ev.get("sourceId"):
                    found[vid]["gone"].add(ev["sourceId"])
                else:
                    found[vid]["whole"] = True
            elif kind == "package-superseded":
                by = ev.get("supersededBy") or {}
                for v in found.values():
                    if v["package"] and v["package"].get("packageId") == ev.get("packageId"):
                        v["superseded"] = True
                        v["supersededBy"] = by.get("packageId")

        live, storage = [], []
        for vid in sorted(found):
            v = found[vid]
            essences = [sources[s]["essence"] for s in v["version"].get("sourceIds") or [] if s in sources]
            kept = [n for n in v["version"].get("originalFiles") or []
                    if not v["whole"] and not any(sources.get(s, {}).get("file", {}).get("name") == n
                                                  for s in v["gone"])]
            canonical = (v["package"] is not None) and not kept
            lost = deletion_gate(essences, (v["package"] or {}).get("essence") or {}) \
                if (v["whole"] or v["gone"]) else []
            storage.append({"versionId": vid, "packageId": (v["package"] or {}).get("packageId"),
                            "removed": v["removed"], "superseded": v["superseded"],
                            "supersededBy": v["supersededBy"], "canonical": canonical,
                            "originalsKept": kept, "permanentLoss": lost})
            if v["removed"]:
                self.note(f"{item['itemId']}: version {vid} was removed by an event; its folder is ignored")
                continue
            if lost:
                self.note(f"{item['itemId']}: version {vid} lost its original(s); the package is the only copy "
                          f"and {', '.join(lost)} cannot come back")
            v["kept"] = kept
            v["canonical"] = canonical
            v["id"] = vid
            live.append(v)
        live.sort(key=lambda v: ((v["version"].get("createdAt") or ""), v["id"]))
        return live, storage

    # -------------------------------------------------- the catalog row
    def row(self, d, item, meta, versions, sources):
        titles = meta.get("titles") or {}
        localized = titles.get("localized") or {}
        body = localized.get(self.text_language) or localized.get("und") or \
            (list(localized.values())[0] if len(localized) == 1 else {})
        library = meta.get("library") or {}
        primary_id = library.get("primaryVersionId")
        primary = next((v for v in versions if v["id"] == primary_id), None) or (versions[0] if versions else None)
        release = meta.get("releaseDate")
        row = {
            "id": item["itemId"], "type": item["type"],
            "title": titles.get("primary") or item.get("title"),
            "sortTitle": titles.get("sort"),
            "year": int(str(release)[:4]) if release and str(release)[:4].isdigit() else None,
            "description": body.get("overview"), "tagline": body.get("tagline"),
            "rating": meta.get("rating"),
            "durationMs": (library.get("reference") or {}).get("runtimeMs"),
            "parentId": item.get("seriesId"), "seasonNumber": item.get("seasonNumber"),
            "episodeNumber": item.get("episodeNumber"),
            "metadataLocked": bool((meta.get("curation") or {}).get("metadataLocked")),
            "createdAt": item.get("createdAt"), "createdBy": item.get("createdBy"),
            "externalIds": [{"source": EXTERNAL_ID_SOURCE.get(k, k), "externalId": v}
                            for k, v in sorted((item.get("externalIds") or {}).items())],
            "genres": list(meta.get("genres") or []), "tags": list(meta.get("tags") or []),
            "people": [{"personId": c["personId"], "name": c.get("name"), "role": c.get("role")}
                       for c in meta.get("credits") or []],
            "chapters": [], "segments": [], "playbackAssets": [], "subtitleAssets": [],
            "trailers": [{"source": v.get("origin"), "site": v.get("site"), "externalId": v.get("key"),
                          "url": v.get("url"), "title": v.get("name"),
                          "durationSec": (v["durationMs"] // 1000) if v.get("durationMs") else None,
                          "localPath": None} for v in meta.get("videos") or []],
            "artwork": [{"kind": i["kind"], "contentType": i["contentType"], "fetchedAt": i.get("fetchedAt"),
                         "sha256": i["sha256"], "sizeBytes": i["sizeBytes"],
                         "file": os.path.join("metadata", i["file"])} for i in meta.get("images") or []],
        }
        if primary:
            for i, c in enumerate(primary["version"].get("chapters") or []):
                row["chapters"].append({"startMs": c["startMs"], "endMs": c["endMs"], "title": c.get("title"),
                                        "ordinal": i + 1})
            for s in primary["version"].get("segments") or []:
                row["segments"].append({"kind": s["kind"], "startMs": s["startMs"], "endMs": s["endMs"],
                                        "source": s.get("detector"), "confidence": s.get("confidence"),
                                        "label": s.get("label")})
        for v in versions:
            row["playbackAssets"] += self.assets(item, v, sources, v is primary)
            if v["package"] and not v["superseded"]:
                row["subtitleAssets"] += self.subtitles(item, v)
        return row

    def assets(self, item, v, sources, is_primary):
        out = []
        for i, name in enumerate(v["kept"]):
            src = next((s for s in sources.values() if s.get("file", {}).get("name") == name), None)
            stream = next((x for x in (src or {}).get("streams") or []
                           if x.get("type") == "video" and not (x.get("dispositions") or {}).get("attachedPic")), None)
            out.append({"id": did(item["itemId"], "asset", v["id"], name), "kind": "primary",
                        "path": os.path.join(v["dir"], name),
                        "codec": (stream or {}).get("codec"),
                        "resolution": f"{stream['width']}x{stream['height']}"
                        if stream and stream.get("width") and stream.get("height") else None,
                        "bitrateKbps": ((src or {}).get("container") or {}).get("bitrate") // 1000
                        if ((src or {}).get("container") or {}).get("bitrate") else None,
                        "sizeBytes": (src or {}).get("file", {}).get("sizeBytes"), "hash": None,
                        "isPrimary": bool(is_primary and i == 0),
                        "audioCodec": None, "audioLanguage": None, "audioChannels": None,
                        "audioBitrateKbps": None, "audioTrackCount": None, "subtitleTrackCount": None,
                        "durationMs": ((src or {}).get("container") or {}).get("durationMs")})
        pkg = v["package"]
        if pkg and not v["superseded"]:
            ren = pkg.get("renditions") or {}
            video = (ren.get("video") or [{}])[0]
            audio = next((a for a in ren.get("audio") or [] if a.get("default")), None) \
                or (ren.get("audio") or [None])[0]
            out.append({"id": did(item["itemId"], "asset", v["id"], pkg["packageId"]), "kind": "packaged",
                        "path": os.path.join(v["dir"], "package.json"), "codec": video.get("codec"),
                        "resolution": f"{video['width']}x{video['height']}"
                        if video.get("width") and video.get("height") else None,
                        "bitrateKbps": (pkg["peakBandwidthBps"] // 1000) if pkg.get("peakBandwidthBps")
                        else ((video["bitrateBps"] // 1000) if video.get("bitrateBps") else None),
                        "sizeBytes": pkg.get("sizeBytes"), "hash": None, "isPrimary": False,
                        "audioCodec": (audio or {}).get("codec"), "audioLanguage": (audio or {}).get("language"),
                        "audioChannels": (audio or {}).get("channels"),
                        "audioBitrateKbps": ((audio or {}).get("bitrateBps") // 1000)
                        if (audio or {}).get("bitrateBps") else None,
                        "audioTrackCount": len(ren.get("audio") or []),
                        "subtitleTrackCount": len(pkg.get("subtitles") or []),
                        "durationMs": pkg.get("durationMs")})
        return out

    def subtitles(self, item, v):
        return [{"id": did(item["itemId"], "subtitle", v["id"], s["id"]),
                 "path": os.path.join(v["dir"], s["path"]), "format": s.get("format"),
                 "language": s.get("language"), "label": s.get("title") or "",
                 "isDefault": bool(s.get("default"))} for s in v["package"].get("subtitles") or []]


# ---------------------------------------------------------------- comparing with a database
LIST_KEYS = {
    "playbackAssets": lambda x: x.get("kind"),
    "subtitleAssets": lambda x: (x.get("language"), os.path.basename(str(x.get("path") or ""))),
    "artwork": lambda x: x.get("sha256"),
    "people": lambda x: (x.get("name"), x.get("role")),
    "chapters": lambda x: x.get("startMs"),
    "segments": lambda x: (x.get("kind"), x.get("startMs")),
    "externalIds": lambda x: x.get("source"),
    "trailers": lambda x: (x.get("site"), x.get("externalId")),
}
SET_FIELDS = ("genres", "tags")


def artwork_key(a):
    """An export carries the bytes and the tree carries their hash: comparing the hash compares the
    image itself, whichever side it came from."""
    if a.get("sha256"):
        return a["sha256"]
    raw = base64.b64decode(a.get("base64") or "", validate=False)
    return "sha256:" + hashlib.sha256(raw).hexdigest() if raw else None


def normalise(row):
    """A catalog row as it can be compared: the artwork reduced to its hash, nothing else changed."""
    out = dict(row)
    out["artwork"] = [{"kind": a.get("kind"), "contentType": a.get("contentType"), "sha256": artwork_key(a),
                       "fetchedAt": a.get("fetchedAt"),
                       "sizeBytes": a.get("sizeBytes") if a.get("sizeBytes") is not None
                       else len(base64.b64decode(a.get("base64") or "", validate=False)) or None}
                      for a in row.get("artwork") or []]
    return out


def compare(tree_rows, db_rows, ignore, subset=False):
    """Both directions, field by field. Returns (lines, number of differences)."""
    tree = {r["id"]: normalise(r) for r in tree_rows}
    db = {r["id"]: normalise(r) for r in db_rows}
    lines, n = [], 0
    only_tree, only_db = sorted(set(tree) - set(db)), sorted(set(db) - set(tree))
    for label, missing, source in (("only on storage", only_tree, tree),
                                   ("only in the database", only_db, db)):
        if not missing:
            continue
        counted = not (subset and label == "only in the database")
        lines.append(f"  {label}: {len(missing)} item(s)" + ("" if counted else ", which a subset is expected to be"))
        for iid in missing[:5]:
            lines.append(f"      {iid} {source[iid].get('title')!r}")
        if len(missing) > 5:
            lines.append(f"      … and {len(missing) - 5} more")
        if counted:
            n += len(missing)
    fields = {}
    for iid in sorted(set(tree) & set(db)):
        a, b = tree[iid], db[iid]
        for key in sorted(set(a) | set(b)):
            if key in ignore:
                continue
            if key in LIST_KEYS:
                n += diff_list(fields, iid, key, a.get(key) or [], b.get(key) or [], ignore)
            elif key in SET_FIELDS:
                miss_a, miss_b = sorted(set(b.get(key) or []) - set(a.get(key) or [])), \
                                 sorted(set(a.get(key) or []) - set(b.get(key) or []))
                for value in miss_a:
                    fields.setdefault(key, []).append((iid, "not on storage", value))
                for value in miss_b:
                    fields.setdefault(key, []).append((iid, "not in the database", value))
                n += len(miss_a) + len(miss_b)
            elif a.get(key) != b.get(key):
                fields.setdefault(key, []).append((iid, a.get(key), b.get(key)))
                n += 1
    for key in sorted(fields):
        rows = fields[key]
        lines.append(f"  {key}: {len(rows)} difference(s)")
        for iid, mine, theirs in rows[:5]:
            lines.append(f"      {iid}: storage {mine!r} != database {theirs!r}")
        if len(rows) > 5:
            lines.append(f"      … and {len(rows) - 5} more")
    return lines, n


def diff_list(fields, iid, key, mine, theirs, ignore):
    keyer = LIST_KEYS[key]
    a = {keyer(x): x for x in mine}
    b = {keyer(x): x for x in theirs}
    n = 0
    for k in sorted(set(a) - set(b), key=str):
        fields.setdefault(key, []).append((iid, a[k], "missing"))
        n += 1
    for k in sorted(set(b) - set(a), key=str):
        fields.setdefault(key, []).append((iid, "missing", b[k]))
        n += 1
    for k in sorted(set(a) & set(b), key=str):
        for f in sorted(set(a[k]) | set(b[k])):
            if f in ignore:
                continue
            if a[k].get(f) != b[k].get(f):
                fields.setdefault(f"{key}.{f}", []).append((iid, a[k].get(f), b[k].get(f)))
                n += 1
    return n


def main():
    ap = argparse.ArgumentParser(prog="library-v2-rebuild.py",
                                 description="Produce the catalog contents a v2 tree implies.")
    ap.add_argument("root", help="the library root holding movies/ and series/")
    ap.add_argument("--out", help="write the rows here as JSON; stdout gets the summary either way")
    ap.add_argument("--compare", help="a catalog export to report against, in both directions")
    ap.add_argument("--text-language", default="und",
                    help="the localized title the description and tagline are read from")
    ap.add_argument("--ignore-fields", default="id,path,hash",
                    help="fields that cannot agree by construction; comma-separated")
    ap.add_argument("--subset", action="store_true",
                    help="the tree holds only some of the database's items: rows only the database "
                         "has are reported but are not a difference")
    args = ap.parse_args()

    r = Rebuild(os.path.abspath(args.root), args.text_language)
    r.run()
    out = {"generatedAt": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
           .isoformat().replace("+00:00", "Z"),
           "root": r.root, "items": r.items, "storage": r.storage, "notes": r.notes}
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
            f.write("\n")
    kinds = {}
    for row in r.items:
        kinds[row["type"]] = kinds.get(row["type"], 0) + 1
    print(f"rebuilt: {kinds}, playback assets: {sum(len(x['playbackAssets']) for x in r.items)}, "
          f"subtitles: {sum(len(x['subtitleAssets']) for x in r.items)}, "
          f"images: {sum(len(x['artwork']) for x in r.items)}")
    for n in r.notes:
        print("  note " + n)
    if not args.compare:
        return 0
    ignore = {x.strip() for x in args.ignore_fields.split(",") if x.strip()}
    with open(args.compare, encoding="utf-8") as f:
        export = json.load(f)
    lines, n = compare(r.items, export.get("items") or [], ignore, args.subset)
    print(f"compared with {args.compare} (ignoring {', '.join(sorted(ignore)) or 'nothing'}):")
    for line in lines:
        print(line)
    print("the tree and the database agree" if not n else f"{n} difference(s)")
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
