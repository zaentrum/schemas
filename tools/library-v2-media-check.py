#!/usr/bin/env python3
"""Check a v2 library tree against the bytes beside it, where the bytes are.

Usage:
  library-v2-media-check.py LIBRARY [--checksums] [--quiet]

The schema validator answers whether the records are well formed; this answers whether they still
describe what is on the disk. It needs nothing but the standard library, so it runs in the pod that
mounts the share:

  oc -n <ns> exec -i deploy/packager -- python3 - /var/lib/katalog/library < library-v2-media-check.py

What it checks, for every item folder:
  * every file a record names is there: the probe and the sidecars of each source, every image
    metadata.json lists, each version's originals, and each package's rendition folders, subtitles
    and trickplay sheet;
  * an original an original-deleted event covers is NOT there, and one nothing covers is, with the
    size and the qh1 fingerprint its source record wrote down;
  * a version folder with a package has its .complete marker, and one without has neither;
  * checksums.sha256 covers exactly the package's files — the rendition, subtitle, trickplay and
    trailer folders and the marker, nothing else — and matches the count, the total size and the
    hash of itself that package.json recorded (--checksums also hashes every file);
  * every image in metadata/ is named by the hash of its own content, has the recorded size, and is
    listed exactly once, with nothing unlisted beside it;
  * no file is a hard link shared with another path, because a library of links is not portable.

It exits non-zero when anything is wrong, and prints one line per problem.
"""
import argparse, hashlib, json, os, re, sys

