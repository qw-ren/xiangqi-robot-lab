"""Inspect classifier input and fit an experimental adapter on labeled boards."""

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from core.runonnx.full_classifier import FULL_CLASSIFIER_ONNX


def load_board(warp: Path, labels: Path, model) -> tuple:
    image = cv2.imdecode(np.fromfile(warp, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Cannot read {warp}")
    rows = labels.read_text(encoding="utf-8-sig").splitlines()
    if len(rows) != 10 or any(len(row) != 9 for row in rows):
        raise ValueError(f"Expected 10 rows of 9 labels in {labels}")
    symbols = list(model.label_2_short.values())
    target = np.array([symbols.index(cell) for row in rows for cell in row])
    tensor = model.preprocess_image(image, is_rgb=False)
    scores = model.run_inference(tensor)[0].astype(np.float64)
    if scores.shape != (90, len(symbols)) or not np.isfinite(scores).all():
        raise ValueError("Invalid classifier output")
    rgb = np.rint(tensor[0].transpose(1, 2, 0) * [58.395, 57.12, 57.375]
                  + [123.675, 116.28, 103.53]).clip(0, 255).astype(np.uint8)
    return scores, target, cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for split in ("train", "eval"):
        parser.add_argument(f"--{split}", nargs=2, action="append", required=True,
                            metavar=("WARP", "MANUAL_LABELS"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    train_paths = {Path(warp).resolve() for warp, _ in args.train}
    if train_paths.intersection(Path(warp).resolve() for warp, _ in args.eval):
        parser.error("training and evaluation must use different images")
    args.out.mkdir(parents=True, exist_ok=True)
    model = FULL_CLASSIFIER_ONNX(str(ROOT / "model/layout_recognition/nano_v3-0319.onnx"))
    symbols = list(model.label_2_short.values())
    batches = {}
    for split, inputs in (("train", args.train), ("eval", args.eval)):
        batches[split] = []
        for index, (warp, labels) in enumerate(inputs, 1):
            scores, target, preview = load_board(Path(warp), Path(labels), model)
            cv2.imencode(".png", preview)[1].tofile(args.out / f"{split}-{index}-model-input.png")
            batches[split].append((scores, target))

    scores = np.concatenate([scores for scores, _ in batches["train"]])
    targets = np.concatenate([target for _, target in batches["train"]])
    # Fit a ridge-regularized residual on the frozen model's per-cell outputs.
    # Unseen classes keep their original scores.
    features = np.column_stack((scores, np.ones(len(scores))))
    weights = np.zeros((features.shape[1], len(symbols)))
    seen = np.unique(targets)
    residual = np.eye(len(symbols))[targets] - scores
    weights[:, seen] = np.linalg.solve(
        features.T @ features + 8.0 * np.eye(features.shape[1]),
        features.T @ residual[:, seen],
    )
    np.savez(args.out / "score-adapter.npz", weights=weights, symbols=symbols)
    report = {"train_boards": len(args.train), "unseen_classes": [s for i, s in enumerate(symbols)
                                                               if i not in seen], "evaluation": []}
    for index, (scores, target) in enumerate(batches["eval"], 1):
        adapted = scores + np.column_stack((scores, np.ones(len(scores)))) @ weights
        before, after = scores.argmax(axis=1), adapted.argmax(axis=1)
        report["evaluation"].append({
            "board": index, "baseline_correct": int(sum(before == target)),
            "adapted_correct": int(sum(after == target)),
            "review_cells": [
                {"row": i + 1, "col": j + 1, "expected": symbols[target[k]],
                 "baseline": symbols[before[k]], "adapted": symbols[after[k]]}
                for k in np.flatnonzero((before != target) | (after != target))
                for i, j in [divmod(int(k), 9)]
            ],
        })
    output = json.dumps(report, ensure_ascii=False, indent=2)
    (args.out / "report.json").write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
