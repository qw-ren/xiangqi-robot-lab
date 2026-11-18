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

proto.action = async function (work) { // 错误统一显示在系统日志。
    try { return await work(); } catch (error) { this.log(error.message, 'error'); return null; }
};

proto.init = async function () {
    await originalInit.call(this);
    const bind = (id, work) => document.getElementById(id)?.addEventListener('click', () => this.action(work));
    bind('btn-dofbot-connect', async () => { const data = await this.api('/dofbot/connect', {}); this.log(`${data.backend.toUpperCase()} 已连接，连接操作没有运动`, 'info'); await this.refreshDofbot(); });
    bind('btn-dofbot-angles', async () => { const data = await this.api('/dofbot/angles'); document.getElementById('robot-trace').textContent = `六轴角度：${JSON.stringify(data.angles)}`; });
    bind('btn-dofbot-park', async () => { this.log('正在执行停靠动作…', 'warning'); await this.api('/dofbot/park', {}); this.log('停靠动作完成', 'info'); await this.refreshDofbot(); });
    bind('btn-dofbot-stop', async () => { await this.api('/dofbot/stop', {}); this.stopDynamicRecognition(); this.setRecognitionMode(false); this.isGameRunning = false; this.log('已软件停止，当前运动段可能仍在执行；这不会断开舵机电源。检查棋盘后重新摆棋开局。', 'warning'); await this.syncFullState(); });
    bind('btn-dofbot-clear', async () => { await this.api('/dofbot/clear', {}); this.log('故障已解除，请重摆标准初局再开始', 'info'); await this.refreshDofbot(); });
    bind('btn-slots-clear', async () => { await this.api('/dofbot/clear', {empty_slots: true}); this.log('已确认吃子槽清空，槽位从 0 重新计数', 'info'); await this.refreshDofbot(); });
    bind('btn-calibration-load', async () => { const data = await this.api('/dofbot/calibration'); document.getElementById('calibration-json').value = JSON.stringify(data.calibration, null, 2); this.log('标定文件已读入编辑器', 'info'); });
    bind('btn-calibration-save', async () => { await this.api('/dofbot/calibration', JSON.parse(document.getElementById('calibration-json').value)); this.log('参数校验通过并已保存', 'info'); await this.refreshDofbot(); });
    bind('btn-teach', async () => { const data = await this.api('/dofbot/teach', {key: document.getElementById('teach-key').value.trim(), level: document.getElementById('teach-level').value}); document.getElementById('calibration-json').value = JSON.stringify(data.calibration, null, 2); this.log(`示教已保存：${data.angles.join(', ')}`, 'info'); await this.refreshDofbot(); });
    bind('btn-refresh-cameras', () => this.loadLocalCameras());
    bind('btn-camera-check', async () => { if (!['camera', 'network'].includes(this.currentInputSource)) throw new Error('请先选择摄像头输入源'); const response = await fetch(`/api/camera/status?${this.cameraFrameQuery()}`); const data = await response.json(); this.log(JSON.stringify(data), data.camera_opened ? 'info' : 'warning'); });
    bind('btn-disconnect-network', async () => { await this.api('/network_camera/disconnect', {}); this.networkCameraUrl = ''; this.stopLocalCameraFrames(); document.getElementById('input-source').value = 'virtual'; this.changeInputSource(); this.log('网络摄像头已断开', 'info'); });
    bind('btn-copy-fen', async () => { const input = document.getElementById('fen-display'); if (!input.value) throw new Error('暂无 FEN'); try { await navigator.clipboard.writeText(input.value); } catch (_) { input.select(); document.execCommand('copy'); } this.log('FEN 已复制', 'info'); });
    bind('btn-clear-log', async () => { document.getElementById('log-output').textContent = ''; });
    bind('btn-export-log', async () => { const url = URL.createObjectURL(new Blob([document.getElementById('log-output').innerText], {type: 'text/plain;charset=utf-8'})); const a = document.createElement('a'); a.href = url; a.download = 'chro_log.txt'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); });
    await this.refreshDofbot();
    await this.syncFullState();
    this.fullStatusTimer = setInterval(() => this.action(async () => { await this.syncFullState(); await this.refreshDofbot(); }), 2000); // 刷新页面和多窗口均读取后端正式状态。
};

