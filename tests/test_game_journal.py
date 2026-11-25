from unittest.mock import Mock, patch

import pytest

from game_manager.game_journal import GameJournal
from tools.vision import full_game_arm_trial as game
from web_simulation.app import app
from xq_utils.rules import START_FEN, checked_transition


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
    assert saved.state["fen"] == expected
    assert saved.state["pending"] is None
    assert saved.state["moves"][0]["capture"] == "P"


def test_web_exposes_saved_confirmed_moves_and_pending_plan(tmp_path, monkeypatch):
    path = tmp_path / "game.json"
    journal = GameJournal(path)
    journal.update(initial_fen=START_FEN, fen=START_FEN)
    journal.confirm("a0a1")
    journal.update(phase="moving", pending={"move": "h9g7", "slot": None})
    monkeypatch.setenv("CHRO_GAME_JOURNAL", str(path))
    with app.test_client() as client:
        assert b"live_game.js" in client.get("/").data
        response = client.get("/api/live-game")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json["moves"][0]["move"] == "a0a1"
    assert response.json["pending"]["move"] == "h9g7"
    assert response.json["fen"] == checked_transition(START_FEN, "a0a1")["fen"]
