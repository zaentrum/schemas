#!/usr/bin/env python3
"""Close the chains of the packages a v2 library adopted with their checksums pending.

Usage:
  library-v2-fill-checksums.py LIBRARY [--apply] [--run RUN] [--items ID[,ID…]] [--until HH:MM]
                               [--max-bytes N] [--verbose]

A migration that could not read a store of many terabytes through while its service was paused
stages its packages with --checksums pending (library-v2-from-catalog.py): each package.json counts
its files and their bytes and names no hash, there is no checksums.sha256, and .complete names that
package.json. The version plays as any other. This hashes such a package's files afterwards — every
file under hls/ subs/ trickplay/ trailers/ and the version.json beside them — and closes the chain:

  checksums.sha256   written, one '<hex>  <path>' line per file
  package.json       written again, its checksums naming that file's hash in place of "pending": true,
                     and nothing else of it changed
  .complete          written again: sha256:<hex> of that package.json

in that order, each put in place in one step. Before a folder is touched it is held against its
record: it must hold exactly the files and the bytes the record counted, and .complete must name the
package.json. A folder that does not is a conflict, reported and left as it was.

A dry run, the default, lists every pending folder with what it would read, and reads nothing.
--apply fills them, folder by folder, journaled under <root>/.work/migration/<run>/ — the run is
fill-checksums unless --run names another — every path relative to the library root:

  checksums.jsonl   {"at", "op": "fill", "dir", "before": "sha256:…", "after": "sha256:…"} before a
                    folder's package.json is replaced, and {"op": "filled", "dir", "itemId",
                    "versionId", "packageId", "files", "bytes", "at"} once its chain is closed: the
                    list of what the catalog can now call verified in full
  before/<dir>/     the package.json and the .complete it replaced

A run that stopped between a folder's package.json and its .complete is finished by the next: the
journal says which package.json it wrote, and the .complete that names it follows. Running it again
changes nothing more.

--until HH:MM (UTC) starts no folder after that time, and --max-bytes none once that many bytes were
read: the reading is all the cost, and a library that is being watched in the evening is left alone.
--items narrows the run to those items' versions.

It will not read or change an original, a source, an extra, a projection or the database, nor a
package whose checksums are not pending.
"""
import argparse, datetime, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    from libv2_records import (LIBV2_RECORDS, chain_problems, checksums_pending, fill_checksums, listdir, sha_file)
except ImportError:
    if "LIBV2_RECORDS" not in globals():
        sys.exit("library-v2-fill-checksums.py needs libv2_records.py: run it from tools/, or pipe the two "
                 "together, cat tools/libv2_records.py tools/library-v2-fill-checksums.py | python3 - …")
if LIBV2_RECORDS < 4:
    sys.exit(f"library-v2-fill-checksums.py needs libv2_records.py 4 or later, found {LIBV2_RECORDS}")

SUMS = "checksums.sha256"


def now():
    return datetime.datetime.now(datetime.timezone.utc)


def stamp(t=None):
    return (t or now()).strftime("%Y-%m-%dT%H:%M:%SZ")


def version_folders(root):
    """Every version folder of the tree, relative to root, in order: movies' and series', then
    each series' episodes'."""
    out = []

    def of_item(item_dir):
        vd = os.path.join(item_dir, "versions")
        if os.path.isdir(vd):
            out.extend(os.path.join(vd, v) for v in listdir(vd) if os.path.isdir(os.path.join(vd, v)))

    for kind in ("movies", "series"):
        base = os.path.join(root, kind)
        if not os.path.isdir(base):
            continue
        for shard in listdir(base):
            sd = os.path.join(base, shard)
            if not os.path.isdir(sd):
                continue
            for item in listdir(sd):
                item_dir = os.path.join(sd, item)
                of_item(item_dir)
                eps = os.path.join(item_dir, "episodes")
                if os.path.isdir(eps):
                    for ep in listdir(eps):
                        of_item(os.path.join(eps, ep))
    return [os.path.relpath(p, root) for p in out]


def ids_of(rel):
    """(itemId, versionId) of a version folder's path relative to the root."""
    parts = rel.replace(os.sep, "/").split("/")
    return parts[-3], parts[-1]


