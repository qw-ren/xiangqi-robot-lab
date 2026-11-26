from pathlib import Path
import unittest


APP_JS = Path(__file__).parents[1] / "web_simulation" / "static" / "js" / "app.js"
INDEX_HTML = Path(__file__).parents[1] / "web_simulation" / "templates" / "index.html"


class FrontendHomingContractTests(unittest.TestCase):
    def setUp(self):
        self.source = APP_JS.read_text(encoding="utf-8")
        self.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_initialization_does_not_probe_stm32_tcp_endpoint(self):
        init_body = self.source[
            self.source.index("async init()") : self.source.index("    bindEvents()")
        ]

        self.assertNotIn("checkRobotConnection", init_body)
        self.assertIn("this.log('系统初始化...'", init_body)

    def test_preview_displays_game_info_without_game_controls_or_diagnostic_panels(self):
        for control in ("btn-start-hardware-game", "btn-start-simulation-game", "btn-reset-game",
                        "ai-analysis", "log-output"):
            self.assertNotIn(f'id="{control}"', self.html)
        for display in ("visual-board", "physical-game-status", "physical-game-detail", "move-list"):
            self.assertIn(f'id="{display}"', self.html)

    def test_game_and_vision_status_are_in_sidebar_and_virtual_board_has_no_controls(self):
        sidebar, visuals = self.html.split('<section class="vision-panel">', 1)
        for status in ("physical-game-status", "physical-game-detail", "physical-game-preflight",
                       "vision-observation-status"):
            self.assertIn(f'id="{status}"', sidebar)
            self.assertNotIn(f'id="{status}"', visuals)
        self.assertIn("<title>中国象棋对弈台</title>", sidebar)
        self.assertIn("<h1>中国象棋对弈台</h1>", sidebar)
        self.assertNotIn("真实棋盘识别 · AI 决策 · 对局状态监控", sidebar)
        virtual_board = visuals.split('class="panel virtual-section"', 1)[1].split("</article>")[0]
        self.assertIn('id="visual-board"', virtual_board)
        self.assertNotIn("识别后的棋盘状态会同步在这里", virtual_board)
        for control in ("<input", "<button", "<a "):
            self.assertNotIn(control, virtual_board)

    def test_hardware_start_goes_directly_to_homing_without_probe(self):
        start_body = self.source[
            self.source.index("async startHardwareGame()") : self.source.index("async startSimulationGame()")
        ]

        self.assertNotIn("checkRobotConnection", start_body)
        self.assertIn("startGame('hardware')", start_body)

    def test_simulation_start_does_not_probe_robot_or_send_homing(self):
        simulation_body = self.source[
            self.source.index("async startSimulationGame()") : self.source.index("async resetGame()")
        ]

        self.assertNotIn("checkRobotConnection", simulation_body)
        self.assertIn("startGame('simulation')", simulation_body)
        self.assertIn("mode: 'simulation'", simulation_body)

    def test_ai_status_polling_predisplays_backend_applied_move_before_ack(self):
        polling_body = self.source[
            self.source.index("startAIStatusPolling()") : self.source.index("async executeRobotMove")
        ]

        self.assertIn("lastAiBoardApplied", self.source)
        self.assertIn("syncAIMoveBoardFromStatus", self.source)
        self.assertIn("data.analysis?.ai_move_applied", polling_body)
        self.assertLess(
            polling_body.index("this.predisplayAIMoveFromStatus(data, bestMove);"),
            polling_body.index("if (!data.ai_thinking)"),
        )

    def test_hardware_move_waits_for_state5_ack_before_resuming_recognition(self):
        predisplay_body = self.source[
            self.source.index("predisplayAIMoveFromStatus") : self.source.index("    // 轮询 AI 思考状态")
        ]
        polling_body = self.source[
            self.source.index("startAIStatusPolling()") : self.source.index("async executeRobotMove")
        ]

        self.assertIn("STATE:5,RESULT:1", self.source)
        self.assertIn("{ autoResume: !isHardwareMode }", predisplay_body)
        self.assertIn("resumeRecognitionAfterRobotAck(true)", polling_body)
        self.assertLess(
            polling_body.index("data.analysis.robot_send_success === true"),
            polling_body.index("this.resumeRecognitionAfterRobotAck(true)"),
        )


if __name__ == "__main__":
    unittest.main()
