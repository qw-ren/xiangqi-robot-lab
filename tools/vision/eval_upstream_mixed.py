"""Evaluate trusted local training checkpoints by argmax over the 16 classes."""
import argparse
import json
from collections import Counter
from pathlib import Path

import cv2
import numpy as np
import torch
from mmengine.config import Config
from mmengine.registry import init_default_scope
from mmpretrain.registry import MODELS
import cchess_reg.models


def evaluate(model, dataset: Path, split: str) -> dict:
    records = json.loads((dataset / "annotations" / f"{split}.json").read_text())["data_list"]
    correct = perfect = 0
    confusions = Counter()
    symbols = ".xKABNRCPkabnrcp"
    mean = np.float32([123.675, 116.28, 103.53])
    std = np.float32([58.395, 57.12, 57.375])
    for start in range(0, len(records), 8):
        batch = records[start:start + 8]
        images = []
        for row in batch:
            image = cv2.imdecode(np.fromfile(dataset / split / row["img_path"], np.uint8),
                                 cv2.IMREAD_COLOR)
            if image is None or image.shape != (500, 450, 3):
                raise ValueError(f"Invalid board image: {row['img_path']}")
            crop = cv2.resize(image[25:475, 25:425, ::-1], (280, 315))
            images.append(((crop - mean) / std).transpose(2, 0, 1))
        inputs = torch.from_numpy(np.stack(images)).cuda()
        with torch.inference_mode():
            pred = model(inputs, mode="tensor").argmax(-1).cpu().numpy()
        target = np.array([row["gt_label"] for row in batch])
        matches = pred == target
        correct += int(matches.sum())
        perfect += int(matches.all(axis=1).sum())
        confusions.update(f"{symbols[t]}->{symbols[p]}" for t, p in
                          zip(target[~matches], pred[~matches]))
    return {"boards": len(records), "correct_cells": correct, "total_cells": len(records) * 90,
            "perfect_boards": perfect, "confusions": dict(confusions.most_common())}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    checkpoints = sorted(args.run.glob("*.pth"))
    if not checkpoints:
        parser.error("no local checkpoints found in --run")
    init_default_scope("mmpretrain")
    cfg = Config.fromfile(args.run / "lab-mixed.py")
    model = MODELS.build(cfg.model).cuda().eval()
    results = []
    for path in checkpoints:
        state = torch.load(path, map_location="cpu", weights_only=False)["state_dict"]
        mismatch = model.load_state_dict(state, strict=False)
        if mismatch.unexpected_keys or any(
            not key.endswith(("relative_coords_table", "relative_position_index"))
            for key in mismatch.missing_keys
        ):
            raise ValueError(f"Incompatible checkpoint {path}: {mismatch}")
        results.append({"checkpoint": path.name,
                        "val": evaluate(model, args.dataset, "val"),
                        "public-val": evaluate(model, args.dataset, "public-val")})
        print(path.name, results[-1]["val"]["correct_cells"],
              results[-1]["public-val"]["correct_cells"], flush=True)
    (args.run / "board-validation.json").write_text(json.dumps(results, indent=2) + "\n",
                                                    encoding="utf-8")


if __name__ == "__main__":
    main()
