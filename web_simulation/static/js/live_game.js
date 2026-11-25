"use strict";

const board = document.getElementById("board");
const ctx = board.getContext("2d");
const history = document.getElementById("moves");
const phases = {
    setup: "正在检查开局", review: "等待确认低分棋子", red: "等待红方走棋",
    thinking: "黑方思考中", selected: "黑方已选招", moving: "机械臂搬运中",
    clearing: "复核吃子格是否清空", verifying: "正在复核黑方走法",
    finished: "对局结束", stopped: "对局已停止",
};
const pieces = { r: "車", n: "馬", b: "象", a: "士", k: "將", c: "炮", p: "卒",
    R: "車", N: "馬", B: "相", A: "仕", K: "帥", C: "炮", P: "兵" };
let state = { moves: [] };
let selected = null;
let renderedMoves = null;
let connected = true;

function point(file, rank) {
    return [50 + file * 55, 52 + rank * 55];
}

function square(coordinate) {
    return point(coordinate.charCodeAt(0) - 97, 9 - Number(coordinate[1]));
}

function line(from, to) {
    ctx.beginPath();
    ctx.moveTo(...from);
    ctx.lineTo(...to);
    ctx.stroke();
}

function draw(fen, move, planned) {
    ctx.clearRect(0, 0, board.width, board.height);
    ctx.strokeStyle = "#76502d";
    ctx.lineWidth = 1.6;
    for (let rank = 0; rank < 10; rank++) line(point(0, rank), point(8, rank));
    for (let file = 0; file < 9; file++) {
        if (file === 0 || file === 8) line(point(file, 0), point(file, 9));
        else {
            line(point(file, 0), point(file, 4));
            line(point(file, 5), point(file, 9));
        }
    }
    for (const rank of [0, 7]) {
        line(point(3, rank), point(5, rank + 2));
        line(point(5, rank), point(3, rank + 2));
    }
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.font = "25px serif";
    ctx.fillStyle = "#76502d";
    ctx.fillText("楚河", 165, 300);
    ctx.fillText("漢界", 375, 300);
    if (!fen) return;
    if (move) {
        ctx.fillStyle = "#ae602d55";
        for (const coordinate of [move.slice(0, 2), move.slice(2)]) {
            const [x, y] = square(coordinate);
            ctx.fillRect(x - 26, y - 26, 52, 52);
        }
    }
    fen.split(" ")[0].split("/").forEach((rank, row) => {
        let file = 0;
        for (const piece of rank) {
            if (/\d/.test(piece)) {
                file += Number(piece);
                continue;
            }
            const [x, y] = point(file++, row);
            const ink = piece === piece.toUpperCase() ? "#9d2924" : "#30261e";
            ctx.beginPath();
            ctx.arc(x, y, 21, 0, 2 * Math.PI);
            ctx.fillStyle = "#fff1ce";
            ctx.fill();
            ctx.strokeStyle = ink;
            ctx.stroke();
            ctx.font = "bold 25px serif";
            ctx.fillStyle = ink;
            ctx.fillText(pieces[piece], x, y + 1);
        }
    });
    if (planned) {
        const from = square(planned.slice(0, 2));
        const to = square(planned.slice(2));
        const angle = Math.atan2(to[1] - from[1], to[0] - from[0]);
        ctx.strokeStyle = "#135d61";
        ctx.lineWidth = 4;
        line(from, to);
        for (const offset of [-0.55, 0.55]) {
            line(to, [to[0] - 15 * Math.cos(angle + offset), to[1] - 15 * Math.sin(angle + offset)]);
        }
    }
}

function moveLabel(entry) {
    const side = entry.side === "w" ? "红方" : "黑方";
    const name = pieces[entry.piece] || "";
    const captured = entry.capture ? `，吃${pieces[entry.capture]}` : "";
    return `${side}${name} ${entry.move.slice(0, 2)} → ${entry.move.slice(2)}${captured}`;
}

function render() {
    const last = state.moves[state.moves.length - 1];
    const viewed = selected === null ? last : state.moves[selected];
    const phase = document.getElementById("phase");
    phase.textContent = connected ? phases[state.phase] || phases.setup : "连接中断，显示最近一次更新";
    if (connected && state.result) {
        const winner = state.result.winner === "red" ? "红方" : "黑方";
        const reason = state.result.reason === "checkmate" ? "将死" : "困毙";
        phase.textContent = `${winner}胜 · ${reason} · 共 ${state.moves.length} 步`;
    }
    document.getElementById("detail").textContent = state.detail || "";
    document.getElementById("view").textContent = selected === null ? "实时棋局" : `回放至第 ${selected + 1} 步`;
    document.getElementById("export").disabled = !state.initial_fen;
    document.getElementById("live").disabled = selected === null;
    const planned = state.pending?.move || (state.phase === "selected" && /^[a-i][0-9]{1}[a-i][0-9]$/.test(state.detail) ? state.detail : null);
    draw(selected === null ? state.fen : viewed?.fen, viewed?.move, selected === null && planned);
    const signature = JSON.stringify(state.moves);
    if (signature !== renderedMoves) {
        history.replaceChildren();
        state.moves.forEach((entry, index) => {
            const row = document.createElement("li");
            const button = document.createElement("button");
            button.type = "button";
            button.textContent = moveLabel(entry);
            button.addEventListener("click", () => { selected = index; render(); });
            row.append(button);
            history.append(row);
        });
        if (selected === null) history.scrollTop = history.scrollHeight;
        renderedMoves = signature;
    }
    [...history.querySelectorAll("button")].forEach((button, index) => {
        button.setAttribute("aria-current", String(index === selected));
    });
}

document.getElementById("live").addEventListener("click", () => {
    selected = null;
    render();
    history.scrollTop = history.scrollHeight;
});
document.getElementById("export").addEventListener("click", () => {
    const data = new Blob([JSON.stringify(state, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(data);
    const link = document.createElement("a");
    link.href = url;
    link.download = "xiangqi-game.json";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
});

async function refresh() {
    try {
        const response = await fetch("/api/live-game", { cache: "no-store", signal: AbortSignal.timeout(5000) });
        if (!response.ok) throw new Error(response.status);
        state = await response.json();
        connected = true;
    } catch (_) {
        connected = false;
    }
    render();
    setTimeout(refresh, 1000);
}

refresh();
