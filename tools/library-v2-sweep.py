#!/usr/bin/env python3
"""Find what a v2 library tree holds that is provably garbage, and with --apply remove it.

Usage:
  library-v2-sweep.py LIBRARY [--export CATALOG.json] [--grace 24h] [--apply] [--verbose]

Deleting an item deletes its folder, a writer that dies leaves a version folder unfinished, and a
projection that drops an image leaves the file behind — and nothing but this collects what a writer
missed. It finds seven kinds of garbage, each proved by the records and the database, never guessed:

  deleted item       an item folder whose id the export's deletion log names (deletedItems), that the
                     database does not hold again, and in which no record states a moment after that
                     deletion — item.json's createdAt and migratedAt, metadata.json's asOf, its
                     databaseUpdatedAt and its TMDB fetchedAt, every source's takenAt, version's and
                     package's createdAt, event's at and extra's createdAt, and for a series its
                     episodes' as well; a record that states none counts with its file's time. A
                     series goes with its episodes, so one the database still holds keeps it. An
                     entry of the log is an item's when its type is an item's, or when it has none.
  deleted person     a person folder whose id the deletion log names as a person (type person) — the
                     catalog deletes a person no title credits any more — whom the database does not
                     hold again (not in its people list, not credited by any of its items), in which
                     nothing is newer than that deletion — person.json's asOf, its databaseUpdatedAt
                     and TMDB fetchedAt, each image's fetchedAt, and the time of a file that states
                     none — and whom no item record on storage still credits: a credit keeps the
                     folder until that item is projected again, unless the same sweep removes the
                     item's folder as a deleted item. An untyped entry never names a person.
  unfinished version a version folder without .complete. The whole folder when it has no version.json
                     — nothing can have known a version that never wrote its record — or when it keeps
                     no original and nothing names it: not the item's metadata.json, not an event other
                     than version-removed, not the export (this needs --export). When it keeps an
                     original, only the unfinished package beside it: hls/ subs/ trickplay/ trailers/,
                     package.json, checksums.sha256 and their temporary files.
  unfinished extra   an extras/<extraId>/ folder without .complete that holds a package — hls/ subs/
                     trickplay/ or package.json — the same way: the whole folder when it has no
                     extra.json, or keeps no original and nothing names it (not the item's
                     metadata.json under library.extras, not the export); when it keeps an original,
                     only the unfinished package beside it, its checksums with it. An extra that holds
                     no package is never garbage: finished by its checksums, or its writer's to finish.
  removed version    the folder of a version a version-removed event retired, still on storage — the
                     whole folder, whatever it holds — once the removal is older than the grace and
                     nothing names the version: not the item's metadata.json, not the export. The
                     catalog deletes such a folder itself, after the grace a superseded version keeps;
                     this collects one that delete missed. The events that name it — the supersession
                     and the deletion of its original — are its history, and keep nothing.
  removed extra      the folder of an extra an extra-removed event retired, still on storage — the
                     whole folder, whatever it holds — once the removal is older than the grace and
                     nothing names the extra: not the item's library.extras, not another event, not the
                     export. This is how the old folder goes when an extra kept only as its original is
                     packaged later, into a new one.
  dropped image      an image in an item's metadata/ or beside a person's person.json that the
                     projection does not list, named by the hash of its own bytes.

Each is older than the grace period (24h by default, --grace 90m, 7d, 3600s or 0) — a write in flight
looks just like garbage: a record is written before its database row, and an image before the
projection that lists it — and so is, for a deleted item or person, the deletion, and for a dropped
image, the projection that dropped it. It never touches anything referenced, anything younger than
the grace, anything it cannot classify, or a person's folder the deletion log does not name as a
person's. Everything it leaves alone that looked like garbage is listed with the reason.

A dry run, the default, lists what it would remove and why. --apply first renames every target into
a quarantine, <root>/.work/quarantine/<YYYYMMDDTHHMMSSZ>/ (--quarantine names another folder on the
same share) — a rename on the same filesystem, never a copy; a target that cannot be renamed is left
where it is — with a sweep.json beside them that says where each came from. It then reads the
export, the projections and the events again and checks that nothing references a quarantined target
and nothing was written where it was — a deleted person last, once every other target is settled,
against the credits on storage then; one that is referenced again is renamed back. Only then is the
quarantine deleted. A quarantine an interrupted run left behind is checked and finished the same way
by the next --apply: there, or in _swept/ at the library root, where a sweep from before 2026-10-06
kept it.

It is plain standard-library Python 3.11, so it runs in the pod that mounts the share:

  oc exec -i deploy/packager -- python3 - /var/lib/katalog --export /tmp/catalog.json < library-v2-sweep.py

It exits non-zero only when --apply could not finish: a target it could neither remove nor put back.
"""
import argparse, datetime, glob, hashlib, json, os, re, shutil, sys

