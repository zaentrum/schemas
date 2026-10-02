#!/usr/bin/env python3
"""Check a v2 library tree against the bytes beside it, where the bytes are.

Usage:
  library-v2-media-check.py LIBRARY [--checksums] [--quiet]

The schema validator answers whether the records are well formed; this answers whether they still
describe what is on the disk. It needs nothing but the standard library, so it runs in the pod that
mounts the share:

  oc -n <ns> exec -i deploy/packager -- python3 - /var/lib/katalog/library < library-v2-media-check.py

What it checks, for every item folder:
  * every write-once folder proves its records, and `sha256sum -c checksums.sha256` there would
    pass: the item folder's checksums list exactly item.json, each sources/<sourceId>/ folder's
    exactly source.json, its probe and its sidecars, each events/<…>/ folder's exactly event.json,
    each with the digest of the file that is there;
  * each version is one chain: .complete holds sha256:<hex> of package.json, package.json the hash
    of checksums.sha256, which lists version.json and every file of the package — the rendition,
    subtitle, trickplay and trailer folders — and never .complete, package.json or itself, and
    matches the count and the total size package.json recorded (--checksums also hashes every
    package file);
  * every file a record names is there: the probe and the sidecars of each source, every image
    metadata.json lists, each version's originals, and each package's rendition folders, subtitles
    and trickplay sheet;
  * an original an original-deleted event covers is NOT there, and one nothing covers is, with the
    size and the qh1 fingerprint its source record wrote down;
  * a version folder with a package has its .complete marker, and one without has neither;
  * every image in metadata/ is named by the hash of its own content, has the recorded size, and is
    listed exactly once, with nothing unlisted beside it — and the same for the images in a person's
    folder under people/, beside the person.json that lists them;
  * no file is a hard link shared with another path, because a library of links is not portable.

Projections — metadata.json and person.json — carry no checksums: they are replaced whole, and an
image is named by its own hash. A tree from before 2026-10-02 (b), with sources/<id>.json and
events/<…>.json files and no item checksums, fails here until library-v2-upgrade.py upgrades it.

It exits non-zero when anything is wrong, and prints one line per problem.
"""
import argparse, hashlib, json, os, re, sys

