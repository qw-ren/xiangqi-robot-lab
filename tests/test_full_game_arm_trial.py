from unittest.mock import Mock, patch

import pytest

from tools.vision import full_game_arm_trial as game
from tools.vision.one_move_arm_trial import starting_scores_ok, wait_for_layout


@pytest.mark.parametrize("piece,score,accepted", [
    (".", 0.79, True),
    (".", 0.0, True),
    ("R", 0.75, True),
    ("R", 0.74, False),
    ("x", 0.95, False),
])
def test_starting_layout_ignores_empty_cell_scores(piece, score, accepted):
    cells = [{"piece": ".", "score": 0.99}] * 89 + [{"piece": piece, "score": score}]
    assert starting_scores_ok({"reasons": ["low_scores"], "cells": cells}) is accepted


def test_opening_accepts_three_matching_frames_and_omits_empty_cells_from_low_score_report():
    expected = game.layout(game.START_FEN)
    cells = [dict(row=row, col=col, piece=piece, score=0.01 if piece == "." else 0.75)
             for row, rank in enumerate(expected.splitlines()) for col, piece in enumerate(rank)]
    frames = [dict(observation=dict(frame_id=i, source="aruco", layout=expected,
                                   cells=cells, reasons=["low_scores"])) for i in range(1, 4)]
    report = Mock()
    with patch("urllib.request.urlopen"), patch("json.load", side_effect=frames), patch("time.sleep"):
        actual = wait_for_layout(expected, game.time.monotonic() + 5, starting_position=True, report=report)
    assert actual == expected
    report.assert_called_once_with(1, "aruco", [], [])


def test_opening_requests_confirmation_on_frame_12_even_when_recognition_changes():
    expected = game.layout(game.START_FEN)

    def frame(number):
        seen = expected if number % 2 else "x" + expected[1:]
        cells = [dict(row=row, col=col, piece=piece, score=0.7 if (row, col) == (0, 0) else 0.99)
                 for row, rank in enumerate(seen.splitlines()) for col, piece in enumerate(rank)]
        reasons = ["low_scores"] if number % 2 else ["unknown_cells"]
        return dict(observation=dict(frame_id=number, source="aruco", layout=seen, cells=cells, reasons=reasons))

    frames = [response for number in range(1, 25) for response in (frame(number), frame(number))]
    with patch("urllib.request.urlopen"), patch("json.load", side_effect=frames) as receive, patch("time.sleep"):
        confirmations = []

        def confirm(observed, cells):
            confirmations.append(receive.call_count)
            assert observed[0] == cells[0]["piece"] == "x"
            return len(confirmations) == 2

        assert wait_for_layout(expected, game.time.monotonic() + 5, starting_position=True,
                               confirm_low_scores=confirm) == expected
    assert confirmations == [23, 47]


def test_manual_opening_confirmation_lists_unknown_square_and_checks_expected_board(capsys):
    expected = game.layout(game.START_FEN)
    cells = [dict(row=0, col=0, piece="x", score=0.9)]
    journal = Mock()
    with patch("builtins.input", return_value="") as confirm:
        assert game.confirm_starting_position("x" + expected[1:], cells, expected=expected, journal=journal)
    assert "实物棋盘与预期局面" in confirm.call_args.args[0]
    assert "a9 应为黑车，识别为未知" in capsys.readouterr().out
    assert journal.status.call_args.args[0] == "review"


def test_unknown_endgame_requests_rescan_instead_of_accepting_invalid_layout():
    cells = [dict(row=0, col=0, piece="x", score=0.9)]
    journal = Mock()
    with patch("builtins.input", return_value="") as confirm:
        assert not game.confirm_starting_position("x", cells, validate_layout=lambda _: False, journal=journal)
    assert "重扫" in confirm.call_args.args[0]
    journal.status.assert_called_with("setup")


@pytest.mark.parametrize(("position", "outcome"), [
    ("R3k4/4R4/9/9/9/9/9/9/9/4K4 b - - 0 1", "checkmate"),
    ("4k4/3R1R3/4P4/9/9/9/9/9/9/4K4 b - - 0 1", "stalemate"),
])
def test_terminal_endgame_celebrates_without_playing_a_move(position, outcome, capsys):
    observed = game.layout(position)
    controller, engine = Mock(), Mock()
    with patch.object(game, "wait_for_matching_layout", return_value=observed) as wait:
        game.play(controller, engine, 10, endgame_turn="b")
    assert wait.call_args.kwargs["validate_layout"](observed)
    assert f"Red wins by {outcome}" in capsys.readouterr().out
    controller.celebrate.assert_called_once_with()
    controller.transfer.assert_not_called()
    assert engine.mock_calls == []


def test_black_captures_use_successive_slots_and_park():
    position = "r2k5/9/9/P8/9/P8/9/9/9/1N2K4 b - - 0 1"
    first = game.checked_transition(position, "a9a6")
    human = game.checked_transition(first["fen"], "b0c2")
    second = game.checked_transition(human["fen"], "a6a4")
    controller = Mock()
    with patch.object(game, "wait_for_layout"), patch.object(
        game, "wait_for_matching_layout",
        side_effect=[game.layout(human["fen"]), game.layout(second["fen"]), KeyboardInterrupt],
    ), patch("builtins.input") as confirm:
        with pytest.raises(KeyboardInterrupt):
            game.play(controller, None, 10, fen=position, captures=3)
    confirm.assert_not_called()
    assert [call.args[:2] for call in controller.transfer.call_args_list] == [
        ("a6", "capture4"), ("a9", "a6"), ("a4", "capture5"), ("a6", "a4"),
    ]
    assert all(call.kwargs["return_to_park"] for call in controller.transfer.call_args_list)


def test_post_transfer_timeout_retries_without_input_or_repeating_arm_motion():
    position = "r2k5/9/9/P8/9/P8/9/9/9/1N2K4 b - - 0 1"
    controller = Mock()
    expected = game.layout(game.checked_transition(position, "a9a6")["fen"])
    with patch.object(game, "wait_for_layout"), patch.object(
        game, "wait_for_matching_layout",
        side_effect=[TimeoutError("wrong destination"), expected, KeyboardInterrupt],
    ) as check, patch("builtins.input", return_value="") as confirm:
        with pytest.raises(KeyboardInterrupt):
            game.play(controller, None, 10, fen=position)
    assert [call.args[:2] for call in controller.transfer.call_args_list] == [
        ("a6", "capture1"), ("a9", "a6"),
    ]
    confirm.assert_not_called()
    assert check.call_args_list[0].args[2] == check.call_args_list[1].args[2]
    assert check.call_args.args[2] == "Red move check"
    assert check.call_args.kwargs["prior"] == expected
