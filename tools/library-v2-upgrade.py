#!/usr/bin/env python3
"""Upgrade a v2 library tree in place to the layout in which every record proves itself.

Usage:
  library-v2-upgrade.py LIBRARY [--dry-run] [--verbose]

A tree written before 2026-10-02 (b) validates against the layout of its day; this brings it to the
one validate-library-v2.py and library-v2-media-check.py now require, and changes nothing else:

  item folder   writes checksums.sha256, listing exactly item.json
  sources/      moves each sources/<sourceId>.json to sources/<sourceId>/source.json, beside the
                probe and sidecars already there, and writes the folder's checksums.sha256 over them
  events/       moves each events/<stamp>[-<eventId8>]-<kind>.json to
                events/<stamp>-<eventId8>-<kind>/event.json, and writes that folder's checksums
  versions/     closes each finished version's chain: checksums.sha256 drops its .complete line and
                lists version.json, package.json records the new file's hash, count and size, and
                .complete is written last, holding sha256:<hex> of package.json

It is plain standard-library Python 3.11 and needs no network, so it runs where the share is
mounted, piped into a pod if that is the only place it is reachable:

  oc exec -i deploy/packager -- python3 - /var/lib/katalog/library --dry-run < library-v2-upgrade.py

What it will not do:
  * write a checksum over a record it cannot check first. An item.json that does not name its
    folder, a probe or sidecar whose bytes contradict its source record, an event whose name
    contradicts its content, a version whose checksums do not match what package.json recorded or
    do not list exactly the package's files: each is reported as a conflict and left as it was;
  * read the media. A package file's digest is carried forward from the checksums the packager
    wrote, never computed again, so a file that changed since is not blessed — the validator's
    --check-checksums and the media check's --checksums still prove every one of them;
  * touch a projection (metadata.json, person.json, images), a version an event removed, a version
    without a finished package, or anything under people/.

Every file is written to a temporary name in its own folder and renamed into place, and a version
is written from the top of the chain down — package.json, then checksums.sha256, then .complete — so
a run that is interrupted anywhere leaves states the next run recognises and finishes. Running it
again changes nothing. It exits non-zero when anything was left as a conflict.
"""
import argparse, datetime, hashlib, json, os, re, stat, sys

