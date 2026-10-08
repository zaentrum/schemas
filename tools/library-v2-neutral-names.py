#!/usr/bin/env python3
"""Give a v2 library tree written before 2026-10-08 the names the library gives its files.

Usage:
  library-v2-neutral-names.py LIBRARY [--apply] [--run RUN] [--verbose]

Since 2026-10-08 no file in the library is named as it arrived and no record names one so, because a
name, a folder or a container title can tell where a file came from: an original is original.<ext>,
or original-<n>.<ext> for a part, wherever it is kept or described, the copy of a subtitle file that
came with it subtitle-<n>.<lang>[.forced][.sdh].<ext>, and a probe names its file as the library does
and keeps no title tag (libv2_records.py makes every one of these names). A tree written before keeps
the names it was given, and this rewrites it to the new ones, changing nothing else:

  sources/<sid>/   file.name becomes the library's name and origin goes; the container's title tag goes,
                   from container.title (null) and container.tags; each subtitle copy is renamed as its
                   place among the record's subtitles and what the record says of it name it, its entry
                   losing originalName, and every other copy — NFO text, an image, anything else, which
                   routinely names where a file came from — leaves the record, moved with its entry into
                   the run's folder; ffprobe.json loses its two names (format.filename, the title tag)
                   and probe.sha256 follows it; the checksums are written again, last
  versions/<vid>/  each original it keeps is renamed as its source record names it now, and originalFiles
                   follows; edition evidence that held a file or folder name holds what it claimed; a
                   subtitle made from a renamed copy names the copy's new name in fromSidecar; and the
                   chain closes again over the records that changed — checksums.sha256 (version.json's
                   line), package.json (the hash of checksums.sha256, and the size it lists), .complete
  extras/<xid>/    each original it keeps is renamed as the library names it, originalFiles and originals
                   follow, packagedFrom names as the library would, the container's title tag goes, and
                   the checksums follow — for a packaged extra package.json and .complete with them

It will not:
  * change a folder it cannot check first: a source whose checksums do not list exactly its files as
    they are, a version or an extra whose chain does not hold over its records, an original that is
    not the size its record says, a record that names a file its folder does not hold. Each is a
    conflict, reported and left exactly as it was, and so is the rest of its item;
  * read the media: a package file's digest is carried from the checksums the packager wrote, and an
    original is renamed, never read;
  * touch a projection, an event, people/, a version an event removed or an extra one retired, a tree
    in the layout before 2026-10-02 (b) — library-v2-upgrade.py upgrades that first — or a v1 folder;
  * take an original that waits outside the record, in .work/incoming/, into its version folder, or
    change the database. Where the catalog names a file by its old path — a subtitle row pointing at a
    copy, an original's playback row — its owner points it at the new one: the journal lists every
    rename.

A dry run, the default, lists every rename, rewrite and removal it would make and changes nothing.
--apply makes them, item by item, journaled under <root>/.work/migration/<run>/ — the run is
neutral-names unless --run names another — every path relative to the library root:

  journal.jsonl     one line per action, {seq, at, item, op, ...}: first an item's plan, {"op": "plan",
                    "steps": [...]}, each step one of
                      {"op": "rename",  "from": "<path>", "to": "<path>"}
                      {"op": "remove",  "from": "<path>", "to": "<path in the run's removed/>"}
                      {"op": "rewrite", "path": "<path>", "before": "sha256:...", "after": "sha256:..."}
                    then a line for each step as it is done, in its order, and {"op": "done"} once all are
  before/<path>     every file it rewrote, as it was: what a revert writes back
  after/<path>      every file it rewrote, as it wrote it: what finishes an interrupted run
  removed/<path>    every copy it took out of the record

An item whose plan has no done line was interrupted: the next --apply of the same run finishes it
first, step by step from wherever it stopped — a step whose effect is there is skipped, and one whose
file is neither as the plan found it nor as it leaves it is a conflict. A run holds a lock on its
journal, so two cannot act on one tree at once. Running it again changes nothing. It exits non-zero
when anything was left as a conflict.

It is plain standard-library Python 3.11, and needs the record logic that makes the names,
libv2_records.py, beside it — or piped in front of it, where the share is mounted:

  cat libv2_records.py library-v2-neutral-names.py | oc exec -i deploy/packager -- python3 - /var/lib/katalog
"""
import argparse, copy, datetime, fcntl, json, os, re, sys

