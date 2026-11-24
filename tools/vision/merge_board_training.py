"""Combine the public board corpus with local training boards; keep validation separate."""
import argparse
import json
import shutil
from pathlib import Path
from zipfile import ZipFile

SYMBOLS = ".xKABNRCPkabnrcp"
PREFIX = "data/cchess_multi_label_layout/"


def check_label(record: dict) -> None:
    labels = record["gt_label"]
    if len(labels) != 90 or any(type(i) is not int or not 0 <= i < 16 for i in labels):
        raise ValueError(f"Invalid labels for {record['img_path']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-zip", type=Path, required=True)
    parser.add_argument("--local", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out / "annotations").mkdir()
    splits = {}
    with ZipFile(args.public_zip) as archive:
        for split in ("train", "val"):
            source = json.loads(archive.read(f"{PREFIX}annotations/{split}.json"))
            if source["metainfo"]["classes"] != list(SYMBOLS):
                raise ValueError("Unexpected public class order")
            dest_split = "train" if split == "train" else "public-val"
            folder = args.out / dest_split
            folder.mkdir()
            rows = []
            for entry in source["data_list"]:
                check_label(entry)
                name = Path(entry["img_path"]).name
                if name != entry["img_path"]:
                    raise ValueError(f"Unexpected archive image path: {entry['img_path']}")
                out_name = "public-" + name
                with archive.open(f"{PREFIX}{split}/{name}") as inp, (folder / out_name).open("xb") as out:
                    shutil.copyfileobj(inp, out)
                rows.append({"img_path": out_name, "gt_label": entry["gt_label"]})
            splits[dest_split] = rows
    for split in ("train", "val"):
        folder = args.out / split
        folder.mkdir(exist_ok=True)
        local = json.loads((args.local / "annotations" / f"{split}.json").read_text(encoding="utf-8"))
        rows = splits.setdefault(split, [])
        for entry in local["data_list"]:
            check_label(entry)
            name = Path(entry["img_path"]).name
            if name != entry["img_path"]:
                raise ValueError(f"Unexpected local image path: {entry['img_path']}")
            out_name = "local-" + name
            with (args.local / split / name).open("rb") as inp, (folder / out_name).open("xb") as out:
                shutil.copyfileobj(inp, out)
            rows.append({"img_path": out_name, "gt_label": entry["gt_label"]})
    for split, rows in splits.items():
        payload = {"metainfo": {"classes": list(SYMBOLS)}, "data_list": rows}
        (args.out / "annotations" / f"{split}.json").write_text(json.dumps(payload, indent=2),
                                                               encoding="utf-8")
        print(f"{split}: {len(rows)} boards")


if __name__ == "__main__":
    main()
