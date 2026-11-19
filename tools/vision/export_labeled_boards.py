"""Validate hand-labeled snapshots and write a session-separated dataset manifest."""

import argparse
import hashlib
import json
from pathlib import Path


SYMBOLS = frozenset(".xKABNRCPkabnrcp")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for split in ("train", "val", "test"):
        parser.add_argument(f"--{split}", action="append", type=Path, default=[],
                            metavar="SESSION_DIRECTORY")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if not args.train or not args.val:
        parser.error("provide at least one training session and one validation session")

    sessions, images, records = set(), set(), []
    for split in ("train", "val", "test"):
        for session in getattr(args, split):
            session = session.resolve(strict=True)
            if session in sessions:
                parser.error(f"session listed more than once: {session}")
            sessions.add(session)
            snapshots = sorted(session.glob("*/warped.png"))
            if not snapshots:
                parser.error(f"no snapshot directories with warped.png in {session}")
            for warp in snapshots:
                snapshot = warp.parent
                labels = snapshot / "manual.txt"
                if not labels.is_file():
                    parser.error(f"missing human annotation: {labels}")
                rows = labels.read_text(encoding="utf-8-sig").splitlines()
                if (len(rows) != 10 or any(len(row) != 9 for row in rows)
                        or set("".join(rows)) - SYMBOLS):
                    parser.error(f"expected 10 rows of 9 valid symbols in {labels}")
                frame = snapshot / "frame.png"
                if not frame.is_file():
                    parser.error(f"missing original frame: {frame}")
                digest = hashlib.sha256(frame.read_bytes()).hexdigest()
                if digest in images:
                    parser.error(f"duplicate original frame: {frame}")
                images.add(digest)
                records.append({"split": split, "session": str(session), "frame": str(frame),
                                "warped": str(warp), "labels": rows, "frame_sha256": digest})
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Validated {len(records)} boards from {len(sessions)} sessions")


if __name__ == "__main__":
    main()
