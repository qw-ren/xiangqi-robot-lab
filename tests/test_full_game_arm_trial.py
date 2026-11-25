from unittest.mock import Mock, patch

import pytest

from tools.vision import full_game_arm_trial as game
from tools.vision.one_move_arm_trial import starting_scores_ok


@pytest.mark.parametrize("piece,score,accepted", [
    (".", 0.79, True),
    (".", 0.74, False),
    ("R", 0.79, False),
    ("x", 0.95, False),
])
def test_starting_layout_only_relaxes_moderately_low_empty_cells(piece, score, accepted):
    cells = [{"piece": ".", "score": 0.99}] * 89 + [{"piece": piece, "score": score}]
    assert starting_scores_ok({"reasons": ["low_scores"], "cells": cells}) is accepted


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
