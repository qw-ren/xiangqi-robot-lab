"""Match recent per-cell observations against legal Xiangqi layouts."""

from collections import Counter, deque


class BoardEvidence:
    """Use the last confirmed board as the prior for unchanged squares."""

    def __init__(self, prior, candidates, required=()):
        self.prior = "".join(prior.splitlines())
        self.candidates = {
            layout: (flat, tuple(i for i, piece in enumerate(flat) if piece != self.prior[i] or i in required))
            for layout in candidates for flat in ("".join(layout.splitlines()),)
        }
        self.frames = deque(maxlen=5)

    def reset(self):
        self.frames.clear()

    def add(self, cells):
        frame = tuple(cell["piece"] if cell["score"] >= 0.55 else None for cell in cells)
        if len(frame) != 90:
            self.reset()
            return None
        self.frames.append(frame)
        if len(self.frames) < 3:
            return None
        votes = [Counter(column) for column in zip(*self.frames)]
        candidates = []
        for layout, (pieces, changed) in self.candidates.items():
            if not all(frame[i] == pieces[i] and votes[i][pieces[i]] >= 3 for i in changed):
                continue
            if any(sum(count for seen, count in counts.items() if seen not in (None, "x", piece)) >= 3
                   for piece, counts in zip(pieces, votes)):
                continue
            candidates.append(layout)
        return candidates[0] if len(candidates) == 1 else None
