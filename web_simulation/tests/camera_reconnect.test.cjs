// 在 Node 中模拟浏览器的异步取帧，验证切换和超时，不访问真实相机。
const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function page() {
    const draws = [], timers = new Map(), requests = [], images = [], revoked = [];
    let timerId = 0;
    const canvas = {width: 640, height: 480, getContext: () => ({
        clearRect() {}, drawImage: image => draws.push(image.src)
    })};
    const elements = {'camera-view': canvas, 'camera-feed': {}};
    const context = vm.createContext({
        document: {addEventListener() {}, getElementById: id => elements[id] || {}},
        Date, console, AbortController,
        setTimeout: fn => {timers.set(++timerId, fn); return timerId;},
        clearTimeout: id => timers.delete(id), clearInterval() {},
        URL: {createObjectURL: () => `blob:${images.length}`, revokeObjectURL: url => revoked.push(url)},
        Image: class {constructor() {this.naturalWidth = 640; this.naturalHeight = 480; images.push(this);}},
        fetch: (url, options) => new Promise((resolve, reject) => {
            requests.push({url, options, resolve});
            options.signal.addEventListener('abort', () => reject(new Error('aborted')), {once: true});
        })
    });
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/js/app.js'), 'utf8'), context);
    const ui = Object.create(vm.runInContext('ChessSimulation.prototype', context));
    Object.assign(ui, {apiBase: '/api', currentInputSource: 'camera', selectedCameraIndex: 0,
        cameraFrameBusy: false, lastCameraFrameErrorAt: 0, log() {}, setCameraPreviewStatus() {}});
    return {ui, requests, images, draws, timers, revoked, elements};
}
const flush = () => new Promise(resolve => setImmediate(resolve));
const response = {ok: true, blob: async () => ({})};

test('switch cancels old frame; late decoded image cannot overwrite the new source', async () => {
    const {ui, requests, images, draws} = page();
    const old = ui.refreshCameraFrame();
    requests[0].resolve(response);
    await flush();
    const lateLoad = images[0].onload;
    ui.stopLocalCameraFrames();
    ui.selectedCameraIndex = 2;
    const next = ui.refreshCameraFrame();
    lateLoad();
    await old;
    assert.equal(ui.cameraFrameBusy, true); // 旧请求的 finally 不能解除新请求的 busy。
    assert.equal(draws.length, 0);
    assert.match(requests[1].url, /camera_index=2/);
    requests[1].resolve(response);
    await flush();
    images[1].onload();
    await next;
    assert.equal(draws.length, 1);
    assert.equal(ui.cameraFrameBusy, false);
});

test('hung frame times out and allows another polling request', async () => {
    const {ui, timers, requests} = page();
    const first = ui.refreshCameraFrame();
    [...timers.values()][0]();
    await first;
    assert.equal(ui.cameraFrameBusy, false);
    const second = ui.refreshCameraFrame();
    assert.equal(requests.length, 2);
    ui.stopLocalCameraFrames();
    await second;
});

test('failed HTTP frame releases busy and successful retry releases its blob URL', async () => {
    const {ui, requests, images, revoked} = page();
    const first = ui.refreshCameraFrame();
    requests[0].resolve({ok: false, status: 503});
    await first;
    assert.equal(ui.cameraFrameBusy, false);
    const second = ui.refreshCameraFrame();
    requests[1].resolve(response);
    await flush();
    images[1].onload();
    await second;
    assert.equal(revoked.length, 1);
});

test('camera scan pauses polling until discovery finishes', async () => {
    const {ui, requests} = page();
    ui.cameraScanBusy = true;
    await ui.refreshCameraFrame();
    assert.equal(requests.length, 0);
});

test('manual refresh forces discovery, prevents duplicate scans and restores preview on failure', async () => {
    const {ui, requests, elements} = page();
    elements['input-source'] = {dataset: {}, value: 'camera:0'};
    elements['btn-refresh-cameras'] = {};
    let resumed = 0;
    ui.startLocalCameraStream = () => {resumed++;};
    const scan = ui.loadLocalCameras(true);
    assert.match(requests[0].url, /cameras\?refresh=1/);
    await ui.loadLocalCameras(true);
    assert.equal(requests.length, 1);
    assert.equal(elements['input-source'].disabled, true);
    requests[0].resolve({ok: false, json: async () => ({success: false, error: 'failed'})});
    await scan;
    assert.equal(ui.cameraScanBusy, false);
    assert.equal(elements['input-source'].disabled, false);
    assert.equal(resumed, 1);
});