OS_ARTEFACTS = re.compile(r"^(\.DS_Store|\._.*|Thumbs\.db|desktop\.ini|@eaDir|\.@__thumb|#recycle|\.AppleDouble)$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
PACKAGE_DIRS = ("hls", "subs", "trickplay", "trailers")
EXT_TYPE = {"jpg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
SUMS = "checksums.sha256"
CHAIN = (".complete", "package.json", SUMS)
SUM_LINE = re.compile(r"^([0-9a-f]{64})  (.+)$")
COMPLETE = re.compile(r"^sha256:[0-9a-f]{64}$")
EVENT_FOLDER = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{8}-[a-z-]+$")
UPGRADE = "library-v2-upgrade.py upgrades a tree from before 2026-10-02 (b) in place"


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
    """Every file of the package in a version folder, relative to it. The records, the chain above
    them and the original beside them are not part of the package."""
    out = []
    for d in PACKAGE_DIRS:
        for root, dirs, files in os.walk(os.path.join(vp, d)):
            dirs[:] = sorted(x for x in dirs if not OS_ARTEFACTS.match(x))
            out += [os.path.relpath(os.path.join(root, f), vp) for f in sorted(files) if not OS_ARTEFACTS.match(f)]
    return sorted(out)


def read_sums(path):
    """A sha256sum file as ({name: hex digest}, [lines that are not '<64 hex>  <name>'])."""
    entries, bad = {}, []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\n")
            m = SUM_LINE.match(line)
            if m:
                entries[m.group(2)] = m.group(1)
            elif line:
                bad.append(line)
    return entries, bad


class Check:
    def __init__(self, hash_everything):
        self.hash_everything = hash_everything
        self.problems = []
        self.counts = {"items": 0, "people": 0, "versions": 0, "packages": 0, "originals": 0, "deleted": 0,
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

    def covered(self, folder, names, what):
        """folder/checksums.sha256 lists exactly names, and each digest is that of the file there."""
        sums = os.path.join(folder, SUMS)
        if not os.path.isfile(sums):
            self.err(sums, f"missing: {what} is covered by a checksums file written with it ({UPGRADE})")
            return
        entries, bad = read_sums(sums)
        for line in bad[:3]:
            self.err(sums, f"not a sha256sum line: {line[:80]!r}")
        for name in sorted(set(names) - set(entries)):
            self.err(sums, f"does not list {name}")
        for name in sorted(set(entries) - set(names)):
            self.err(sums, f"lists {name}, which is not {what}")
        for name in sorted(set(names) & set(entries)):
            f = os.path.join(folder, name)
            if os.path.isfile(f) and sha_file(f).split(":", 1)[1] != entries[name]:
                self.err(f, f"does not match the checksum {SUMS} recorded for it: changed after it was written")

    # -------------------------------------------------- one item
    def item(self, d):
        item = self.load(os.path.join(d, "item.json"))
        if item is None:
            return
        self.counts["items"] += 1
        self.covered(d, {"item.json"}, "item.json")
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
        """events/<stamp>-<eventId8>-<kind>/: event.json and the checksums written with it."""
        base = os.path.join(d, "events")
        out = []
        for name in (listdir(base) if os.path.isdir(base) else []):
            p = os.path.join(base, name)
            if not os.path.isdir(p) or not EVENT_FOLDER.match(name):
                self.err(p, f"not an events/<YYYYMMDDTHHMMSSZ>-<eventId8>-<kind>/ folder"
                            + (f" ({UPGRADE})" if name.endswith(".json") else ""))
                continue
            ev = self.load(os.path.join(p, "event.json"))
            if ev:
                out.append(ev)
                self.covered(p, {"event.json"}, "event.json")
        return out

    def sources(self, d):
        """sources/<sourceId>/: source.json, the probe and sidecars it names, and the checksums
        written with all of them."""
        base = os.path.join(d, "sources")
        records = {}
        for name in (listdir(base) if os.path.isdir(base) else []):
            p = os.path.join(base, name)
            if not os.path.isdir(p) or not UUID_RE.match(name):
                self.err(p, "not a sources/<sourceId>/ folder" + (f" ({UPGRADE})" if name.endswith(".json") else ""))
                continue
            src = self.load(os.path.join(p, "source.json"))
            if not src:
                continue
            records[src.get("sourceId") or name] = src
            self.covered(p, {"source.json"} | {os.path.basename(x.get("file") or "") for x in src.get("sidecars") or []}
                         | ({os.path.basename((src.get("probe") or {})["file"])} if (src.get("probe") or {}).get("file") else set()),
                         "source.json, its probe or one of its sidecars")
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
        listed = self.images(md, (meta or {}).get("images") or [], "metadata.json")
        for name in (listdir(md) if os.path.isdir(md) else []):
            if name not in listed:
                self.err(os.path.join(md, name), "not listed in metadata.json")

    def images(self, folder, entries, owner):
        """Every image a projection lists is in folder, named by the hash of its own content, with
        the size and extension recorded. Returns the names listed."""
        listed = set()
        for img in entries:
            name = img.get("file") or ""
            f = os.path.join(folder, name)
            if name in listed:
                self.err(f, f"listed twice in {owner}")
            listed.add(name)
            if not os.path.isfile(f):
                self.err(f, f"image listed in {owner} but not there")
                continue
            self.counts["images"] += 1
            digest = sha_file(f)
            if name.rsplit(".", 1)[0] != digest.split(":", 1)[1]:
                self.err(f, "image is not named by the hash of its own content")
            if img.get("sha256") and img["sha256"] != digest:
                self.err(f, f"image sha256 does not match {owner}")
            if img.get("sizeBytes") is not None and os.path.getsize(f) != img["sizeBytes"]:
                self.err(f, f"image is {os.path.getsize(f)} bytes, {owner} says {img['sizeBytes']}")
            if img.get("contentType") and EXT_TYPE.get(name.rsplit(".", 1)[-1]) != img["contentType"]:
                self.err(f, f"image extension does not match {img['contentType']}")
        return listed

    def person(self, d):
        """people/<aa>/<personId>/: person.json and the images it lists, nothing else."""
        doc = self.load(os.path.join(d, "person.json"))
        if doc is None:
            return
        self.counts["people"] += 1
        listed = self.images(d, doc.get("images") or [], "person.json")
        for name in listdir(d):
            if name != "person.json" and name not in listed:
                self.err(os.path.join(d, name), "not person.json and not an image person.json lists")
        self.hard_links(d)

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
            self.package(vp, self.load(os.path.join(vp, "package.json")), marker)

    def package(self, vp, pkg, marker):
        if pkg is None:
            return
        self.counts["packages"] += 1
        if marker:
            with open(os.path.join(vp, ".complete"), "rb") as f:
                body = f.read().decode("utf-8", "replace").strip()
            if not COMPLETE.match(body):
                self.err(os.path.join(vp, ".complete"), f"holds {body[:40]!r}, not sha256:<hex> of package.json, "
                                                        f"the head of the chain that covers this version ({UPGRADE})")
            elif body != sha_file(os.path.join(vp, "package.json")):
                self.err(os.path.join(vp, ".complete"),
                         "does not name this package.json: package.json changed after the package completed")
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
        listed, bad = read_sums(f)
        for line in bad[:3]:
            self.err(f, f"not a sha256sum line: {line[:80]!r}")
        for name in CHAIN:
            if name in listed:
                self.err(f, f"lists {name}, a link of the chain above it: each link holds the hash of the one below")
        vj = os.path.join(vp, "version.json")
        if "version.json" not in listed:
            self.err(f, f"does not list version.json, the record its package was made for ({UPGRADE})")
        elif os.path.isfile(vj) and sha_file(vj).split(":", 1)[1] != listed["version.json"]:
            self.err(vj, f"does not match the checksum {SUMS} recorded for it: changed after the package completed")
        on_disk = package_files(vp) + (["version.json"] if os.path.isfile(vj) else [])
        for rel in sorted(set(on_disk) - set(listed)):
            self.err(os.path.join(vp, rel), "is a file of this package that checksums.sha256 does not list")
        for rel in sorted(set(listed) - set(on_disk) - set(CHAIN)):
            self.err(f, f"lists {rel}, which is not a file of this package")
        if cs.get("files") is not None and len(listed) != cs["files"]:
            self.err(f, f"lists {len(listed)} files, package.json says {cs['files']}")
        present = [rel for rel in listed if rel in set(on_disk)]
        total = sum(os.path.getsize(os.path.join(vp, rel)) for rel in present)
        self.counts["files"] += len(present)
        self.counts["bytes"] += total
        if len(present) == len(listed) and cs.get("bytes") is not None and total != cs["bytes"]:
            self.err(f, f"the files it lists total {total} bytes, package.json says {cs['bytes']}")
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
        base = os.path.join(root, "people")
        for shard in (listdir(base) if os.path.isdir(base) else []):
            sd = os.path.join(base, shard)
            for pid in (listdir(sd) if os.path.isdir(sd) else []):
                if os.path.isdir(os.path.join(sd, pid)):
                    self.person(os.path.join(sd, pid))
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
