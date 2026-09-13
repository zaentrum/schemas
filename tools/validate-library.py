#!/usr/bin/env python3
"""Validate a zaentrum library tree against the published JSON Schemas and its own structure.

Usage:
  validate-library.py [--check-media] [--schemas URL-or-dir] ROOT [ROOT ...]

ROOT is a folder holding movies/ and/or shows/. Every item folder must contain manifest.json and
metadata/metadata.json. Beyond JSON Schema, the checks that span files:

  - itemId equals the folder name, and the metadata belongs to the same item and type
  - every image listed in metadata.json exists in metadata/ with the recorded sha256 and size,
    and metadata/ holds no unlisted files
  - a series lists exactly the episode folders under episodes/, and each names the series as its parent
  - every version path exists, exactly one version is primary, and probe files match their sha256
  - with --check-media: every playback path named by the manifest exists (rendition dirs,
    subtitle files, trickplay), so a package folder is really playable

The schemas are loaded from the local library/v1 folder next to this tool by default; pass
--schemas https://zaentrum.github.io/schemas/library/v1 to validate against the published copies.
"""
import argparse, hashlib, json, os, sys, urllib.request
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "library", "v1")
NAMES = ["defs", "manifest", "metadata"]
BASE = "https://zaentrum.github.io/schemas/library/v1/"


def load_schemas(where):
    docs = {}
    for n in NAMES:
        if where.startswith("http"):
            with urllib.request.urlopen(f"{where.rstrip('/')}/{n}.schema.json", timeout=30) as r:
                docs[n] = json.load(r)
        else:
            docs[n] = json.load(open(os.path.join(where, f"{n}.schema.json")))
    registry = Registry().with_resources((BASE + f"{n}.schema.json", Resource.from_contents(d)) for n, d in docs.items())
    for n, d in docs.items():
        Draft202012Validator.check_schema(d)
        if d["$id"] != BASE + f"{n}.schema.json":
            raise SystemExit(f"{n}.schema.json: $id {d['$id']} is not {BASE}{n}.schema.json")
    return {n: Draft202012Validator(docs[n], registry=registry, format_checker=FormatChecker()) for n in ("manifest", "metadata")}


def sha_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