OS_ARTEFACTS = re.compile(r"^(\.DS_Store|\._.*|Thumbs\.db|desktop\.ini|@eaDir|\.@__thumb|#recycle|\.AppleDouble)$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
KINDS = ("original-deleted", "version-removed", "package-superseded", "source-removed", "note")
OLD_EVENT = re.compile(r"^(\d{8}T\d{6}Z)(?:-([0-9a-f]{8}))?-(" + "|".join(KINDS) + r")\.json$")
EVENT_FOLDER = re.compile(r"^(\d{8}T\d{6}Z)-([0-9a-f]{8})-(" + "|".join(KINDS) + r")$")
SUM_LINE = re.compile(r"^([0-9a-f]{64})  (.+)$")
COMPLETE = re.compile(r"^sha256:[0-9a-f]{64}$")
PACKAGE_DIRS = ("hls", "subs", "trickplay", "trailers")
SUMS = "checksums.sha256"
CHAIN = (".complete", "package.json", SUMS)
ACTIONS = (("item", "item checksums written", "write the checksums of"),
           ("source-moved", "source records moved into their folders", "move a source record to"),
           ("source", "source checksums written", "write the checksums of"),
           ("event-moved", "events moved into their folders", "move an event to"),
           ("event", "event checksums written", "write the checksums of"),
           ("version", "version chains closed", "close the chain of"))


def listdir(d):
    return sorted(n for n in os.listdir(d) if not OS_ARTEFACTS.match(n))


def sha_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read(p):
    with open(p, "rb") as f:
        return f.read()


def json_bytes(doc):
    return (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def parse_sums(data):
    """sha256sum lines as an ordered {name: hex}, or None when any line is not one."""
    out = {}
    for line in data.decode("utf-8", "replace").splitlines():
        m = SUM_LINE.match(line)
        if not m:
            if line.strip():
                return None
            continue
        out[m.group(2)] = m.group(1)
    return out


def format_sums(entries):
    return "".join(f"{entries[n]}  {n}\n" for n in sorted(entries)).encode()


def stamp_of(timestamp):
    t = datetime.datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    return t.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def package_files(vp):
    out = []
    for d in PACKAGE_DIRS:
        for root, dirs, files in os.walk(os.path.join(vp, d)):
            dirs[:] = sorted(x for x in dirs if not OS_ARTEFACTS.match(x))
            out += [os.path.relpath(os.path.join(root, f), vp).replace(os.sep, "/")
                    for f in files if not OS_ARTEFACTS.match(f)]
    return sorted(out)


class Conflict(Exception):
    """Something that contradicts the records: reported, and left exactly as it was."""


class Upgrade:
    def __init__(self, root, dry_run, verbose):
        self.root = root
        self.dry_run = dry_run
        self.verbose = verbose
        self.done = {key: 0 for key, _, _ in ACTIONS}
        self.already = 0
        self.conflicts = []
        self.notes = []

    # -------------------------------------------------- writing
    def act(self, key, where):
        self.done[key] += 1
        if self.verbose:
            verb = {k: v for k, _, v in ACTIONS}[key]
            print(f"  {'would ' if self.dry_run else ''}{verb} {os.path.relpath(where, self.root)}")

    def write(self, path, data, like=None):
        """Write to a temporary name in the same folder and rename into place; a rewritten file keeps
        the permissions of the one it replaces."""
        if self.dry_run:
            return
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if like and os.path.exists(like):
            os.chmod(tmp, stat.S_IMODE(os.stat(like).st_mode))
        os.replace(tmp, path)

    def move(self, src, dst):
        """A rename within the tree, never a copy: the record that was there is the record that is."""
        if self.dry_run:
            return
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.exists(dst):
            raise Conflict(f"{dst} appeared while it was being upgraded")
        os.rename(src, dst)

    def cover(self, folder, names, key):
        """checksums.sha256 over exactly names, or confirm the one there lists them as they are."""
        sums = os.path.join(folder, SUMS)
        want = {n: sha_file(os.path.join(folder, n)) for n in names if os.path.isfile(os.path.join(folder, n))}
        if len(want) != len(names):
            if self.dry_run:  # a record this run would have moved here first
                self.act(key, folder)
                return
            raise Conflict(f"{folder}: {', '.join(sorted(set(names) - set(want)))} missing")
        if os.path.isfile(sums):
            if parse_sums(read(sums)) == want:
                return
            raise Conflict(f"{sums} does not list exactly {', '.join(sorted(names))} as they are; not overwritten")
        self.write(sums, format_sums(want))
        self.act(key, folder)

    # -------------------------------------------------- one item folder
    def item(self, d):
        try:
            self._item(d)
        except Conflict as e:
            self.conflicts.append(str(e))
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            self.conflicts.append(f"{d}: cannot be upgraded: {type(e).__name__}: {e}")

    def _item(self, d):
        if os.path.isfile(os.path.join(d, "manifest.json")):
            raise Conflict(f"{d}: a v1 item folder; library-v2-from-v1.py converts it")
        ip = os.path.join(d, "item.json")
        item = json.loads(read(ip))
        if not isinstance(item, dict) or item.get("itemId") != os.path.basename(d):
            raise Conflict(f"{ip}: does not name its folder, so it is not covered")
        before = (sum(self.done.values()), len(self.conflicts))
        self.cover(d, ["item.json"], "item")
        removed = self.removed(d)
        self.events(d)
        self.sources(d)
        base = os.path.join(d, "versions")
        for name in (listdir(base) if os.path.isdir(base) else []):
            vp = os.path.join(base, name)
            if not os.path.isdir(vp) or not UUID_RE.match(name):
                continue
            if name in removed:
                self.notes.append(f"{vp}: removed by an event, so its folder is ignored and not upgraded")
                continue
            try:
                self.version(vp)
            except Conflict as e:
                self.conflicts.append(str(e))
        if (sum(self.done.values()), len(self.conflicts)) == before:
            self.already += 1

    def removed(self, d):
        """The versions a version-removed event names, from either layout the events are in."""
        out, base = set(), os.path.join(d, "events")
        for name in (listdir(base) if os.path.isdir(base) else []):
            p = os.path.join(base, name, "event.json") if os.path.isdir(os.path.join(base, name)) else os.path.join(base, name)
            try:
                ev = json.loads(read(p))
            except (OSError, ValueError):
                continue
            if isinstance(ev, dict) and ev.get("kind") == "version-removed" and ev.get("versionId"):
                out.add(ev["versionId"])
        return out

    # -------------------------------------------------- sources/
    def sources(self, d):
        """Each sources/<id>.json checked, moved into its folder and covered, then every folder that was
        already one, covered."""
        base = os.path.join(d, "sources")
        if not os.path.isdir(base):
            return
        handled = set()
        for name in listdir(base):
            p = os.path.join(base, name)
            if name.endswith(".json") and UUID_RE.match(name[:-5]) and os.path.isfile(p):
                handled.add(name[:-5])
                self.source(d, os.path.join(base, name[:-5]), old=p)
        for name in listdir(base):
            p = os.path.join(base, name)
            if os.path.isdir(p) and UUID_RE.match(name) and name not in handled:
                self.source(d, p)

    def source(self, d, folder, old=None):
        try:
            self._source(d, folder, old)
        except Conflict as e:
            self.conflicts.append(str(e))
        except (ValueError, TypeError, AttributeError) as e:
            self.conflicts.append(f"{old or folder}: not a source record that can be upgraded: {type(e).__name__}: {e}")

    def _source(self, d, folder, old):
        """Everything is checked before anything moves: a source the upgrade refuses stays exactly as it
        was, in whichever layout it was in."""
        sp = os.path.join(folder, "source.json")
        if old is None and not os.path.isfile(sp):
            raise Conflict(f"{folder}: a source folder without its source.json record")
        data = read(old or sp)
        rec = json.loads(data)
        if not isinstance(rec, dict) or rec.get("sourceId") != os.path.basename(folder):
            raise Conflict(f"{old or sp}: its sourceId is not its folder's name, so it is not upgraded")
        if old and os.path.isfile(sp) and read(sp) != data:
            raise Conflict(f"{old}: {sp} already holds another record; both are left")
        names = ["source.json"]
        probe = rec.get("probe") or {}
        if probe.get("file"):
            f = os.path.join(d, probe["file"])
            if os.path.dirname(os.path.abspath(f)) != os.path.abspath(folder):
                raise Conflict(f"{old or sp}: its probe {probe['file']} is not in its own folder")
            if not os.path.isfile(f) or (probe.get("sha256") and "sha256:" + sha_file(f) != probe["sha256"]):
                raise Conflict(f"{f}: missing, or not the probe its source record hashed; not upgraded")
            names.append(os.path.basename(f))
        for sc in rec.get("sidecars") or []:
            f = os.path.join(d, sc.get("file") or "")
            if os.path.dirname(os.path.abspath(f)) != os.path.abspath(folder):
                raise Conflict(f"{old or sp}: its sidecar {sc.get('file')} is not in its own folder")
            if not os.path.isfile(f) or (sc.get("sha256") and "sha256:" + sha_file(f) != sc["sha256"]) \
                    or (sc.get("sizeBytes") is not None and os.path.getsize(f) != sc["sizeBytes"]):
                raise Conflict(f"{f}: missing, or not the sidecar its source record describes; not upgraded")
            names.append(os.path.basename(f))
        stray = [n for n in (listdir(folder) if os.path.isdir(folder) else [])
                 if n not in names and n not in (SUMS, SUMS + ".tmp")]
        if stray:
            raise Conflict(f"{folder}: holds {', '.join(stray)}, which its record does not name; not upgraded")
        if old:
            if os.path.isfile(sp):
                if not self.dry_run:
                    os.unlink(old)  # an earlier run moved it and was stopped before it could finish
            else:
                self.move(old, sp)
                self.act("source-moved", sp)
        self.cover(folder, names, "source")

    # -------------------------------------------------- events/
    def events(self, d):
        base = os.path.join(d, "events")
        if not os.path.isdir(base):
            return
        for name in listdir(base):
            p = os.path.join(base, name)
            m = OLD_EVENT.match(name)
            if not (m and os.path.isfile(p)):
                continue
            try:
                ev = json.loads(read(p))
                if not isinstance(ev, dict) or not ev.get("eventId") or not ev.get("at"):
                    raise Conflict(f"{p}: not an event record, so it is not moved")
                if m.group(1) != stamp_of(ev["at"]) or m.group(3) != ev.get("kind") or \
                        (m.group(2) and not ev["eventId"].startswith(m.group(2))):
                    raise Conflict(f"{p}: its name contradicts its moment, kind or id, so it is not moved")
                folder = os.path.join(base, f"{m.group(1)}-{ev['eventId'][:8]}-{ev['kind']}")
                target = os.path.join(folder, "event.json")
                if os.path.isfile(target):
                    if read(target) != read(p):
                        raise Conflict(f"{p}: {target} already holds another event; both are left")
                    if not self.dry_run:
                        os.unlink(p)
                else:
                    self.move(p, target)
                    self.act("event-moved", target)
                self.cover(folder, ["event.json"], "event")
            except Conflict as e:
                self.conflicts.append(str(e))
            except (ValueError, KeyError, TypeError) as e:
                self.conflicts.append(f"{p}: not an event record that can be moved: {type(e).__name__}: {e}")
        for name in listdir(base):
            p = os.path.join(base, name)
            if os.path.isdir(p) and EVENT_FOLDER.match(name):
                try:
                    self.cover(p, ["event.json"], "event")
                except Conflict as e:
                    self.conflicts.append(str(e))

    # -------------------------------------------------- versions/
    def version(self, vp):
        """Close the chain .complete -> package.json -> checksums.sha256 -> version.json and the
        package, from whichever state an earlier run, or the old layout, left it in."""
        mark, pp, sp, vj = (os.path.join(vp, n) for n in (".complete", "package.json", SUMS, "version.json"))
        if not os.path.isfile(mark) and not os.path.isfile(pp):
            return  # no package yet: nothing covers version.json until one completes
        if not (os.path.isfile(mark) and os.path.isfile(pp) and os.path.isfile(sp) and os.path.isfile(vj)):
            raise Conflict(f"{vp}: a finished version is version.json, checksums.sha256, package.json and .complete "
                           f"together, and here one is missing; not upgraded")
        package_data, sums_data = read(pp), read(sp)
        package = json.loads(package_data)
        recorded = ((package.get("checksums") or {}).get("sha256") or "").replace("sha256:", "")
        sums = parse_sums(sums_data)
        if sums is None:
            raise Conflict(f"{sp}: not a sha256sum file; not upgraded")
        version_digest = sha_file(vj)
        if "version.json" in sums and sums["version.json"] != version_digest:
            raise Conflict(f"{vj}: does not match the checksum {SUMS} recorded for it; not upgraded")
        new_form = "version.json" in sums and not set(CHAIN) & set(sums)
        if recorded == sha_bytes(sums_data) and new_form:
            self.close(vp, mark, package_data, "version")  # finished, or stopped before .complete
            return
        upgraded = {n: h for n, h in sums.items() if n != ".complete"}
        upgraded["version.json"] = version_digest
        upgraded_data = format_sums(upgraded)
        if recorded == sha_bytes(sums_data) and "version.json" not in sums:
            # the layout before: the checksums are the ones package.json recorded, over the package
            # and its marker. They must list exactly the package's files to be carried forward.
            listed = set(sums) - {".complete"}
            on_disk = set(package_files(vp))
            if listed != on_disk:
                raise Conflict(f"{sp}: does not list exactly the package's files "
                               f"({len(on_disk - listed)} unlisted, {len(listed - on_disk)} not there); "
                               f"not upgraded — validate-library-v2.py --check-media says which")
            if ".complete" in sums and sums[".complete"] != sha_file(mark):
                self.notes.append(f"{mark}: changed after the package completed; it is replaced by the hash of "
                                  f"package.json, which is all it holds from now on")
            package["checksums"] = dict(package["checksums"], sha256="sha256:" + sha_bytes(upgraded_data),
                                        files=len(upgraded),
                                        bytes=sum(os.path.getsize(os.path.join(vp, n)) for n in upgraded))
            package_data = json_bytes(package)
            self.write(pp, package_data, like=pp)
            self.write(sp, upgraded_data, like=sp)
        elif recorded == sha_bytes(upgraded_data) and "version.json" not in sums:
            # an earlier run wrote package.json and was stopped before the checksums
            self.write(sp, upgraded_data, like=sp)
        else:
            raise Conflict(f"{sp}: matches neither the hash package.json records nor the upgrade of it; not upgraded")
        self.close(vp, mark, package_data, "version", force=True)

    def close(self, vp, mark, package_data, key, force=False):
        """.complete, last: the hash of package.json and nothing else."""
        want = "sha256:" + sha_bytes(package_data)
        if not force and read(mark).decode("utf-8", "replace").strip() == want:
            return
        self.write(mark, (want + "\n").encode(), like=mark)
        self.act(key, vp)

    # -------------------------------------------------- the tree
    def run(self):
        found = 0
        for category in ("movies", "series"):
            base = os.path.join(self.root, category)
            for shard in (listdir(base) if os.path.isdir(base) else []):
                sd = os.path.join(base, shard)
                for iid in (listdir(sd) if os.path.isdir(sd) else []):
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
        return found


def main():
    ap = argparse.ArgumentParser(prog="library-v2-upgrade.py",
                                 description="Upgrade a v2 library tree in place to the layout in which every "
                                             "record proves itself.")
    ap.add_argument("root", help="the library root holding movies/ and series/")
    ap.add_argument("--dry-run", action="store_true", help="say what would be done and change nothing")
    ap.add_argument("--verbose", action="store_true", help="list every folder that is changed")
    args = ap.parse_args()
    u = Upgrade(os.path.abspath(args.root), args.dry_run, args.verbose)
    found = u.run()
    print(f"{'would upgrade' if args.dry_run else 'upgraded'} {u.root}: {found} item folder(s), "
          f"{u.already} already in the current layout")
    for key, label, _ in ACTIONS:
        if u.done[key]:
            print(f"  {label}: {u.done[key]}")
    for n in u.notes:
        print("  note " + n)
    if u.conflicts:
        print(f"conflicts, left exactly as they were: {len(u.conflicts)}")
        for c in u.conflicts:
            print("  " + c)
        return 1
    if not found:
        print("  no item folders under movies/ or series/")
        return 1
    if not any(u.done.values()):
        print("  nothing to do: the tree is in the current layout")
    return 0


if __name__ == "__main__":
    sys.exit(main())
