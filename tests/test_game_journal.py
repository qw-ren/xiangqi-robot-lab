from unittest.mock import Mock, patch

import pytest

from game_manager.game_journal import GameJournal
from tools.vision import full_game_arm_trial as game
from web_simulation.app import app
from xq_utils.rules import checked_transition


def test_interrupted_black_capture_preserves_committed_position_and_resumes_without_motion(tmp_path):
    fen = "r2k5/9/9/P8/9/9/9/9/9/1N2K4 b - - 0 1"
    path = tmp_path / "game.json"
    journal = GameJournal(path)
    arm = Mock()
    arm.transfer.side_effect = KeyboardInterrupt
    with patch.object(game, "wait_for_layout"):
        with pytest.raises(KeyboardInterrupt):
            game.play(arm, None, 10, fen=fen, journal=journal)
    saved = GameJournal.load(path)
    assert saved.state["fen"] == fen
    assert saved.state["moves"] == []
    assert saved.state["pending"] == {"move": "a9a6", "slot": "capture1"}
    assert saved.state["captures"] == 1

    arm.reset_mock()
    with patch.object(game, "wait_for_layout") as check, patch.object(
        game, "wait_for_matching_layout", side_effect=KeyboardInterrupt,
    ):
        with pytest.raises(KeyboardInterrupt):
            game.play(arm, None, 10, fen=fen, captures=1, journal=saved)
    expected = checked_transition(fen, "a9a6")["fen"]
    assert check.call_args.args[0] == game.layout(expected)
    arm.transfer.assert_not_called()
    arm.set_torque.assert_called_once_with(True)
    assert saved.state["fen"] == expected
    assert saved.state["pending"] is None
    assert saved.state["moves"][0]["capture"] == "P"


def test_classic_web_shows_confirmed_board_and_planned_move(tmp_path, monkeypatch):
    path = tmp_path / "game.json"
    journal = GameJournal(path)
    journal.confirm("a0a1", starting_fen=game.START_FEN, captures=0)
    journal.update(phase="selected", planned_move="h9g7", detail="等待定位棋盘")
    monkeypatch.setenv("CHRO_GAME_JOURNAL", str(path))
    with app.test_client() as client:
        page = client.get("/")
        assert page.status_code == 200
        assert b"visual-board" in page.data
        assert b"live_game.js" not in page.data
        assert b"physical-game-status" in page.data
        snapshot = client.get("/api/live-game").get_json()
        assert snapshot["last_move"] == "a0a1"
        assert snapshot["planned_move"] == "h9g7"
        assert snapshot["board_state"]["0,8"] == "R"
        assert "0,9" not in snapshot["board_state"]
        assert snapshot["detail"] == "等待定位棋盘"
        assert snapshot["moves"][0]["fen"] == snapshot["fen"]
        assert snapshot["initial_fen"] == game.START_FEN
        exported = client.get("/api/live-game?download=1")
        assert "attachment" in exported.headers["Content-Disposition"]
        assert exported.get_json()["moves"][0]["move"] == "a0a1"