OS_ARTEFACTS = re.compile(r"^(\.DS_Store|\._.*|Thumbs\.db|desktop\.ini|@eaDir|\.@__thumb|#recycle|\.AppleDouble)$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ANY_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
IMAGE_NAME = re.compile(r"^([0-9a-f]{64})\.(jpg|png|webp)$")
PACKAGE_PARTS = ("hls", "subs", "trickplay", "trailers", "package.json", "checksums.sha256")
RECORDS_TMP = ("version.json.tmp", "package.json.tmp", "checksums.sha256.tmp", ".complete.tmp")
# What makes an extra a packaged one; beside them an unfinished package also leaves its checksums,
# which list files that go with it.
EXTRA_PARTS = ("hls", "subs", "trickplay", "package.json")
EXTRA_TMP = ("extra.json.tmp", "package.json.tmp", "checksums.sha256.tmp", ".complete.tmp")
# Where --apply quarantines what it removes, under the library root: the work tree beside the record.
QUARANTINE = os.path.join(".work", "quarantine")
# Where a sweep from before 2026-10-06 kept its quarantine, which the next --apply finishes.
OLD_QUARANTINE = "_swept"
RECORD_MOMENTS = (
    ("item.json", ("createdAt", "provenance.migratedAt")),
    # databaseUpdatedAt is on the database's clock, the one the deletion log's deletedAt is on
    ("metadata.json", ("asOf", "databaseUpdatedAt", "sources.tmdb.fetchedAt")),
    ("sources/*.json", ("takenAt", "probe.at")),
    ("sources/*/source.json", ("takenAt", "probe.at")),
    ("versions/*/version.json", ("createdAt",)),
    ("versions/*/package.json", ("createdAt",)),
    ("events/*.json", ("at",)),
    ("events/*/event.json", ("at",)),
    ("extras/*/extra.json", ("createdAt",)),
    ("extras/*/package.json", ("createdAt",)),
)


# ---------------------------------------------------------------- small helpers
def listdir(d):
    return sorted(n for n in os.listdir(d) if not OS_ARTEFACTS.match(n))


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def sha_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def instant(v):
    try:
        t = datetime.datetime.fromisoformat(str(v).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else None


def utc(t):
    return t.astimezone(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def mtime(p):
    return datetime.datetime.fromtimestamp(os.lstat(p).st_mtime, datetime.timezone.utc)


def newest_file(p):
    """The newest modification time of p and everything under it: a folder is as young as anything
    written into it."""
    newest = mtime(p)
    if os.path.isdir(p) and not os.path.islink(p):
        for base, dirs, files in os.walk(p):
            for n in dirs + files:
                t = mtime(os.path.join(base, n))
                newest = t if t > newest else newest
    return newest


def size_of(p):
    if os.path.isfile(p) or os.path.islink(p):
        return os.lstat(p).st_size
    return sum(os.lstat(os.path.join(b, f)).st_size for b, _, fs in os.walk(p) for f in fs)


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def grace_seconds(text):
    m = re.fullmatch(r"(\d+)([smhd]?)", text.strip())
    if not m:
        raise argparse.ArgumentTypeError(f"{text!r} is not a grace period: 24h, 90m, 7d, 3600s or 0")
    return int(m.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]


def newest_record(d):
    """The newest moment any record in the item folder d states; a record that states none, or that
    cannot be read, counts with its file's modification time."""
    newest = None
    for pattern, fields in RECORD_MOMENTS:
        for p in glob.glob(os.path.join(glob.escape(d), *pattern.split("/"))):
            moments = []
            try:
                doc = load(p)
                for field in fields:
                    v = doc
                    for part in field.split("."):
                        v = v.get(part) if isinstance(v, dict) else None
                    if instant(v):
                        moments.append(instant(v))
            except (OSError, ValueError, AttributeError):
                pass
            for t in moments or [mtime(p)]:
                newest = t if newest is None or t > newest else newest
    return newest


PERSON_MOMENTS = ("asOf", "databaseUpdatedAt", "sources.tmdb.fetchedAt")
ITEM_TYPES = ("movie", "series", "episode")


def newest_person(d):
    """The newest moment a person folder states: person.json's asOf, its databaseUpdatedAt and TMDB
    fetchedAt, and for each file beside it the fetchedAt person.json records for that image. A
    person.json that states none or cannot be read, and a file it records none for, count with their
    modification time."""
    moments, fetched = [], {}
    p = os.path.join(d, "person.json")
    try:
        doc = load(p)
        for field in PERSON_MOMENTS:
            v = doc
            for part in field.split("."):
                v = v.get(part) if isinstance(v, dict) else None
            if instant(v):
                moments.append(instant(v))
        for img in doc.get("images") or []:
            origin = img.get("origin") if isinstance(img.get("origin"), dict) else {}
            t = instant(img.get("fetchedAt")) or instant(origin.get("fetchedAt"))
            if t and isinstance(img.get("file"), str):
                fetched[img["file"]] = t
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    if not moments and os.path.lexists(p):
        moments.append(mtime(p))
    for name in listdir(d):
        if name != "person.json":
            moments.append(fetched.get(name) or newest_file(os.path.join(d, name)))
    return max(moments) if moments else None


def logged_as(entry):
    """What a deletion log entry deleted: 'item' — an entry of an item's type, or of none, as every
    entry of an export from before people were logged is — 'person', or None for a type the format
    does not know, which proves nothing."""
    kind = entry.get("type")
    return "item" if not kind or kind in ITEM_TYPES else "person" if kind == "person" else None


# ---------------------------------------------------------------- what the database and the records say
class References:
    """Everything that can keep a folder or a file alive, read fresh each time it is asked for — the
    export from its file, the projections and the events from the tree — so the check after the
    quarantine sees what is true then, not what was true at the start."""

    def __init__(self, root, export_path):
        self.root = root
        self.export_path = export_path
        self.reload()

    def reload(self):
        self.export = None
        self.live, self.log, self.mentioned = set(), None, set()
        self.people_log, self.live_people, self.untyped = None, set(), False
        self.credits, self.unreadable = {}, []
        self.parent_of = {}
        if not self.export_path:
            return
        with open(self.export_path, encoding="utf-8") as f:
            text = f.read()
        self.export = json.loads(text)
        for row in self.export.get("items") or []:
            if isinstance(row, dict) and row.get("id"):
                self.live.add(str(row["id"]))
                if row.get("parentId"):
                    self.parent_of[str(row["id"])] = str(row["parentId"])
                self.live_people |= {str(c["personId"]) for c in row.get("people") or []
                                     if isinstance(c, dict) and c.get("personId")}
        self.live_people |= {str(p["id"]) for p in self.export.get("people") or [] if isinstance(p, dict) and p.get("id")}
        if self.export.get("deletedItems") is not None:
            entries = [e for e in self.export["deletedItems"] if isinstance(e, dict) and e.get("id")]
            self.log = {str(e["id"]): e for e in entries if logged_as(e) == "item"}
            self.people_log = {str(e["id"]): e for e in entries if logged_as(e) == "person"}
            self.untyped = bool(entries) and not any(e.get("type") for e in entries)
        self.mentioned = set(ANY_UUID.findall(text))
        if self.people_log:
            self.credits, self.unreadable = self.credited_on_storage()

    def credited_on_storage(self):
        """Whom the item records on storage credit: {personId: [the item folders, relative to the
        root]}, and the projections that cannot be read, which may credit anyone."""
        credits, unreadable = {}, []
        for category in ("movies", "series"):
            base = os.path.join(self.root, category)
            for shard in (listdir(base) if os.path.isdir(base) else []):
                sd = os.path.join(base, shard)
                for iid in (listdir(sd) if os.path.isdir(sd) else []):
                    d = os.path.join(sd, iid)
                    ed = os.path.join(d, "episodes")
                    for f in [d] + [os.path.join(ed, e) for e in (listdir(ed) if os.path.isdir(ed) else [])]:
                        p = os.path.join(f, "metadata.json")
                        if not os.path.lexists(p):
                            continue
                        try:
                            meta = load(p)
                            names = {str(c["personId"]) for c in meta.get("credits") or [] if c.get("personId")}
                        except (OSError, ValueError, AttributeError, TypeError, KeyError):
                            unreadable.append(os.path.relpath(p, self.root))
                            continue
                        for pid in names:
                            credits.setdefault(pid, []).append(os.path.relpath(f, self.root))
        return credits, unreadable

    def version_named(self, item_dir, vid):
        """What names a version and so keeps its folder: the item's projection, an event other than the
        one that removed it, the database."""
        try:
            lib = load(os.path.join(item_dir, "metadata.json")).get("library") or {}
            if lib.get("primaryVersionId") == vid or vid in (lib.get("versionLabels") or {}):
                return "metadata.json names it"
        except (OSError, ValueError, AttributeError):
            if os.path.exists(os.path.join(item_dir, "metadata.json")):
                return "metadata.json cannot be read, so it may name it"
        base = os.path.join(item_dir, "events")
        for name in (listdir(base) if os.path.isdir(base) else []):
            p = os.path.join(base, name, "event.json") if os.path.isdir(os.path.join(base, name)) else os.path.join(base, name)
            try:
                ev = load(p)
            except (OSError, ValueError):
                return f"event {name} cannot be read, so it may name it"
            if ev.get("kind") != "version-removed" and vid in (ev.get("versionId"), (ev.get("supersededBy") or {}).get("versionId")):
                return f"event {name} names it"
        if vid in self.mentioned:
            return "the export names it"
        return None

    def extra_named(self, item_dir, xid):
        """What names an extra and so keeps its folder: the item's projection, an event other than the
        one that removed it, the database."""
        try:
            lib = load(os.path.join(item_dir, "metadata.json")).get("library") or {}
            if xid in (lib.get("extras") or {}):
                return "metadata.json names it"
        except (OSError, ValueError, AttributeError):
            if os.path.exists(os.path.join(item_dir, "metadata.json")):
                return "metadata.json cannot be read, so it may name it"
        base = os.path.join(item_dir, "events")
        for name in (listdir(base) if os.path.isdir(base) else []):
            p = os.path.join(base, name, "event.json") if os.path.isdir(os.path.join(base, name)) else os.path.join(base, name)
            try:
                ev = load(p)
            except (OSError, ValueError):
                return f"event {name} cannot be read, so it may name it"
            if ev.get("kind") != "extra-removed" and ev.get("extraId") == xid:
                return f"event {name} names it"
        if xid in self.mentioned:
            return "the export names it"
        return None

    def removed_versions(self, item_dir):
        """The versions a version-removed event of the item retired: {versionId: that event}. An event
        that cannot be read retires nothing."""
        out, base = {}, os.path.join(item_dir, "events")
        for name in (listdir(base) if os.path.isdir(base) else []):
            try:
                ev = load(os.path.join(base, name, "event.json"))
            except (OSError, ValueError):
                continue
            if isinstance(ev, dict) and ev.get("kind") == "version-removed" and ev.get("versionId"):
                out[str(ev["versionId"])] = ev
        return out

    def removed_version_named(self, item_dir, vid):
        """What still names a removed version and so keeps its folder: the item's projection, the
        database. The events that name it are its history — its supersession, the deletion of its
        original — and keep nothing."""
        try:
            lib = load(os.path.join(item_dir, "metadata.json")).get("library") or {}
            if lib.get("primaryVersionId") == vid or vid in (lib.get("versionLabels") or {}):
                return "metadata.json names it"
        except (OSError, ValueError, AttributeError):
            if os.path.exists(os.path.join(item_dir, "metadata.json")):
                return "metadata.json cannot be read, so it may name it"
        if vid in self.mentioned:
            return "the export names it"
        return None

    def retired(self, item_dir):
        """The extras an extra-removed event of the item retired: {extraId: that event}. An event that
        cannot be read retires nothing — it proves nothing."""
        out, base = {}, os.path.join(item_dir, "events")
        for name in (listdir(base) if os.path.isdir(base) else []):
            try:
                ev = load(os.path.join(base, name, "event.json"))
            except (OSError, ValueError):
                continue
            if isinstance(ev, dict) and ev.get("kind") == "extra-removed" and ev.get("extraId"):
                out[str(ev["extraId"])] = ev
        return out

    def listed(self, projection):
        """The image files a projection lists, or None when it cannot be read."""
        try:
            doc = load(projection)
            return {i["file"] for i in doc.get("images") or []}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None


# ---------------------------------------------------------------- finding garbage
class Sweep:
    def __init__(self, root, refs, grace, now, verbose=False):
        self.root = root
        self.refs = refs
        self.grace = grace
        self.cutoff = now - datetime.timedelta(seconds=grace)
        self.verbose = verbose
        self.targets = []   # {"path", "class", "reason", "bytes"}
        self.left = []      # (path, reason)
        self.notes = []

    def rel(self, p):
        return os.path.relpath(p, self.root)

    def target(self, p, kind, reason):
        self.targets.append({"path": self.rel(p), "class": kind, "reason": reason, "bytes": size_of(p)})

    def leave(self, p, reason):
        self.left.append((self.rel(p), reason))

    def young(self, p):
        """When p, or anything under it, was written within the grace: then, else None."""
        t = newest_file(p)
        return utc(t) if t > self.cutoff else None

    # -------------------------------------------------- the tree
    def run(self):
        if self.refs.export is None:
            self.notes.append("no --export: no item folder can be proved deleted, and a version that wrote its "
                              "record is swept only when the database's word on it is known")
        elif self.refs.log is None:
            self.notes.append("the export carries no deletion log (deletedItems is "
                              + ("null" if "deletedItems" in self.refs.export else "absent")
                              + "): no item folder can be proved deleted")
        elif self.refs.untyped:
            self.notes.append("the deletion log's entries carry no type, as before people were logged: every one is "
                              "an item's, so no person's folder can be proved deleted")
        for category in ("movies", "series"):
            base = os.path.join(self.root, category)
            for shard in (listdir(base) if os.path.isdir(base) else []):
                sd = os.path.join(base, shard)
                for iid in (listdir(sd) if os.path.isdir(sd) else []):
                    d = os.path.join(sd, iid)
                    if os.path.isdir(d) and not self.deleted_item(d, iid, category == "series"):
                        self.inside(d)
                        ed = os.path.join(d, "episodes")
                        for eid in (listdir(ed) if os.path.isdir(ed) else []):
                            e = os.path.join(ed, eid)
                            if os.path.isdir(e) and not self.deleted_item(e, eid, False):
                                self.inside(e, episode=True)
        # people last: a credit in an item folder this sweep removes as a deleted item keeps nobody
        base = os.path.join(self.root, "people")
        for shard in (listdir(base) if os.path.isdir(base) else []):
            sd = os.path.join(base, shard)
            for pid in (listdir(sd) if os.path.isdir(sd) else []):
                p = os.path.join(sd, pid)
                if os.path.isdir(p) and not self.deleted_person(p, pid):
                    self.images(p, os.path.join(p, "person.json"), keep={"person.json"})

    def inside(self, d, episode=False):
        """What an item folder that stays may still hold that is garbage."""
        base = os.path.join(d, "versions")
        removed = self.refs.removed_versions(d) if os.path.isdir(base) else {}
        for name in (listdir(base) if os.path.isdir(base) else []):
            vp = os.path.join(base, name)
            if name in removed and os.path.lexists(vp):
                self.removed_version(d, vp, name, removed[name])
            elif os.path.isdir(vp) and not os.path.isfile(os.path.join(vp, ".complete")):
                self.unfinished(d, vp, name)
        base = os.path.join(d, "extras")
        if os.path.isdir(base) and episode:
            self.leave(base, "an extras/ folder under an episode, which has none: the sweep cannot classify it")
        elif os.path.isdir(base):
            retired = self.refs.retired(d)
            for name in listdir(base):
                xp = os.path.join(base, name)
                if name in retired and os.path.lexists(xp):
                    self.removed_extra(d, xp, name, retired[name])
                elif os.path.isdir(xp) and not os.path.isfile(os.path.join(xp, ".complete")):
                    self.unfinished_extra(d, xp, name)
        if os.path.isdir(os.path.join(d, "metadata")):
            self.images(os.path.join(d, "metadata"), os.path.join(d, "metadata.json"))

    # -------------------------------------------------- (a) a folder its item outlived
    def deleted_item(self, d, iid, is_series):
        """Returns True when the folder is a target, so nothing inside it needs looking at."""
        log, refs = self.refs.log, self.refs
        if log is None or iid not in log:
            return False
        entry = log[iid]
        if iid in refs.live:
            self.leave(d, f"in the deletion log, but the database holds it again: the item that exists wins")
            return False
        try:
            item = load(os.path.join(d, "item.json"))
            if item.get("itemId") != iid:
                raise ValueError("item.json names another item")
        except (OSError, ValueError, AttributeError) as e:
            self.leave(d, f"in the deletion log, but its item.json cannot be read ({e}), so it is not proved to be that item")
            return False
        deleted = instant(entry.get("deletedAt"))
        if deleted is None:
            self.leave(d, "in the deletion log, but the entry has no deletedAt to prove it")
            return False
        folders = [d] + ([os.path.join(d, "episodes", e) for e in listdir(os.path.join(d, "episodes"))]
                         if is_series and os.path.isdir(os.path.join(d, "episodes")) else [])
        alive = sorted(e for e in (os.path.basename(f) for f in folders[1:]) if e in refs.live)
        if alive:
            self.leave(d, f"a deleted series, but the database still holds its episode {alive[0]}")
            return False
        newest = max((t for t in (newest_record(f) for f in folders) if t), default=None)
        if newest and newest > deleted:
            self.leave(d, f"deleted {entry['deletedAt']}, but it holds a record of {utc(newest)}: created again since, "
                          f"so it is lost, not an orphan")
            return False
        if deleted > self.cutoff:
            self.leave(d, f"deleted {entry['deletedAt']}, within the grace period")
            return False
        young = self.young(d)
        if young:
            self.leave(d, f"deleted, but written to within the grace period ({young})")
            return False
        self.target(d, "deleted item", f"deleted {entry['deletedAt']} by {entry.get('deletedBy') or 'unknown'}, "
                                       f"and nothing in it is newer")
        return True

    # -------------------------------------------------- (a') a person the catalog deleted
    def deleted_person(self, d, pid):
        """A person folder the catalog deleted the person of — it deletes a person no title credits any
        more, and logs them as a person. Returns True when the folder is a target."""
        refs = self.refs
        if refs.log and pid in refs.log:
            self.leave(d, "the deletion log names this id as an item's, not a person's: an entry without a type "
                          "is an item's, so the person is not proved deleted")
        if refs.people_log is None or pid not in refs.people_log:
            return False
        entry = refs.people_log[pid]
        if pid in refs.live_people:
            self.leave(d, "a deleted person, but the database holds them again: the person who exists wins")
            return False
        try:
            doc = load(os.path.join(d, "person.json"))
            if doc.get("personId") != pid:
                raise ValueError("person.json names another person")
        except (OSError, ValueError, AttributeError) as e:
            self.leave(d, f"a deleted person, but their person.json cannot be read ({e}), so the folder is not "
                          f"proved to be theirs")
            return False
        deleted = instant(entry.get("deletedAt"))
        if deleted is None:
            self.leave(d, "in the deletion log as a person, but the entry has no deletedAt to prove it")
            return False
        newest = newest_person(d)
        if newest and newest > deleted:
            self.leave(d, f"deleted {entry['deletedAt']}, but their folder holds a record of {utc(newest)}: created "
                          f"again since, so they are lost, not an orphan")
            return False
        gone = [t["path"] for t in self.targets if t["class"] == "deleted item"]
        holders = [c for c in refs.credits.get(pid, []) if not any(c == g or c.startswith(g + os.sep) for g in gone)]
        if holders:
            self.leave(d, f"a deleted person, but {holders[0]}/metadata.json still credits them: projected again "
                          f"(library-v2-from-catalog.py --projections-only), it will not")
            return False
        if refs.unreadable:
            self.leave(d, f"a deleted person, but {refs.unreadable[0]} cannot be read, so it may credit them")
            return False
        if deleted > self.cutoff:
            self.leave(d, f"deleted {entry['deletedAt']}, within the grace period")
            return False
        young = self.young(d)
        if young:
            self.leave(d, f"a deleted person, but their folder was written to within the grace period ({young})")
            return False
        self.target(d, "deleted person", f"deleted {entry['deletedAt']} by {entry.get('deletedBy') or 'unknown'}: "
                                         f"nothing in the folder is newer, and no item record credits them")
        return True

    # -------------------------------------------------- (b) a version that never finished
    def unfinished(self, item_dir, vp, vid):
        entries = listdir(vp)
        vj = os.path.join(vp, "version.json")
        originals = []
        if os.path.isfile(vj):
            try:
                version = load(vj)
                if version.get("versionId") != vid:
                    raise ValueError("it names another version")
                originals = [n for n in version.get("originalFiles") or [] if isinstance(n, str)]
            except (OSError, ValueError, AttributeError) as e:
                self.leave(vp, f"an unfinished version whose version.json cannot be read ({e})")
                return
        known = set(PACKAGE_PARTS) | set(RECORDS_TMP) | {"version.json"} | set(originals)
        unknown = [n for n in entries if n not in known]
        if unknown:
            self.leave(vp, f"an unfinished version holding {unknown[0]}, which the sweep cannot classify")
            return
        young = self.young(vp)
        if young:
            self.leave(vp, f"an unfinished version written to within the grace period ({young})")
            return
        kept = [n for n in originals if os.path.isfile(os.path.join(vp, n))]
        named = self.refs.version_named(item_dir, vid)
        if named:
            self.leave(vp, f"an unfinished version, but {named}")
            return
        if not os.path.isfile(vj):
            self.target(vp, "unfinished version", "no .complete and no version.json: nothing can have known it")
            return
        if not kept:
            if self.refs.export is None:
                self.leave(vp, "an unfinished version that wrote its record: without --export the sweep cannot tell "
                               "whether the database knows it")
                return
            self.target(vp, "unfinished version", "no .complete, no original, and nothing names it")
            return
        for n in [n for n in entries if n in PACKAGE_PARTS or n in RECORDS_TMP]:
            self.target(os.path.join(vp, n), "unfinished package",
                        f"no .complete: the package never finished beside the original {kept[0]}, which stays")

    # -------------------------------------------------- (b+) a version an event retired
    def removed_version(self, item_dir, vp, vid, ev):
        """A version-removed event says the version is no longer part of the item, so its folder, still
        on storage, is garbage — whatever it holds, finished or not — once the removal and everything
        in the folder are older than the grace, and nothing names the version any more."""
        at = instant(ev.get("at"))
        if at is None:
            self.leave(vp, "a version-removed event names it, but states no moment to prove it by")
            return
        if at > self.cutoff:
            self.leave(vp, f"a version-removed event of {ev['at']} names it, within the grace period")
            return
        young = self.young(vp)
        if young:
            self.leave(vp, f"a removed version, but written to within the grace period ({young})")
            return
        named = self.refs.removed_version_named(item_dir, vid)
        if named:
            self.leave(vp, f"a removed version, but {named}")
            return
        self.target(vp, "removed version", f"a version-removed event of {ev['at']} says it is no longer part of the item")

    # -------------------------------------------------- (b'') an extra an event retired
    def removed_extra(self, item_dir, xp, xid, ev):
        """An extra-removed event says the extra is no longer part of the item, so its folder, still
        on storage, is garbage — whatever it holds, finished or not — once the removal and everything
        in the folder are older than the grace, and nothing names the extra any more."""
        at = instant(ev.get("at"))
        if at is None:
            self.leave(xp, "an extra-removed event names it, but states no moment to prove it by")
            return
        if at > self.cutoff:
            self.leave(xp, f"an extra-removed event of {ev['at']} names it, within the grace period")
            return
        young = self.young(xp)
        if young:
            self.leave(xp, f"a removed extra, but written to within the grace period ({young})")
            return
        named = self.refs.extra_named(item_dir, xid)
        if named:
            self.leave(xp, f"a removed extra, but {named}")
            return
        self.target(xp, "removed extra", f"an extra-removed event of {ev['at']} says it is no longer part of the item")

    # -------------------------------------------------- (b') an extra whose package never finished
    def unfinished_extra(self, item_dir, xp, xid):
        """An extra without .complete is garbage only when it was packaged and the package never
        finished — like an unfinished version. One with no package is either finished, by its
        checksums, or a writer's to finish; the sweep removes nothing else in an extra."""
        entries = listdir(xp)
        if not any(n in EXTRA_PARTS for n in entries):
            if not os.path.isfile(os.path.join(xp, "checksums.sha256")):
                self.leave(xp, "an extra that never finished and holds no package: the sweep removes nothing "
                               "else in an extra")
            return
        xj = os.path.join(xp, "extra.json")
        originals = []
        if os.path.isfile(xj):
            try:
                x = load(xj)
                if x.get("extraId") != xid:
                    raise ValueError("it names another extra")
                originals = [n for n in x.get("originalFiles") or [] if isinstance(n, str)]
            except (OSError, ValueError, AttributeError) as e:
                self.leave(xp, f"an unfinished extra whose extra.json cannot be read ({e})")
                return
        known = set(EXTRA_PARTS) | set(EXTRA_TMP) | {"extra.json", "checksums.sha256"} | set(originals)
        unknown = [n for n in entries if n not in known]
        if unknown:
            self.leave(xp, f"an unfinished extra holding {unknown[0]}, which the sweep cannot classify")
            return
        young = self.young(xp)
        if young:
            self.leave(xp, f"an unfinished extra written to within the grace period ({young})")
            return
        named = self.refs.extra_named(item_dir, xid)
        if named:
            self.leave(xp, f"an unfinished extra, but {named}")
            return
        kept = [n for n in originals if os.path.isfile(os.path.join(xp, n))]
        if not os.path.isfile(xj):
            self.target(xp, "unfinished extra", "no .complete and no extra.json: nothing can have known it")
            return
        if not kept:
            if self.refs.export is None:
                self.leave(xp, "an unfinished extra that wrote its record: without --export the sweep cannot tell "
                               "whether the database knows it")
                return
            self.target(xp, "unfinished extra", "no .complete, no original, and nothing names it")
            return
        for n in [n for n in entries if n in EXTRA_PARTS or n in EXTRA_TMP or n == "checksums.sha256"]:
            self.target(os.path.join(xp, n), "unfinished extra package",
                        f"no .complete: the extra's package never finished beside the original {kept[0]}, which stays")

    # -------------------------------------------------- (c) an image a projection dropped
    def images(self, folder, projection, keep=frozenset()):
        listed = self.refs.listed(projection)
        names = [n for n in listdir(folder) if n not in keep]
        unlisted = [n for n in names if listed is None or n not in listed]
        if not unlisted:
            return
        if listed is None:
            self.leave(folder, f"{os.path.basename(projection)} cannot be read, so no image here is proved dropped")
            return
        projected_young = mtime(projection) > self.cutoff
        for n in unlisted:
            p = os.path.join(folder, n)
            m = IMAGE_NAME.match(n)
            if not m or not os.path.isfile(p) or os.path.islink(p):
                self.leave(p, f"not listed in {os.path.basename(projection)}, and not an image the sweep can classify")
            elif sha_file(p) != m.group(1):
                self.leave(p, "not named by the hash of its own bytes, so not an image a projection wrote")
            elif projected_young:
                self.leave(p, f"dropped by a {os.path.basename(projection)} written within the grace period")
            elif mtime(p) > self.cutoff:
                self.leave(p, "written within the grace period")
            else:
                self.target(p, "dropped image", f"{os.path.basename(projection)} no longer lists it")

    # -------------------------------------------------- is a quarantined target still garbage?
    def still_garbage(self, t):
        """Asked again after the target was renamed away, against references read fresh."""
        where = os.path.join(self.root, t["path"])
        if os.path.lexists(where):
            return "something was written where it was"
        parts = t["path"].split(os.sep)
        if t["class"] == "deleted item":
            iid = parts[-1]
            if iid in self.refs.live:
                return "the database holds it again"
            if self.refs.log is None or iid not in self.refs.log:
                return "the deletion log no longer names it"
            return None
        if t["class"] == "deleted person":
            pid = parts[-1]
            if pid in self.refs.live_people:
                return "the database holds them again"
            if self.refs.people_log is None or pid not in self.refs.people_log:
                return "the deletion log no longer names them as a person"
            if self.refs.credits.get(pid):
                return f"{self.refs.credits[pid][0]}/metadata.json credits them"
            if self.refs.unreadable:
                return f"{self.refs.unreadable[0]} cannot be read, so it may credit them"
            return None
        if t["class"] in ("unfinished version", "unfinished package"):
            i = parts.index("versions")
            item_dir, vid = os.path.join(self.root, *parts[:i]), parts[i + 1]
            if t["class"] == "unfinished package" and os.path.exists(os.path.join(item_dir, "versions", vid, ".complete")):
                return "its version has finished since"
            named = self.refs.version_named(item_dir, vid)
            return named
        if t["class"] == "removed version":
            i = parts.index("versions")
            item_dir, vid = os.path.join(self.root, *parts[:i]), parts[i + 1]
            if vid not in self.refs.removed_versions(item_dir):
                return "no version-removed event names it any more"
            return self.refs.removed_version_named(item_dir, vid)
        if t["class"] == "removed extra":
            i = parts.index("extras")
            item_dir, xid = os.path.join(self.root, *parts[:i]), parts[i + 1]
            if xid not in self.refs.retired(item_dir):
                return "no extra-removed event names it any more"
            return self.refs.extra_named(item_dir, xid)
        if t["class"] in ("unfinished extra", "unfinished extra package"):
            i = parts.index("extras")
            item_dir, xid = os.path.join(self.root, *parts[:i]), parts[i + 1]
            if t["class"] == "unfinished extra package" and os.path.exists(os.path.join(item_dir, "extras", xid, ".complete")):
                return "its extra has finished since"
            return self.refs.extra_named(item_dir, xid)
        if t["class"] == "dropped image":
            folder = os.path.dirname(where)
            projection = os.path.join(folder, "person.json") if parts[0] == "people" else \
                os.path.join(os.path.dirname(folder), "metadata.json")
            listed = self.refs.listed(projection)
            if listed is None:
                return f"{os.path.basename(projection)} cannot be read"
            return f"{os.path.basename(projection)} lists it again" if parts[-1] in listed else None
        return "not a class the sweep knows"


# ---------------------------------------------------------------- removing it, through a quarantine
class Apply:
    def __init__(self, sweep, quarantine=None):
        self.s = sweep
        self.root = sweep.root
        # where this run quarantines, and where a sweep kept its quarantine before the work tree
        self.base = quarantine or os.path.join(self.root, QUARANTINE)
        self.old_base = os.path.join(self.root, OLD_QUARANTINE)
        self.removed = self.bytes = 0
        self.put_back = []
        self.failed = []

    def quarantine_dir(self, now):
        base = self.base
        os.makedirs(base, exist_ok=True)
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        for n in range(100):
            q = os.path.join(base, stamp + (f"-{n}" if n else ""))
            try:
                os.mkdir(q)
                return q
            except FileExistsError:
                continue
        raise SystemExit("cannot make a quarantine folder")

    def write_plan(self, q, targets, now):
        plan = {"startedAt": utc(now), "grace": self.s.grace, "export": self.s.refs.export_path, "targets": targets}
        tmp = os.path.join(q, "sweep.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, os.path.join(q, "sweep.json"))

    def run(self, now):
        for q in self.leftovers():
            self.finish(q)
        if not self.s.targets:
            return
        q = self.quarantine_dir(now)
        self.write_plan(q, self.s.targets, now)
        for t in self.s.targets:
            src, dst = os.path.join(self.root, t["path"]), os.path.join(q, t["path"])
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                os.rename(src, dst)
            except OSError as e:
                t["left"] = True
                self.failed.append(f"{t['path']}: could not be renamed into the quarantine ({e}); left where it is")
        self.finish(q)

    def leftovers(self):
        """The quarantines an interrupted --apply left: where this run quarantines, and where a sweep
        from before the work tree did."""
        out = []
        for base in dict.fromkeys((self.base, self.old_base)):
            for name in (listdir(base) if os.path.isdir(base) else []):
                q = os.path.join(base, name)
                if not os.path.isfile(os.path.join(q, "sweep.json")):
                    self.failed.append(f"{os.path.relpath(q, self.root)}: a quarantine without its sweep.json; nobody "
                                       f"knows where its contents came from, so it is left for a person")
                    continue
                out.append(q)
        return out

    def finish(self, q):
        """Check each quarantined target against references read now, put back what is not garbage
        any more, and delete the rest; then the quarantine itself. A deleted person is checked last,
        against references read again once every other target is settled: an item folder put back
        brings back the credits in it."""
        plan = load(os.path.join(q, "sweep.json"))
        keep = False
        people = [t for t in plan["targets"] if t["class"] == "deleted person"]
        for group in ([t for t in plan["targets"] if t["class"] != "deleted person"], people):
            if not group and group is people:
                break
            self.s.refs.reload()
            for t in group:
                keep |= self.settle(q, t)
        if keep:
            return
        os.unlink(os.path.join(q, "sweep.json"))
        self.tidy(q)

    def settle(self, q, t):
        """One quarantined target: put back when it is not garbage any more, deleted when it still is.
        Returns True when it has to stay in the quarantine for a person: its path is taken."""
        held = os.path.join(q, t["path"])
        if t.get("left") or not os.path.lexists(held):
            return False
        why = self.s.still_garbage(t)
        if why:
            where = os.path.join(self.root, t["path"])
            if os.path.lexists(where):
                self.failed.append(f"{t['path']}: {why}, and the path is taken, so it stays in "
                                   f"{os.path.relpath(held, self.root)} for a person")
                return True
            os.makedirs(os.path.dirname(where), exist_ok=True)
            os.rename(held, where)
            self.put_back.append(f"put back {t['path']}: {why}")
            return False
        size = size_of(held)
        if os.path.isdir(held) and not os.path.islink(held):
            shutil.rmtree(held)
        else:
            os.unlink(held)
        self.removed += 1
        self.bytes += size
        return False

    def tidy(self, q):
        """The emptied quarantine, and the folder of quarantines a sweep kept at the library root before
        the work tree, when nothing else is in it: the work tree's own stays."""
        for base, dirs, files in os.walk(q, topdown=False):
            if not os.listdir(base):
                os.rmdir(base)
        parent = os.path.dirname(q)
        if parent == self.old_base and os.path.isdir(parent) and not os.listdir(parent):
            os.rmdir(parent)


def main():
    ap = argparse.ArgumentParser(prog="library-v2-sweep.py",
                                 description="Find provable garbage in a v2 library tree; with --apply, remove it.")
    ap.add_argument("root", help="the library root holding movies/, series/ and people/")
    ap.add_argument("--export", help="the catalog export: its deletion log proves an item's or a person's folder "
                                     "deleted, and its rows keep what the database still holds")
    ap.add_argument("--grace", default="24h", type=grace_seconds,
                    help="how old anything must be before it can be swept: 24h (default), 90m, 7d, 3600s, 0")
    ap.add_argument("--apply", action="store_true", help="remove what is found, through the quarantine")
    ap.add_argument("--quarantine", help="where --apply quarantines what it removes, on the same share: "
                                         "<root>/.work/quarantine by default")
    ap.add_argument("--verbose", action="store_true", help="also list what is left alone and why, every one")
    args = ap.parse_args()
    root = os.path.abspath(args.root)
    if not os.path.isdir(os.path.join(root, "movies")) and not os.path.isdir(os.path.join(root, "series")):
        ap.error(f"{root} holds neither movies/ nor series/")
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        refs = References(root, args.export)
    except (OSError, ValueError) as e:
        ap.error(f"the export {args.export} cannot be read: {e}")
    s = Sweep(root, refs, args.grace, now, args.verbose)
    s.run()

    hours = f"{args.grace // 3600}h" if args.grace % 3600 == 0 else f"{args.grace}s"
    print(f"sweep of {root} (grace {hours}, {'export ' + args.export if args.export else 'no export'}, "
          f"{'apply' if args.apply else 'dry run'})")
    for n in s.notes:
        print("  note " + n)
    total = sum(t["bytes"] for t in s.targets)
    for t in s.targets:
        print(f"  {'remove' if args.apply else 'would remove'}  {t['path']}  ({human(t['bytes'])}) — {t['class']}: {t['reason']}")
    if s.left:
        print(f"  left alone: {len(s.left)}")
        for path, why in s.left[:None if args.verbose else 50]:
            print(f"    {path} — {why}")
        if not args.verbose and len(s.left) > 50:
            print(f"    … and {len(s.left) - 50} more (--verbose lists them all)")
    quarantine = os.path.abspath(args.quarantine) if args.quarantine else os.path.join(root, QUARANTINE)
    if not args.apply:
        for q in sorted(glob.glob(os.path.join(glob.escape(quarantine), "*")) +
                        glob.glob(os.path.join(glob.escape(root), OLD_QUARANTINE, "*"))):
            print(f"  note an earlier --apply did not finish: {os.path.relpath(q, root)}; the next --apply finishes it")
        print(f"would remove {len(s.targets)} target(s), {human(total)}" +
              ("; run again with --apply to remove them" if s.targets else ""))
        return 0
    a = Apply(s, quarantine)
    a.run(now)
    for n in a.put_back:
        print("  " + n)
    for f in a.failed:
        print("  FAILED " + f)
    print(f"removed {a.removed} target(s), {human(a.bytes)}; put back {len(a.put_back)}")
    return 1 if a.failed else 0


if __name__ == "__main__":
    sys.exit(main())
