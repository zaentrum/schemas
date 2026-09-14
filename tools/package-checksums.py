#!/usr/bin/env python3
"""Write or verify the checksums of package files in library item folders.

Usage:
  package-checksums.py ITEM_FOLDER [ITEM_FOLDER ...]            write checksums.sha256 and record it in manifest.json
  package-checksums.py --verify ITEM_FOLDER [ITEM_FOLDER ...]   recompute and compare, change nothing

An item folder is movies/<aa>/<id>/ or shows/<aa>/<id>/; a series folder is walked into its episodes. For every
version with a package, the package files in the version's folder (hls/, subs/, trickplay/, trailers/ and the
.complete marker) are hashed into checksums.sha256 in that folder — the format `sha256sum -c` reads — and the
manifest's package.checksums records the file's own hash, the number of files and their total size.

It must run where the package files are readable, i.e. on storage or in a pod that mounts it. Files are written
next to their target and renamed over it, so a hard-linked manifest is never written through.
"""
import datetime, hashlib, json, os, re, sys, tempfile

PACKAGE_DIRS = ("hls", "subs", "trickplay", "trailers")
PACKAGE_MARKERS = (".complete",)
OS_ARTEFACTS = re.compile(r"^(\.DS_Store|\._.*|Thumbs\.db|desktop\.ini|@eaDir|\.@__thumb|#recycle|\.AppleDouble)$")
NAME = "checksums.sha256"


def now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def package_files(vp):
    out = []
    for d in PACKAGE_DIRS:
        for root, dirs, files in os.walk(os.path.join(vp, d)):
            dirs[:] = sorted(x for x in dirs if not OS_ARTEFACTS.match(x))
            out += [os.path.relpath(os.path.join(root, f), vp) for f in sorted(files) if not OS_ARTEFACTS.match(f)]
    out += [m for m in PACKAGE_MARKERS if os.path.isfile(os.path.join(vp, m))]
    return sorted(out)


def replace_file(path, data):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def items(folder):
    man = json.load(open(os.path.join(folder, "manifest.json")))
    if man.get("type") == "series":
        for season in man["series"]["seasons"]:
            for e in season["episodes"]:
                yield from items(os.path.join(folder, e["path"]))
    else:
        yield folder, man


def main():
    args = sys.argv[1:]
    verify = "--verify" in args
    folders = [a for a in args if a != "--verify"]
    if not folders:
        sys.exit(__doc__)
    problems = written = verified = 0
    for top in folders:
        for folder, man in items(top):
            changed = False
            for v in man.get("versions") or []:
                pkg = v.get("package")
                if not pkg or pkg.get("state") not in ("complete", "stale"):
                    continue
                vp = os.path.normpath(os.path.join(folder, v["path"]))
                files = package_files(vp)
                lines = [f"{sha_file(os.path.join(vp, rel))}  {rel}" for rel in files]
                data = ("\n".join(lines) + "\n").encode()
                if verify:
                    target = os.path.join(vp, NAME)
                    if not os.path.isfile(target) or open(target, "rb").read() != data:
                        print(f"MISMATCH {vp}")
                        problems += 1
                    else:
                        verified += 1
                    continue
                replace_file(os.path.join(vp, NAME), data)
                pkg["checksums"] = {"file": NAME, "algorithm": "sha256", "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
                                    "files": len(files), "bytes": sum(os.path.getsize(os.path.join(vp, rel)) for rel in files), "at": now()}
                changed = True
                written += 1
            if changed:
                man["rev"] = man.get("rev", 0) + 1
                man["updatedAt"] = now()
                man["updatedBy"] = "package-checksums"
                replace_file(os.path.join(folder, "manifest.json"), (json.dumps(man, indent=2, ensure_ascii=False) + "\n").encode())
    if verify:
        print(f"verified {verified} package(s), {problems} mismatch(es)")
        sys.exit(1 if problems else 0)
    print(f"wrote checksums for {written} package(s)")


if __name__ == "__main__":
    main()