def replace(path, data):
    """data at path, put in place in one step."""
    tmp = path + ".filling"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class Run:
    def __init__(self, root, name):
        self.root = root
        self.dir = os.path.join(root, ".work", "migration", name)
        self.journal = os.path.join(self.dir, "checksums.jsonl")

    def lines(self):
        if not os.path.isfile(self.journal):
            return []
        out = []
        with open(self.journal, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass  # a line a crash cut short: the folder it was about is checked again
        return out

    def add(self, **entry):
        os.makedirs(self.dir, exist_ok=True)
        with open(self.journal, "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": stamp(), **entry}, ensure_ascii=False, sort_keys=True) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def keep(self, rel, name):
        """A copy of the record about to be replaced, under before/."""
        dst = os.path.join(self.dir, "before", rel, name)
        if os.path.isfile(dst):
            return
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(os.path.join(self.root, rel, name), "rb") as s, open(dst, "wb") as d:
            d.write(s.read())

    def finish_interrupted(self):
        """A folder whose package.json this run wrote and whose .complete does not name it yet."""
        done = {e.get("dir") for e in self.lines() if e.get("op") == "filled"}
        finished = 0
        for e in self.lines():
            rel = e.get("dir")
            if e.get("op") != "fill" or rel in done:
                continue
            folder = os.path.join(self.root, rel)
            pkg = os.path.join(folder, "package.json")
            if not os.path.isfile(pkg) or sha_file(pkg) != e.get("after"):
                continue  # the package.json was never replaced: the folder is filled as any pending one
            with open(pkg, "rb") as f:
                package_bytes = f.read()
            from libv2_records import complete
            replace(os.path.join(folder, ".complete"), complete(package_bytes))
            self.filled(rel, package_bytes)
            done.add(rel)
            finished += 1
        return finished

    def filled(self, rel, package_bytes):
        package = json.loads(package_bytes)
        item, version = ids_of(rel)
        self.add(op="filled", dir=rel.replace(os.sep, "/"), itemId=item, versionId=version,
                 packageId=package.get("packageId"), files=package["checksums"]["files"],
                 bytes=package["checksums"]["bytes"])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("root")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--run", default="fill-checksums")
    ap.add_argument("--items", default="", help="comma-separated item ids: only their versions")
    ap.add_argument("--until", default="", help="HH:MM in UTC: start no folder after that time")
    ap.add_argument("--max-bytes", type=int, default=0, help="start no folder once this many bytes were read")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    root = os.path.abspath(a.root)
    if not os.path.isdir(root):
        sys.exit(f"{root} is not a folder")
    only = {i for i in a.items.split(",") if i}
    until = None
    if a.until:
        try:
            hh, mm = a.until.split(":")
            until = now().replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
            if until <= now():
                until += datetime.timedelta(days=1)
        except ValueError:
            sys.exit("--until takes HH:MM")
    run = Run(root, a.run)
    if a.apply:
        n = run.finish_interrupted()
        if n:
            print(f"finished {n} folder(s) an earlier run left between package.json and .complete")

    pending, filled, conflicts, read, stopped = [], 0, [], 0, None
    for rel in version_folders(root):
        folder = os.path.join(root, rel)
        if only and ids_of(rel)[0] not in only:
            continue
        if not checksums_pending(folder):
            continue
        with open(os.path.join(folder, "package.json"), encoding="utf-8") as f:
            cs = json.load(f)["checksums"]
        pending.append((rel, cs.get("files") or 0, cs.get("bytes") or 0))
        if not a.apply:
            if a.verbose:
                print(f"pending  {rel}: {cs.get('files')} files, {cs.get('bytes')} bytes")
            continue
        if stopped:
            continue
        if until and now() >= until:
            stopped = f"it is past {a.until} UTC"
            continue
        if a.max_bytes and read >= a.max_bytes:
            stopped = f"{read} bytes were read, --max-bytes is {a.max_bytes}"
            continue
        problems = chain_problems(folder)
        if problems:
            conflicts.append((rel, problems[0]))
            continue
        out = fill_checksums(folder)
        if out is None:
            conflicts.append((rel, "its files changed while they were read"))
            continue
        sums, package_bytes, complete_bytes = out
        pkg = os.path.join(folder, "package.json")
        from libv2_records import sha_bytes
        run.keep(rel, "package.json")
        run.keep(rel, ".complete")
        run.add(op="fill", dir=rel.replace(os.sep, "/"), before=sha_file(pkg), after=sha_bytes(package_bytes))
        replace(os.path.join(folder, SUMS), sums)
        replace(pkg, package_bytes)
        replace(os.path.join(folder, ".complete"), complete_bytes)
        left = chain_problems(folder, full=False)
        if left:
            conflicts.append((rel, "after the fill: " + left[0]))
            continue
        run.filled(rel, package_bytes)
        filled += 1
        read += cs.get("bytes") or 0
        if a.verbose:
            print(f"filled   {rel}: {cs.get('files')} files, {cs.get('bytes')} bytes")

    total_files, total_bytes = sum(p[1] for p in pending), sum(p[2] for p in pending)
    if not a.apply:
        print(f"{len(pending)} package(s) with their checksums pending: {total_files} files, {total_bytes} bytes to read")
        print("a dry run: --apply fills them")
        return
    for rel, why in conflicts:
        print(f"conflict {rel}: {why}")
    print(f"filled {filled} of {len(pending)} pending package(s), {read} bytes read; "
          f"{len(conflicts)} conflict(s), {len(pending) - filled - len(conflicts)} left")
    if stopped:
        print(f"stopped: {stopped}; run it again to go on")
    if conflicts:
        sys.exit(1)


if __name__ == "__main__":
    main()
