"""Train a CUDA grid classifier on the labeled board manifest."""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

SYMBOLS = ".xKABNRCPkabnrcp"


def grid_patches(warp: np.ndarray) -> np.ndarray:
    """Extract a 48 px patch around each nominal intersection of the 450x500 warp."""
    if warp.shape != (500, 450, 3):
        raise ValueError(f"Unexpected rectified image shape: {warp.shape}")
    rgb = cv2.cvtColor(warp, cv2.COLOR_BGR2RGB)
    return np.stack([
        cv2.getRectSubPix(rgb, (48, 48), (50 + col * 350 / 8, 50 + row * 400 / 9))
        for row in range(10) for col in range(9)
    ]).transpose(0, 3, 1, 2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--epochs", type=int, default=24)
    args = parser.parse_args()
    if args.epochs < 1:
        parser.error("epochs must be positive")
    if not torch.cuda.is_available():
        parser.error("PyTorch CUDA is required")
    device = torch.device("cuda:0")
    print(f"Training device: {torch.cuda.get_device_name(device)}, CUDA {torch.version.cuda}", flush=True)
    torch.manual_seed(42)
    args.out.mkdir(parents=True, exist_ok=True)
    symbols = list(SYMBOLS)
    records = json.loads(args.manifest.read_text(encoding="utf-8"))
    splits = {}
    for split in ("train", "val"):
        patches, labels, baselines = [], [], []
        for record in records:
            if record["split"] != split:
                continue
            warp = cv2.imdecode(np.fromfile(record["warped"], np.uint8), cv2.IMREAD_COLOR)
            if warp is None:
                raise ValueError(f"Unreadable image: {record['warped']}")
            patches.append(grid_patches(warp))
            labels.extend(symbols.index(cell) for row in record["labels"] for cell in row)
            if split == "val":
                rows = Path(record["warped"]).with_name("layout.txt").read_text(encoding="utf-8").splitlines()
                if len(rows) != 10 or any(len(row) != 9 for row in rows):
                    raise ValueError("Invalid saved baseline layout")
                baselines.extend(symbols.index(cell) for row in rows for cell in row)
        if not patches:
            parser.error(f"no samples for {split}")
        splits[split] = (
            torch.from_numpy(np.concatenate(patches)).to(device),
            torch.tensor(labels, device=device),
            torch.tensor(baselines, dtype=torch.long, device=device),
        )

    model = nn.Sequential(
        nn.Conv2d(3, 24, 3, padding=1), nn.BatchNorm2d(24), nn.ReLU(), nn.MaxPool2d(2),
        nn.Conv2d(24, 48, 3, padding=1), nn.BatchNorm2d(48), nn.ReLU(), nn.MaxPool2d(2),
        nn.Conv2d(48, 64, 3, padding=1), nn.BatchNorm2d(64), nn.ReLU(),
        nn.AdaptiveAvgPool2d((4, 4)), nn.Flatten(), nn.Linear(64 * 16, len(symbols)),
    ).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.001)
    train_x, train_y, _ = splits["train"]
    val_x, val_y, baseline = splits["val"]
    counts = train_y.bincount(minlength=len(symbols))
    weights = counts.clamp_min(1).float().rsqrt()
    weights /= weights.mean()
    best = -1
    history = []
    baseline_correct = int((baseline == val_y).sum())
    print(f"Train cells: {len(train_y)}, validation cells: {len(val_y)}, ONNX correct: {baseline_correct}", flush=True)
    for epoch in range(1, args.epochs + 1):
        model.train()
        losses = []
        for indexes in torch.randperm(len(train_y), device=device).split(128):
            batch = train_x[indexes].float() / 255
            angles = (torch.rand(len(batch), device=device) - 0.5) * 0.6
            cos, sin = angles.cos(), angles.sin()
            dx, dy = (torch.rand(2, len(batch), device=device) - 0.5) * 0.20
            affine = torch.stack((cos, -sin, dx, sin, cos, dy), dim=1).reshape(-1, 2, 3)
            grid = F.affine_grid(affine, batch.shape, align_corners=False)
            batch = F.grid_sample(batch, grid, padding_mode="border", align_corners=False)
            batch = (batch * (0.8 + 0.4 * torch.rand(len(batch), 1, 1, 1, device=device))).clamp(0, 1)
            optimizer.zero_grad(set_to_none=True)
            loss = F.cross_entropy(model(batch), train_y[indexes], weight=weights)
            loss.backward()
            optimizer.step()
            losses.append(loss.detach())
        model.eval()
        with torch.inference_mode():
            pred = torch.cat([model(x.float() / 255).argmax(1) for x in val_x.split(256)])
            correct = int((pred == val_y).sum())
            entire_boards = int((pred == val_y).reshape(-1, 90).all(1).sum())
        result = {"epoch": epoch, "loss": float(torch.stack(losses).mean()),
                  "val_correct": correct, "val_perfect_boards": entire_boards}
        print(json.dumps(result), flush=True)
        history.append(result)
        if correct > best:
            best = correct
            torch.save({"model": model.state_dict(), "symbols": symbols, "result": result},
                       args.out / "best.pt")
            (args.out / "best-predictions.json").write_text(json.dumps([
                {"expected": symbols[y], "baseline": symbols[b], "trained": symbols[p]}
                for y, b, p in zip(val_y.tolist(), baseline.tolist(), pred.tolist())
            ], indent=2), encoding="utf-8")
    report = {"device": torch.cuda.get_device_name(device), "cuda": torch.version.cuda,
              "peak_allocated_bytes": torch.cuda.max_memory_allocated(device),
              "val_cells": len(val_y), "baseline_correct": baseline_correct, "best_correct": best,
              "train_class_counts": dict(zip(symbols, counts.tolist())), "epochs": history}
    (args.out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