try:  # the record logic that names the files: beside this tool, or piped in front of it
    from libv2_records import (ORIGINAL_NAME_RE, SUMS, UUID_RE, checksums, complete, json_bytes, library_original_name,
                               listdir, read_sums, scrub_probe, sha_bytes, sha_file, subtitle_copy_name)
except ImportError:
    if "LIBV2_RECORDS" not in globals():
        sys.exit("library-v2-neutral-names.py needs libv2_records.py: run it from tools/, or pipe the two together, "
                 "cat tools/libv2_records.py tools/library-v2-neutral-names.py | python3 - …")

DEFAULT_RUN = "neutral-names"
RUN_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
EVENT_KINDS_REMOVING = {"version-removed": "versionId", "extra-removed": "extraId"}


class Conflict(Exception):
    """Something that contradicts the records: reported, and its item left exactly as it was."""


def read_bytes(p):
    with open(p, "rb") as f:
        return f.read()


def load_json(p):
    return json.loads(read_bytes(p))


def hex_of(digest):
    return digest.split(":", 1)[1]


def untitled(container):
    """A container as a record keeps it now: no title tag, under title or among its tags, in any case."""
    out = dict(container)
    if out.get("title") is not None:
        out["title"] = None
    if isinstance(out.get("tags"), dict):
        out["tags"] = {k: v for k, v in out["tags"].items() if str(k).lower() != "title"}
    return out


def part_of(file):
    """The part a source record says its file is, or None."""
    part = file.get("part") if isinstance(file.get("part"), dict) else {}
    index = part.get("index")
    return index if isinstance(index, int) and not isinstance(index, bool) and index >= 1 else None


def neutral_original(name, part=None):
    """The name the library gives an original known by name: a neutral one stays as it is, any other is
    original.<ext> of it, the part numbered when there is one."""
    return name if ORIGINAL_NAME_RE.match(str(name)) else library_original_name(name, part)


class Plan:
    """What one item folder needs: the steps in their order, and the bytes each rewrite writes."""

    def __init__(self, item):
        self.item = item
        self.steps = []
        self.data = {}

    def rename(self, src, dst):
        self.steps.append({"op": "rename", "from": src, "to": dst})

    def remove(self, src, dst):
        self.steps.append({"op": "remove", "from": src, "to": dst})

    def rewrite(self, path, before, after):
        self.steps.append({"op": "rewrite", "path": path, "before": sha_bytes(before), "after": sha_bytes(after)})
        self.data[path] = (before, after)