OS_ARTEFACTS = re.compile(r"^(\.DS_Store|\._.*|Thumbs\.db|desktop\.ini|@eaDir|\.@__thumb|#recycle|\.AppleDouble)$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
PACKAGE_DIRS = ("hls", "subs", "trickplay", "trailers")
PACKAGE_MARKERS = (".complete",)
EXT_TYPE = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


def listdir(d):
    return sorted(n for n in os.listdir(d) if not OS_ARTEFACTS.match(n))


def sha_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def qh1(p):
    """sha256(first 64 KiB || last 64 KiB || uint64be(size)): two reads however big the file is."""
    size = os.path.getsize(p)
    h = hashlib.sha256()
    with open(p, "rb") as f:
        h.update(f.read(65536))
        if size > 65536:
            f.seek(max(size - 65536, 0))
            h.update(f.read(65536))
    h.update(size.to_bytes(8, "big"))
    return "sha256:" + h.hexdigest()


def package_files(vp):
    """Every file of the package in a version folder, relative to it. The records, the checksums
    file and the original beside them are not part of the package."""
    out = []
    for d in PACKAGE_DIRS:
        for root, dirs, files in os.walk(os.path.join(vp, d)):
            dirs[:] = sorted(x for x in dirs if not OS_ARTEFACTS.match(x))
            out += [os.path.relpath(os.path.join(root, f), vp) for f in sorted(files) if not OS_ARTEFACTS.match(f)]
    out += [m for m in PACKAGE_MARKERS if os.path.isfile(os.path.join(vp, m))]
    return sorted(out)


class Check:
    def __init__(self, hash_everything):
        self.hash_everything = hash_everything
        self.problems = []
        self.counts = {"items": 0, "versions": 0, "packages": 0, "originals": 0, "deleted": 0,
                       "images": 0, "files": 0, "bytes": 0}

    def err(self, where, msg):
        self.problems.append(f"{where}: {msg}")

    def load(self, p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            self.err(p, "missing")
        except (OSError, ValueError) as e:
            self.err(p, f"cannot be read: {e}")
        return None

    # -------------------------------------------------- one item
    def item(self, d):
        item = self.load(os.path.join(d, "item.json"))
        if item is None:
            return
        self.counts["items"] += 1
        events = self.events(d)
        removed = {e["versionId"] for e in events if e.get("kind") == "version-removed" and e.get("versionId")}
        sources = self.sources(d)
        self.metadata(d)
        base = os.path.join(d, "versions")
        for name in (listdir(base) if os.path.isdir(base) else []):
            vp = os.path.join(base, name)
            if not os.path.isdir(vp) or not UUID_RE.match(name):
                continue
            if name in removed:
                continue
            self.version(vp, name, sources, events)
        self.hard_links(d)

    def events(self, d):
        base = os.path.join(d, "events")
        out = []
        for name in (listdir(base) if os.path.isdir(base) else []):
            ev = self.load(os.path.join(base, name))
            if ev:
                out.append(ev)
        return out

    def sources(self, d):
        base = os.path.join(d, "sources")
        records = {}
        for name in (listdir(base) if os.path.isdir(base) else []):
            p = os.path.join(base, name)
            if os.path.isdir(p) or not (name.endswith(".json") and UUID_RE.match(name[:-5])):
                continue
            src = self.load(p)
            if not src:
                continue
            records[src.get("sourceId") or name[:-5]] = src
            probe = src.get("probe") or {}
            if probe.get("file"):
                f = os.path.join(d, probe["file"])
                if not os.path.isfile(f):
                    self.err(f, "probe file a source record names is missing")
                elif probe.get("sha256") and sha_file(f) != probe["sha256"]:
                    self.err(f, "probe file does not match the sha256 its source record wrote down")
            for sc in src.get("sidecars") or []:
                f = os.path.join(d, sc["file"])
                if not os.path.isfile(f):
                    self.err(f, "sidecar a source record names is missing")
                    continue
                if sc.get("sizeBytes") is not None and os.path.getsize(f) != sc["sizeBytes"]:
                    self.err(f, "sidecar size does not match its source record")
                if sc.get("sha256") and sha_file(f) != sc["sha256"]:
                    self.err(f, "sidecar sha256 does not match its source record")
        return records

    def metadata(self, d):
        meta = None
        if os.path.isfile(os.path.join(d, "metadata.json")):
            meta = self.load(os.path.join(d, "metadata.json"))
        md = os.path.join(d, "metadata")
        listed = set()
        for img in (meta or {}).get("images") or []:
            name = img.get("file") or ""
            f = os.path.join(md, name)
            if name in listed:
                self.err(f, "listed twice in metadata.json")
            listed.add(name)
            if not os.path.isfile(f):
                self.err(f, "image listed in metadata.json but not there")
                continue
            self.counts["images"] += 1
            digest = sha_file(f)
            if name.rsplit(".", 1)[0] != digest.split(":", 1)[1]:
                self.err(f, "image is not named by the hash of its own content")
            if img.get("sha256") and img["sha256"] != digest:
                self.err(f, "image sha256 does not match metadata.json")
            if img.get("sizeBytes") is not None and os.path.getsize(f) != img["sizeBytes"]:
                self.err(f, f"image is {os.path.getsize(f)} bytes, metadata.json says {img['sizeBytes']}")
            if img.get("contentType") and EXT_TYPE.get(name.rsplit(".", 1)[-1]) != img["contentType"]:
                self.err(f, f"image extension does not match {img['contentType']}")
        for name in (listdir(md) if os.path.isdir(md) else []):
            if name not in listed:
                self.err(os.path.join(md, name), "not listed in metadata.json")

    # -------------------------------------------------- one version
    def version(self, vp, vid, sources, events):
        v = self.load(os.path.join(vp, "version.json"))
        if v is None:
            return
        self.counts["versions"] += 1
        has_package = os.path.isfile(os.path.join(vp, "package.json"))
        marker = os.path.isfile(os.path.join(vp, ".complete"))
        if has_package != marker:
            self.err(vp, "a finished package is package.json and .complete together, and here only "
                         + ("package.json" if has_package else ".complete") + " is there")
        gone, whole = set(), False
        for e in events:
            if e.get("kind") == "original-deleted" and e.get("versionId") == vid:
                if e.get("sourceId"):
                    gone.add(e["sourceId"])
                else:
                    whole = True
        for name in v.get("originalFiles") or []:
            f = os.path.join(vp, name)
            sid = next((s for s in v.get("sourceIds") or []
                        if s in sources and (sources[s].get("file") or {}).get("name") == name), None)
            src = sources.get(sid) or {}
            if whole or (sid and sid in gone):
                self.counts["deleted"] += 1
                if os.path.isfile(f):
                    self.err(f, "an original-deleted event says this original is gone, but it is still here")
                continue
            if not os.path.isfile(f):
                self.err(f, "original file missing, and no original-deleted event says it was removed")
                continue
            self.counts["originals"] += 1
            size = (src.get("file") or {}).get("sizeBytes")
            if size is not None and os.path.getsize(f) != size:
                self.err(f, f"original is {os.path.getsize(f)} bytes, its source record says {size}")
            elif ((src.get("file") or {}).get("fixity") or {}).get("qh1") and qh1(f) != src["file"]["fixity"]["qh1"]:
                self.err(f, "original does not match the qh1 fingerprint its source record wrote down")
        if has_package:
            self.package(vp, self.load(os.path.join(vp, "package.json")))

    def package(self, vp, pkg):
        if pkg is None:
            return
        self.counts["packages"] += 1
        ren = pkg.get("renditions") or {}
        for r in (ren.get("video") or []) + (ren.get("audio") or []):
            if not os.path.isdir(os.path.join(vp, r.get("dir") or "")):
                self.err(vp, f"rendition folder {r.get('dir')} is missing")
        for sub in pkg.get("subtitles") or []:
            if not os.path.isfile(os.path.join(vp, sub.get("path") or "")):
                self.err(vp, f"subtitle {sub.get('path')} is missing")
        tp = pkg.get("trickplay")
        if tp and not os.path.isfile(os.path.join(vp, tp.get("vttPath") or "")):
            self.err(vp, f"trickplay {tp.get('vttPath')} is missing")
        for t in pkg.get("trailers") or []:
            if not os.path.isfile(os.path.join(vp, t.get("manifestPath") or "")):
                self.err(vp, f"trailer {t.get('manifestPath')} is missing")
        cs = pkg.get("checksums") or {}
        f = os.path.join(vp, cs.get("file") or "checksums.sha256")
        if not os.path.isfile(f):
            self.err(f, "the checksums file the package record names is missing")
            return
        if cs.get("sha256") and sha_file(f) != cs["sha256"]:
            self.err(f, "the checksums file does not match the sha256 package.json wrote down")
        listed = {}
        for line in open(f, encoding="utf-8"):
            digest, _, rel = line.rstrip("\n").partition("  ")
            if rel:
                listed[rel] = digest
        on_disk = package_files(vp)
        for rel in sorted(set(on_disk) - set(listed)):
            self.err(os.path.join(vp, rel), "is a file of this package that checksums.sha256 does not list")
        for rel in sorted(set(listed) - set(on_disk)):
            self.err(f, f"lists {rel}, which is not a file of this package")
        if cs.get("files") is not None and len(listed) != cs["files"]:
            self.err(f, f"lists {len(listed)} files, package.json says {cs['files']}")
        present = [rel for rel in listed if rel in set(on_disk)]
        total = sum(os.path.getsize(os.path.join(vp, rel)) for rel in present)
        self.counts["files"] += len(present)
        self.counts["bytes"] += total
        if len(present) == len(listed) and cs.get("bytes") is not None and total != cs["bytes"]:
            self.err(f, f"the package's files total {total} bytes, package.json says {cs['bytes']}")
        if self.hash_everything:
            for rel in present:
                if sha_file(os.path.join(vp, rel)).split(":", 1)[1] != listed[rel]:
                    self.err(os.path.join(vp, rel), "does not match its checksum")

    def hard_links(self, d):
        """A library is portable only when its files are its own: a hard link ties one to another
        path, where something else can change or delete it."""
        linked = []
        for root, dirs, files in os.walk(d):
            dirs[:] = sorted(x for x in dirs if not OS_ARTEFACTS.match(x) and not (root == d and x == "episodes"))
            for name in sorted(files):
                p = os.path.join(root, name)
                try:
                    if not OS_ARTEFACTS.match(name) and not os.path.islink(p) and os.stat(p).st_nlink > 1:
                        linked.append(os.path.relpath(p, d))
                except OSError:
                    continue
        if linked:
            self.err(d, f"{len(linked)} file(s) are hard links shared with another path, e.g. {linked[0]}")

    # -------------------------------------------------- the tree
    def run(self, root):
        found = 0
        for category in ("movies", "series"):
            base = os.path.join(root, category)
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
                    found += 1
                    ed = os.path.join(d, "episodes")
                    for eid in (listdir(ed) if os.path.isdir(ed) else []):
                        if os.path.isdir(os.path.join(ed, eid)):
                            self.item(os.path.join(ed, eid))
                            found += 1
        if not found:
            self.err(root, "no item folders under movies/ or series/")


def main():
    ap = argparse.ArgumentParser(prog="library-v2-media-check.py",
                                 description="Check a v2 library tree against the bytes beside it.")
    ap.add_argument("root", help="the library root holding movies/ and series/")
    ap.add_argument("--checksums", action="store_true", help="also hash every package file")
    ap.add_argument("--quiet", action="store_true", help="print the summary only")
    args = ap.parse_args()
    c = Check(args.checksums)
    c.run(os.path.abspath(args.root))
    print("checked:", c.counts)
    if not c.problems:
        print("OK")
        return 0
    if not args.quiet:
        for p in c.problems[:200]:
            print("  " + p)
        if len(c.problems) > 200:
            print(f"  … and {len(c.problems) - 200} more")
    print(f"FAILED: {len(c.problems)} problem(s)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
