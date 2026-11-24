from unittest.mock import Mock, patch

import pytest

from tools.vision import full_game_arm_trial as game


@pytest.mark.parametrize(("position", "outcome"), [
    ("R3k4/4R4/9/9/9/9/9/9/9/4K4 b - - 0 1", "checkmate"),
    ("4k4/3R1R3/4P4/9/9/9/9/9/9/4K4 b - - 0 1", "stalemate"),
])
def test_terminal_endgame_exits_before_engine_or_arm(position, outcome, capsys):
    observed = game.layout(position)
    controller, engine = Mock(), Mock()
    with patch.object(game, "wait_for_matching_layout", return_value=observed) as wait:
        game.play(controller, engine, 10, endgame_turn="b")
    assert wait.call_args.kwargs["validate_layout"](observed)
    assert f"Red wins by {outcome}" in capsys.readouterr().out
    assert controller.mock_calls == engine.mock_calls == []


def test_black_captures_use_successive_slots_and_park():
    position = "r2k5/9/9/P8/9/P8/9/9/9/1N2K4 b - - 0 1"
    first = game.checked_transition(position, "a9a6")
    human = game.checked_transition(first["fen"], "b0c2")
    second = game.checked_transition(human["fen"], "a6a4")
    controller = Mock()
    with patch.object(game, "wait_for_layout"), patch.object(
        game, "wait_for_matching_layout",
        side_effect=[game.layout(human["fen"]), game.layout(second["fen"]), TimeoutError],
    ):
        with pytest.raises(TimeoutError):
            game.play(controller, None, 10, fen=position, captures=3)
    assert [call.args[:2] for call in controller.transfer.call_args_list] == [
        ("a6", "capture4"), ("a9", "a6"), ("a4", "capture5"), ("a6", "a4"),
    ]
    assert all(call.kwargs["return_to_park"] for call in controller.transfer.call_args_list)
