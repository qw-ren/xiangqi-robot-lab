"""Match recent per-cell observations against legal Xiangqi layouts."""

from collections import deque

MIN_CELL_SCORE = 0.55
MIN_RED_ELEPHANT_SCORE = 0.60


def cell_score_threshold(piece):
    return MIN_RED_ELEPHANT_SCORE if piece == "B" else MIN_CELL_SCORE


def piece_side(piece):
    return piece.isupper() if piece not in (None, ".", "x") else piece


class BoardEvidence:
    """Verify only the square changes of legal successors of the confirmed board."""

    def __init__(self, prior, candidates, required=(), robot_destination=None):
        prior = "".join(prior.splitlines())
        self.robot_destination = robot_destination
        self.candidates = {
            layout: {i: piece for i, piece in enumerate("".join(layout.splitlines()))
                     if piece != prior[i] or i in required}
            for layout in candidates
        }
        self.frames = deque(maxlen=5)

    def closest_changes(self, observed):
        return min(self.candidates.values(),
                   key=lambda changes: (sum(not self._matches(i, piece, observed[i])
                                            for i, piece in changes.items()), -len(changes)),
                   default={})

    def _matches(self, index, piece, seen):
        # The arm's commanded piece keeps its identity at the destination.
        if index == self.robot_destination and piece.islower():
            return piece_side(piece) == piece_side(seen)
        return seen == piece

    def reset(self):
        self.frames.clear()

    def add(self, cells):
        frame = tuple(cell["piece"] if cell["score"] >= cell_score_threshold(cell["piece"]) else None
                      for cell in cells)
        if len(frame) != 90:
            self.reset()
            return None
        self.frames.append(frame)
        if len(self.frames) < 3:
            return None
        supported = {}
        for layout, changes in self.candidates.items():
            def matches(sample):
                return all(self._matches(i, piece, sample[i]) for i, piece in changes.items())

            if changes and matches(frame) and sum(map(matches, self.frames)) >= 3:
                supported[layout] = changes.items()

        # An already-played red reply can provide all of the black move's evidence
        # plus its own. Keep the combined transition when both are supported.
        maximal = [layout for layout, changes in supported.items()
                   if not any(changes < other for other in supported.values())]
        return maximal[0] if len(maximal) == 1 else None