proto.refreshDofbot = async function () {
    const data = await this.api('/dofbot/status');
    this.dofbot = data;
    const label = data.backend === 'mock' ? 'MOCK：电脑模拟，无实体动作' : 'DOFBOT：树莓派 Arm_Lib 真机';
    document.getElementById('dofbot-status').textContent = `${label}｜${data.connected ? '已连接' : '未连接'}｜${data.busy ? '动作中' : '空闲'}｜${data.calibrated ? '标定完整' : data.calibration_error}｜吃子槽 ${data.capture_used}/${data.capture_capacity}${data.slots_confirmed ? '' : '（请先确认已清空）'}${data.fault ? '｜故障：' + data.fault : ''}`;
    document.getElementById('btn-start-hardware-game').textContent = data.backend === 'mock' ? '开始 DOFBOT 模拟对局' : '开始 DOFBOT 实体对局';
    document.getElementById('btn-start-hardware-game').disabled = !data.connected || !data.calibrated || !data.slots_confirmed || !!data.fault || data.busy || !!this.currentState?.ai_thinking || !!this.currentState?.pending_dofbot || !!this.currentState?.awaiting_initial_board;
    document.getElementById('btn-dofbot-park').disabled = !data.connected || data.busy || !!data.fault || this.isGameRunning;
    document.getElementById('btn-dofbot-connect').disabled = data.busy;
    document.getElementById('btn-dofbot-clear').disabled = data.busy || this.isGameRunning;
    document.getElementById('btn-slots-clear').disabled = data.busy || this.isGameRunning;
    return data;
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
    document.getElementById('piece-count').textContent = Object.keys(this.boardState).length;
    document.getElementById('fen-display').value = state.current_fen || '';
    if (this.isGameRunning) { // 空闲时保留用户正在选择的下一局参数。
        document.getElementById('ai-color').value = this.aiColor;
        document.getElementById('first-player').value = this.firstPlayer;
    }
    this.setGameSettingControlsEnabled(!this.isGameRunning);
    this.updateMoveList();
    this.drawVisualBoard();
    this.showAnalysis(state.ai_analysis || {});
    const el = document.getElementById('game-status');
    if (state.ai_thinking) el.textContent = state.robot_moving ? 'DOFBOT 正在搬运…' : 'AI 正在思考…';
    else if (state.awaiting_initial_board) el.textContent = '等待标准初局视觉确认';
    else if (state.pending_dofbot) el.textContent = state.ai_analysis?.robot_send_success === false ? '动作失败：请检查后结束本局' : '等待落子后全部棋子视觉确认';
    else if (state.ai_analysis?.error) el.textContent = state.ai_analysis.error;
    else if (!this.isGameRunning) el.textContent = state.ai_analysis?.error || '等待开始';
    else this.updateGameStatusFromTurn(this.currentTurnFromFen(state.current_fen));
    document.getElementById('btn-start-simulation-game').disabled = !!state.ai_thinking || !!state.pending_dofbot || !!state.awaiting_initial_board || !!state.robot_moving;
    document.getElementById('btn-reset-game').disabled = !!state.ai_thinking || !!state.pending_dofbot || !!state.awaiting_initial_board || !!state.robot_moving;
    document.getElementById('btn-ai-move').disabled = !this.isGameRunning || this.isPlayerTurn || !!state.ai_thinking || !!state.pending_dofbot || !!state.awaiting_initial_board || !!state.robot_moving;
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
    ['btn-refresh-cameras', 'btn-connect-network', 'btn-disconnect-network', 'btn-calibration-save', 'btn-teach'].forEach(id => { document.getElementById(id).disabled = this.isGameRunning || !!state.robot_moving; });
};