class Neutral:
    def __init__(self, root, run, apply, verbose):
        self.root = root
        self.run_name = run
        self.apply = apply
        self.verbose = verbose
        self.run_dir = os.path.join(root, ".work", "migration", run)
        self.journal = None
        self.seq = 0
        self.already = 0
        self.done = {"rename": 0, "remove": 0, "rewrite": 0}
        self.conflicts, self.notes = [], []
        self.renamed_files = 0

    # -------------------------------------------------- paths
    def rel(self, p):
        return os.path.relpath(p, self.root).replace(os.sep, "/")

    def abs(self, rel):
        return os.path.join(self.root, *rel.split("/"))

    # -------------------------------------------------- the journal
    def open_journal(self):
        """The run's journal, locked for this run, and what earlier runs of it left unfinished."""
        os.makedirs(self.run_dir, exist_ok=True)
        path = os.path.join(self.run_dir, "journal.jsonl")
        self.journal = open(path, "a+", encoding="utf-8")
        try:
            fcntl.lockf(self.journal, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            sys.exit(f"another run of {self.run_name} acts on this tree: {path} is locked")
        self.journal.seek(0)
        held = self.journal.read()
        if held and not held.endswith("\n"):
            self.journal.write("\n")  # a line a crash cut short ends here, so the next is a line of its own
        plans, finished = {}, set()
        for line in held.splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue  # a line a crash cut short is the last; its step is found again from the files
            self.seq = max(self.seq, entry.get("seq") or 0)
            if entry.get("op") == "plan":
                plans[entry["item"]] = entry["steps"]
                finished.discard(entry["item"])
            elif entry.get("op") == "done":
                finished.add(entry["item"])
        return {item: steps for item, steps in plans.items() if item not in finished}

    def log(self, item, entry):
        self.seq += 1
        line = {"seq": self.seq, "at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "item": item, **entry}
        self.journal.write(json.dumps(line, ensure_ascii=False) + "\n")
        self.journal.flush()
        os.fsync(self.journal.fileno())

    def keep(self, folder, rel, data):
        """A copy of a rewritten file, as it was or as it is written, in the run's folder."""
        p = os.path.join(self.run_dir, folder, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        if os.path.isfile(p) and read_bytes(p) == data:
            return
        with open(p + ".tmp", "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(p + ".tmp", p)

    # -------------------------------------------------- acting
    def write(self, path, data):
        """Write to a temporary name in the same folder and rename into place, keeping the file's mode."""
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if os.path.exists(path):
            os.chmod(tmp, os.stat(path).st_mode & 0o7777)
        os.replace(tmp, path)

    def perform(self, item, steps, data=None):
        """Make each step whose effect is not there yet, in order, journaling each; a step whose file is
        neither as the plan found it nor as it leaves it stops the item as a conflict."""
        for step in steps:
            if step["op"] in ("rename", "remove"):
                src, dst = self.abs(step["from"]), (self.abs(step["to"]) if step["op"] == "rename"
                                                   else os.path.join(self.run_dir, *step["to"].split("/")))
                if os.path.lexists(dst) and not os.path.lexists(src):
                    continue  # done by a run that stopped after it
                if not os.path.lexists(src) or os.path.lexists(dst):
                    raise Conflict(f"{item}: cannot {step['op']} {step['from']}: "
                                   + ("it is not there" if not os.path.lexists(src) else f"{step['to']} is there already"))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                os.rename(src, dst)
            else:
                path = self.abs(step["path"])
                now = sha_file(path) if os.path.isfile(path) else None
                if now == step["after"]:
                    continue
                if now != step["before"]:
                    raise Conflict(f"{item}: {step['path']} changed since it was planned; not rewritten")
                after = (data or {}).get(step["path"], (None, None))[1]
                if after is None:
                    kept = os.path.join(self.run_dir, "after", *step["path"].split("/"))
                    after = read_bytes(kept) if os.path.isfile(kept) else None
                if after is None or sha_bytes(after) != step["after"]:
                    raise Conflict(f"{item}: the bytes {step['path']} is to be rewritten with are not in the run's after/")
                self.write(path, after)
            self.log(item, step)
            self.done[step["op"]] += 1

    def say(self, steps):
        if not (self.verbose or not self.apply):
            return
        for step in steps:
            if step["op"] == "rename":
                print(f"  {'would ' if not self.apply else ''}rename  {step['from']} -> {os.path.basename(step['to'])}")
            elif step["op"] == "remove":
                print(f"  {'would ' if not self.apply else ''}remove  {step['from']} (to the run's removed/)")
            else:
                print(f"  {'would ' if not self.apply else ''}rewrite {step['path']}")

    # -------------------------------------------------- one item folder
    def item(self, d):
        try:
            plan = self.plan(d)
        except Conflict as e:
            self.conflicts.append(str(e))
            return
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            self.conflicts.append(f"{self.rel(d)}: cannot be read as it is: {type(e).__name__}: {e}")
            return
        if not plan.steps:
            self.already += 1
            return
        self.say(plan.steps)
        self.renamed_files += sum(1 for s in plan.steps if s["op"] == "rename")
        if not self.apply:
            for step in plan.steps:
                self.done[step["op"]] += 1
            return
        for path, (before, after) in plan.data.items():
            self.keep("before", path, before)
            self.keep("after", path, after)
        self.log(plan.item, {"op": "plan", "steps": plan.steps})
        try:
            self.perform(plan.item, plan.steps, plan.data)
        except Conflict as e:
            self.conflicts.append(str(e))
            return
        self.log(plan.item, {"op": "done"})

    def plan(self, d):
        """Every step the item folder d needs, checked against its records before anything is planned."""
        item = self.rel(d)
        if os.path.isfile(os.path.join(d, "manifest.json")):
            raise Conflict(f"{item}: a v1 item folder; library-v2-from-v1.py converts it first")
        p = Plan(item)
        removed, retired = self.removed(d)
        sources = self.sources(d, p)
        for name in (listdir(os.path.join(d, "versions")) if os.path.isdir(os.path.join(d, "versions")) else []):
            vp = os.path.join(d, "versions", name)
            if os.path.isdir(vp) and UUID_RE.match(name):
                if name in removed:
                    self.notes.append(f"{self.rel(vp)}: removed by an event, so its folder is ignored")
                else:
                    self.version(vp, sources, p)
        for name in (listdir(os.path.join(d, "extras")) if os.path.isdir(os.path.join(d, "extras")) else []):
            xp = os.path.join(d, "extras", name)
            if os.path.isdir(xp) and UUID_RE.match(name):
                if name in retired:
                    self.notes.append(f"{self.rel(xp)}: retired by an event, so its folder is ignored")
                else:
                    self.extra(xp, p)
        return p

    def removed(self, d):
        """The versions and extras an event of the item says are gone from it."""
        out = {"version-removed": set(), "extra-removed": set()}
        base = os.path.join(d, "events")
        for name in (listdir(base) if os.path.isdir(base) else []):
            ep = os.path.join(base, name, "event.json")
            if not os.path.isfile(ep):
                if name.endswith(".json"):
                    raise Conflict(f"{self.rel(d)}: an event in the layout before 2026-10-02 (b); "
                                   f"library-v2-upgrade.py upgrades the tree first")
                continue
            try:
                ev = load_json(ep)
            except (OSError, ValueError):
                continue
            key = EVENT_KINDS_REMOVING.get(ev.get("kind")) if isinstance(ev, dict) else None
            if key and ev.get(key):
                out[ev["kind"]].add(ev[key])
        return out["version-removed"], out["extra-removed"]

    # -------------------------------------------------- sources/
    def sources(self, d, p):
        """Each source of the item, checked and planned: {sourceId: {old, new, copies, removed}} — its
        file's name as the record has it and as it will, and its copies' paths, old to new."""
        out = {}
        base = os.path.join(d, "sources")
        names = listdir(base) if os.path.isdir(base) else []
        for name in names:
            if name.endswith(".json") and os.path.isfile(os.path.join(base, name)):
                raise Conflict(f"{self.rel(os.path.join(base, name))}: a source in the layout before 2026-10-02 (b); "
                               f"library-v2-upgrade.py upgrades the tree first")
        for name in names:
            folder = os.path.join(base, name)
            if os.path.isdir(folder) and UUID_RE.match(name):
                out[name] = self.source(d, folder, name, p)
        return out

    def source(self, d, folder, sid, p):
        sp = os.path.join(folder, "source.json")
        where = self.rel(folder)
        if not os.path.isfile(sp):
            raise Conflict(f"{where}: a source folder without its source.json")
        before = read_bytes(sp)
        rec = json.loads(before)
        if not isinstance(rec, dict) or rec.get("sourceId") != sid:
            raise Conflict(f"{self.rel(sp)}: does not name its folder")
        # the folder proves its records before anything of it changes
        sums = os.path.join(folder, SUMS)
        if not os.path.isfile(sums):
            raise Conflict(f"{where}: no {SUMS}, so nothing proves its records; not rewritten")
        listed, bad = read_sums(sums)
        if bad:
            raise Conflict(f"{self.rel(sums)}: holds a line that is not one; not rewritten")
        present = {n for n in listdir(folder) if n != SUMS}
        if set(listed) != present:
            raise Conflict(f"{where}: {SUMS} does not list exactly what the folder holds; not rewritten")
        for n in sorted(present):
            if hex_of(sha_file(os.path.join(folder, n))) != listed[n]:
                raise Conflict(f"{self.rel(os.path.join(folder, n))}: does not match its checksum; not rewritten")
        named = {"source.json", SUMS} | {os.path.basename(str((sc or {}).get("file"))) for sc in rec.get("sidecars") or []
                                         if isinstance(sc, dict)} \
            | ({"ffprobe.json"} if (rec.get("probe") or {}).get("file") else set())
        stray = sorted(present - named)
        if stray:
            raise Conflict(f"{where}: holds {', '.join(stray)}, which its record does not name; not rewritten")
        file = rec.get("file") or {}
        old = file.get("name")
        if not isinstance(old, str) or not old:
            raise Conflict(f"{self.rel(sp)}: names no file")
        new = neutral_original(old, part_of(file))
        doc = copy.deepcopy(rec)
        doc["file"]["name"] = new
        doc.pop("origin", None)
        if isinstance(doc.get("container"), dict):
            doc["container"] = untitled(doc["container"])
        prefix = f"sources/{sid}/"
        copies, gone, entries, n = {}, [], [], 0
        for sc in rec.get("sidecars") or []:
            f = sc.get("file") if isinstance(sc, dict) else None
            if not isinstance(f, str) or not f.startswith(prefix) or "/" in f[len(prefix):] or f[len(prefix):] not in present:
                raise Conflict(f"{self.rel(sp)}: lists a sidecar {f!r} its folder does not hold")
            if sc.get("kind") != "subtitle":
                gone.append(f)
                continue
            n += 1
            ext = os.path.splitext(f)[1] or "." + str(sc.get("format") or "")
            name = subtitle_copy_name(n, sc.get("language"), bool(sc.get("forced")), bool(sc.get("hearingImpaired")), ext)
            entry = {k: v for k, v in sc.items() if k != "originalName"}
            entry["file"] = prefix + name
            entries.append(entry)
            copies[f] = prefix + name
        if "sidecars" in doc:
            doc["sidecars"] = entries
        probe = rec.get("probe") or {}
        pf = probe.get("file")
        probe_after = None
        if pf:
            if pf != prefix + "ffprobe.json" or "ffprobe.json" not in present:
                raise Conflict(f"{self.rel(sp)}: its probe {pf!r} is not the ffprobe.json its folder holds")
            probe_before = read_bytes(os.path.join(folder, "ffprobe.json"))
            if probe.get("sha256") and sha_bytes(probe_before) != probe["sha256"]:
                raise Conflict(f"{self.rel(os.path.join(folder, 'ffprobe.json'))}: not the probe its source record hashed")
            raw = json.loads(probe_before)
            scrubbed = scrub_probe(raw, new)
            probe_after = json_bytes(scrubbed) if scrubbed != raw else probe_before
            if probe_after != probe_before:
                doc["probe"]["sha256"] = sha_bytes(probe_after)
        after = json_bytes(doc) if doc != rec else before
        # the steps: the copies first, each renamed once, the other copies out of the record, the records, and
        # the checksums last. A copy's new name is no file of the folder: names given before were never of
        # that form, so one that is was written by someone else
        moving = {a: b for a, b in copies.items() if a != b}
        if any(os.path.basename(b) in present for b in moving.values()):
            raise Conflict(f"{where}: a copy's new name is a file the folder holds already; not rewritten")
        for a in sorted(moving):
            p.rename(f"{self.rel(folder)}/{os.path.basename(a)}", f"{self.rel(folder)}/{os.path.basename(moving[a])}")
        for g in gone:
            p.remove(f"{self.rel(folder)}/{os.path.basename(g)}", f"removed/{self.rel(folder)}/{os.path.basename(g)}")
        if pf and probe_after != probe_before:
            p.rewrite(f"{self.rel(folder)}/ffprobe.json", probe_before, probe_after)
        if after != before:
            p.rewrite(self.rel(sp), before, after)
        kept = {os.path.basename(moving.get(prefix + n, prefix + n)): listed[n] for n in present
                if prefix + n not in gone and n not in ("source.json", "ffprobe.json")}
        kept["source.json"] = hex_of(sha_bytes(after))
        if pf:
            kept["ffprobe.json"] = hex_of(sha_bytes(probe_after))
        sums_before = read_bytes(sums)
        sums_after = "".join(f"{kept[k]}  {k}\n" for k in sorted(kept)).encode("utf-8")
        if sums_after != sums_before:
            p.rewrite(self.rel(sums), sums_before, sums_after)
        return {"old": old, "new": new, "copies": copies, "removed": set(gone), "record": rec}

    # -------------------------------------------------- versions/
    def chain(self, folder, record, chained):
        """The records of a finished folder's chain as they are — .complete names package.json, which
        names checksums.sha256, which lists the record as it is — or a conflict. Returns (package.json
        bytes, the checksums' entries), or None for a folder with no package."""
        mark, pp, sp = (os.path.join(folder, n) for n in (".complete", "package.json", SUMS))
        if not os.path.isfile(mark) and not os.path.isfile(pp):
            return None
        where = self.rel(folder)
        if not (os.path.isfile(mark) and os.path.isfile(pp) and os.path.isfile(sp)):
            raise Conflict(f"{where}: a finished package is package.json, {SUMS} and .complete together, and one "
                           f"is missing; not rewritten")
        package = read_bytes(pp)
        if read_bytes(mark).decode("utf-8", "replace").strip() != sha_bytes(package):
            raise Conflict(f"{where}: .complete does not name its package.json; not rewritten")
        if (json.loads(package).get("checksums") or {}).get("sha256") != sha_file(sp):
            raise Conflict(f"{where}: {SUMS} is not the one package.json names; not rewritten")
        listed, bad = read_sums(sp)
        if bad or listed.get(record) != hex_of(sha_bytes(chained)):
            raise Conflict(f"{where}: {record} does not match the digest {SUMS} lists for it; not rewritten")
        return package, listed

    def close(self, folder, record, before, after, listed, package_before, package_doc, p, renamed=()):
        """The chain over a record that changed, written again from the bottom up: the checksums with
        the record's new digest and every renamed file's line under its new name, package.json with the
        checksums' hash and the size they list, and .complete."""
        entries = {dict(renamed).get(k, k): v for k, v in listed.items()}
        entries[record] = hex_of(sha_bytes(after))
        sums_before = read_bytes(os.path.join(folder, SUMS))
        sums_after = checksums([(k, v, 0) for k, v in entries.items()])[0]
        if sums_after != sums_before:
            p.rewrite(self.rel(os.path.join(folder, SUMS)), sums_before, sums_after)
            package_doc["checksums"] = dict(package_doc["checksums"], sha256=sha_bytes(sums_after),
                                            bytes=package_doc["checksums"]["bytes"] - len(before) + len(after))
        package_after = json_bytes(package_doc) if package_doc != json.loads(package_before) else package_before
        if package_after != package_before:
            p.rewrite(self.rel(os.path.join(folder, "package.json")), package_before, package_after)
            p.rewrite(self.rel(os.path.join(folder, ".complete")), read_bytes(os.path.join(folder, ".complete")),
                      complete(package_after))

    def version(self, vp, sources, p):
        vj = os.path.join(vp, "version.json")
        if not os.path.isfile(vj):
            return  # nothing a record names: an unfinished folder, the sweep's
        before = read_bytes(vj)
        v = json.loads(before)
        chained = self.chain(vp, "version.json", before)
        mine = [sources[s] for s in v.get("sourceIds") or [] if s in sources]
        doc = copy.deepcopy(v)
        names, renames = [], []
        for name in v.get("originalFiles") or []:
            src = next((s for s in mine if name in (s["old"], s["new"])), None)
            new = src["new"] if src else name if ORIGINAL_NAME_RE.match(name) else None
            if new is None:
                raise Conflict(f"{self.rel(vj)}: names the original {name!r}, which no source of the version names")
            if new in names:
                raise Conflict(f"{self.rel(vj)}: two of its originals would be named {new}: their source records "
                               f"number no parts; not rewritten")
            names.append(new)
            f = os.path.join(vp, name)
            if new != name and os.path.isfile(f):
                size = ((src or {}).get("record") or {}).get("file", {}).get("sizeBytes")
                if size is not None and os.path.getsize(f) != size:
                    raise Conflict(f"{self.rel(f)}: is not the size its source record says; not renamed")
                if os.path.lexists(os.path.join(vp, new)):
                    raise Conflict(f"{self.rel(f)}: its new name {new} is taken in the folder; not renamed")
                renames.append((name, new))
        doc["originalFiles"] = names
        named = {x for s in mine for x in (s["old"],) + tuple(self.folder_names(s["record"]))}
        edition = doc.get("edition") or {}
        claims = [(s["record"].get("labels") or {}) for s in mine]
        for e in edition.get("evidence") or []:
            if isinstance(e, dict) and isinstance(e.get("value"), str) and e["value"] in named:
                folder = next((c.get("folderEdition") for c in claims if c.get("folderEdition")), None)
                e["value"] = folder if e.get("signal") == "folder-name" and folder else edition.get("kind")
        after = json_bytes(doc) if doc != v else before
        for name, new in renames:
            p.rename(self.rel(os.path.join(vp, name)), self.rel(os.path.join(vp, new)))
        if after != before:
            p.rewrite(self.rel(vj), before, after)
        if chained is None:
            return
        package_before, listed = chained
        package = json.loads(package_before)
        copies = {a: b for s in mine for a, b in s["copies"].items()}
        gone = {g for s in mine for g in s["removed"]}
        for sub in package.get("subtitles") or []:
            f = sub.get("fromSidecar")
            if f in gone:
                raise Conflict(f"{self.rel(vp)}: a subtitle was made from {f}, which is no subtitle copy")
            if f in copies:
                sub["fromSidecar"] = copies[f]
        self.close(vp, "version.json", before, after, listed, package_before, package, p)

    def folder_names(self, rec):
        """The names of where a source record says its original came from, as an evidence value held them."""
        origin = rec.get("origin") or {}
        out = [x for x in (origin.get("libraryPath"), origin.get("folder")) if isinstance(x, str) and x]
        return out + [os.path.basename(x) for x in out]

    # -------------------------------------------------- extras/
    def extra(self, xp, p):
        xj = os.path.join(xp, "extra.json")
        if not os.path.isfile(xj):
            return
        before = read_bytes(xj)
        x = json.loads(before)
        where = self.rel(xp)
        chained = self.chain(xp, "extra.json", before)
        sums = os.path.join(xp, SUMS)
        if chained is None and os.path.isfile(sums):
            listed, bad = read_sums(sums)
            if bad or listed.get("extra.json") != hex_of(sha_bytes(before)):
                raise Conflict(f"{where}: extra.json does not match the digest {SUMS} lists for it; not rewritten")
        elif chained is None:
            return  # an extra its writer never finished: not the record yet
        else:
            listed = chained[1]
        doc = copy.deepcopy(x)
        olds = [n for n in x.get("originalFiles") or [] if isinstance(n, str)]
        news = [neutral_original(n, i + 1 if len(olds) > 1 else None) for i, n in enumerate(olds)]
        if len(set(news)) != len(news):
            raise Conflict(f"{where}: two of its originals would have one name; not rewritten")
        sizes = {o.get("name"): o.get("sizeBytes") for o in x.get("originals") or [] if isinstance(o, dict)}
        renames = []
        for old, new in zip(olds, news):
            f = os.path.join(xp, old)
            if old == new:
                continue
            if not os.path.isfile(f) or (sizes.get(old) is not None and os.path.getsize(f) != sizes[old]):
                raise Conflict(f"{self.rel(f)}: missing, or not the size extra.json records; not renamed")
            if os.path.lexists(os.path.join(xp, new)):
                raise Conflict(f"{self.rel(f)}: its new name {new} is taken in the folder; not renamed")
            renames.append((old, new))
        doc["originalFiles"] = news
        doc["originals"] = [dict(o, name=dict(zip(olds, news)).get(o.get("name"), o.get("name")))
                            for o in x.get("originals") or []]
        made = x.get("packagedFrom")
        if made:
            doc["packagedFrom"] = [dict(o, name=neutral_original(o.get("name"), i + 1 if len(made) > 1 else None))
                                   for i, o in enumerate(made)]
        if isinstance(doc.get("container"), dict):
            doc["container"] = untitled(doc["container"])
        after = json_bytes(doc) if doc != x else before
        for old, new in renames:
            p.rename(self.rel(os.path.join(xp, old)), self.rel(os.path.join(xp, new)))
        if after != before:
            p.rewrite(self.rel(xj), before, after)
        if chained is not None:
            self.close(xp, "extra.json", before, after, listed, chained[0], json.loads(chained[0]), p, renames)
        elif after != before or renames:
            entries = {dict(renames).get(k, k): v for k, v in listed.items()}
            entries["extra.json"] = hex_of(sha_bytes(after))
            sums_after = checksums([(k, v, 0) for k, v in entries.items()])[0]
            if sums_after != read_bytes(sums):
                p.rewrite(self.rel(sums), read_bytes(sums), sums_after)

    # -------------------------------------------------- the tree
    def run(self):
        if self.apply:
            for item, steps in sorted(self.open_journal().items()):
                try:
                    self.perform(item, steps)
                    self.log(item, {"op": "done"})
                    self.notes.append(f"{item}: an interrupted run of {self.run_name} is finished")
                except Conflict as e:
                    self.conflicts.append(str(e))
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
    ap = argparse.ArgumentParser(prog="library-v2-neutral-names.py",
                                 description="Give a v2 library tree written before 2026-10-08 the names the "
                                             "library gives its files.")
    ap.add_argument("root", help="the library root holding movies/ and series/")
    ap.add_argument("--apply", action="store_true", help="make the changes; without it, say what they would be")
    ap.add_argument("--run", default=DEFAULT_RUN, help="the run whose journal, under <root>/.work/migration/<run>/, "
                                                       "records the changes (default: neutral-names)")
    ap.add_argument("--verbose", action="store_true", help="with --apply, list every change too")
    args = ap.parse_args()
    if not RUN_NAME.fullmatch(args.run):
        ap.error(f"--run {args.run!r} is not a folder name: letters, digits, '.', '_' and '-'")
    n = Neutral(os.path.abspath(args.root), args.run, args.apply, args.verbose)
    found = n.run()
    print(f"{'gave' if args.apply else 'would give'} {n.root} its neutral names: {found} item folder(s), "
          f"{n.already} named so already")
    for op, label in (("rename", "files renamed"), ("remove", "copies taken out of the record"),
                      ("rewrite", "records rewritten")):
        if n.done[op]:
            print(f"  {label}: {n.done[op]}")
    if args.apply and any(n.done.values()):
        print(f"  journal: {os.path.relpath(os.path.join(n.run_dir, 'journal.jsonl'), n.root)}")
    if n.renamed_files:
        print("  note: where the catalog names a renamed file by its old path — a subtitle row pointing at a copy, "
              "an original's playback row — its owner points it at the new one; the journal lists every rename")
    for note in n.notes:
        print("  note " + note)
    if n.conflicts:
        print(f"conflicts, left exactly as they were: {len(n.conflicts)}")
        for c in n.conflicts:
            print("  " + c)
        return 1
    if not found:
        print("  no item folders under movies/ or series/")
        return 1
    if not any(n.done.values()):
        print("  nothing to do: every file is named as the library names it")
    elif not args.apply:
        print("  run again with --apply to make these changes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