class Checker:
    def __init__(self, validators, check_media):
        self.v = validators
        self.check_media = check_media
        self.errors = []
        self.counts = {"movie": 0, "series": 0, "episode": 0, "images": 0, "probes": 0}

    def err(self, where, msg):
        self.errors.append(f"{where}: {msg}")

    def schema(self, kind, doc, where):
        for e in sorted(self.v[kind].iter_errors(doc), key=lambda e: list(e.absolute_path)):
            self.err(where, f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message[:300]}")

    def load(self, p):
        try:
            return json.load(open(p))
        except FileNotFoundError:
            self.err(p, "missing")
        except json.JSONDecodeError as e:
            self.err(p, f"not JSON: {e}")
        return None

    def item(self, d, expect_type=None, parent=None):
        mp = os.path.join(d, "manifest.json")
        man = self.load(mp)
        if man is None:
            return None
        self.schema("manifest", man, mp)
        if man.get("itemId") != os.path.basename(d.rstrip("/")):
            self.err(mp, f"itemId {man.get('itemId')} does not match folder {os.path.basename(d)}")
        if expect_type and man.get("type") != expect_type:
            self.err(mp, f"type {man.get('type')} where {expect_type} was expected")
        t = man.get("type")
        if t in self.counts:
            self.counts[t] += 1

        md = os.path.join(d, "metadata")
        meta = self.load(os.path.join(md, "metadata.json"))
        if meta is not None:
            self.schema("metadata", meta, os.path.join(md, "metadata.json"))
            if meta.get("itemId") != man.get("itemId"):
                self.err(md, "metadata itemId differs from manifest")
            if meta.get("type") != t:
                self.err(md, "metadata type differs from manifest")
            listed = set()
            for img in meta.get("images") or []:
                f = os.path.join(md, img.get("file", ""))
                listed.add(img.get("file"))
                if not os.path.isfile(f):
                    self.err(f, "image listed in metadata.json does not exist")
                    continue
                self.counts["images"] += 1
                if os.path.getsize(f) != img.get("sizeBytes"):
                    self.err(f, f"size {os.path.getsize(f)} != {img.get('sizeBytes')}")
                if sha_file(f) != img.get("sha256"):
                    self.err(f, "sha256 does not match metadata.json")
                if t != "series" and img.get("season") is not None:
                    self.err(f, "season-specific image on a non-series item")
            if os.path.isdir(md):
                for name in os.listdir(md):
                    if name != "metadata.json" and name not in listed:
                        self.err(os.path.join(md, name), "file in metadata/ is not listed in metadata.json")
            if t == "series":
                known = {s.get("number") for s in (meta.get("series") or {}).get("seasons") or []}
                for img in meta.get("images") or []:
                    if img.get("season") is not None and known and img["season"] not in known:
                        self.err(md, f"image {img['file']} names season {img['season']} that metadata does not describe")

        if t == "episode" and parent is not None and (man.get("episode") or {}).get("seriesId") != parent:
            self.err(mp, f"episode.seriesId {(man.get('episode') or {}).get('seriesId')} is not the enclosing series {parent}")
        if t == "episode" and parent is None:
            self.err(mp, "episode outside a series folder")

        if t == "series":
            listed = {}
            for s in (man.get("series") or {}).get("seasons") or []:
                for e in s.get("episodes") or []:
                    if e["itemId"] in listed:
                        self.err(mp, f"episode {e['itemId']} listed twice")
                    listed[e["itemId"]] = e["path"]
                    if e["path"].rstrip("/") != f"episodes/{e['itemId']}":
                        self.err(mp, f"episode path {e['path']} is not episodes/{e['itemId']}/")
            ed = os.path.join(d, "episodes")
            present = set(os.listdir(ed)) if os.path.isdir(ed) else set()
            for missing in sorted(set(listed) - present):
                self.err(mp, f"lists episode {missing} but episodes/{missing}/ does not exist")
            for orphan in sorted(present - set(listed)):
                self.err(os.path.join(ed, orphan), "episode folder not listed in the series manifest")
            for eid in sorted(present & set(listed)):
                self.item(os.path.join(ed, eid), "episode", parent=man.get("itemId"))

        versions = man.get("versions") or []
        if versions and sum(1 for v in versions if v.get("primary")) != 1:
            self.err(mp, "exactly one version must be primary")
        for v in versions:
            vp = os.path.normpath(os.path.join(d, v.get("path", ".")))
            if not os.path.isdir(vp):
                self.err(mp, f"version path {v.get('path')} does not exist")
            for s in v.get("sources") or []:
                pf = (s.get("probe") or {}).get("file")
                if pf:
                    f = os.path.join(d, pf)
                    if not os.path.isfile(f):
                        self.err(f, "probe file missing")
                    else:
                        self.counts["probes"] += 1
                        if sha_file(f) != s["probe"].get("sha256"):
                            self.err(f, "probe sha256 does not match the manifest")
                for sc in s.get("sidecars") or []:
                    if not os.path.isfile(os.path.join(d, sc["file"])):
                        self.err(os.path.join(d, sc["file"]), "sidecar missing")
            pkg = v.get("package")
            if v.get("path") == "." and pkg and pkg.get("state") == "complete" and "renditions" not in man:
                self.err(mp, "the version stored in this folder has a complete package but the manifest has no playback fields")
            if self.check_media and pkg and pkg.get("state") == "complete":
                pb = man if v.get("path") == "." else (pkg.get("playback") or {})
                for r in ((pb.get("renditions") or {}).get("video") or []) + ((pb.get("renditions") or {}).get("audio") or []):
                    if not os.path.isdir(os.path.join(vp, r["dir"])):
                        self.err(vp, f"rendition dir {r['dir']} missing")
                for sub in pb.get("subtitles") or []:
                    if not os.path.isfile(os.path.join(vp, sub["path"])):
                        self.err(vp, f"subtitle {sub['path']} missing")
                tp = pb.get("trickplay")
                if tp and not os.path.isfile(os.path.join(vp, tp["vttPath"])):
                    self.err(vp, f"trickplay {tp['vttPath']} missing")
                if not os.path.isfile(os.path.join(vp, ".complete")):
                    self.err(vp, ".complete marker missing")
        return man

    def root(self, r):
        found = 0
        for category, kind in (("movies", "movie"), ("shows", "series")):
            base = os.path.join(r, category)
            if not os.path.isdir(base):
                continue
            for shard in sorted(os.listdir(base)):
                sd = os.path.join(base, shard)
                if not os.path.isdir(sd):
                    continue
                for iid in sorted(os.listdir(sd)):
                    if not iid.startswith(shard):
                        self.err(os.path.join(sd, iid), f"item folder is not in shard {iid[:2]}")
                    self.item(os.path.join(sd, iid), kind)
                    found += 1
        if not found:
            self.err(r, "no items found under movies/ or shows/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="+")
    ap.add_argument("--schemas", default=HERE)
    ap.add_argument("--check-media", action="store_true")
    args = ap.parse_args()
    c = Checker(load_schemas(args.schemas), args.check_media)
    for r in args.roots:
        c.root(r)
    print("checked:", c.counts)
    if c.errors:
        for e in c.errors[:200]:
            print("  " + e)
        if len(c.errors) > 200:
            print(f"  ... and {len(c.errors) - 200} more")
        print(f"FAILED: {len(c.errors)} problem(s)")
        sys.exit(1)
    print("OK")


if __name__ == "__main__":
    main()