proto.startGame = async function (mode = 'simulation') {
    await this.action(async () => {
        const settings = this.getGameSettings();
        this.stopDynamicRecognition();
        const data = await this.api('/game/start', {mode, ai_color: settings.aiColor, first_player: settings.firstPlayer, input_source: this.currentInputSource});
        this.lastAiBestMoveHandled = null;
        this.lastAiBoardApplied = null;
        this.failedAutoFen = null;
        this.selectedSquare = null;
        await this.syncFullState();
        this.log(`${mode === 'hardware' ? 'DOFBOT' : '虚拟'}对局已开始：程序执${this.colorName(data.ai_color)}，${this.colorName(data.first_player)}先手${data.awaiting_initial_board ? '；等待摄像头确认标准初局' : ''}`, 'info');
        if (['camera', 'network'].includes(this.currentInputSource)) { this.setRecognitionMode(true); this.startDynamicRecognition(); }
        if (!data.awaiting_initial_board) this.checkAutoTriggerAI(data.current_fen || data.fen);
        await this.refreshDofbot();
    });
};

proto.getAIMove = async function () {
    if (this.aiRequestBusy || this.aiPollingTimer) return;
    this.aiRequestBusy = true;
    try {
        const depth = Number(document.getElementById('ai-depth').value);
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
            this.showAnalysis(data.analysis || {});
            await this.syncFullState();
            if (!data.ai_thinking) {
                clearInterval(this.aiPollingTimer); this.aiPollingTimer = null;
                const analysis = data.analysis || {};
                if (analysis.error || analysis.robot_send_success === false) {
                    this.failedAutoFen = this.currentState.current_fen;
                    this.log(analysis.error || analysis.robot_send_error || '动作失败', 'error');
                } else if (analysis.best_move) {
                    this.log(`AI 选择 ${analysis.best_move}；${data.pending_dofbot ? '动作已结束，等待视觉核对' : '正式棋局已更新'}`, 'info');
                    if (['camera', 'network'].includes(this.currentInputSource) && this.isGameRunning) { this.setRecognitionMode(true); this.startDynamicRecognition(); }
                }
                await this.refreshDofbot();
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

proto.loadLocalCameras = async function () {
    await originalCameras.call(this);
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
    await this.refreshDofbot();
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

proto.log = function (message, type = 'info') { // 日志一律作为文本，避免 URL 或异常内容变成 HTML。
    console.log(`[${type}] ${message}`);
    if (this.logPaused) { this.pausedLogCount++; return; }
    const output = document.getElementById('log-output');
    const entry = document.createElement('div');
    entry.className = `log-entry log-${type}`;
    entry.textContent = `[${new Date().toLocaleTimeString('zh-CN')}] ${message}`;
    output.appendChild(entry);
    while (output.children.length > 500) output.firstChild.remove();
    output.scrollTop = output.scrollHeight;
};

proto.showAnalysis = function (analysis) { // 完整展示引擎分析，不将 PV 当作 HTML。
    const panel = document.getElementById('ai-analysis');
    panel.textContent = '';
    const fields = [
        ['最佳走法', analysis.best_move], ['搜索深度', analysis.depth],
        ['评估分数（引擎当前方视角，厘兵）', analysis.score],
        ['杀棋距离（引擎 mate 值）', analysis.mate], ['搜索节点', analysis.nodes],
        ['PV 主变化', analysis.pv], ['错误', analysis.error || analysis.robot_send_error]
    ];
    let shown = 0;
    for (const [label, value] of fields) {
        if (value === undefined || value === null || value === '') continue;
        const line = document.createElement('p'); line.textContent = `${label}：${value}`; panel.appendChild(line); shown++;
    }
    if (!shown) { const line = document.createElement('p'); line.textContent = '等待 AI 分析…'; panel.appendChild(line); }
};
