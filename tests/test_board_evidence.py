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


def test_persistent_unrelated_occupancy_error_does_not_overwrite_confirmed_board():
    evidence = BoardEvidence(PRIOR, [AFTER])
    sample = cells(AFTER.replace("n", "."))
    for _ in range(2):
        assert evidence.add(sample) is None
    assert evidence.add(sample) == AFTER


def test_weak_destination_blocks_move():
    evidence = BoardEvidence(PRIOR, [AFTER])
    for _ in range(5):
        assert evidence.add(cells(AFTER, low=(2,))) is None


def test_move_requires_joint_evidence_for_both_squares():
    evidence = BoardEvidence(PRIOR, [AFTER])
    for weak in ((0,), (2,), (0,), (2,), ()):
        assert evidence.add(cells(AFTER, low=weak)) is None


def test_early_red_move_uses_combined_transition_if_both_moves_are_confirmed():
    prior = "r.R." + "." * 86
    black_after = ".rR." + "." * 86
    red_after = ".r.R" + "." * 86
    evidence = BoardEvidence(prior, [black_after, red_after], robot_destination=1)
    for _ in range(2):
        assert evidence.add(cells(red_after)) is None
    assert evidence.add(cells(red_after)) == red_after


def test_red_capture_requires_target_piece_to_replace_captured_piece():
    prior = "Rbn" + "." * 87
    after = ".Rn" + "." * 87
    evidence = BoardEvidence(prior, [after])
    for sample in (".b." + "." * 87, "..." + "." * 87):
        for _ in range(3):
            assert evidence.add(cells(sample)) is None
    for _ in range(2):
        assert evidence.add(cells(".R." + "." * 87)) is None
    assert evidence.add(cells(".R." + "." * 87)) == after


def test_known_robot_move_retains_identity_despite_two_classification_errors():
    prior = "bR" + "." * 88
    expected = ".Rb" + "." * 87
    evidence = BoardEvidence(prior, [expected], robot_destination=2)
    frame = cells(".Bp" + "." * 87)
    assert evidence.add(frame) is None
    assert evidence.add(frame) is None
    assert evidence.add(frame) == expected


def test_robot_verification_rejects_empty_destination_or_wrong_color():
    for seen in (".R.", ".RP", "bRp"):
        evidence = BoardEvidence("bR" + "." * 88, [".Rb" + "." * 87], robot_destination=2)
        for _ in range(5):
            assert evidence.add(cells(seen + "." * 87)) is None


def test_red_elephant_requires_higher_score_when_confirming_a_move():
    after = ".nB" + "." * 87
    evidence = BoardEvidence("Bn" + "." * 88, [after])
    frame = cells(after)
    frame[2]["score"] = 0.58
    for _ in range(5):
        assert evidence.add(frame) is None
    for _ in range(2):
        assert evidence.add(cells(after)) is None
    assert evidence.add(cells(after)) == after


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
