const assert = require('node:assert/strict');
const { test } = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function page() {
    const elements = new Map();
    const document = {
        addEventListener() {},
        getElementById(id) {
            if (!elements.has(id)) elements.set(id, {});
            return elements.get(id);
        }
    };
    const context = vm.createContext({ document, console, Date });
    const scripts = path.resolve(__dirname, '../../web_simulation/static/js');
    vm.runInContext(fs.readFileSync(path.join(scripts, 'app.js'), 'utf8'), context);
    vm.runInContext(fs.readFileSync(path.join(scripts, 'full_features.js'), 'utf8'), context);
    const prototype = vm.runInContext('ChessSimulation.prototype', context);
    const ui = Object.create(prototype);
    Object.assign(ui, {
        currentInputSource: 'local', isGameRunning: false,
        log() {}, drawVisualBoard() {}, drawBoard() {}, updateMoveList() {},
        setGameSettingControlsEnabled() {}, showAnalysis() {},
        currentTurnFromFen() { return 'red'; }, updateGameStatusFromTurn() {}
    });
    const state = { is_game_running: false, board_state: { '0,0': 'r' }, current_fen: 'formal' };
    ui.api = async () => ({ success: true, state });
    const preview = { observation: {status: 'provisional', reasons: ['unconfirmed_geometry']},
                      observed_board_state: { '4,7': 'C' } };
    return { ui, state, preview, document };
}

test('formal-state polling preserves an idle ArUco preview', async () => {
    const { ui, preview, document } = page();
    ui.showVisionObservation(preview);
    await ui.syncFullState();
    assert.equal(JSON.stringify(ui.boardState), JSON.stringify({ '4,7': 'C' }));
    assert.equal(document.getElementById('fen-display').value, '');
    assert.match(document.getElementById('vision-observation-status').textContent, /未写入棋局/);
});

test('an active game retains its formal board and drops old preview state', async () => {
    const { ui, state, preview } = page();
    ui.showVisionObservation(preview);
    state.is_game_running = true;
    await ui.syncFullState();
    ui.showVisionObservation(preview);
    assert.equal(JSON.stringify(ui.boardState), JSON.stringify(state.board_state));
});

test('observations without a prediction clear the previous preview', () => {
    const { ui, preview } = page();
    ui.showVisionObservation(preview);
    ui.showVisionObservation({observation: {status: 'rejected', reasons: ['localization_failed']}});
    assert.equal(JSON.stringify(ui.boardState), '{}');
});

test('unknown and low-score cells do not hide the available preview', () => {
    const { ui, preview, document } = page();
    preview.observation = {status: 'rejected', reasons: ['unknown_cells', 'low_scores'],
                           cells: [{piece: 'x'}]};
    ui.showVisionObservation(preview);
    assert.equal(JSON.stringify(ui.boardState), JSON.stringify({ '4,7': 'C' }));
    assert.match(document.getElementById('vision-observation-status').textContent, /1 个未知格未绘制/);
});

test('stale live preview is not kept looking current', async () => {
    const { ui, preview, document } = page();
    ui.currentInputSource = 'camera';
    ui.showVisionObservation(preview);
    ui.visionPreviewAt = Date.now() - 6000;
    await ui.syncFullState();
    assert.equal(ui.latestVisionPreview, null);
    assert.match(document.getElementById('vision-observation-status').textContent, /过期/);
});
