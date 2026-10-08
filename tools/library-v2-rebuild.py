#!/usr/bin/env python3
"""Read a v2 library tree and produce the catalog contents it implies.

Usage:
  library-v2-rebuild.py LIBRARY [--out rows.json] [--compare CATALOG.json [--subset] [--arrivals-root DIR]]
                        [--text-language LANG] [--ignore-fields a,b,c]

The database is the working copy and this tree is the record that can rebuild it. This reads the
records, applies the item's events in the order they happened, and writes the rows a catalog would
hold: the items and their texts, the images, the credits — each person's role, the job in the
source's own words, the character, the billing order and a series' episode count, under the names
the export gives them — the chapters and segments, one playback asset per package and one per
original that is still there, the subtitles the package carries — none of them a default, which is
behaviour (below) — and the people under people/ with their biographies, dates, reference ids and
portraits — which one is primary, and the path TMDB lists each under — and for every row the state
of the database row its projection reflects (modifiedAt, from databaseUpdatedAt) and when TMDB was
last asked (tmdbFetchedAt, tmdbChangedAt, from sources). It needs no network and no other service,
it can run against a copy, and running it twice gives the same answer.

The bonus material beside a movie or series becomes rows of its own, under extras in the rows
JSON: kind, title, language, runtime and season as extra.json recorded them, the order, hidden
flag and label the item's metadata.json decided, and the original and package to play, in the shape
an item's playback rows have, listed in the order a viewer sees them. Only a finished extra counts —
a packaged one by its .complete, one that keeps only its original by its checksums file. An extra
whose origin names a link that metadata.json's videos[] lists gives that link its localPath back:
the original it keeps. The catalog has no table for extras yet, so they are in the rows JSON only,
and --compare counts them without comparing them.

Applying events, earliest first:
  original-deleted    the originals it names are gone, so they are not playback assets any more,
                      and the version's package is the only copy of it — canonical whatever its
                      record says, with everything the package failed to carry permanently lost
  version-removed     the version is not part of the item: its folder is ignored altogether
  package-superseded  the package it names is not the one to use; the successor is
  extra-removed       the extra is not part of the item: its folder is ignored, and it has no row
  note                nothing

--compare reports both directions against a catalog export, and says which of three things each
difference about an item's existence is. Deleting an item is supposed to delete its folder; when a
writer missed that, the tree holds a record the database does not, and only the database's deletion
log (the export's top-level deletedItems: [{id, type, deletedAt, deletedBy}]) can say whether the
database lost the item or deleted it. An entry's type is the item's own — movie, series, episode — or
person, for a person the catalog deleted because no title credits them any more; an entry without a
type is an item's, as every entry of an export from before people were logged is, and one of a type
the format does not know proves nothing:

  orphan          on storage, not in the database, in the deletion log, and nothing in its folder
                  is newer than that deletion: the database deleted it, so the folder is safe to
                  remove (library-v2-sweep.py removes it). An episode whose series was deleted is an
                  orphan with it.
  lost            on storage and not in the database, but not in the log either — or in the log
                  while a record in the folder is newer than the deletion, because the id was
                  created again since: the database lost what the record still knows, restore it
  missing record  in the database, not on storage: the tree cannot restore it

"Nothing newer" is the newest moment any record in the folder states — item.json's createdAt and
migratedAt, metadata.json's asOf, the databaseUpdatedAt of the row it reflects and when its TMDB
data was fetched, every source's takenAt, version's and package's createdAt, event's at and extra's
createdAt, the episodes' too for a series — and a record that states none counts with its file's
modification time. An id that is both in the log and in the database is present: the item that
exists wins. deletedItems null (a catalog that keeps no log yet) or absent means there is no log, and
then nothing can be called deleted: every item only on storage is lost.

People are compared too. The database's people are the export's top-level people list when it
carries one, and otherwise everyone its items credit, by personId and name — all a catalog without
person records knows — and only the fields the export carries are compared: every field of the
people list there is, and of each portrait its bytes by their hash, kind, type, size, dimensions,
whether it is primary, the path TMDB lists it under and when it was fetched. A person only on
storage is an orphan when the deletion log names them as a person and nothing in their folder is
newer than that deletion — person.json's asOf, its databaseUpdatedAt and TMDB fetchedAt, each image's
fetchedAt, and the modification time of a file that states none — and an item record on storage that
still credits them is a note: its projection is stale, and the sweep keeps the person's folder until
no item record credits them. Otherwise a person only on storage is lost when an item on storage that
is lost, or that the database holds, credits them, and unreferenced — kept, not counted, and never
swept — when nothing the database holds credits them; so is one an untyped entry of an older export
names. A person the database holds is present, whatever the log says, and one it holds and the tree
does not is a missing record; with --subset only when an item on storage credits them.

Every row both sides hold is then judged by how fresh its projection is. A projection says which
state of its database row it reflects — databaseUpdatedAt, the row's modifiedAt when it was
projected — so against the export's modifiedAt, to the second, an item or a person is one of:

  stale projection  the row was modified after the state the projection reflects: the database
                    changed since. It is fixed by projecting again — library-v2-from-catalog.py
                    --projections-only does, touching no record — never by editing the file.
  projection ahead  the projection reflects a later state than the export holds: an export older
                    than the tree, or a database restored from before it.

A projection that does not say which state it reflects — one written before 2026-10-02 (f) — is
counted as of unknown freshness, and one whose row the export gives no modifiedAt is not judged.

Field by field it then reports where the rows both sides hold disagree, and marks the rows whose
projection is stale. It exits non-zero for a lost item or person, a missing record, a projection that
is stale or ahead and a field that disagrees — never for an orphan, an unreferenced person or an
unknown freshness alone — so it can gate a migration. An item row's tmdbFetchedAt and tmdbChangedAt,
and an image row's dimensions, primary flag and TMDB path, are compared where the export carries
them: an export from before them makes no tree that has them differ. So are a credit's job,
character, billing order and episode count; a credit is matched by its person and role, so one whose
name or job changed is that credit changed, and namesakes in one role stay apart. An image row is
matched by its bytes and its kind, because one file can be several kinds — a backdrop that is the
poster is two rows, both given back — so an image of a kind the tree lacks is missing. A tree from
before credits were general is compared in every field the export carries: where the database knows
a value the tree lacks, the difference reads storage None — the database knowing more, which
projecting again fixes — and a value the database does not know either is no difference.

Fields that cannot agree by construction are ignored by default (--ignore-fields):
  id     a database key, not a fact about the item
  path   the bytes moved into the version folder, so the database's old paths are stale
  hash   never filled by either side
Ignoring modifiedAt leaves the freshness of every projection unjudged.
A trailer link's localPath is compared only for whether there is one, for the same reason: a
downloaded trailer moved into its extra's folder, under the name the library gives an original, so
whether the link has a local copy can be compared, and neither where it lives nor the name it came
with.

A subtitle row's isDefault is never compared, whatever --ignore-fields says, and the rebuild writes
it false: which subtitle a viewer gets is behaviour — the player's rule, or a default a person
chose and the database keeps — which no record holds; a package's default is only what its playlist
says. A database rebuilt from the tree has lost the defaults people chose.

--subset reports the rows only the database has without counting them, for a tree that was built
from part of a catalog.

The arrivals are not the record. An original waits outside the library — in <root>/.work/incoming
on the platform — until its package is recorded, and is deleted then, so the export's rows of the
files there are no rows the tree can give back: --arrivals-root names that folder, and the asset and
subtitle rows of files under it are not compared. Neither is ever the row of an original that was
retired (kind original). A subtitle the package made from a sidecar is the catalog's row of that
sidecar while the original waits — at the arrivals, not compared — and the rebuild gives it no row of
its own until the original is deleted; then it is a row of the package's subtitle in the sidecar's
language, and a sidecar nothing was made from is a row of the copy its source folder keeps.
"""
import argparse, base64, datetime, glob, hashlib, json, os, re, sys, uuid

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
# A person's reference ids as an export lists them ([{source, externalId}]) and as person.json keys them.
PERSON_ID_FIELD = {"tmdb": "tmdbPerson", "themoviedb": "tmdbPerson", "tmdb-person": "tmdbPerson",
                   "imdb": "imdb", "tvdb": "tvdb", "wikidata": "wikidata"}
