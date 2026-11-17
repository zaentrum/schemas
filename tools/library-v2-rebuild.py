#!/usr/bin/env python3
"""Read a v2 library tree and produce the catalog contents it implies.

Usage:
  library-v2-rebuild.py LIBRARY [--out rows.json] [--compare CATALOG.json [--subset] [--arrivals-root DIR]]
                        [--shard aa] [--text-language LANG] [--ignore-fields a,b,c] [--leave-out FILE]

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

A file that holds several episodes is its holder's, and the rows say so the way the catalog does: an
episode another episode's source covers (source.json covers, the holder first) is covered by that
holder, its row's coveredBy the holder's id, and every other row's is null. The record restores the
link — the source is written once, and says what the file holds — and the covered episode's own
projection (library.coveredBy) only where the holder's file has no source recorded yet, so that no
record contradicts it. A covered episode has no playback rows of its own: it plays its holder's.

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
and its coveredBy, and an image row's dimensions, primary flag and TMDB path, are compared where the
export carries them: an export from before them — from before the catalog linked covered episodes,
for coveredBy — makes no tree that has them differ. So are a credit's job,
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

An item's chapters and segments are the database's as its projection holds them (metadata.json
marks), which follow every change of them; a projection written before it held them leaves the marks
of the version that plays, the ones it was made with. A text is compared as a record keeps it: on one
line where it is a line (a title, a name, a label, a chapter's title), without the whitespace around
it, none when nothing is left — the writers of records keep it so — so " Chapter 1" and "Chapter 1"
are one text, and an empty trailer title is none.

A person's tmdbFetchedAt and tmdbChangedAt are never compared either: when the catalog last asked its
source about a person, and the day of the change it last saw there, move every time it asks, and a
person no value of whom changed is not projected again — the row's modifiedAt does not move. A
database rebuilt from the tree asks again sooner, and has lost nothing.

A subtitle row's isDefault is never compared, whatever --ignore-fields says, and the rebuild writes
it false: which subtitle a viewer gets is behaviour — the player's rule, or a default a person
chose and the database keeps — which no record holds; a package's default is only what its playlist
says. A database rebuilt from the tree has lost the defaults people chose.

A subtitle attached to a title after its package completed — a file in the subs/ of the version it
was attached to, named by its bytes, attached-<sha256>.<language>[.forced][.sdh].vtt, which no
record lists — is a subtitle row of its own, rebuilt from what its name and its folder say: its
language, cased as a BCP 47 tag is (zh-Hans, pt-BR), its format, webvtt, whether it is for deaf and
hard-of-hearing viewers (isSdh), the version it follows (versionId) and its hash (sha256), and never
the default. A title whose file was taken in before anything packaged it has its subtitles attached
to that version, which has no package, and their rows are rebuilt the same way while no package of
the title plays: once one does, the catalog's attached subtitles are that version's, or carried over
to it. What no record holds is the catalog's alone, and the row leaves it empty: how it was
made (origin: source, provider, transcribed, translated or converted in the catalog, null here), the
label it was given, who attached it and when (attachedBy, attachedAt). A database rebuilt from the
tree tells an attached subtitle by its versionId and sha256, and has lost the rest. Every other
subtitle row says what the catalog says of a title's own: isSdh false, and none of the others.
--compare compares the attached rows apart, as attachedSubtitles — an export's row is one when its
file is named and placed as one, or it has an origin — matched by the version and the file's name,
in their format and language and, where the export carries them, isSdh, versionId and sha256; never
in what no record holds. The other subtitle rows are compared as they were, the fields the catalog
gained with attached subtitles only where the export carries them.

A styled subtitle's row says the script its package keeps beside the subtitle's file, as
katalog-manager keeps it (its migration 053): styledPath and styledFormat (ass), none of every other
subtitle row, and of a script whose path leaves the package. An item row's fontAssets are the fonts of
the package that plays, in their order: each by the name-based id katalog-manager gives it — of the
title, the version and its place — with its versionId, path, format (none for bytes that are none of
the formats), the names a script asks for it by, its place (ordinal, from 0) and the attachment it was
taken from (sourceStreamIndex); a font whose path leaves the package keeps its place empty. --compare
compares them only where the export carries them, the script's path only where paths are compared —
ignoring path ignores styledPath — and a font by its place.

--subset reports the rows only the database has without counting them, for a tree that was built
from part of a catalog.

--shard aa reads one shard of the tree — movies/aa/ and series/aa/, with their episodes, and the
people their items credit — which is what an export of that shard holds (psql -v shard=aa): a
catalog too large for one export is compared shard by shard.

The arrivals are not the record. A file waits there — in <root>/.work/incoming on the platform — until
the first record of its title is made: an original until its version folder takes it in, a subtitle
file that came with it until the original is deleted, an extra's original until its package is
recorded. The export's rows of the files there are no rows the tree can give back: --arrivals-root
names that folder, and the asset and subtitle rows of files under it are not compared. Neither is
ever the row of an original that was retired (kind original). An original in its version folder is
the record, and its row is compared. A subtitle the package made from a sidecar is the catalog's row
of that sidecar while the original is there — the row at the arrivals, not compared — and the
rebuild gives it no row of its own until an event deletes the original, from whichever version folder
kept it; then it is a row of the package's subtitle in the sidecar's language, and a sidecar nothing
was made from is a row of the copy its source folder keeps.
"""
import argparse, base64, datetime, glob, hashlib, json, os, re, sys, uuid

NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://zaentrum.github.io/schemas/library")
OS_ARTEFACTS = re.compile(r"^(\.DS_Store|\._.*|Thumbs\.db|desktop\.ini|@eaDir|\.@__thumb|#recycle|\.AppleDouble)$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


# ==== the deletion gate, shared ====
# From here to the end of this block the text is the same, byte for byte, in tools/libv2_records.py,
# tools/validate-library-v2.py and tools/library-v2-rebuild.py: every reader computes the gate with it, and
# test-library-v2-tools.py holds the three copies equal. It needs nothing but the modules json and re.
#
# A picture's closed captions are read in windows, by ONE plan for every reader: library/v2/caption-windows.json,
# whose bytes are CAPTION_PLAN_JSON here, held to the file by test-library-v2-tools.py and to its pinned sha256
# by every repository that vendors it. It is data the code checks itself against, not configuration: nothing
# reads it at run time from a path a setting could change. The plan's span is what the gate knows without
# reading (plan_of: the stored probe's picture start_time + duration); the start, then 10, 30, 50, 70 and 90 %
# of it, each but the start at most CAPTION_TAIL_S short of its end (plan_places), CAPTION_PACKETS packets
# each, at least CAPTION_MIN_FRAMES of them decoded (half, and 24 at least), each starting at its place by
# the plan's slack (window_lands: max(10 s, gop + 0.5 s), up to 30 s before it where no keyframe lies between,
# of a gop of 0 within the tolerance alone). A read whose span is not the plan's within the tolerance
# (caption_tolerance: a frame step and 0.5 s) tells nothing. Where two places lie closer than a window reaches (plan_short), the picture is read
# whole (a window that says so, whole, and counts its packets), and tells none only where every packet of it
# was decoded (windows_tell_none).
CAPTION_PLAN_JSON = r'''{
  "schema": "zaentrum.library.caption-windows/1",
  "note": "The plan every reader of an original's closed captions reads its windows by, and the data it checks itself against: not configuration. tools/libv2_records.py carries these bytes in its code and its tests hold them to this file; every other reader vendors this file, pinned by its sha256, and a copy that differs fails its build. No installation edits it, and nothing reads it from a path a setting could change.",
  "start": true,
  "at": [0.1, 0.3, 0.5, 0.7, 0.9],
  "span": "picture",
  "picture": "the stored probe's film: its one video stream that is no cover by the closed lists' rule, a cover being all of these - marked as an attachment (the attached_pic disposition, or both a filename and a mimetype tag, never one alone), of a cover's codec (mjpeg, png, bmp; its mimetype, where it has one, image/*, and never in the codec's stead), of at most one frame (nb_frames, NUMBER_OF_FRAMES) and of no length of its own (no DURATION tag) -, wherever a cover lies; any other video stream may be the picture: where none is, or two could each be it, there is no plan",
  "end": "the stored probe's picture start_time + duration; the format's start_time where the picture gives none; where it gives no duration, the record's container.durationMs, and the format's duration only where the record gives none",
  "frameStep": "one over the stored probe's picture avg_frame_rate, or its r_frame_rate where avg_frame_rate is 0/0: what the gate can know without reading; where the probe states no rate there is no plan, and the read is not known; no reader measures a frame step of its own, which the gate could not check",
  "endTolerance": {"frameSteps": 1, "s": 0.5, "words": "frameStep + 0.5: one frame step of the picture plus 0.5 s, the same on every side: both the start and the end of a read's measured span, and both the first frame and the end of a whole read, lie within it of the plan's span"},
  "tailS": 30,
  "startCapped": false,
  "packets": 48,
  "minFrames": 24,
  "packetsShare": 0.5,
  "gopPackets": 1200,
  "placeSlack": {"s": 10, "gopPlus": 0.5, "backS": 30, "words": "a window's first frame lies within max(10 s, gop + 0.5 s) of its place, before or after it; or up to backS before it where the packets from its seek show no keyframe between its first frame and its place (keyframeBefore); of a gop of 0 a window counts only at its own place, within endTolerance"},
  "shortReach": {"packets": 48, "gop": true, "s": 0.5, "words": "packets x frameStep + gop + 0.5"},
  "short": "whole",
  "wholeLeadingMax": 16,
  "wholeComplete": "packets - leading <= frames <= packets: leading the stream's own leading pictures, the packets before its first keyframe in decode order and the first gop's packets timed before that keyframe, at most wholeLeadingMax; a decoder that outputs the leading pictures too is complete as well; a read that gives no leading is not complete",
  "bounds": {"threads": 1, "addressSpaceMiB": 1280, "cpuS": 600, "timeoutS": 180},
  "rule": "The picture is the stored probe's film by the closed lists' cover rule (picture); where two streams could each be it there is no plan. The plan's span is what the gate knows without reading: from the stored probe's picture start_time (the format's where the picture gives none) to that plus its duration (where the picture gives none, the record's container.durationMs, and the format's duration only where the record gives none); frameStep is one over the stored probe's picture avg_frame_rate, or its r_frame_rate where avg_frame_rate is 0/0; where the probe states no rate there is no plan, and the read is not known. A read places six windows on it: the start, at the start itself, never capped by the tail; then each of at, at start + at x (end - start), at most tailS short of the end and never before the start - never half the span or any other share of it. A reader measures the end of the picture's last video packet, and the first frame of its start: a read whose measured span does not lie with both its start and its end within endTolerance of the plan's start and end fails, and tells nothing; it reports its span beside the file's length (duration), and a validator holds the span a read reports to its record's plan within endTolerance. gop is the longest run between two keyframes among the picture's first gopPackets packets, at frameStep, and 0 where they list fewer than two keyframes; a read reports it. Each window decodes packets packets from the keyframe its seek lands on, and counts only where it decoded at least packetsShare of them and at least minFrames frames, and its first frame lies at its place by placeSlack. The six tell none only where all six are such windows, one at each place, none shows captions, and their spans, in the order of their places, are disjoint and rise: a file whose gop is 0 never tells none of windows that overlap or don't start at their own places. Where two of the places lie closer than shortReach (packets x frameStep + gop + 0.5 s), as on a span too short for six disjoint windows at most tailS short of its end, the read is the whole picture, every packet decoded: one window at 0, whole: true, with its frames, its packets and its leading pictures (leading) counted. It stands for the six, and tells none only where it is complete by wholeComplete - it decoded every packet but the stream's own leading pictures, the packets before its first keyframe in decode order and the first gop's packets timed before that keyframe, at most wholeLeadingMax, or those too -, none shows captions, and its first frame and its end lie within endTolerance of the plan's start and end. A record tells the two apart by their shape: six windows, or one whole window alone; a picture whose places lie closer than shortReach tells none by a whole read alone, and a whole read of a picture long enough for six windows is accepted too: it is the stronger proof. Every read, a window's, the keyframe listings, the end's and the short picture's whole read alike, runs under the same bounds, on every side: threads decoding threads, addressSpaceMiB of address space, cpuS of CPU time and timeoutS seconds of wall time for each read; a bound that is hit means not known, never none. The read of every packet of an HEVC picture for a caption signature, which is deferred, is not bound by these: it gets a bound of its own once its form is fixed. Neither a whole read nor windows say none of a picture whose packets carry captions its decoder does not export (HEVC's suffix SEI) unless every packet of it was read for a caption signature and none carries one. Any other read is not known."
}
'''
CAPTION_PLAN = json.loads(CAPTION_PLAN_JSON)
CAPTION_PACKETS = CAPTION_PLAN["packets"]
CAPTION_WINDOWS = (0.0, *CAPTION_PLAN["at"]) if CAPTION_PLAN["start"] else tuple(CAPTION_PLAN["at"])
CAPTION_TAIL_S = float(CAPTION_PLAN["tailS"])
CAPTION_GOP_PACKETS = CAPTION_PLAN["gopPackets"]
CAPTION_MIN_FRAMES = max(CAPTION_PLAN["minFrames"], -(-CAPTION_PACKETS * CAPTION_PLAN["packetsShare"] // 1))
CAPTION_BOUNDS = CAPTION_PLAN["bounds"]
# The closed lists of how closed captions and fields are carried, kept the same as the analyzer's, by its
# names: the containers (CONTAINERS, ffprobe's format_name); the codecs of the film's picture (VIDEO_CODECS),
# of them those whose packets carry A53 the decoder does not export (SCANNED_CODECS: HEVC's suffix SEI); one
# film, beside attached pictures (an image the container marks as an attached file, of no length of its own:
# COVER_CODECS); streams of captions of their own (CAPTION_CODECS, CAPTION_TAGS); the subtitle streams
# (SUBTITLE_CODECS) and data streams (DATA_CODECS, DATA_TAGS) that carry none; audio and attachments of any
# kind; and no picture of an analog capture's height, whose blanking lines keep line 21 (VBI_HEIGHTS). A file
# with anything off them is one whose captions and fields can't be told (off_list, in the analyzer's offList
# names: container:flv, stream:data:bin_data, video-streams:2, codec:vc1, picture:vbi-lines), and the gate
# keeps it.
CONTAINERS = ("matroska,webm", "mov,mp4,m4a,3gp,3g2,mj2", "mpegts", "mpeg", "avi")
VIDEO_CODECS = ("h264", "hevc", "mpeg1video", "mpeg2video", "av1")
SCANNED_CODECS = ("hevc",)
COVER_CODECS = ("mjpeg", "png", "bmp")
CAPTION_CODECS = ("eia_608",)
CAPTION_TAGS = ("c608", "c708")
SUBTITLE_CODECS = ("subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text", "microdvd", "hdmv_pgs_subtitle",
                   "dvd_subtitle", "dvb_subtitle", "dvb_teletext")
DATA_CODECS = ("scte_35", "timed_id3", "epg")
DATA_TAGS = ("tmcd",)
# And an MP4's chapter track (chapter_track), by this rule, the same words in the analyzer, the record logic and
# the waves' check: a data stream is the file's chapter track, and on the list, only where all of these hold in
# the stored probe: the container is the MP4 family (mov,mp4,m4a,3gp,3g2,mj2); the stream is codec_type data,
# codec_name bin_data, codec_tag_string text; the file has chapters; the stream's sample count (nb_frames)
# equals the number of chapters; and it is the file's only such stream. A probe that lacks the tag or the
# count leaves it off the list. A package loses no chapters by it: they come from the catalog's marks, the
# build hands them to the packager, and the version keeps them.
CHAPTER_CONTAINER = "mov,mp4,m4a,3gp,3g2,mj2"
CHAPTER_CODEC, CHAPTER_TAG = "bin_data", "text"
VBI_HEIGHTS = (486, 512, 608)
# The one word, of a picture's field order (ffprobe's field_order) and of a scan's found, that says a picture
# is whole frames, as it is written. Every other — tt, bb, tb, bt, unknown, a word not known here, the same
# word written otherwise, and no field order at all, which is how ffprobe's JSON gives an unknown one — says
# it may be in fields.
WHOLE_FRAMES = "progressive"
# What an original carries that a package can fail to carry, in the terms an original-deleted event accepts.
# 'chapters' is not here (the version keeps the marks, not the file); 'closedCaptions' and 'interlaced' are
# the gate's own rules, which no event accepts (deletion_gate).
LOSS_FLAGS = ("surround", "losslessAudio", "objectAudio", "hdr10Metadata", "dolbyVision", "stereo3d",
              "imageSubtitles", "styledSubtitles", "fonts", "closedCaptions")
LOSS_COUNTS = ("maxAudioChannels", "maxVideoHeight", "videoBitDepth", "subtitleTracks",
               "commentaryTracks", "commentarySubtitles", "audioDescriptionTracks")
LOSS_LANGUAGES = ("audioLanguages", "subtitleLanguages", "sdhSubtitleLanguages", "forcedSubtitleLanguages")


def gate_number(v):
    """v where it is a number (no boolean, no text), as a float, else None."""
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def gate_count(v, top=None):
    """v where it is a whole number from 0 (to top, where given), else None."""
    if not isinstance(v, int) or isinstance(v, bool) or v < 0 or (top is not None and v > top):
        return None
    return v


def window_of(w):
    """One entry of a picture's or a read's caption windows as every reader reads it: (window, sound). sound: it
    is a window — an object whose at is a number from 0 to 1, start and end numbers, end not before start,
    frames a whole number from 0, captions a boolean (null too, of one that decoded no frame; never true of
    one), and interlacedFrames and repeatFrames, where it gives them, whole numbers from 0 to frames; a read of
    the whole picture (whole true) at 0, its packets (packets) a whole number from frames, its leading pictures
    (leading), where it gives them, a whole number from 0 to wholeLeadingMax; keyframeBefore, where
    it gives it, a boolean (the packets from its seek show no keyframe between its first frame and its place).
    window: the window as
    a record keeps it; of an entry that is no window, a failed one (frames 0, captions false, at its fraction
    where it has one), never left out."""
    d = w if isinstance(w, dict) else {}
    at, start, end = gate_number(d.get("at")), gate_number(d.get("start")), gate_number(d.get("end"))
    frames, captions = gate_count(d.get("frames")), d.get("captions")
    counts = {k: d[k] for k in ("interlacedFrames", "repeatFrames") if k in d}
    whole = {k: d[k] for k in ("whole", "packets", "leading") if k in d}
    keyed = {"keyframeBefore": True} if d.get("keyframeBefore") is True else {}
    sound = (isinstance(w, dict) and at is not None and 0 <= at <= 1 and start is not None and end is not None
             and end >= start and frames is not None
             and (isinstance(captions, bool) and not (frames == 0 and captions) or captions is None and frames == 0)
             and all(gate_count(v, frames) is not None for v in counts.values())
             and ("keyframeBefore" not in d or isinstance(d["keyframeBefore"], bool))
             and (not whole or whole.get("whole") is True and at == 0 and gate_count(whole.get("packets")) is not None
                  and whole["packets"] >= frames
                  and ("leading" not in whole or gate_count(whole["leading"], CAPTION_PLAN["wholeLeadingMax"]) is not None)))
    if not sound:
        a = at if at is not None and 0 <= at <= 1 else 0.0
        p = start if start is not None else 0.0
        return {"at": round(a, 3), "start": round(p, 3), "end": round(p, 3), "frames": 0, "captions": False}, False
    return ({"at": round(at, 3), "start": round(start, 3), "end": round(end, 3), "frames": frames,
             "captions": captions is True, **counts, **whole, **keyed}, True)


def caption_windows_of(windows):
    """The caption windows a manifest, a reading or a record gives, as a record keeps them (window_of), in their
    order, every entry: one that is no window is a failed one. None for anything that is no list."""
    return [window_of(w)[0] for w in windows] if isinstance(windows, list) else None


def windows_show(windows):
    """Whether a window of windows (as given) is one that decoded frames and shows captions."""
    return isinstance(windows, list) and any(s and w["captions"] for w, s in map(window_of, windows))


def gate_seconds(v):
    """A time or a length as ffprobe gives one ('12.345000') or a record keeps one (a number), as seconds, or
    None."""
    if isinstance(v, bool):
        return None
    try:
        t = float(v)
    except (TypeError, ValueError):
        return None
    return t if t == t and abs(t) != float("inf") else None


def frame_step_of(stream):
    """One over the frame rate of a probe's picture stream (the plan's frameStep: avg_frame_rate, or r_frame_rate
    where that is 0/0, as 'num/den'), in seconds, or None where neither gives one."""
    for key in ("avg_frame_rate", "r_frame_rate"):
        m = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", str((stream or {}).get(key) or ""))
        if m and int(m.group(1)) > 0 and int(m.group(2)) > 0:
            return int(m.group(2)) / int(m.group(1))
    return None


def plan_of(probe, duration_ms=None):
    """The plan's span of the picture of a stored probe, as the gate knows it without reading: {start, end, step},
    start its picture's start_time (the format's where the picture gives none), end that plus its duration -
    where the picture gives none, the record's container.durationMs (duration_ms), and the format's duration
    only where the record gives none -, step its frame step (frame_step_of); its picture the probe's film
    (film_of). None where the probe is no probe, has no film or more than one, or gives no start, length or
    frame rate: where the probe states no rate there is no plan,
    and no reader measures one of its own, which the gate could not check."""
    if not isinstance(probe, dict) or not isinstance(probe.get("streams"), list):
        return None
    fmt = probe.get("format") if isinstance(probe.get("format"), dict) else {}
    picture = film_of(probe)
    if picture is None:
        return None
    start = gate_seconds(picture.get("start_time"))
    if start is None:
        start = gate_seconds(fmt.get("start_time"))
    length = gate_seconds(picture.get("duration"))
    if length is None or length <= 0:
        ms = gate_number(duration_ms)
        length = ms / 1000 if ms is not None and ms > 0 else gate_seconds(fmt.get("duration"))
    step = frame_step_of(picture)
    if start is None or length is None or length <= 0 or step is None:
        return None
    return {"start": start, "end": start + length, "step": step}


def film_of(probe):
    """The film of a probe: its one video stream that is no cover by the closed lists' rule (attached_picture:
    the attachment marks, an image, no length of its own), wherever a cover lies among its streams; None where
    there is none, or where two streams could each be it."""
    films = [x for x in (probe or {}).get("streams") or [] if isinstance(x, dict) and x.get("codec_type") == "video"
             and not attached_picture(x)]
    return films[0] if len(films) == 1 else None


def caption_tolerance(plan):
    """How far a read's span may lie from the plan's, at either end, and a window's first frame after its place:
    the plan's endTolerance, a frame step and 0.5 s."""
    t = CAPTION_PLAN["endTolerance"]
    return t["frameSteps"] * plan["step"] + t["s"]


def window_lands(start, place, gop, plan, keyed=False):
    """Whether a window whose first frame is at start lies at its place (seconds) by the plan's slack
    (placeSlack): within max(10 s, gop + 0.5 s) of it, before or after; or up to 30 s before it where the packets
    from its seek show no keyframe between its first frame and its place (keyed, its keyframeBefore). Of a gop
    of 0 - fewer than two keyframes listed - only at its own place, within the tolerance (caption_tolerance)."""
    if not gop:
        return abs(start - place) <= caption_tolerance(plan)
    slack = CAPTION_PLAN["placeSlack"]
    return abs(start - place) <= max(slack["s"], gop + slack["gopPlus"]) \
        or keyed is True and place - slack["backS"] <= start <= place


def plan_places(plan):
    """Where the plan's windows lie on the plan's span (plan_of), in seconds, one for each of CAPTION_WINDOWS:
    the start at the start itself, never capped by the tail; each other at start + at x (end - start), at most
    CAPTION_TAIL_S short of the end and never before the start."""
    start, end = plan["start"], plan["end"]
    return [start if at == 0 else max(start, min(start + at * (end - start), end - CAPTION_TAIL_S))
            for at in CAPTION_WINDOWS]


def plan_short(plan, gop):
    """Whether the plan's windows can't be read on frames of their own: two of its places (plan_places) lie
    closer than a window reaches, its packets at the frame step from a keyframe up to gop before its place, and
    0.5 s (the plan's shortReach). Such a picture is read whole."""
    r = CAPTION_PLAN["shortReach"]
    reach = r["packets"] * plan["step"] + (gop if r["gop"] else 0.0) + r["s"]
    places = sorted(plan_places(plan))
    return any(b - a < reach for a, b in zip(places, places[1:]))


def whole_complete(w):
    """Whether a read of the whole picture (a window as window_of keeps it) is complete by the plan (wholeComplete):
    it decoded at least every packet but the stream's own leading pictures (leading: the packets before its first
    keyframe in decode order and the first gop's packets timed before it, at most wholeLeadingMax) and no more than
    its packets - a decoder that outputs the leading pictures too is complete as well. One that gives no leading is
    not complete."""
    leading = gate_count(w.get("leading"), CAPTION_PLAN["wholeLeadingMax"])
    return leading is not None and w["frames"] > 0 and w["packets"] - leading <= w["frames"] <= w["packets"]


def caption_read_of(picture):
    """A record's picture's caption read, as windows_tell_none reads one: its windows (captionWindows), the span
    the read measured (captionSpan) and the gop it found (captionGop)."""
    picture = picture if isinstance(picture, dict) else {}
    return {"windows": picture.get("captionWindows"), "span": picture.get("captionSpan"), "gop": picture.get("captionGop")}


def windows_tell_none(read, plan):
    """Whether a read (a record's picture's, caption_read_of, or a deletion's captionsRead: {windows, span, gop})
    tells that its picture carries no closed captions, by the plan (CAPTION_PLAN) over the plan's span the gate
    knows (plan_of): every entry of its windows is a window and none shows captions; the span it measured is the
    plan's within the tolerance at either end (caption_tolerance); its gop a number from 0 (0 where it gives
    none); and its windows are the plan's six - one at each of CAPTION_WINDOWS, each of at least
    CAPTION_MIN_FRAMES frames (half its CAPTION_PACKETS packets, and 24 at least), its first frame at its place
    (plan_places) by the plan's slack (window_lands), their spans, in the order of their places, disjoint
    and rising, of a picture whose places are not too close (plan_short) -, or the one read of the whole picture
    that is complete (whole_complete: every packet decoded but the stream's own leading pictures) over the plan's
    span within the tolerance.
    Anything else - no plan, a failed or malformed window, one too few (a read of five, without the start), one
    of fewer frames than that, elsewhere than its place, two on the same frames, six of a picture too
    short for them, a span that is not the plan's, a whole read that missed a packet or gives no leading - is not
    known: of a gop of 0 none that overlap or don't start at their places."""
    if plan is None or not isinstance(read, dict):
        return False
    windows = read.get("windows")
    if not isinstance(windows, list) or not windows:
        return False
    checked = [window_of(w) for w in windows]
    if not all(s for _, s in checked):
        return False
    ws = sorted((w for w, _ in checked), key=lambda w: w["at"])
    if any(w["captions"] for w in ws):
        return False
    tol = caption_tolerance(plan)
    span = read.get("span") if isinstance(read.get("span"), dict) else {}
    first, last = gate_number(span.get("start")), gate_number(span.get("end"))
    if first is None or last is None or abs(first - plan["start"]) > tol or abs(last - plan["end"]) > tol:
        return False
    gop = 0.0 if read.get("gop") is None else gate_number(read.get("gop"))
    if gop is None or gop < 0:
        return False
    if any(w.get("whole") for w in ws):
        w = ws[0]
        return (len(ws) == 1 and whole_complete(w)
                and abs(w["start"] - plan["start"]) <= tol and abs(w["end"] - plan["end"]) <= tol)
    if plan_short(plan, gop) or len(ws) != len(CAPTION_WINDOWS):
        return False
    for w, at, place in zip(ws, CAPTION_WINDOWS, plan_places(plan)):
        if abs(w["at"] - at) > 1e-3 or w["frames"] < CAPTION_MIN_FRAMES \
                or not window_lands(w["start"], place, gop, plan, w.get("keyframeBefore")):
            return False
    return all(b["start"] >= a["end"] and b["start"] > a["start"] for a, b in zip(ws, ws[1:]))


def windows_count_fields(windows):
    """Whether windows (as given) put their picture in fields: a window counted interlaced frames, or says a
    count it can't be read for (no whole number from 0 to its frames, or one of an entry that is no window);
    one that is no list, of a read that has no windows, too."""
    if not isinstance(windows, list):
        return True
    for w in windows:
        if not isinstance(w, dict):
            continue
        frames = gate_count(w.get("frames"))
        for key in ("interlacedFrames", "repeatFrames"):
            if key in w and gate_count(w[key], frames) is None:
                return True
        if gate_count(w.get("interlacedFrames"), frames):
            return True
    return False


def picture_of(streams):
    """The picture of a record's streams: its first video stream that is no attached picture, or None."""
    return next((x for x in streams or [] if isinstance(x, dict) and x.get("type") == "video"
                 and not (x.get("dispositions") or {}).get("attachedPic")), None)


def captions_known(streams, off, plan):
    """Whether a source's essence knows its closed captions, of its record's streams, what of its file is off
    the closed lists (off) and the plan's span of its stored probe (plan_of): a stream that is closed captions
    of its own, or its picture's caption signature, says them; or its picture's caption windows show them; or
    its picture's read tells none (windows_tell_none, by the plan), of a file on the lists (off empty), and of a
    picture whose packets carry A53 its decoder does not export (SCANNED_CODECS) only where every packet of it
    was read and none carries a caption signature."""
    picture = picture_of(streams) or {}
    if any(x.get("closedCaptions") is True for x in streams or [] if isinstance(x, dict)
           and x.get("type") in ("subtitle", "data")) or picture.get("captionSignature") is True:
        return True
    if windows_show(picture.get("captionWindows")):
        return True
    return (windows_tell_none(caption_read_of(picture), plan) and not off
            and (picture.get("codec") not in SCANNED_CODECS or picture.get("captionSignature") is False))


def field_order_word(order):
    """A field order as in_fields reads it: as written, and unknown where none is given."""
    return order if isinstance(order, str) and order else "unknown"


def read_through(verified):
    """Whether a scan's read-through of the whole title (verified) bore out whole frames and read all of it: ok
    and complete true, and its counts adding up - it read every packet of the stream but its leading pictures,
    frames = packets - leading, no other slack. One without leading (a scan from before that rule), or without
    packets, is never complete."""
    if not isinstance(verified, dict) or verified.get("ok") is not True or verified.get("complete") is not True:
        return False
    frames, packets, leading = (gate_count(verified.get(k)) for k in ("frames", "packets", "leading"))
    return None not in (frames, packets, leading) and frames == packets - leading


def scan_holds(pkg):
    """Why the scan of the package record pkg keeps its title's originals in fields, or None where it says
    nothing (a package without a scan) or lets them go the ordinary way: one whose found is exactly progressive,
    whose read-through of the whole title is ok and complete (read_through), and whose done is progressive or
    none. A record that is no object, a scan that is no object, any other found (unknown, blank, not text, a
    word not known here, progressive without its read-through) and any other done keep them."""
    if not isinstance(pkg, dict):
        return "a package record of the title can't be read"
    scan = pkg.get("scan")
    if scan is None:
        return None
    if not isinstance(scan, dict):
        return "the scan of a package of the title is no object"
    found, done, verified = scan.get("found"), scan.get("done"), scan.get("verified")
    if found != WHOLE_FRAMES:
        return f"the scan of a package of the title found {found!r}"
    if not read_through(verified):
        return "the scan of a package of the title found progressive without its whole title read through"
    if done not in ("progressive", "none"):
        return f"the scan of a package of the title says {done!r} was done"
    return None


def in_fields(sources, packages=(), probes=(), reads=()):
    """Why the originals of a version are in fields as the deletion gate reads it, widely, or None where nothing
    says so. sources are their source records, probes the probes kept of them (ffprobe.json), packages the
    package records of every version of the title, of other sources too, and reads the reads their deletion
    events carry (captionsRead). In fields: a record marked so (its essence's interlaced); a picture of a record
    or of a probe whose field order is not exactly progressive (tt, bb, tb, bt, unknown, a word written
    otherwise, none given, since ffprobe's JSON leaves an unknown one out); a caption window of a picture or of
    a read that counted interlaced frames, or a count that can't be read (windows_count_fields); a read that is
    no object or has no windows; or the scan of any package of the title that doesn't let it go (scan_holds).
    A package without a scan says nothing. No word of what a scan says was done lets an original go."""
    for s in sources:
        if not isinstance(s, dict):
            return "a source record of it can't be read"
        if (s.get("essence") or {}).get("interlaced") is True:
            return "its record is marked interlaced"
        for x in s.get("streams") or []:
            if isinstance(x, dict) and x.get("type") == "video" and not (x.get("dispositions") or {}).get("attachedPic"):
                if field_order_word(x.get("fieldOrder")) != WHOLE_FRAMES:
                    return f"its record's field order is {field_order_word(x.get('fieldOrder'))!r}"
                if "captionWindows" in x and windows_count_fields(x.get("captionWindows")):
                    return "a caption window of its picture counted interlaced frames, or a count it can't be read for"
    for p in probes:
        for x in (p.get("streams") if isinstance(p, dict) and isinstance(p.get("streams"), list) else []):
            if isinstance(x, dict) and x.get("codec_type") == "video" \
                    and not (x.get("disposition") or {}).get("attached_pic"):
                if field_order_word(x.get("field_order")) != WHOLE_FRAMES:
                    return f"its probe's field order is {field_order_word(x.get('field_order'))!r}"
    for r in reads:
        if not isinstance(r, dict) or windows_count_fields(r.get("windows")):
            return "the read its deletion carries counted interlaced frames, or can't be read"
    for pkg in packages:
        why = scan_holds(pkg)
        if why:
            return why
    return None


def caption_stream(s):
    """Whether the probe's stream s is closed captions of its own (CAPTION_CODECS, CAPTION_TAGS), whatever its
    kind."""
    return str(s.get("codec_name") or "").lower() in CAPTION_CODECS \
        or str(s.get("codec_tag_string") or "").lower() in CAPTION_TAGS


def attached_picture(st):
    """Whether a video stream of a probe is a cover, a still picture the container holds as an attached file, as the
    analyzer reads it - all of these: marked as one (the attached_pic disposition, or Matroska's filename and
    mimetype tags); an image of a cover's codec (COVER_CODECS: JPEG, PNG, BMP), its mimetype, where it has one,
    image/*, and without one the attached_pic disposition (an MP4 cover); and of no length of its own (no more
    than one frame counted, no DURATION tag; the duration ffprobe gives a stream is the file's where the container
    gives none, so it is no mark). A stream of any other codec - a webp image among them - is a video stream,
    whatever its marks say."""
    tags = {str(k).lower(): str(v) for k, v in (st.get("tags") or {}).items()}
    pic = bool((st.get("disposition") or {}).get("attached_pic"))
    marked = pic or ("filename" in tags and "mimetype" in tags)
    mime = tags.get("mimetype", "").strip().lower()
    image = st.get("codec_name") in COVER_CODECS and (mime.startswith("image/") if mime else pic)
    counted = [float(v) for v in (st.get("nb_frames"), tags.get("number_of_frames"))
               if isinstance(v, (int, float, str)) and re.fullmatch(r"[0-9]+(\.[0-9]+)?", str(v))]
    length = max(counted, default=0) > 1 or any(k.startswith("duration") for k in tags)
    return marked and image and not length


def chapter_stream(st):
    """codec_type data, codec_name bin_data, codec_tag_string text."""
    return isinstance(st, dict) and st.get("codec_type") == "data" and st.get("codec_name") == CHAPTER_CODEC \
        and str(st.get("codec_tag_string") or "").lower() == CHAPTER_TAG


def chapter_track(st, probe):
    """Whether a data stream of a probe is the file's chapter track, and on the list: only where all of these
    hold in the stored probe: the container is the MP4 family (mov,mp4,m4a,3gp,3g2,mj2); the stream is
    codec_type data, codec_name bin_data, codec_tag_string text; the file has chapters; the stream's sample
    count (nb_frames) equals the number of chapters; and it is the file's only such stream. A probe that lacks
    the tag or the count leaves it off the list."""
    fmt = probe.get("format") if isinstance(probe.get("format"), dict) else {}
    chapters = probe.get("chapters")
    marks = len(chapters) if isinstance(chapters, list) else 0
    count = st.get("nb_frames")
    try:
        counted = count is not None and not isinstance(count, bool) and int(count) == marks
    except (TypeError, ValueError):
        counted = False
    return fmt.get("format_name") == CHAPTER_CONTAINER and chapter_stream(st) and marks > 0 and counted \
        and sum(1 for x in probe.get("streams") or [] if chapter_stream(x)) == 1


def probe_shape(probe):
    """Why the probe kept of a source (ffprobe.json) can't be read as one, or None: it is no object, its format
    is no object, or its streams no list of objects, or none."""
    if not isinstance(probe, dict):
        return "its probe can't be read"
    if not isinstance(probe.get("format"), dict) or not isinstance(probe.get("streams"), list) \
            or not probe["streams"] or not all(isinstance(x, dict) for x in probe["streams"]):
        return "its probe is no probe: no format, or no list of streams"
    return None


def off_list(probe):
    """What of the file a probe describes is off the closed lists of how captions and fields are carried, in the
    analyzer's offList names and in the order the probe gives them: its container (container:<format_name>),
    each stream (stream:<codec_type>:<codec, else its tag, else none>), the count of its films
    (video-streams:<n>), each film's codec (codec:<codec_name>), its picture (picture:vbi-lines). Empty for a
    file on them."""
    fmt = probe.get("format") if isinstance((probe or {}).get("format"), dict) else {}
    off, films = [], []
    name = str(fmt.get("format_name") or "none")
    if name not in CONTAINERS:
        off.append(f"container:{name}")
    for st in (probe or {}).get("streams") or []:
        if not isinstance(st, dict):
            continue
        kind, codec = str(st.get("codec_type") or "none"), str(st.get("codec_name") or "none")
        tag = str(st.get("codec_tag_string") or "").lower()
        if codec == "unknown":
            codec = "none"
        what = codec if codec != "none" or not tag or tag.startswith("[") else tag
        if codec in CAPTION_CODECS or tag in CAPTION_TAGS:
            continue
        if kind == "video":
            if not attached_picture(st):
                films.append(st)
        elif kind in ("audio", "attachment") or (kind == "subtitle" and codec in SUBTITLE_CODECS) \
                or (kind == "data" and (codec in DATA_CODECS or tag in DATA_TAGS or chapter_track(st, probe))):
            continue
        else:
            off.append(f"stream:{kind}:{what}")
    if len(films) > 1:
        off.append(f"video-streams:{len(films)}")
    for st in films:
        codec = str(st.get("codec_name") or "none")
        if codec not in VIDEO_CODECS and f"codec:{codec}" not in off:
            off.append(f"codec:{codec}")
    if any(st.get("height") in VBI_HEIGHTS for st in films):
        off.append("picture:vbi-lines")
    return off


def record_off_list(record):
    """What of the file a source record describes is off the closed lists: its offList, where it keeps one (a
    list of words; one of another shape can't be read: off), else as its container and streams say it — a data
    stream's tag, which a record does not keep, is no tag, and a video stream an attached picture by its
    disposition alone —, of a record written before record logic 11."""
    if "offList" in (record or {}):
        off = record["offList"]
        return list(off) if isinstance(off, list) and all(isinstance(x, str) and x for x in off) \
            else ["offList:unreadable"]
    name = (((record or {}).get("container") or {}).get("format")) or "none"
    off = [] if name in CONTAINERS else [f"container:{name}"]
    films = []
    for x in (record or {}).get("streams") or []:
        if not isinstance(x, dict):
            off.append("stream:unreadable")
            continue
        kind, codec = x.get("type") or "none", x.get("codec") or "none"
        if codec == "unknown":
            codec = "none"
        if codec in CAPTION_CODECS or (kind in ("subtitle", "data") and x.get("closedCaptions")):
            continue
        if kind == "video":
            if not (x.get("dispositions") or {}).get("attachedPic"):
                films.append(x)
        elif kind in ("audio", "attachment") or (kind == "subtitle" and codec in SUBTITLE_CODECS) \
                or (kind == "data" and codec in DATA_CODECS):
            continue
        else:
            off.append(f"stream:{kind}:{codec}")
    if len(films) > 1:
        off.append(f"video-streams:{len(films)}")
    for x in films:
        if (x.get("codec") or "none") not in VIDEO_CODECS and f"codec:{x.get('codec') or 'none'}" not in off:
            off.append(f"codec:{x.get('codec') or 'none'}")
    if any(x.get("height") in VBI_HEIGHTS for x in films):
        off.append("picture:vbi-lines")
    return off


def gate_holds(sources, packages=(), probes=(), reads=()):
    """What the deletion gate keeps the originals of a version for that their essences may not say, read from
    their records, {key: why}. sources are their source records, one for each, or None where it can't be read;
    probes the probes kept of them, one for each, in their order, None where there is none or it can't be read
    (the plan's span of a record's picture is its probe's: plan_of); packages and
    reads as in_fields'. Both keys, closedCaptions and interlaced: no source; a record that can't be read, or
    whose streams are no list; a probe that can't be read as one (probe_shape); a record or a probe off the
    closed lists (record_off_list, off_list); a read that is no object or has no list of windows. interlaced: in
    fields (in_fields). closedCaptions: a stream of a record or a probe that is closed captions of its own
    (caption_stream), as of records written before they said so; a record's picture that says it carries them
    (a caption signature found, or windows that show them), whatever its essence says; an essence that says it
    knows its captions where its record's streams and its probe's plan don't bear that out (captions_known), as no record the
    record logic writes does; a picture whose packets carry A53 its decoder does not export (SCANNED_CODECS)
    and whose packets were not read whole with none carrying a caption signature (captionSignature not
    false)."""
    out = {}

    def both(why):
        out.setdefault("closedCaptions", why)
        out.setdefault("interlaced", why)

    if not sources:
        both("no source of it is known")
    for s in sources:
        if not isinstance(s, dict):
            both("a source record of it can't be read")
    good = [s for s in sources if isinstance(s, dict)]
    why = in_fields(good, packages, [p for p in probes if isinstance(p, dict)], reads)
    if why:
        out.setdefault("interlaced", why)
    probes = list(probes)
    probe_of = {id(s): probes[i] if i < len(probes) else None for i, s in enumerate(sources)}
    for s in good:
        streams = s.get("streams")
        if not isinstance(streams, list):
            both("its record's streams can't be read")
            streams = []
        if any(isinstance(x, dict) and x.get("type") in ("subtitle", "data")
               and (x.get("closedCaptions") or str(x.get("codec") or "").lower() in CAPTION_CODECS)
               for x in streams):
            out.setdefault("closedCaptions", "a stream of its record is closed captions of its own")
        picture = picture_of(streams) or {}
        if picture.get("codec") in SCANNED_CODECS and picture.get("captionSignature") is not False:
            out.setdefault("closedCaptions", f"its picture is {picture.get('codec')}, whose packets were not read "
                                             f"whole for a caption signature")
        if picture.get("captionSignature") is True or windows_show(picture.get("captionWindows")):
            out.setdefault("closedCaptions", "its record's picture carries closed captions")
        off = record_off_list(s)
        if off:
            both("off the closed lists: " + ", ".join(off))
        essence = s.get("essence") if isinstance(s.get("essence"), dict) else {}
        plan = plan_of(probe_of.get(id(s)), (s.get("container") if isinstance(s.get("container"), dict) else {})
                       .get("durationMs"))
        if essence.get("closedCaptionsKnown") is True and not captions_known(streams, off, plan):
            out.setdefault("closedCaptions", "its essence says it knows its closed captions, which its record's "
                                             "streams don't bear out")
    for p in probes:
        why = probe_shape(p)
        if why:
            both(why)
            continue
        if any(caption_stream(x) for x in p["streams"]):
            out.setdefault("closedCaptions", "a stream of its probe is closed captions of its own")
        off = off_list(p)
        if off:
            both("off the closed lists: " + ", ".join(off))
    for r in reads:
        if not isinstance(r, dict) or not isinstance(r.get("windows"), list):
            both("the read its deletion carries can't be read")
    return out


def essence_as_read(essence, reads, plan):
    """The essence of a source as the reads its deletion events carry (captionsRead: {windows, span, gop, at};
    one, or a list of them) tell it over the plan's span of its stored probe (plan_of), the worst deciding:
    where one of them shows captions, it carries them, known; where every one tells none (windows_tell_none, by
    the plan), it knows its captions, carried where its record says so; where one counted interlaced frames, or
    a count of it can't be read, its picture is in fields. Any other read tells nothing more: the essence as its
    record says it. Whether the file is on the closed lists is its record's and its probe's to say
    (gate_holds), not a read's."""
    reads = [reads] if isinstance(reads, dict) else list(reads or [])
    if not reads:
        return essence
    out = dict(essence)
    windows = [r.get("windows") if isinstance(r, dict) else None for r in reads]
    if any(windows_show(w) for w in windows):
        out.update(closedCaptions=True, closedCaptionsKnown=True)
    elif all(windows_tell_none(r, plan) for r in reads):
        out.update(closedCaptions=essence.get("closedCaptions") is True, closedCaptionsKnown=True)
    if any(windows_count_fields(w) for w in windows):
        out["interlaced"] = True
    return out


def deletion_gate(sources, package, held):
    """What deleting a version's originals costs: the essence of its sources minus that of its package, sorted.
    Nothing records it, so every reader computes it the same way, and an original-deleted event accepts no more
    than it - never closedCaptions or interlaced, which keep the original (ADR-0015).

    sources are the essences of the version's sources (as their deletions' reads tell them: essence_as_read),
    package its package's, and held what gate_holds says of their records, each of its keys lost: required,
    as a reader that has only the essences reads neither the lists, the probes, the packages' scans nor the
    reads. An original whose closed captions are carried, or not known (closedCaptions not false, or
    closedCaptionsKnown not true), is kept - closedCaptions - whatever its package carries; an original in fields
    (interlaced not false) is kept - interlaced - whatever was done to make its package progressive. No source
    at all keeps both."""
    lost = set()
    if not sources:
        lost |= {"closedCaptions", "interlaced"}
    for key in LOSS_FLAGS:
        if any(s.get(key) for s in sources) and not package.get(key):
            lost.add(key)
    if any(s.get("closedCaptions") is not False or s.get("closedCaptionsKnown") is not True for s in sources):
        lost.add("closedCaptions")
    if any(s.get("interlaced") is not False for s in sources):
        lost.add("interlaced")
    for key in LOSS_COUNTS:
        had = [s[key] for s in sources if s.get(key) is not None]
        kept = package.get(key)
        if had and (kept is None or kept < max(had)):
            lost.add(key)
    for key in LOSS_LANGUAGES:
        kept = set(package.get(key) or [])
        lost |= {f"{key}:{x}" for s in sources for x in s.get(key) or [] if x not in kept}
    lost |= set(held)
    return sorted(lost)
# ==== the deletion gate, shared: end ====


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
# A subtitle attached to a title after its package completed, in the subs/ of a version folder, named by
# the hex sha256 of its bytes, its BCP 47 language in lower case and its flags (libv2_records.py
# ATTACHED_SUBTITLE_RE).
ATTACHED_NAME = re.compile(r"^attached-([0-9a-f]{64})\.([a-z0-9]+(?:-[a-z0-9]+)*)(\.forced)?(\.sdh)?\.vtt$")
# What the catalog keeps of a subtitle attached to a title beside what every subtitle row has, under the
# names an export gives them, and what it says of a title's own subtitle there: its package's, or a
# file's beside its source.
ATTACHED_FIELDS = ("isSdh", "origin", "versionId", "sha256", "attachedBy", "attachedAt")
TITLES_OWN = {"isSdh": False, "origin": None, "versionId": None, "sha256": None, "attachedBy": None, "attachedAt": None}
# What no record holds of an attached subtitle — how it was made, the label it was given, who attached
# it and when — is the catalog's alone: the rebuild writes none of it, and --compare compares none of it.
ATTACHED_UNRECORDED = ("label", "origin", "attachedBy", "attachedAt")
# What the catalog keeps of a styled subtitle's script beside the subtitle's file (katalog-manager's
# migration 053), under the names an export gives them; and the formats it keeps of a script and of a
# font of the package, as package.json names them.
STYLED_FIELDS = ("styledPath", "styledFormat")
STYLED_FORMATS = ("ass",)
FONT_FORMATS = ("ttf", "otf", "ttc", "woff", "woff2")


def did(*parts):
    return str(uuid.uuid5(NS, ":".join(str(p) for p in parts)))


def listdir(d):
    return sorted(n for n in os.listdir(d) if not OS_ARTEFACTS.match(n))


def inside(root, rel):
    """rel, a path a package's record names relative to its folder, joined to root as the row of a file
    of the package is, or None for one that is no text, is absolute, or leaves root — which
    katalog-manager keeps no row of either."""
    if not isinstance(rel, str) or not rel or os.path.isabs(rel):
        return None
    full = os.path.join(root, rel)
    return full if os.path.normpath(full).startswith(os.path.normpath(root) + os.sep) else None


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


class Twice(ValueError):
    """A JSON document that gives a key twice in one object: which of the two is meant can't be told."""


def load_once(p):
    """The JSON document at p, as load(); one that gives a key twice in an object raises Twice."""
    twice = []

    def pairs(items):
        keys = [k for k, _ in items]
        twice.extend(k for k in dict.fromkeys(keys) if keys.count(k) > 1)
        return dict(items)

    with open(p, encoding="utf-8") as f:
        doc = json.load(f, object_pairs_hook=pairs)
    if twice:
        raise Twice(f"it gives the key {', '.join(dict.fromkeys(twice))} twice")
    return doc


def readable(p):
    """The JSON object at p, or None where there is none or it can't be read - a key given twice too."""
    try:
        doc = load_once(p)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def plan_for(d, src):
    """The plan's span of a source record's picture (plan_of), of the probe kept beside it in the item folder d
    and its container's length; None where either can't be read."""
    if not isinstance(src, dict) or not isinstance(src.get("sourceId"), str) or not UUID_RE.match(src["sourceId"]):
        return None
    probe = readable(os.path.join(d, "sources", src["sourceId"], "ffprobe.json"))
    return plan_of(probe, (src.get("container") if isinstance(src.get("container"), dict) else {}).get("durationMs"))


def title_holds(d, records, reads=()):
    """gate_holds() of the source records records of a version of the item folder d (None of one that can't be
    read): the probe kept of each (sources/<sourceId>/ffprobe.json), every one passed, None where there is none
    or it can't be read; the package record of every version of the title, finished or not, None of one there
    that can't be read; and every read its deletion events carry."""
    probes = [readable(os.path.join(d, "sources", r["sourceId"], "ffprobe.json"))
              if isinstance(r, dict) and isinstance(r.get("sourceId"), str) and UUID_RE.match(r["sourceId"]) else None
              for r in records]
    base = os.path.join(d, "versions")
    names = listdir(base) if os.path.isdir(base) else []
    packages = [readable(os.path.join(base, n, "package.json")) for n in names
                if UUID_RE.match(n) and os.path.isfile(os.path.join(base, n, "package.json"))]
    return gate_holds(records, packages, probes, reads)


def bcp47_cased(code):
    """A BCP 47 tag an attached subtitle's name carries in lower case, cased as the catalog spells it:
    the language in lower case, then a script of four letters in title case, a region of two letters in
    capitals or of three digits, and any variant in lower case, each in that order — de, zh-Hans,
    pt-BR, es-419. Its subtags are told apart by their form alone, so the casing is the catalog's."""
    out, seen = [], 0  # seen: 1 after a script, 2 after a region, 3 in the variants
    for i, part in enumerate(code.split("-")):
        if i and seen < 1 and re.fullmatch(r"[a-z]{4}", part):
            out.append(part.title())
            seen = 1
        elif i and seen < 2 and re.fullmatch(r"[a-z]{2}|[0-9]{3}", part):
            out.append(part.upper())
            seen = 2
        else:
            out.append(part)
            seen = 3 if i else 0
    return "-".join(out)


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
    def __init__(self, root, text_language, shard=None):
        self.root = root
        self.shard = shard   # --shard: the one shard of movies/ and series/ read, and the people its items credit
        self.text_language = text_language
        self.items = []
        self.people = []
        self.extras = []
        self.storage = []
        self.notes = []
        self.newest = {}
        self.people_newest = {}
        # what each episode says of a file that covers several: its series, its sources and those a
        # source-removed event retired, and the holder its projection names
        self.covering = {}

    def note(self, msg):
        self.notes.append(msg)

    def run(self):
        for category, kind in (("movies", "movie"), ("series", "series")):
            base = os.path.join(self.root, category)
            if not os.path.isdir(base):
                continue
            for shard in ([self.shard] if self.shard else listdir(base)):
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
        self.covered()
        base = os.path.join(self.root, "people")
        if self.shard:
            # the people the shard's items credit, as an export of the shard carries them, and no other
            credited = sorted({str(c.get("personId")) for r in self.items for c in r.get("people") or [] if c.get("personId")})
            for pid in credited:
                if os.path.isdir(os.path.join(base, pid[:2], pid)):
                    self.person(os.path.join(base, pid[:2], pid))
        else:
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
        if item.get("type") == "episode":
            self.covering[item["itemId"]] = {
                "series": item.get("seriesId"), "sources": sources,
                "dropped": {ev.get("sourceId") for ev in events if ev.get("kind") == "source-removed"},
                "projected": (meta.get("library") or {}).get("coveredBy")}
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

    def covered(self):
        """The episodes another episode's file covers, as the catalog links them (coveredBy): an episode a
        source of an episode of its series lists after the holder (source.json covers) is covered by that
        holder. The record restores the link. The covered episode's projection decides only where nothing
        records the holder's file yet — a holder with no source that counts — and a projection that names
        a holder whose source does not cover it is outdone by the record."""
        rows = {r["id"]: r for r in self.items}
        counted = lambda hid: [s for s in self.covering[hid]["sources"] if s not in self.covering[hid]["dropped"]]
        claims = {}
        for hid, c in sorted(self.covering.items()):
            for sid in counted(hid):
                for iid in (c["sources"][sid].get("covers") or [])[1:]:
                    if iid != hid and iid in self.covering and self.covering[iid]["series"] == c["series"]:
                        claims.setdefault(iid, set()).add(hid)
        for iid, c in sorted(self.covering.items()):
            holders, projected = sorted(claims.get(iid, ())), c["projected"]
            if holders:
                holder = projected if projected in holders else holders[0]
                if len(holders) > 1:
                    self.note(f"{iid}: the sources of {', '.join(holders)} each cover it; the row is covered by {holder}")
                elif projected and projected != holder:
                    self.note(f"{iid}: its projection names {projected} as the holder of its file, and a source of "
                              f"{holder} covers it: the record wins")
            elif projected in self.covering and self.covering[projected]["series"] == c["series"] and not counted(projected):
                holder = projected  # nothing records the holder's file yet, so nothing contradicts the projection
            else:
                holder = None
                if projected:
                    self.note(f"{iid}: its projection names {projected} as the holder of its file, and no source of it "
                              f"covers this episode: the record wins, and the row is covered by nothing")
            if iid in rows:
                rows[iid]["coveredBy"] = holder

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
                ev = load_once(p)
            except Twice as e:
                # what happened stands, but no read it carries can be told: both keys, where it deleted an original
                ev = load(p)
                self.note(f"{iid}: event {name}: {e}, so the read it carries can't be told")
                if isinstance(ev, dict) and ev.get("kind") == "original-deleted":
                    ev["captionsRead"] = {"windows": None, "why": str(e)}
            except (OSError, ValueError) as e:
                self.note(f"{iid}: event {name} could not be read ({e})")
                continue
            if not isinstance(ev, dict):
                self.note(f"{iid}: event {name} is no object")
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
                rec = load_once(p)
                out[rec["sourceId"]] = rec
            except Twice as e:
                self.note(f"{os.path.basename(d)}: source {name}: {e}, so its record can't be read")
            except (OSError, ValueError, KeyError, TypeError):
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
                if "captionsRead" in ev:
                    found[vid].setdefault("reads", {}).setdefault(ev.get("sourceId"), []).append(ev["captionsRead"])
            elif kind == "package-superseded":
                by = ev.get("supersededBy") or {}
                for v in found.values():
                    if v["package"] and v["package"].get("packageId") == ev.get("packageId"):
                        v["superseded"] = True
                        v["supersededBy"] = by.get("packageId")
        # an original deleted is its source's, whichever version folder kept it: a re-package keeps none, and
        # the subtitles it made from the copies its source keeps stand for the catalog's rows once it is gone
        deleted = {ev["sourceId"] for ev in events if ev.get("kind") == "original-deleted" and ev.get("sourceId")}
        deleted |= {s for v in found.values() if v["whole"] for s in v["version"].get("sourceIds") or []}
        # a source a version is made of whose original no version folder lists, and no event says was deleted, is
        # gone all the same - a folder that kept it removed by hand: it counts as deleted, and the gate says the loss
        name_of = lambda s: (sources.get(s) or {}).get("file", {}).get("name") if isinstance(sources.get(s), dict) \
            and isinstance(sources[s].get("file"), dict) else None  # noqa: E731
        listed = {s for v in found.values() for s in v["version"].get("sourceIds") or [] if isinstance(s, str)
                  and name_of(s) in (v["version"].get("originalFiles") or [])}
        vanished = sorted({s for v in found.values() if not v["removed"] for s in v["version"].get("sourceIds") or []
                           if isinstance(s, str) and name_of(s) and s not in listed and s not in deleted})
        if vanished:
            self.note(f"{item['itemId']}: the original of source(s) {', '.join(vanished)} is listed by no version "
                      f"folder, and no original-deleted event says it was deleted: it counts as deleted, its loss as "
                      f"the gate says")
            deleted |= set(vanished)
        for v in found.values():
            v["deletedSources"] = deleted

        live, storage = [], []
        for vid in sorted(found):
            v = found[vid]
            ids = [s for s in v["version"].get("sourceIds") or [] if isinstance(s, str)]
            # a source deleted is gone though its record is not there, or can't be read: both keys then
            records = [sources.get(s) for s in ids]
            # a source's essence as every read its deletion events carry tells it, the worst deciding: the
            # catalog's reads, which its record may not keep; an event of the whole version carries the read
            # of its one source
            reads = v.get("reads") or {}
            read_for = lambda s: reads.get(s, []) + (reads.get(None, []) if len(ids) == 1 else [])  # noqa: E731
            essences = [essence_as_read(r["essence"], read_for(s), plan_for(d, r)) for s, r in zip(ids, records)
                        if isinstance(r, dict) and isinstance(r.get("essence"), dict)]
            # an original whose source's record is gone, or can't be read, and that no record of a source not
            # deleted names, counts as deleted with it
            lost_records = [s for s in v["gone"] if not isinstance(sources.get(s), dict)]
            if lost_records:
                self.note(f"{item['itemId']}: version {vid}: the record of deleted source(s) {', '.join(lost_records)} "
                          f"is gone or can't be read; its original counts as deleted, and the loss as both of "
                          f"closedCaptions and interlaced")
            named = {sources[s].get("file", {}).get("name") for s in ids
                     if isinstance(sources.get(s), dict) and s not in v["gone"]}
            kept = [n for n in v["version"].get("originalFiles") or []
                    if not v["whole"] and not any(sources.get(s, {}).get("file", {}).get("name") == n
                                                  for s in v["gone"] if isinstance(sources.get(s), dict))
                    and not (lost_records and n not in named)]
            canonical = (v["package"] is not None) and not kept
            # the loss of every version one of whose sources was deleted, whichever version's event says so
            if v["whole"] or v["gone"] or any(s in deleted for s in ids) or not ids:
                held = title_holds(d, records, [x for s in ids for x in read_for(s)])
                if len(essences) < len(ids) or not ids:
                    held.setdefault("closedCaptions", "a source record of it can't be read")
                    held.setdefault("interlaced", "a source record of it can't be read")
                lost = deletion_gate(essences, (v["package"] or {}).get("essence") or {}, held) \
                    if (v["whole"] or v["gone"] or any(s in deleted for s in ids)) else []
            else:
                lost = []
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
            # an episode whose file is another's: covered() gives it the holder, once every episode is read
            "coveredBy": None,
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
            "chapters": [], "segments": [], "playbackAssets": [], "subtitleAssets": [], "fontAssets": [],
            "trailers": [{"source": v.get("origin"), "site": v.get("site"), "externalId": v.get("key"),
                          "url": v.get("url"), "title": v.get("name"),
                          "durationSec": (v["durationMs"] // 1000) if v.get("durationMs") else None,
                          "localPath": copies.get(("site", v.get("site"), v.get("key"))) or copies.get(("url", v.get("url")))}
                         for v in meta.get("videos") or []],
            "artwork": [artwork_row(i, os.path.join("metadata", i["file"])) for i in meta.get("images") or []],
        }
        # The marks are the database's as the projection holds them, which follow every change of them; a
        # projection written before it held them leaves the marks the version that plays was made with.
        marks = meta.get("marks") if isinstance(meta.get("marks"), dict) else None
        if marks is not None:
            for c in marks.get("chapters") or []:
                row["chapters"].append({"startMs": c["startMs"], "endMs": c["endMs"], "title": c.get("title"),
                                        "ordinal": c.get("ordinal")})
        elif primary:
            for i, c in enumerate(primary["version"].get("chapters") or []):
                row["chapters"].append({"startMs": c["startMs"], "endMs": c["endMs"], "title": c.get("title"),
                                        "ordinal": i + 1})
        for s in (marks.get("segments") or [] if marks is not None
                  else primary["version"].get("segments") or [] if primary else []):
            row["segments"].append({"kind": s["kind"], "startMs": s["startMs"], "endMs": s["endMs"],
                                    "source": s.get("detector"), "confidence": s.get("confidence"),
                                    "label": s.get("label")})
        # The title's file is the original the version that plays was made from, in whichever version
        # folder it is kept: a package made again goes to a folder of its own, and the original stays in
        # the folder of the version before it until it is retired.
        made_from = (primary.get("version") or {}).get("sourceIds") if primary else None
        made_from = set(made_from) if isinstance(made_from, list) else None
        # A subtitle attached to a title follows the version whose package plays; a title with none — its
        # file taken in before anything packaged it — has its subtitles attached to the version taken in,
        # and once a package plays, those of a version of another file are the catalog's no more.
        plays = any(v["package"] and not v["superseded"] for v in versions)
        for v in versions:
            row["playbackAssets"] += self.assets(item, v, sources, v is primary, made_from)
            if v["package"] and not v["superseded"]:
                row["subtitleAssets"] += [dict(s, **TITLES_OWN) for s in self.subtitles(item, v, sources)]
                row["subtitleAssets"] += self.attached(item, v)
                row["fontAssets"] += self.fonts(item, v)
            elif v["package"] is None and not plays:
                row["subtitleAssets"] += self.attached(item, v)
        return row

    def assets(self, item, v, sources, is_primary, made_from=None):
        out = []
        of = {id(src): sid for sid, src in sources.items()}
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
                        "isPrimary": bool(i == 0 and (is_primary or (
                            made_from is not None and src is not None and of.get(id(src)) in made_from))),
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
        gone = lambda sid: v.get("whole") or sid in (v.get("gone") or ()) or sid in (v.get("deletedSources") or ())
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
            styled = s.get("styled") if isinstance(s.get("styled"), dict) else {}
            script = inside(v["dir"], styled.get("path")) if styled.get("format") in STYLED_FORMATS else None
            out.append({"id": did(item["itemId"], "subtitle", v["id"], s["id"]),
                        "path": os.path.join(v["dir"], s["path"]), "format": s.get("format"),
                        "language": language, "label": s.get("title") or "", "isDefault": False,
                        "styledPath": script, "styledFormat": styled["format"] if script else None})
        item_dir = os.path.dirname(os.path.dirname(v["dir"]))
        for sid in v.get("version", {}).get("sourceIds") or []:
            for x in (sources.get(sid) or {}).get("sidecars") or [] if gone(sid) else []:
                if x.get("kind") == "subtitle" and x.get("file") not in made:
                    out.append({"id": did(item["itemId"], "subtitle", sid, x["file"]),
                                "path": os.path.join(item_dir, x["file"]), "format": x.get("format"),
                                "language": x.get("language"), "label": None, "isDefault": False,
                                "styledPath": None, "styledFormat": None})
        return out

    def fonts(self, item, v):
        """The rows of the fonts of a version's package, in their order, as katalog-manager keeps them
        (db/migrations/053): each at its place among the package's fonts (ordinal, from 0), by a
        name-based id of the title, the version and that place, with its file, its format (one of
        FONT_FORMATS, else none), the names a script asks for it by and the attachment it was taken from.
        A font whose path leaves the version's folder is none of the package's, and keeps its place
        empty."""
        out = []
        for i, f in enumerate(v["package"].get("fonts") or []):
            path = inside(v["dir"], f.get("path")) if isinstance(f, dict) else None
            if not path:
                continue
            index = f.get("sourceStreamIndex")
            out.append({"id": did(item["itemId"], "font", v["id"], i), "versionId": v["id"], "path": path,
                        "format": f.get("format") if f.get("format") in FONT_FORMATS else None,
                        "names": [n for n in f.get("names") or [] if isinstance(n, str) and n.strip()],
                        "ordinal": i,
                        "sourceStreamIndex": index if isinstance(index, int) and not isinstance(index, bool) else None})
        return out

    def attached(self, item, v):
        """The rows of the subtitles attached to the title in the folder of the version v: each file
        directly in its subs/ named as one is, rebuilt from that name and that folder — its language,
        cased as the catalog spells a BCP 47 tag, WebVTT, whether it is SDH, the version it follows and
        its hash — and never the default. Whatever no record holds stays empty: how it was made
        (origin), the label it was given, who attached it and when."""
        out = []
        subs = os.path.join(v["dir"], "subs")
        for name in (listdir(subs) if os.path.isdir(subs) else []):
            m = ATTACHED_NAME.match(name)
            if not m or not os.path.isfile(os.path.join(subs, name)):
                continue
            out.append({"id": did(item["itemId"], "subtitle", v["id"], name), "path": os.path.join(subs, name),
                        "format": "webvtt", "language": bcp47_cased(m.group(2)), "label": None, "isDefault": False,
                        "isSdh": bool(m.group(4)), "origin": None, "versionId": v["id"],
                        "sha256": "sha256:" + m.group(1), "attachedBy": None, "attachedAt": None,
                        "styledPath": None, "styledFormat": None})
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
def path_parts(x):
    return str(x.get("path") or "").replace("\\", "/").split("/")


def attached_row(x):
    """Whether a subtitle row is one of a subtitle attached to its title: its file is named as one,
    directly in the subs/ of a version folder (versions/<versionId>/subs/), or — in an export that
    carries it — it has an origin, which a title's own subtitle has not."""
    p = path_parts(x)
    return (len(p) >= 4 and p[-2] == "subs" and p[-4] == "versions" and bool(UUID_RE.match(p[-3]))
            and bool(ATTACHED_NAME.match(p[-1]))) or bool(x.get("origin"))


def attached_key(x):
    """An attached subtitle's row by its file: the version folder it lies in, and the name its bytes
    give it. Where the tree or the database mounts the library is no part of it."""
    p = path_parts(x)
    return (p[-3] if len(p) >= 3 else "", p[-1])


def normalise_attached(x):
    """An attached subtitle's row as it can be compared: its hash in the form the library writes one,
    sha256:<hex>, whichever form the row holds, and the moment it was attached to the second."""
    out = dict(x)
    digest = str(out.get("sha256") or "").strip().lower()
    digest = digest[len("sha256:"):] if digest.startswith("sha256:") else digest
    if re.fullmatch(r"[0-9a-f]{64}", digest):
        out["sha256"] = "sha256:" + digest
    if "attachedAt" in out:
        out["attachedAt"] = stamp(out["attachedAt"])
    return out


LIST_KEYS = {
    "playbackAssets": lambda x: x.get("kind"),
    "subtitleAssets": lambda x: (x.get("language"), os.path.basename(str(x.get("path") or ""))),
    "attachedSubtitles": attached_key,
    # a font is the one at its place among the fonts of the package the title plays
    "fontAssets": lambda x: x.get("ordinal"),
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
                        "people": ("job", "character", "order", "episodeCount"),
                        # what a subtitle row gained with attached subtitles (katalog-manager's migration 048),
                        # and with styled subtitles (053)
                        "subtitleAssets": ATTACHED_FIELDS + STYLED_FIELDS,
                        "attachedSubtitles": ATTACHED_FIELDS + STYLED_FIELDS}
# The fields of a list's entries --compare never compares, whatever --ignore-fields says: of an attached
# subtitle, what no record holds.
UNRECORDED_ENTRY_FIELDS = {"attachedSubtitles": ATTACHED_UNRECORDED}
# The fields of an item row compared only when the export's row carries them, for the same reason:
# items do not carry the first two yet, coveredBy only once the catalog links covered episodes, and
# fontAssets only once it keeps the fonts of a package (053).
CARRIED_ITEM_FIELDS = ("tmdbFetchedAt", "tmdbChangedAt", "coveredBy", "fontAssets")
# A person's row says when the catalog last asked its source about them, and the day of the change it
# last saw there. Asking moves both and changes nothing of the person: the row's modifiedAt — and so
# its projection — moves only when a value does. --compare never compares them, whatever
# --ignore-fields says: a database rebuilt from the tree asks again sooner, and has lost nothing.
PERSON_BOOKKEEPING = ("tmdbFetchedAt", "tmdbChangedAt")
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


def one_line(v):
    """A text as a record keeps one that is a line — a title, a name, a label: on one line, without the
    whitespace around it, none when nothing is left; anything else as it is."""
    if not isinstance(v, str):
        return v
    return v.replace("\r", " ").replace("\n", " ").strip() or None


def paragraphs(v):
    """A text as a record keeps a longer one — an overview, a tagline, a biography: without the
    whitespace around it, none when nothing is left; anything else as it is."""
    if not isinstance(v, str):
        return v
    return v.strip() or None


# The texts of a row, and of its lists' entries, compared as a record keeps them: its writers take the
# whitespace around a text and a line break out of one that is a line, and keep no empty one, so the
# forms of one text that a database may hold — "Chapter 1" and " Chapter 1", an overview and the same
# overview followed by a tab — are one text, and an empty title is none.
ONE_LINE_FIELDS = ("title", "sortTitle", "name", "sortName", "birthPlace", "knownForDepartment")
PARAGRAPH_FIELDS = ("description", "tagline")
ENTRY_TEXTS = {"people": ("name", "job", "character"), "chapters": ("title",), "segments": ("source", "label"),
               "subtitleAssets": ("label",), "trailers": ("title",)}


def text_form(out):
    """out, a row or a person, with its texts as a record keeps them (one_line, paragraphs)."""
    for key in ONE_LINE_FIELDS:
        if key in out:
            out[key] = one_line(out[key])
    for key in PARAGRAPH_FIELDS:
        if key in out:
            out[key] = paragraphs(out[key])
    if isinstance(out.get("alsoKnownAs"), list):
        out["alsoKnownAs"] = [one_line(x) for x in out["alsoKnownAs"]]
    if isinstance(out.get("biography"), dict):
        out["biography"] = {k: paragraphs(v) for k, v in out["biography"].items()}
    for key, fields in ENTRY_TEXTS.items():
        if isinstance(out.get(key), list):
            out[key] = [dict(e, **{f: one_line(e[f]) for f in fields if f in e}) if isinstance(e, dict) else e
                        for e in out[key]]
    return out


def normalise(row):
    """A catalog row as it can be compared: the artwork reduced to its hash, its freshness in one form,
    its texts as a record keeps them (text_form), nothing else changed."""
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
    if isinstance(row.get("subtitleAssets"), list):
        # a subtitle attached to the title is compared apart, in what its file's name and folder say:
        # never as a row of the package's or the source's that one side lacks
        subs = [x for x in row["subtitleAssets"] if isinstance(x, dict)]
        attached = [normalise_attached(x) for x in subs if attached_row(x)]
        if attached:
            out["subtitleAssets"] = [x for x in subs if not attached_row(x)]
            out["attachedSubtitles"] = attached
    # no tagline is no tagline, however a row says it: a record keeps none for an empty one, and no
    # whitespace around one
    return text_form(out)


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
    return text_form(out)


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
                out[c["personId"]] = text_form({"id": c["personId"], "name": c.get("name")})
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
            people_newest=None, arrivals=(), differences=None):
    """Both directions, field by field. Returns (lines, number of differences, number of orphans safe
    to remove). newest is the newest moment each item folder's records state, by item id, and
    people_newest each person folder's, by person id. extras are the tree's bonus material, which the
    catalog has no table for yet: they are counted, never compared. Behaviour is never compared.
    differences, a path, gets every field difference (write_differences): the lines show five of each."""
    ignore = set(ignore) | set(BEHAVIOUR)
    # a styled subtitle's script is a file beside the subtitle's, which moved with it
    if "path" in ignore:
        ignore.add("styledPath")
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
        for key in sorted(k for k in theirs[pid] if k not in ("id", "modifiedAt") and k not in ignore
                          and k not in PERSON_BOOKKEEPING):
            n += diff_field(fields, pid, key, mine[pid], theirs[pid], ignore, "people.")
    for key in sorted(fields):
        rows = fields[key]
        lines.append(f"  {key}: {len(rows)} difference(s)")
        for iid, a, b in rows[:5]:
            lines.append(f"      {iid}: storage {a!r} != database {b!r}" + (" — stale projection" if iid in went_stale else ""))
        if len(rows) > 5:
            lines.append(f"      … and {len(rows) - 5} more")
    if differences:
        write_differences(differences, fields)
    return lines, n, removable


def write_differences(path, fields):
    """Every field difference, one JSON object a line, for a tool to act on: the field, the row's id
    (an item's, or a person's for people.*), only — "storage" or "database" when one side alone holds
    an entry of a list or a member of a set, else null — and what storage and the database hold, null
    for the side that holds nothing."""
    with open(path, "w", encoding="utf-8") as f:
        for key in sorted(fields):
            for iid, a, b in fields[key]:
                only = None
                if a == "not on storage":                 # a set's member the database alone holds
                    only, a, b = "database", None, b
                elif a == "not in the database":          # one storage alone holds
                    only, a, b = "storage", b, None
                elif b == "missing":                      # a list's entry storage alone holds
                    only, b = "storage", None
                elif a == "missing":
                    only, a = "database", None
                f.write(json.dumps({"field": key, "id": iid, "only": only, "storage": a, "database": b},
                                   ensure_ascii=False, default=str) + "\n")


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
    carried, never = CARRIED_ENTRY_FIELDS.get(key, ()), UNRECORDED_ENTRY_FIELDS.get(key, ())
    for k in sorted(set(a) & set(b), key=str):
        for f in sorted(set(a[k]) | set(b[k])):
            if f in ignore or f in never or (f in carried and f not in b[k]):
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
    ap.add_argument("--shard", default="",
                    help="read one shard of the tree only — movies/<shard>/ and series/<shard>/, and the people "
                         "their items credit — to compare with an export of that shard")
    ap.add_argument("--subset", action="store_true",
                    help="the tree holds only some of the database's items: rows only the database "
                         "has are reported but are not a difference")
    ap.add_argument("--differences", metavar="FILE",
                    help="with --compare: write every field difference here, one JSON object a line — the "
                         "field, the id, which side alone holds an entry (only), and both sides' values — where "
                         "the summary shows five of each field")
    ap.add_argument("--leave-out", metavar="FILE",
                    help="item ids, one a line, the database holds and the tree is not to: what a migration "
                         "left out by design (music, disc images) or did not adopt. They are taken out of the "
                         "export before the compare, and counted")
    args = ap.parse_args()

    r = Rebuild(os.path.abspath(args.root), args.text_language, shard=args.shard or None)
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
    left_out = 0
    if args.leave_out:
        with open(args.leave_out, encoding="utf-8") as f:
            leave = {l.strip() for l in f if l.strip() and not l.startswith("#")}
        items = export.get("items") or []
        export["items"] = [i for i in items if i.get("id") not in leave]
        left_out = len(items) - len(export["items"])
        # and the people only they credit: a person they share with an item the tree holds stays,
        # and is compared where that item is
        gone = {c.get("personId") for i in items if i.get("id") in leave for c in i.get("people") or []}
        kept = {c.get("personId") for i in export["items"] for c in i.get("people") or []}
        only_theirs = gone - kept - {None}
        people_left_out = 0
        if isinstance(export.get("people"), list):
            before = len(export["people"])
            export["people"] = [p for p in export["people"] if not isinstance(p, dict) or p.get("id") not in only_theirs]
            people_left_out = before - len(export["people"])
    lines, n, orphans = compare(r.items, r.people, export, ignore, args.subset, args.text_language, r.newest,
                                extras=r.extras, people_newest=r.people_newest, arrivals=args.arrivals_root,
                                differences=args.differences)
    print(f"compared with {args.compare} (ignoring {', '.join(sorted(ignore)) or 'nothing'}):")
    if args.leave_out:
        print(f"  left out: {left_out} item(s) of the export that {args.leave_out} names"
              + (f", and {people_left_out} person(s) only they credit" if people_left_out else ""))
    for line in lines:
        print(line)
    tail = f"; {orphans} orphan(s) on storage are safe to remove" if orphans else ""
    print(("the tree and the database agree" if not n else f"{n} difference(s)") + tail)
    return 1 if n else 0


if __name__ == "__main__":
    sys.exit(main())
