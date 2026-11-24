from vision.board_evidence import BoardEvidence


PRIOR = "Rn" + "." * 88
AFTER = ".nR" + "." * 87


def cells(layout, low=()):
    return [{"piece": piece, "score": 0.2 if index in low else 0.95}
            for index, piece in enumerate(layout)]


def test_stable_move_ignores_low_scores_and_one_transient_error_on_unchanged_square():
    evidence = BoardEvidence(PRIOR, [AFTER])
    assert evidence.add(cells(AFTER, low=(1,))) is None
    assert evidence.add(cells(AFTER.replace("n", "p"))) is None
    assert evidence.add(cells(AFTER, low=(1,))) == AFTER


def test_conflicting_unchanged_square_or_weak_destination_blocks_move():
    for sample in (cells(AFTER.replace("n", "p")), cells(AFTER, low=(2,))):
        evidence = BoardEvidence(PRIOR, [AFTER])
        for _ in range(5):
            assert evidence.add(sample) is None


def test_ambiguous_candidates_need_a_unique_confirmed_board():
    alternative = ".n.R" + "." * 86
    evidence = BoardEvidence(PRIOR, [AFTER, alternative])
    for _ in range(2):
        assert evidence.add(cells(alternative)) is None
    assert evidence.add(cells(alternative)) == alternative


def test_same_board_requires_fresh_evidence_at_explicitly_required_squares():
    evidence = BoardEvidence(PRIOR, [PRIOR], required=(0,))
    for _ in range(3):
        assert evidence.add(cells(PRIOR, low=(0,))) is None
    for _ in range(2):
        assert evidence.add(cells(PRIOR)) is None
    assert evidence.add(cells(PRIOR)) == PRIOR