PERSON_FIELDS = ("name", "sortName", "alsoKnownAs", "birthDate", "deathDate", "birthPlace", "knownForDepartment",
                 "biography", "externalIds", "artwork", "metadataLocked", "lockedFields", "fieldOrigins",
                 "tmdbFetchedAt", "tmdbChangedAt", "modifiedAt")
# A credit as an item row's people list holds it, under the export's names.
CREDIT_FIELDS = ("personId", "name", "role", "job", "character", "order", "episodeCount")
DAY_RE = re.compile(r"^[0-9]{4}(-[0-9]{2}(-[0-9]{2})?)?")


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


# The moments each record of an item folder states, by where the record sits. A deletion explains a
# folder only when it came after every one of them.
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
EXTRA_DIRS = ("hls", "subs", "trickplay")


def utc(t):
    return t.astimezone(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def image_origin(img):
    """An image's origin as the object it has been since 2026-10-02 (f). A projection written before
    names the source alone, which is read as {source: <it>}."""
    o = img.get("origin")
    return dict(o) if isinstance(o, dict) else {"source": o} if isinstance(o, str) and o else {}


def artwork_row(img, file):
    """An image of a projection as a catalog's artwork row holds it: what it is, its bytes by their
    hash and size, whether it is the primary one of its kind, and the path TMDB lists it under when
    it came from there."""
    origin = image_origin(img)
    return {"kind": img["kind"], "contentType": img["contentType"],
            "fetchedAt": img.get("fetchedAt") or origin.get("fetchedAt"), "sha256": img["sha256"],
            "sizeBytes": img["sizeBytes"], "width": img.get("width"), "height": img.get("height"),
            "isPrimary": bool(img.get("primary")),
            "sourcePath": origin.get("ref") if origin.get("source") == "tmdb" else None, "file": file}


def local_copies(extras):
    """Where an item's extras keep a file downloaded from a link, by the link's site and key and by
    its url: what gives a trailer link of metadata.json its localPath back."""
    out = {}
    for x in extras:
        origin = x.get("origin") or {}
        path = next((a["path"] for a in x["playbackAssets"] if a["kind"] == "primary"), None)
        if origin.get("kind") != "link" or not path:
            continue
        if origin.get("site") and origin.get("externalId"):
            out.setdefault(("site", origin["site"], origin["externalId"]), path)
        if origin.get("url"):
            out.setdefault(("url", origin["url"]), path)
    return out


PERSON_MOMENTS = ("asOf", "databaseUpdatedAt", "sources.tmdb.fetchedAt")


def newest_person(d):
    """The newest moment a person folder states: person.json's asOf, the databaseUpdatedAt of the row
    it reflects and when its TMDB data was fetched, and for each file beside it the fetchedAt
    person.json records for that image. A person.json that states none, and a file it records none
    for, count with their modification time, so a folder never looks older than what is in it.
    Returns an RFC 3339 string, or None."""
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
            t = instant(img.get("fetchedAt")) or instant(image_origin(img).get("fetchedAt"))
            if t and isinstance(img.get("file"), str):
                fetched[img["file"]] = t
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    if not moments and os.path.isfile(p):
        moments.append(datetime.datetime.fromtimestamp(os.path.getmtime(p), datetime.timezone.utc))
    for name in listdir(d):
        if name != "person.json":
            moments.append(fetched.get(name) or
                           datetime.datetime.fromtimestamp(os.path.getmtime(os.path.join(d, name)), datetime.timezone.utc))
    return utc(max(moments)) if moments else None


def newest_record(d):
    """The newest moment any record in the item folder d states — its own records, not an episode's.
    A record that cannot be read, or that states no moment, counts with its file's modification time,
    so a folder can never look older than what is in it. Returns an RFC 3339 string, or None."""
    newest = None
    for pattern, fields in RECORD_MOMENTS:
        for p in sorted(glob.glob(os.path.join(glob.escape(d), *pattern.split("/")))):
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
            if not moments:
                moments = [datetime.datetime.fromtimestamp(os.path.getmtime(p), datetime.timezone.utc)]
            newest = max([newest] + moments) if newest else max(moments)
    return utc(newest) if newest else None


# ---------------------------------------------------------------- reading one item
class Rebuild:
    def __init__(self, root, text_language):
        self.root = root
        self.text_language = text_language
        self.items = []
        self.people = []
        self.extras = []
        self.storage = []
        self.notes = []
        self.newest = {}
        self.people_newest = {}

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
        base = os.path.join(self.root, "people")
        for shard in (listdir(base) if os.path.isdir(base) else []):
            sd = os.path.join(base, shard)
            for pid in (listdir(sd) if os.path.isdir(sd) else []):
                if os.path.isdir(os.path.join(sd, pid)):
                    self.person(os.path.join(sd, pid))
        self.items.sort(key=lambda r: r["id"])
        self.people.sort(key=lambda r: r["id"])
        # an item's extras in the order a viewer sees them: by the order a person gave, then the rest
        # in the order they were taken in
        self.extras.sort(key=lambda r: (r["itemId"], r["order"] is None, r["order"] or 0, r["createdAt"] or "", r["id"]))
        self.storage.sort(key=lambda r: r["itemId"])

    def person(self, d):
        """A person row, as the last projection left it: the texts, the dates, the department, the
        reference ids, the portraits — which one is primary, and where each came from — the lock and
        the locked fields, where each field came from, when TMDB was last asked, and the state of the
        row the projection reflects. No credits: those are the items' rows."""
        try:
            doc = load(os.path.join(d, "person.json"))
        except (OSError, ValueError) as e:
            self.note(f"{d}: no person.json to rebuild from ({e})")
            return
        curation = doc.get("curation") or {}
        tmdb = (doc.get("sources") or {}).get("tmdb") or {}
        self.people.append({
            "id": doc["personId"], "name": doc.get("name"), "sortName": doc.get("sortName"),
            "alsoKnownAs": list(doc.get("alsoKnownAs") or []), "birthDate": doc.get("birthDate"),
            "deathDate": doc.get("deathDate"), "birthPlace": doc.get("birthPlace"),
            "knownForDepartment": doc.get("knownForDepartment"),
            "biography": dict(doc.get("biography") or {}), "externalIds": dict(doc.get("externalIds") or {}),
            "metadataLocked": bool(curation.get("metadataLocked")), "lockedFields": list(curation.get("lockedFields") or []),
            "fieldOrigins": dict(doc.get("fieldOrigins") or {}),
            "tmdbFetchedAt": tmdb.get("fetchedAt"), "tmdbChangedAt": tmdb.get("changedAt"),
            "modifiedAt": doc.get("databaseUpdatedAt"),
            "artwork": [artwork_row(i, i["file"]) for i in doc.get("images") or []]})
        self.people_newest[doc["personId"]] = newest_person(d)

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
        extras = []
        if item.get("type") in ("movie", "series"):
            extras = self.extra_rows(d, item, meta, events)
        elif os.path.isdir(os.path.join(d, "extras")):
            self.note(f"{item['itemId']}: an episode has no extras, so its extras/ folder is ignored")
        self.items.append(self.row(d, item, meta, versions, sources, extras))
        self.extras += extras
        self.newest[item["itemId"]] = newest_record(d)
        self.storage.append({"itemId": item["itemId"], "newestRecordAt": self.newest[item["itemId"]],
                             "versions": storage})

    def events(self, d, iid):
        """events/<…>/event.json — and the events/<…>.json files of a tree from before each event was
        a folder, so restoring a copy taken before library-v2-upgrade.py ran loses nothing."""
        base = os.path.join(d, "events")
        out = []
        for name in (listdir(base) if os.path.isdir(base) else []):
            p = os.path.join(base, name, "event.json")
            if not os.path.isdir(os.path.join(base, name)):
                if not name.endswith(".json"):
                    continue
                p = os.path.join(base, name)
                self.note(f"{iid}: event {name} is in the layout before 2026-10-02 (b); read as it is")
            try:
                ev = load(p)
            except (OSError, ValueError) as e:
                self.note(f"{iid}: event {name} could not be read ({e})")
                continue
            out.append((ev.get("at") or "", name, ev))
        return [ev for _, _, ev in sorted(out, key=lambda x: (x[0], x[1]))]

    def sources(self, d):
        """sources/<sourceId>/source.json — and the sources/<sourceId>.json of a tree from before each
        source was a folder."""
        base = os.path.join(d, "sources")
        out = {}
        for name in (listdir(base) if os.path.isdir(base) else []):
            if UUID_RE.match(name) and os.path.isfile(os.path.join(base, name, "source.json")):
                p = os.path.join(base, name, "source.json")
            elif name.endswith(".json") and UUID_RE.match(name[:-5]):
                p = os.path.join(base, name)
                self.note(f"{os.path.basename(d)}: source {name} is in the layout before 2026-10-02 (b); read as it is")
            else:
                continue
            try:
                rec = load(p)
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
    def row(self, d, item, meta, versions, sources, extras=()):
        copies = local_copies(extras)
        titles = meta.get("titles") or {}
        localized = titles.get("localized") or {}
        body = localized.get(self.text_language) or localized.get("und") or \
            (list(localized.values())[0] if len(localized) == 1 else {})
        library = meta.get("library") or {}
        tmdb = (meta.get("sources") or {}).get("tmdb") or {}
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
            "modifiedAt": meta.get("databaseUpdatedAt"),
            "tmdbFetchedAt": tmdb.get("fetchedAt"), "tmdbChangedAt": tmdb.get("changedAt"),
            "createdAt": item.get("createdAt"), "createdBy": item.get("createdBy"),
            # the ids the database holds now, as the projection says them; a projection from before it
            # said them leaves the ones the item was created with
            "externalIds": [{"source": EXTERNAL_ID_SOURCE.get(k, k), "externalId": v} for k, v in sorted(
                (meta["externalIds"] if isinstance(meta.get("externalIds"), dict) else item.get("externalIds") or {})
                .items())],
            "genres": list(meta.get("genres") or []), "tags": list(meta.get("tags") or []),
            # a credit from before credits were general has no job and no episode count: null, as the
            # export writes a field it does not know
            "people": [{k: c.get(k) for k in CREDIT_FIELDS} for c in meta.get("credits") or []],
            "chapters": [], "segments": [], "playbackAssets": [], "subtitleAssets": [],
            "trailers": [{"source": v.get("origin"), "site": v.get("site"), "externalId": v.get("key"),
                          "url": v.get("url"), "title": v.get("name"),
                          "durationSec": (v["durationMs"] // 1000) if v.get("durationMs") else None,
                          "localPath": copies.get(("site", v.get("site"), v.get("key"))) or copies.get(("url", v.get("url")))}
                         for v in meta.get("videos") or []],
            "artwork": [artwork_row(i, os.path.join("metadata", i["file"])) for i in meta.get("images") or []],
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
                row["subtitleAssets"] += self.subtitles(item, v, sources)
        return row

    def assets(self, item, v, sources, is_primary):
        out = []
        # an original is found among the sources of its own version: every version names its original
        # original.<ext>, so another version's source may carry the same name
        ids = (v.get("version") or {}).get("sourceIds")
        mine = [sources[s] for s in ids if s in sources] if isinstance(ids, list) else list(sources.values())
        for i, name in enumerate(v["kept"]):
            src = next((s for s in mine if s.get("file", {}).get("name") == name), None)
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

    def subtitles(self, item, v, sources=None):
        """The subtitle rows of a version's package — and of the sidecars its sources keep once their
        originals are gone. A subtitle made from a sidecar has no row of its own while its original waits
        beside the sidecar: the catalog's row of the sidecar stands for it, at the arrivals, which are no
        part of the record. Once the original is deleted the catalog points that row at the package's
        subtitle, in the sidecar's language, and the row of a sidecar nothing was made from at the copy
        the source folder keeps — which has no label of its own the record could give back."""
        sources = sources or {}
        gone = lambda sid: v.get("whole") or sid in (v.get("gone") or ())
        out, made = [], set()
        for s in v["package"].get("subtitles") or []:
            language, f = s.get("language"), s.get("fromSidecar")
            if f:
                made.add(f)
                sid = f.split("/")[1] if f.count("/") == 2 else None
                if not gone(sid):
                    continue
                kept = next((x for x in (sources.get(sid) or {}).get("sidecars") or [] if x.get("file") == f), {})
                language = kept.get("language", language)
            # never the package's default: that is what its playlist says, and which subtitle a viewer
            # gets is behaviour, which the database keeps and no record holds
            out.append({"id": did(item["itemId"], "subtitle", v["id"], s["id"]),
                        "path": os.path.join(v["dir"], s["path"]), "format": s.get("format"),
                        "language": language, "label": s.get("title") or "", "isDefault": False})
        item_dir = os.path.dirname(os.path.dirname(v["dir"]))
        for sid in v.get("version", {}).get("sourceIds") or []:
            for x in (sources.get(sid) or {}).get("sidecars") or [] if gone(sid) else []:
                if x.get("kind") == "subtitle" and x.get("file") not in made:
                    out.append({"id": did(item["itemId"], "subtitle", sid, x["file"]),
                                "path": os.path.join(item_dir, x["file"]), "format": x.get("format"),
                                "language": x.get("language"), "label": None, "isDefault": False})
        return out

    # -------------------------------------------------- bonus material
    def extra_rows(self, d, item, meta, events=()):
        """One row per finished extra beside a movie or series: what extra.json recorded, what the
        projection decided about showing it, and what there is to play — the original kept beside it
        and the package, in the shape an item's playback rows have. An extra that never finished is
        left out: a packaged one is finished by its .complete, one that keeps only its original by its
        checksums file. So is one an extra-removed event retired, whether or not its folder is gone."""
        iid = item["itemId"]
        decided = ((meta or {}).get("library") or {}).get("extras") or {}
        retired = {e.get("extraId") for e in events if e.get("kind") == "extra-removed" and e.get("extraId")}
        base = os.path.join(d, "extras")
        rows = []
        for xid in (listdir(base) if os.path.isdir(base) else []):
            xp = os.path.join(base, xid)
            if not os.path.isdir(xp) or not UUID_RE.match(xid):
                continue
            if xid in retired:
                self.note(f"{iid}: extra {xid} was removed by an event; its folder is ignored")
                continue
            try:
                x = load(os.path.join(xp, "extra.json"))
            except (OSError, ValueError) as e:
                self.note(f"{iid}: extra {xid} has no extra.json to rebuild from ({e})")
                continue
            marker = os.path.isfile(os.path.join(xp, ".complete"))
            has_package = os.path.isfile(os.path.join(xp, "package.json"))
            packaged = marker and has_package
            original_only = not marker and not has_package and os.path.isfile(os.path.join(xp, "checksums.sha256")) \
                and not any(os.path.exists(os.path.join(xp, n)) for n in EXTRA_DIRS)
            if not (packaged or original_only):
                self.note(f"{iid}: extra {xid} never finished, so it is left out")
                continue
            package = None
            if marker:
                try:
                    package = load(os.path.join(xp, "package.json"))
                except (OSError, ValueError) as e:
                    self.note(f"{iid}: the package of extra {xid} could not be read ({e})")
            kept = [n for n in x.get("originalFiles") or [] if isinstance(n, str)]
            sizes = {o.get("name"): o.get("sizeBytes") for o in x.get("originals") or [] if isinstance(o, dict)}
            facts = {n: {"file": {"name": n, "sizeBytes": sizes.get(n)}, "streams": x.get("streams") or [],
                         "container": x.get("container") or {}} for n in kept}
            v = {"id": xid, "dir": xp, "kept": kept, "package": package, "superseded": False}
            decision = decided.get(xid) or {}
            rows.append({
                "id": xid, "itemId": iid, "kind": x.get("kind"), "title": x.get("title"),
                "localizedTitles": dict(x.get("localizedTitles") or {}), "language": x.get("language"),
                "runtimeMs": x.get("runtimeMs"), "seasonNumber": x.get("seasonNumber"),
                "createdAt": x.get("createdAt"), "createdBy": x.get("createdBy"),
                "order": decision.get("order"), "hidden": bool(decision.get("hidden")), "label": decision.get("label"),
                "origin": dict(x.get("origin") or {}),
                "playbackAssets": self.assets(item, v, facts, False),
                "subtitleAssets": self.subtitles(item, v) if package else []})
        return rows


# ---------------------------------------------------------------- comparing with a database
LIST_KEYS = {
    "playbackAssets": lambda x: x.get("kind"),
    "subtitleAssets": lambda x: (x.get("language"), os.path.basename(str(x.get("path") or ""))),
    # an image row is one kind of an image, so the bytes alone do not name it: a backdrop that is the
    # poster is two rows of one file
    "artwork": lambda x: (x.get("sha256"), x.get("kind")),
    # a credit is one person in one role: a credited name or a job that changed is that credit changed,
    # never one lost and another gained, and two namesakes in one role stay two credits
    "people": lambda x: (x.get("personId"), x.get("role")),
    "chapters": lambda x: x.get("startMs"),
    "segments": lambda x: (x.get("kind"), x.get("startMs")),
    "externalIds": lambda x: x.get("source"),
    "trailers": lambda x: (x.get("site"), x.get("externalId")),
}
SET_FIELDS = ("genres", "tags")
# The lists whose entries are compared only in the fields the database's entry carries: an image row
# gained its dimensions, its primary flag and its TMDB path with the people list, and a credit its job,
# character, billing order and episode count when credits became general, and an export from before
# them does not make every image or credit on storage differ. A tree from before them is compared in
# all of them: the export's value against none, the database knowing more until it is projected again.
CARRIED_ENTRY_FIELDS = {"artwork": ("width", "height", "isPrimary", "sourcePath"),
                        "people": ("job", "character", "order", "episodeCount")}
# The fields of an item row compared only when the export's row carries them, for the same reason:
# items do not carry them yet.
CARRIED_ITEM_FIELDS = ("tmdbFetchedAt", "tmdbChangedAt")
# Behaviour, not data: which subtitle a viewer gets is the player's rule, or a default a person chose,
# which the database keeps and no record holds. The rebuild writes isDefault false, and --compare never
# compares it, whatever --ignore-fields says.
BEHAVIOUR = ("isDefault",)


def moment_of(value):
    """A timestamp as an aware datetime — one without a zone read as UTC, the way the writer reads it —
    or None when it is not one."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        t = datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=datetime.timezone.utc)


def stamp(value):
    """A timestamp in one form and to the second, the precision an export prints, so one moment
    written two ways compares as one; anything else as it is."""
    t = moment_of(value)
    return utc(t) if t else value


def day(value):
    """A date as a record holds one: the date part of a timestamp; anything else as it is."""
    m = DAY_RE.match(value.strip()) if isinstance(value, str) else None
    return m.group(0) if m else value


def raw_of(a):
    """The bytes an export's image row carries, or b"" when it carries none it can decode."""
    try:
        return base64.b64decode(a.get("base64") or "", validate=False)
    except (ValueError, TypeError):
        return b""


def artwork_key(a):
    """An export carries the bytes and the tree carries their hash: comparing the hash compares the
    image itself, whichever side it came from. The bytes win over a hash the row records beside them,
    and a bare hex digest is the same hash as sha256:<hex>."""
    raw = raw_of(a)
    if raw:
        return "sha256:" + hashlib.sha256(raw).hexdigest()
    digest = str(a.get("sha256") or "").strip().lower()
    return ("sha256:" + digest if re.fullmatch(r"[0-9a-f]{64}", digest) else digest) or None


def normalise_artwork(a):
    """One image row as it can be compared: its bytes reduced to their hash, its fetchedAt to the
    second, and of the fields an image row gained later only those the row carries."""
    out = {"kind": a.get("kind"), "contentType": a.get("contentType"), "sha256": artwork_key(a),
           "fetchedAt": stamp(a.get("fetchedAt")),
           "sizeBytes": a.get("sizeBytes") if a.get("sizeBytes") is not None else len(raw_of(a)) or None}
    for key in CARRIED_ENTRY_FIELDS["artwork"]:
        if key in a:
            out[key] = bool(a[key]) if key == "isPrimary" else a[key]
    return out


def normalise(row):
    """A catalog row as it can be compared: the artwork reduced to its hash, its freshness in one form,
    nothing else changed."""
    out = dict(row)
    out["artwork"] = [normalise_artwork(a) for a in row.get("artwork") or [] if isinstance(a, dict)]
    if "tmdbFetchedAt" in out:
        out["tmdbFetchedAt"] = stamp(out["tmdbFetchedAt"])
    if "tmdbChangedAt" in out:
        out["tmdbChangedAt"] = day(out["tmdbChangedAt"])
    if isinstance(row.get("trailers"), list):
        # a downloaded trailer moved into its extra's folder, under the name the library gives an
        # original, so of its local copy only whether there is one can be compared: neither where it
        # lives nor the name it came with
        out["trailers"] = [dict(t, localPath="a local copy") if isinstance(t, dict) and t.get("localPath") else t
                           for t in row["trailers"]]
    return out


def instant(value):
    """A timestamp as an aware datetime, or None when it is not one."""
    try:
        t = datetime.datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else None


ITEM_TYPES = ("movie", "series", "episode")


def logged_as(entry):
    """What a deletion log entry deleted: 'item' — an entry of an item's type, or of none, as every
    entry of an export from before people were logged is — 'person', or None for a type the format
    does not know, which proves nothing."""
    kind = entry.get("type")
    return "item" if not kind or kind in ITEM_TYPES else "person" if kind == "person" else None


def deletion_log(export, of="item"):
    """The database's record of what it deleted — its items, or with of='person' its people — by id,
    or None when there is no log: the key is absent, or null because the catalog keeps no log yet. An
    empty list is a log that says nothing was deleted, which is not the same thing."""
    if export.get("deletedItems") is None:
        return None
    return {str(e["id"]): e for e in export["deletedItems"]
            if isinstance(e, dict) and e.get("id") and logged_as(e) == of}


def untyped_log(export):
    """Whether the deletion log holds entries and none of them says what it deleted: an export from
    before people were logged, which can name no person as deleted."""
    entries = [e for e in export.get("deletedItems") or [] if isinstance(e, dict)]
    return bool(entries) and not any(e.get("type") for e in entries)


def folder_newest(tree, newest):
    """The newest moment stated anywhere in each item's folder: a series folder holds its episodes,
    so they count for it."""
    out = dict(newest)
    for iid, row in tree.items():
        parent = row.get("parentId")
        if parent in out and newest.get(iid) and (out[parent] is None or instant(newest[iid]) > instant(out[parent])):
            out[parent] = newest[iid]
    return out


def existence(tree, db, log, newest):
    """Which items exist on one side only, and why. tree and db are rows keyed by id; log is the
    deletion log or None; newest is the newest moment each folder's records state. Returns
    {class: [(id, note)]} for orphan, lost and missing."""
    out = {"orphan": [], "lost": [], "missing": []}
    within = folder_newest(tree, newest)
    for iid in sorted(set(tree) - set(db)):
        row = tree[iid]
        if log is None:
            out["lost"].append((iid, "the export carries no deletion log, so nothing can be called deleted"))
            continue
        parent = row.get("parentId")
        entry = log.get(iid) or (log.get(parent) if parent and parent not in db else None)
        how = "deleted" if iid in log else "its series was deleted"
        deleted = instant((entry or {}).get("deletedAt"))
        # An episode deleted with its series shares the series folder's verdict: the deletion it
        # relies on is the series', and anything newer in that folder contradicts it.
        latest = within.get(iid) if iid in log else within.get(parent)
        if entry is None:
            out["lost"].append((iid, "not in the deletion log"))
        elif deleted is None:
            out["lost"].append((iid, f"{how}, but the log entry has no deletedAt to prove it"))
        elif latest and instant(latest) and instant(latest) > deleted:
            out["lost"].append((iid, f"{how} {entry['deletedAt']}, but its folder holds a record of {latest}: "
                                     f"created again since"))
        else:
            out["orphan"].append((iid, f"{how} {entry['deletedAt']} by {entry.get('deletedBy') or 'unknown'}"))
    out["missing"] = [(iid, "not on storage") for iid in sorted(set(db) - set(tree))]
    return out


CLASSES = (
    ("orphan", "orphan — on storage and in the deletion log, nothing newer: the database deleted it, safe to remove"),
    ("lost", "lost — on storage, not in the database, and no deletion explains it: restore candidates"),
    ("missing", "missing record — in the database, not on storage"),
)
PEOPLE_CLASSES = (
    ("orphan", "people: orphan — on storage and in the deletion log as a person, nothing newer: the database deleted them, "
               "safe to remove once no item record credits them"),
    ("unreferenced", "people: unreferenced — on storage, not in the database, nothing it holds credits them and the log does "
                     "not name them: kept, never swept"),
    ("lost", "people: lost — on storage, not in the database, and credited by an item on storage: restore candidates"),
    ("missing", "people: missing record — in the database, not on storage"),
)
FRESHNESS = (
    ("stale", "stale projection — the database changed after it was projected: project it again, never edit the file"),
    ("ahead", "projection ahead of the database — it reflects a later state of the row than the export holds: "
              "an export older than the tree, or a database restored from before it"),
)
PEOPLE_FRESHNESS = tuple((key, "people: " + label) for key, label in FRESHNESS)
LISTED = 200


def freshness(mine, theirs):
    """How the state of the row a projection reflects (mine, its databaseUpdatedAt) stands to the row
    the export holds (theirs, its modifiedAt): 'stale' when the row was modified after it, 'ahead' when
    the projection reflects a later state than the export holds, 'unknown' when the projection does
    not say, and None when they agree or the export holds no modification time. To the second, the
    precision an export prints."""
    d = moment_of(theirs)
    if d is None:
        return None
    t = moment_of(mine)
    if t is None:
        return "unknown"
    d, t = d.replace(microsecond=0), t.replace(microsecond=0)
    return "stale" if d > t else "ahead" if d < t else None


def judge(found, rid, mine, theirs):
    """File one row both sides hold under its freshness class. Returns True when its projection does
    not say which state it reflects, so its freshness is unknown."""
    v = freshness(mine.get("modifiedAt"), theirs.get("modifiedAt"))
    if v == "stale":
        found["stale"].append((rid, f"the row was modified {theirs['modifiedAt']}, after the {mine['modifiedAt']} "
                                    f"this projection reflects"))
    elif v == "ahead":
        found["ahead"].append((rid, f"this projection reflects {mine['modifiedAt']}, and the export's row was "
                                    f"modified {theirs['modifiedAt']}"))
    return v == "unknown"


def report_classes(lines, classes, labels, rows_of, what, counts):
    """Each class on its own, with every id. Returns how many of them count as differences: those
    counts(class, id) says count — of the missing records, those a subset is not expected to lack."""
    n = 0
    for key, label in labels:
        found = classes[key]
        if not found:
            continue
        counted = sum(1 for iid, _ in found if counts(key, iid))
        excused = len(found) - counted if key == "missing" else 0
        lines.append(f"  {label}: {len(found)} {what}"
                     + (f", {excused} of which a subset is expected to lack" if excused else ""))
        for iid, why in found[:LISTED]:
            title = (rows_of(iid) or {}).get("title") or (rows_of(iid) or {}).get("name")
            lines.append(f"      {iid} {title!r} — {why}")
        if len(found) > LISTED:
            lines.append(f"      … and {len(found) - LISTED} more")
        n += counted
    return n


def person_ids(v):
    """A person's reference ids in the form person.json keys them, from either form an export uses."""
    if isinstance(v, dict):
        # An export keeps every key of the shape and writes null for an id it does not know;
        # person.json leaves such a key out, so drop it here too or every gap reads as a difference.
        return {k: str(x) for k, x in v.items() if x not in (None, "")}
    out = {}
    for e in v or []:
        field = PERSON_ID_FIELD.get(str((e or {}).get("source") or "").lower())
        if field and (e or {}).get("externalId"):
            out[field] = str(e["externalId"])
    return out


def normalise_person(p, text_language):
    """A person as the export carries them, in the shape of a rebuilt person row, keeping only the
    fields the export carries: a field it does not carry is not compared."""
    out = {"id": str(p["id"])}
    for key in PERSON_FIELDS:
        if key not in p:
            continue
        v = p[key]
        if key == "biography" and not isinstance(v, dict):
            v = {text_language: v} if v else {}
        elif key == "externalIds":
            v = person_ids(v)
        elif key == "artwork":
            v = normalise({"artwork": v})["artwork"]
        elif key == "tmdbFetchedAt":
            v = stamp(v)
        elif key in ("birthDate", "deathDate", "tmdbChangedAt"):
            v = day(v)
        out[key] = v
    return out


def database_people(export, text_language):
    """The people the database holds: its own list when the export carries one, and otherwise
    everyone its items credit, by personId and name — all a catalog without person records knows."""
    if isinstance(export.get("people"), list):
        return {str(p["id"]): normalise_person(p, text_language)
                for p in export["people"] if isinstance(p, dict) and p.get("id")}
    out = {}
    for row in export.get("items") or []:
        for c in row.get("people") or []:
            if c.get("personId") and c["personId"] not in out:
                out[c["personId"]] = {"id": c["personId"], "name": c.get("name")}
    return out


def people_existence(tree_people, db_people, tree, item_classes, log=None, newest=None):
    """A person only on storage is an orphan when the deletion log names them as a person — the
    catalog deletes a person no title credits any more — and nothing in their folder is newer than
    that deletion; an item record on storage that still credits them is a note, and keeps the folder
    from the sweep. Otherwise a person only on storage is lost when an item on storage that is lost,
    or that the database holds, credits them, and unreferenced when nothing the database holds
    credits them: then nothing would restore them, and nothing sweeps them either. log is the
    people's deletion log or None; newest the newest moment each person folder states."""
    credited = {}
    for iid, row in tree.items():
        for c in row.get("people") or []:
            credited.setdefault(c.get("personId"), set()).add(iid)
    of = {key: {iid for iid, _ in found} for key, found in item_classes.items()}
    out = {"orphan": [], "unreferenced": [], "lost": [], "missing": []}
    for pid in sorted(set(tree_people) - set(db_people)):
        by = credited.get(pid, set())
        entry = (log or {}).get(pid)
        deleted = instant((entry or {}).get("deletedAt"))
        latest = (newest or {}).get(pid)
        logged = ""
        if entry is not None and deleted is None:
            logged = "; in the deletion log as a person, but the entry has no deletedAt to prove it"
        elif entry is not None and latest and instant(latest) and instant(latest) > deleted:
            logged = f"; deleted {entry['deletedAt']}, but their folder holds a record of {latest}: created again since"
        elif entry is not None:
            still = (f"; a note: {', '.join(sorted(by))} on storage still credits them, so the sweep keeps their "
                     f"folder until no item record does") if by else ""
            out["orphan"].append((pid, f"deleted {entry['deletedAt']} by {entry.get('deletedBy') or 'unknown'}{still}"))
            continue
        live = sorted(by - of["orphan"] - of["lost"])
        if by & of["lost"]:
            out["lost"].append((pid, f"credited by {sorted(by & of['lost'])[0]}, which is lost{logged}"))
        elif live:
            out["lost"].append((pid, f"credited by {live[0]}, which the database holds{logged}"))
        elif by:
            out["unreferenced"].append((pid, f"only items the database deleted credit them{logged}"))
        else:
            out["unreferenced"].append((pid, f"nothing on storage credits them{logged}"))
    out["missing"] = [(pid, "not on storage") for pid in sorted(set(db_people) - set(tree_people))]
    return out, credited


def outside_arrivals(row, arrivals):
    """An export's item row without what the record does not hold: the asset and subtitle rows of the
    files that wait at the arrivals — an original stays there until its package is recorded, and is no
    part of the record — and the row of an original that was retired (kind original)."""
    roots = [os.path.normpath(a) for a in arrivals]
    at = lambda p: any(os.path.normpath(str(p or "")) == r or os.path.normpath(str(p or "")).startswith(r + os.sep)
                       for r in roots)
    out = dict(row)
    if isinstance(row.get("playbackAssets"), list):
        out["playbackAssets"] = [a for a in row["playbackAssets"] if isinstance(a, dict)
                                 and a.get("kind") != "original" and not at(a.get("path"))]
    if isinstance(row.get("subtitleAssets"), list):
        out["subtitleAssets"] = [x for x in row["subtitleAssets"] if isinstance(x, dict) and not at(x.get("path"))]
    return out


def compare(tree_rows, tree_people, export, ignore, subset=False, text_language="und", newest=None, extras=(),
            people_newest=None, arrivals=()):
    """Both directions, field by field. Returns (lines, number of differences, number of orphans safe
    to remove). newest is the newest moment each item folder's records state, by item id, and
    people_newest each person folder's, by person id. extras are the tree's bonus material, which the
    catalog has no table for yet: they are counted, never compared. Behaviour is never compared."""
    ignore = set(ignore) | set(BEHAVIOUR)
    tree = {r["id"]: normalise(r) for r in tree_rows}
    db = {r["id"]: normalise(outside_arrivals(r, arrivals)) for r in export.get("items") or []}
    log, people_log = deletion_log(export), deletion_log(export, "person")
    lines = []
    if log is None:
        lines.append("  the export carries no deletion log (deletedItems is "
                     + ("null" if "deletedItems" in export else "absent")
                     + "), so nothing on storage can be called deleted")
    elif untyped_log(export):
        lines.append("  the deletion log's entries say nothing of what they deleted, as before people were logged: "
                     "every one is an item's, so no person on storage can be called deleted")
    if extras:
        lines.append(f"  extras: {len(extras)} on storage, not compared: the catalog has no table for them yet")
    classes = existence(tree, db, log, newest or {})
    again = sorted(iid for iid in set(log or {}) & set(db))
    n = report_classes(lines, classes, CLASSES, lambda iid: tree.get(iid) or db.get(iid), "item(s)",
                       lambda key, iid: key == "lost" or (key == "missing" and not subset))
    if again:
        lines.append(f"  in the deletion log but held by the database again: {len(again)} item(s), "
                     f"compared as the live items they are")
        lines += [f"      {iid}" for iid in again[:LISTED]]

    mine = {p["id"]: normalise(p) for p in tree_people}
    theirs = database_people(export, text_language)
    who, credited = people_existence(mine, theirs, tree, classes, people_log, people_newest)
    n += report_classes(lines, who, PEOPLE_CLASSES, lambda pid: mine.get(pid) or theirs.get(pid), "person(s)",
                        lambda key, pid: key == "lost" or (key == "missing" and (not subset or bool(credited.get(pid)))))
    back = sorted(pid for pid in set(people_log or {}) & set(theirs))
    if back:
        lines.append(f"  people in the deletion log but held by the database again: {len(back)} person(s), "
                     f"compared as the live people they are")
        lines += [f"      {pid}" for pid in back[:LISTED]]
    removable = len(classes["orphan"]) + sum(1 for pid, _ in who["orphan"] if not credited.get(pid))

    # how fresh each projection both sides hold is: the state of its row it reflects, against the row
    fresh, people_fresh, unknown = {"stale": [], "ahead": []}, {"stale": [], "ahead": []}, 0
    if "modifiedAt" not in ignore:
        unknown += sum(judge(fresh, iid, tree[iid], db[iid]) for iid in sorted(set(tree) & set(db)))
        unknown += sum(judge(people_fresh, pid, mine[pid], theirs[pid]) for pid in sorted(set(mine) & set(theirs)))
    n += report_classes(lines, fresh, FRESHNESS, lambda iid: tree.get(iid), "item(s)", lambda key, iid: True)
    n += report_classes(lines, people_fresh, PEOPLE_FRESHNESS, lambda pid: mine.get(pid), "person(s)",
                        lambda key, pid: True)
    if unknown:
        lines.append(f"  freshness unknown: {unknown} projection(s) do not say which state of their database row "
                     f"they reflect (no databaseUpdatedAt), so whether they are stale cannot be told")
    went_stale = {rid for rid, _ in fresh["stale"] + people_fresh["stale"]}

    fields = {}
    for iid in sorted(set(tree) & set(db)):
        a, b = tree[iid], db[iid]
        for key in sorted(set(a) | set(b)):
            if key in ignore or key == "modifiedAt" or (key in CARRIED_ITEM_FIELDS and key not in b):
                continue
            n += diff_field(fields, iid, key, a, b, ignore)
    for pid in sorted(set(mine) & set(theirs)):
        for key in sorted(k for k in theirs[pid] if k not in ("id", "modifiedAt") and k not in ignore):
            n += diff_field(fields, pid, key, mine[pid], theirs[pid], ignore, "people.")
    for key in sorted(fields):
        rows = fields[key]
        lines.append(f"  {key}: {len(rows)} difference(s)")
        for iid, a, b in rows[:5]:
            lines.append(f"      {iid}: storage {a!r} != database {b!r}" + (" — stale projection" if iid in went_stale else ""))
        if len(rows) > 5:
            lines.append(f"      … and {len(rows) - 5} more")
    return lines, n, removable


def diff_field(fields, iid, key, a, b, ignore, prefix=""):
    """One field of two rows: a list by the key of its entries, a set by its members, anything else
    by value — a person's externalIds among them, which is a map where an item's is a list. Returns
    the number of differences."""
    if key in LIST_KEYS and not isinstance(a.get(key), dict) and not isinstance(b.get(key), dict):
        return diff_list(fields, iid, key, a.get(key) or [], b.get(key) or [], ignore, prefix)
    if key in SET_FIELDS or (prefix and key in ("alsoKnownAs", "lockedFields")):
        not_here = sorted(set(b.get(key) or []) - set(a.get(key) or []))
        not_there = sorted(set(a.get(key) or []) - set(b.get(key) or []))
        fields.setdefault(prefix + key, []).extend([(iid, "not on storage", v) for v in not_here] +
                                                    [(iid, "not in the database", v) for v in not_there])
        if not fields[prefix + key]:
            del fields[prefix + key]
        return len(not_here) + len(not_there)
    if a.get(key) != b.get(key):
        fields.setdefault(prefix + key, []).append((iid, a.get(key), b.get(key)))
        return 1
    return 0


def diff_list(fields, iid, key, mine, theirs, ignore, prefix=""):
    keyer = LIST_KEYS[key]
    a = {keyer(x): x for x in mine}
    b = {keyer(x): x for x in theirs}
    n = 0
    for k in sorted(set(a) - set(b), key=str):
        fields.setdefault(prefix + key, []).append((iid, a[k], "missing"))
        n += 1
    for k in sorted(set(b) - set(a), key=str):
        fields.setdefault(prefix + key, []).append((iid, "missing", b[k]))
        n += 1
    carried = CARRIED_ENTRY_FIELDS.get(key, ())
    for k in sorted(set(a) & set(b), key=str):
        for f in sorted(set(a[k]) | set(b[k])):
            if f in ignore or (f in carried and f not in b[k]):
                continue
            if a[k].get(f) != b[k].get(f):
                fields.setdefault(f"{prefix}{key}.{f}", []).append((iid, a[k].get(f), b[k].get(f)))
                n += 1
    return n


def main():
    ap = argparse.ArgumentParser(prog="library-v2-rebuild.py",
                                 description="Produce the catalog contents a v2 tree implies.")
    ap.add_argument("root", help="the library root holding movies/, series/ and people/")
    ap.add_argument("--out", help="write the rows here as JSON; stdout gets the summary either way")
    ap.add_argument("--compare", help="a catalog export to report against, in both directions")
    ap.add_argument("--text-language", default="und",
                    help="the localized title the description and tagline are read from")
    ap.add_argument("--ignore-fields", default="id,path,hash",
                    help="fields that cannot agree by construction; comma-separated. A subtitle's isDefault is "
                         "never compared: it is behaviour, which no record holds")
    ap.add_argument("--arrivals-root", action="append", default=[],
                    help="where the platform keeps the files that are not the record, e.g. <root>/.work: the "
                         "export's asset and subtitle rows of files there are not compared; repeatable")
    ap.add_argument("--subset", action="store_true",
                    help="the tree holds only some of the database's items: rows only the database "
                         "has are reported but are not a difference")
    args = ap.parse_args()

    r = Rebuild(os.path.abspath(args.root), args.text_language)
    r.run()
    out = {"generatedAt": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
           .isoformat().replace("+00:00", "Z"),
           "root": r.root, "items": r.items, "people": r.people, "extras": r.extras, "storage": r.storage,
           "notes": r.notes}
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
            f.write("\n")
    kinds = {}
    for row in r.items:
        kinds[row["type"]] = kinds.get(row["type"], 0) + 1
    print(f"rebuilt: {kinds}, people: {len(r.people)}, extras: {len(r.extras)}, "
          f"playback assets: {sum(len(x['playbackAssets']) for x in r.items)}, "
          f"subtitles: {sum(len(x['subtitleAssets']) for x in r.items)}, "
          f"images: {sum(len(x['artwork']) for x in r.items) + sum(len(p['artwork']) for p in r.people)}")
    for n in r.notes:
        print("  note " + n)
    if not args.compare:
        return 0
    ignore = {x.strip() for x in args.ignore_fields.split(",") if x.strip()} | set(BEHAVIOUR)
    with open(args.compare, encoding="utf-8") as f:
        export = json.load(f)
    lines, n, orphans = compare(r.items, r.people, export, ignore, args.subset, args.text_language, r.newest,
                                extras=r.extras, people_newest=r.people_newest, arrivals=args.arrivals_root)
    print(f"compared with {args.compare} (ignoring {', '.join(sorted(ignore)) or 'nothing'}):")
    for line in lines:
        print(line)
    tail = f"; {orphans} orphan(s) on storage are safe to remove" if orphans else ""
    print(("the tree and the database agree" if not n else f"{n} difference(s)") + tail)
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
