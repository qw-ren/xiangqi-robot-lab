"""Train and validate the ONNX classifier's residual adapter on CUDA."""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

import numpy as np
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from core.runonnx.classifier_adapter import corrected_scores, features


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--input-width", type=int, choices=(280, 288), default=280)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--features-only", action="store_true")
    mode.add_argument("--fit-only", action="store_true", help="Use the features already extracted into --out")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    cache = args.out / ("features.npz" if args.input_width == 280 else "features-288x324.npz")
    if not args.fit_only:
        extract_features(args.manifest, cache, parser, args.input_width)
    if args.features_only:
        return
    with np.load(cache) as data:
        fit(args.out, **dict(data), input_width=args.input_width)


def extract_features(manifest: Path, cache: Path, parser: argparse.ArgumentParser, input_width: int = 280) -> None:
    import cv2
    from core.runonnx.full_classifier import FULL_CLASSIFIER_ONNX

    records = json.loads(manifest.read_text(encoding="utf-8-sig"))
    train = [record for record in records if record["split"] == "train"]
    val = [record for record in records if record["split"] == "val"]
    for key in ("session", "frame_sha256"):
        if not train or not val or {r[key] for r in train} & {r[key] for r in val}:
            parser.error("Training and validation must have separate capture sessions and frames")
    model = FULL_CLASSIFIER_ONNX(str(ROOT / "model/layout_recognition/nano_v3-0319.onnx"),
                                 input_size=(input_width, input_width * 9 // 8))
    symbols = "".join(model.label_2_short.values())

    def collect(split: list) -> tuple:
        inputs, targets = [], []
        for record in split:
            warp = cv2.imdecode(np.fromfile(record["warped"], np.uint8), cv2.IMREAD_COLOR)
            tensor = model.preprocess_image(warp, is_rgb=False)
            scores = model.run_inference(tensor)[0]
            inputs.append(features(scores, tensor))
            targets.extend(symbols.index(cell) for row in record["labels"] for cell in row)
        return np.concatenate(inputs), np.asarray(targets)

    train_features, train_targets = collect(train)
    val_features, val_targets = collect(val)
    np.savez(cache, train_features=train_features, train_targets=train_targets,
             val_features=val_features, val_targets=val_targets, symbols=symbols)


def fit(out: Path, train_features: np.ndarray, train_targets: np.ndarray,
        val_features: np.ndarray, val_targets: np.ndarray, symbols: np.ndarray, input_width: int = 280) -> None:
    import torch
    from torch import nn
    from torch.nn import functional as F

    if not torch.cuda.is_available():
        sys.exit("CUDA is required to train this adapter")
    symbols = symbols.item()
    torch.manual_seed(42)
    x, y = torch.from_numpy(train_features).cuda(), torch.from_numpy(train_targets).long().cuda()
    baseline_logits = x[:, :16].clamp_min(1e-5).log()
    head = nn.Linear(x.shape[1], len(symbols)).cuda()
    nn.init.zeros_(head.weight)
    nn.init.zeros_(head.bias)
    optimizer = torch.optim.AdamW(head.parameters(), lr=0.025)
    for _ in range(80):
        optimizer.zero_grad()
        residual = head(x)
        loss = F.cross_entropy(baseline_logits + residual, y) + 0.02 * residual.square().mean()
        loss.backward()
        optimizer.step()

    weight = head.weight.detach().cpu().numpy()
    bias = head.bias.detach().cpu().numpy()
    adapted = corrected_scores(val_features, weight, bias)
    report = {"device": torch.cuda.get_device_name(), "train_boards": len(train_targets) // 90,
              "val_boards": len(val_targets) // 90, "total_cells": len(val_targets)}
    for name, scores in (("baseline", val_features[:, :16]), ("adapter", adapted)):
        predictions = scores.argmax(axis=1)
        correct = predictions == val_targets
        report[name] = {
            "correct": int(correct.sum()),
            "perfect_boards": int(correct.reshape(-1, 90).all(axis=1).sum()),
            "confusions": Counter(f"{symbols[a]}->{symbols[b]}"
                                  for a, b in zip(val_targets[~correct], predictions[~correct])),
        }
    filename = "nano_v3-0319.adapter.npz" if input_width == 280 else "nano_v3-0319.288x324.adapter.npz"
    np.savez(out / filename, weight=weight, bias=bias, symbols=symbols)
    output = json.dumps(report, indent=2)
    (out / "report.json").write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
