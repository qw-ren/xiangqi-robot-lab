// 完整网页扩展：保留原棋盘绘制和识别界面，替换固定 STM32 的操作流程。
const proto = ChessSimulation.prototype;
const originalInit = proto.init;
const originalInput = proto.changeInputSource;
const originalCameras = proto.loadLocalCameras;
const originalReset = proto.resetGame;
const originalDynamic = proto.pollDynamicRecognition;

proto.api = async function (path, data) { // 所有新按钮共用错误处理。
    const response = await fetch(`/api${path}`, data === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
    const result = await response.json();
    if (!result.success) throw new Error(result.error || `请求失败 ${response.status}`);
    return result;
};

proto.action = async function (work) {
    try { return await work(); } catch (error) { this.log(error.message, 'error'); return null; }
};

proto.init = async function () {
    await originalInit.call(this);
    const bind = (id, work) => document.getElementById(id)?.addEventListener('click', () => this.action(work));
    bind('btn-refresh-cameras', () => this.loadLocalCameras(true));
    bind('btn-camera-check', async () => {
        const status = document.getElementById('vision-observation-status');
        if (!['camera', 'network'].includes(this.currentInputSource)) {
            status.textContent = '请先选择摄像头输入源。';
            return;
        }
        try {
            const response = await fetch(`/api/camera/status?${this.cameraFrameQuery()}`);
            const data = await response.json();
            status.textContent = response.ok && data.camera_opened ? '摄像头已就绪。' : '摄像头目前不可用。';
        } catch (_) {
            status.textContent = '无法读取摄像头状态。';
        }
    });
    bind('btn-disconnect-network', async () => { this.stopLocalCameraFrames(); await this.api('/network_camera/disconnect', {}); this.networkCameraUrl = ''; document.getElementById('input-source').value = 'virtual'; this.changeInputSource(); this.log('网络摄像头已断开', 'info'); });
    await this.syncFullState();
    this.fullStatusTimer = setInterval(() => this.action(() => this.syncFullState()), 2000); // 刷新页面和多窗口均读取后端正式状态。
};

proto.syncFullState = async function () {
    const data = await this.api('/status');
    const state = data.state;
    this.currentState = state;
    this.isGameRunning = !!state.is_game_running;
    this.robotMode = state.robot_mode;
    this.aiColor = state.ai_color || 'black';
    this.playerColor = state.player_color || 'red';
    this.firstPlayer = state.first_player || 'red';
    this.boardState = state.board_state || {};
    this.moveHistory = state.display_history || [];
    this.isPlayerTurn = this.currentTurnFromFen(state.current_fen) === this.playerColor;
    this.updateMoveList();
    this.drawVisualBoard();
    const el = document.getElementById('game-status');
    if (state.ai_thinking) el.textContent = state.robot_moving ? '正在发送 AI 决策…' : 'AI 正在思考…';
    else if (state.awaiting_initial_board) el.textContent = '等待标准初局视觉确认';
    else if (state.pending_dofbot) el.textContent = state.ai_analysis?.robot_send_success === false ? '动作失败：请检查后结束本局' : '等待落子后全部棋子视觉确认';
    else if (state.ai_analysis?.error) el.textContent = state.ai_analysis.error;
    else if (!this.isGameRunning) el.textContent = state.ai_analysis?.error || '等待开始';
    else this.updateGameStatusFromTurn(this.currentTurnFromFen(state.current_fen));
    const source = document.getElementById('input-source');
    source.disabled = this.isGameRunning; // 对局期间保持同一视觉来源。
    document.getElementById('btn-capture').disabled = !['camera', 'network'].includes(this.currentInputSource);
    document.getElementById('btn-recognize').disabled = this.currentInputSource === 'virtual' || (this.currentInputSource === 'local' && this.isGameRunning);
    if (this.isGameRunning) {
        this.latestVisionPreview = null;
    } else if (this.latestVisionPreview && this.visionPreviewSource === this.currentInputSource) {
        if (['camera', 'network'].includes(this.currentInputSource) && Date.now() - this.visionPreviewAt > 5000) {
            this.latestVisionPreview = null;
            document.getElementById('vision-observation-status').textContent = 'ArUco 观测已过期，请检查采集或重新开始识别。';
        } else {
            this.showVisionObservation(this.latestVisionPreview, false);
        }
    }
    ['btn-refresh-cameras', 'btn-connect-network', 'btn-disconnect-network'].forEach(id => { document.getElementById(id).disabled = this.isGameRunning || !!state.robot_moving; });
};

proto.startGame = async function (mode = 'simulation') {
    await this.action(async () => {
        if (mode === 'hardware') {
            const output = await this.api('/dofbot/status');
            if (!output.connected) {
                // 空请求沿用后端既有串口/模拟器配置，不在网页中修改硬件参数。
                await this.api('/dofbot/connect', {});
            }
        }
        this.stopDynamicRecognition();
        const data = await this.api('/game/start', {mode, ai_color: 'black', first_player: 'red', input_source: this.currentInputSource});
        this.lastAiBestMoveHandled = null;
        this.lastAiBoardApplied = null;
        this.failedAutoFen = null;
        this.selectedSquare = null;
        await this.syncFullState();
        this.log(`${mode === 'hardware' ? '串口/模拟器' : '虚拟'}对局已开始：程序执${this.colorName(data.ai_color)}，${this.colorName(data.first_player)}先手${data.awaiting_initial_board ? '；等待摄像头确认标准初局' : ''}`, 'info');
        if (['camera', 'network'].includes(this.currentInputSource)) { this.setRecognitionMode(true); this.startDynamicRecognition(); }
        if (!data.awaiting_initial_board) this.checkAutoTriggerAI(data.current_fen || data.fen);
    });
};

proto.getAIMove = async function () {
    if (this.aiRequestBusy || this.aiPollingTimer) return;
    this.aiRequestBusy = true;
    try {
        const depth = 8; // 沿用移除设置面板前的默认深度；自动 AI 流程保留。
        await this.api('/ai_move', {depth});
        this.log(`AI 搜索已启动，深度上限 ${depth}`, 'info');
        this.startAIStatusPolling();
    } catch (error) { this.log(error.message, 'error'); }
    finally { this.aiRequestBusy = false; }
};

proto.startAIStatusPolling = function () {
    if (this.aiPollingTimer) return;
    let busy = false;
    this.aiPollingTimer = setInterval(async () => {
        if (busy) return;
        busy = true;
        try {
            const data = await this.api('/ai_status');
            await this.syncFullState();
            if (!data.ai_thinking) {
                clearInterval(this.aiPollingTimer); this.aiPollingTimer = null;
                const analysis = data.analysis || {};
                if (analysis.error || analysis.robot_send_success === false) {
                    this.failedAutoFen = this.currentState.current_fen;
                    this.log(analysis.error || analysis.robot_send_error || '动作失败', 'error');
                } else if (analysis.best_move) {
                    this.log(`AI 选择 ${analysis.best_move}；${data.pending_dofbot ? '网页已同步，走棋消息已发送；等待视觉核对' : '正式棋局已更新'}`, 'info');
                    if (['camera', 'network'].includes(this.currentInputSource) && this.isGameRunning) { this.setRecognitionMode(true); this.startDynamicRecognition(); }
                }
            }
        } catch (error) { this.log(error.message, 'error'); clearInterval(this.aiPollingTimer); this.aiPollingTimer = null; }
        finally { busy = false; }
    }, 250);
};

proto.checkAutoTriggerAI = function (fen) {
    const state = this.currentState || {};
    if (!this.isGameRunning || !fen || this.aiPollingTimer || this.aiRequestBusy || state.ai_thinking || state.pending_dofbot || state.awaiting_initial_board || this.failedAutoFen === fen) return;
    this.updateGameStatusFromTurn(this.currentTurnFromFen(fen));
    if (this.currentTurnFromFen(fen) === this.aiColor) this.getAIMove();
};

proto.pollDynamicRecognition = async function () {
    await originalDynamic.call(this);
    if (this.dynamicRecognitionEnabled && this.isGameRunning) { // 开局视觉确认或 AI 落子确认后立即同步正式状态。
        await this.syncFullState();
        this.checkAutoTriggerAI(this.currentState.current_fen);
    }
};

proto.loadLocalCameras = async function (forceRefresh = false) {
    await originalCameras.call(this, forceRefresh);
    const select = document.getElementById('input-source');
    if (![...select.options].some(x => x.value === 'virtual')) select.add(new Option('虚拟棋盘（不使用摄像头）', 'virtual'), 0);
};

proto.changeInputSource = function () {
    this.latestVisionPreview = null;
    this.lastVisionMessage = null;
    document.getElementById('vision-observation-status').textContent = '输入源已切换，等待新的视觉观测。';
    originalInput.call(this);
    const local = this.currentInputSource === 'local';
    document.getElementById('camera-feed').style.display = local ? 'block' : 'none';
    document.getElementById('camera-view').style.display = local ? 'none' : 'block';
    document.getElementById('btn-capture').disabled = !['camera', 'network', 'local'].includes(this.currentInputSource);
    document.getElementById('btn-recognize').disabled = this.currentInputSource === 'virtual';
};

proto.captureImage = async function () {
    await this.action(async () => {
        if (!['camera', 'network'].includes(this.currentInputSource)) throw new Error('捕获图像需要摄像头；本地图片请直接点击开始识别');
        const data = await this.api('/capture', this.cameraRequestPayload());
        this.stopLocalCameraFrames();
        const image = document.getElementById('camera-feed');
        image.src = `data:image/jpeg;base64,${data.image}`;
        image.style.display = 'block';
        document.getElementById('camera-view').style.display = 'none';
        this.log('已捕获快照；点击开始识别恢复实时画面与动态识别', 'info');
    });
};

const originalToggle = proto.toggleRecognition;
proto.toggleRecognition = function () {
    if (['camera', 'network'].includes(this.currentInputSource) && !this.dynamicRecognitionEnabled) this.startLocalCameraStream();
    originalToggle.call(this);
};

proto.resetGame = async function () {
    await originalReset.call(this);
    await this.syncFullState();
    this.selectedSquare = null;
    this.failedAutoFen = null;
};

proto.simulateRobotMove = async function () { // 仅预演，不调用运动接口。
    await this.action(async () => {
        const data = await this.api('/dofbot/plan', {move: document.getElementById('preview-move').value.trim()});
        document.getElementById('robot-trace').textContent = JSON.stringify(data, null, 2);
        this.log(`已生成 ${data.plan.steps.length} 段取放计划，${data.plan.capture ? '包含吃子搬运' : '普通落子'}；未发送运动指令`, 'info');
    });
};

proto.testRobotSequence = async function () { // 后端只允许 mock 执行调试序列。
    await this.action(async () => {
        const data = await this.api('/dofbot/test', {move: document.getElementById('preview-move').value.trim()});
        document.getElementById('robot-trace').textContent = JSON.stringify(data, null, 2);
        this.log('模拟取放序列通过，正式棋局未改变', 'info');
    });
};
