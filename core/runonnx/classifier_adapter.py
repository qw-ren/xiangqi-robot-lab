"""NumPy inference for a residual whole-board classifier adapter."""

import numpy as np

FEATURE_COUNT = 22


def features(scores: np.ndarray, tensor: np.ndarray) -> np.ndarray:
    """Combine ONNX scores with color statistics near each grid intersection."""
    import cv2

    rgb = tensor[0].transpose(1, 2, 0) * [58.395, 57.12, 57.375]
    rgb = ((rgb + [123.675, 116.28, 103.53]) / 255).clip(0, 1).astype(np.float32)
    height, width = rgb.shape[:2]
    patches = np.stack([
        cv2.getRectSubPix(rgb, (19, 19), (width * x / 400, height * y / 450))
        for y in np.linspace(25, 425, 10) for x in np.linspace(25, 375, 9)
    ])
    red_excess = patches[..., 0] - patches[..., 1:].max(axis=-1)
    darkness = patches.min(axis=-1)
    stats = np.column_stack((
        patches.mean(axis=(1, 2)),
        (red_excess > 0.1).mean(axis=(1, 2)),
        (darkness < 0.25).mean(axis=(1, 2)),
        red_excess.max(axis=(1, 2)),
    ))
    return np.column_stack((scores, stats)).astype(np.float32)


def corrected_scores(inputs: np.ndarray, weight: np.ndarray, bias: np.ndarray) -> np.ndarray:
    logits = np.log(inputs[:, :16].clip(min=1e-5)) + inputs @ weight.T + bias
    prob = np.exp(logits - logits.max(axis=1, keepdims=True))
    return prob / prob.sum(axis=1, keepdims=True)
