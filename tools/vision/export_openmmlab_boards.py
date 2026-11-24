"""Export verified board labels to the upstream MMPretrain dataset format."""
import argparse
import json
import shutil
from pathlib import Path


SYMBOLS = ".xKABNRCPkabnrcp"
CLASSES = ("point", "other", "red_king", "red_advisor", "red_bishop", "red_knight",
           "red_rook", "red_cannon", "red_pawn", "black_king", "black_advisor",
           "black_bishop", "black_knight", "black_rook", "black_cannon", "black_pawn")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    records = json.loads(args.manifest.read_text(encoding="utf-8"))
    splits = {split: [] for split in ("train", "val", "test")}
    for record in records:
        split = record["split"]
        warp = Path(record["warped"])
        rows = record["labels"]
        if len(rows) != 10 or any(len(row) != 9 for row in rows):
            raise ValueError(f"Expected a 10x9 board: {warp}")
        name = f"{Path(record['session']).name}-{warp.parent.name}"
        splits[split].append((name, warp, rows))
    if not splits["train"] or not splits["val"]:
        parser.error("train and val must both contain labeled boards")
    annotations = args.out / "annotations"
    annotations.mkdir(parents=True, exist_ok=True)
    for split, boards in splits.items():
        if not boards:
            continue
        destination = args.out / split
        destination.mkdir(exist_ok=True)
        items = []
        names = set()
        for name, warp, rows in boards:
            if name in names:
                raise ValueError(f"Duplicate sample name: {name}")
            names.add(name)
            items.append({"img_path": name + ".png",
                          "gt_label": [SYMBOLS.index(cell) for row in rows for cell in row]})
            shutil.copyfile(warp, destination / (name + ".png"))
            (destination / (name + ".txt")).write_text("\n".join(rows) + "\n", encoding="utf-8")
        payload = {"metainfo": {"classes": CLASSES}, "data_list": items}
        (annotations / f"{split}.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"{split}: {len(items)} boards")


if __name__ == "__main__":
    main()
