// ==UserScript==
// @name         Шахматный тренер (локальный Stockfish)
// @namespace    chess-coach-local
// @version      1.0.0
// @description  Читает позицию с доски chess.com/lichess и отправляет локальному Stockfish; рисует стрелку лучшего хода
// @match        https://www.chess.com/*
// @match        https://lichess.org/*
// @grant        GM_xmlhttpRequest
// @grant        GM.xmlHttpRequest
// @connect      127.0.0.1
// @connect      localhost
// @run-at       document-idle
// ==/UserScript==

(function () {
  'use strict';

  const HOST = 'http://127.0.0.1:8765';
  const TICK_MS = 220;
  const STATE_MS = 250;

  const TYPE_BY_WORD = { pawn: 'p', knight: 'n', bishop: 'b', rook: 'r', queen: 'q', king: 'k' };
  const TYPE_LETTER = 'pnbrqk';

  const prefs = {
    overlay: loadPref('overlay', true),
    debug: loadPref('debug', false),
    orientation: loadPref('orientation', 'auto'),
  };

  function loadPref(key, fallback) {
    try {
      const raw = localStorage.getItem('chessCoach.' + key);
      return raw === null ? fallback : JSON.parse(raw);
    } catch (e) {
      return fallback;
    }
  }

  function savePref(key, value) {
    try {
      localStorage.setItem('chessCoach.' + key, JSON.stringify(value));
    } catch (e) {
      /* приватный режим — не страшно */
    }
  }

  // ------------------------------------------------------------------ сеть

  function api(method, path, body) {
    const url = HOST + path;
    const payload = body ? JSON.stringify(body) : undefined;

    if (typeof GM_xmlhttpRequest === 'function') {
      return new Promise((resolve, reject) => {
        GM_xmlhttpRequest({
          method,
          url,
          data: payload,
          headers: payload ? { 'Content-Type': 'application/json' } : {},
          timeout: 2500,
          onload: (res) => {
            try {
              resolve(JSON.parse(res.responseText));
            } catch (e) {
              reject(e);
            }
          },
          onerror: reject,
          ontimeout: reject,
        });
      });
    }

    if (typeof GM !== 'undefined' && GM && typeof GM.xmlHttpRequest === 'function') {
      return GM.xmlHttpRequest({
        method,
        url,
        data: payload,
        headers: payload ? { 'Content-Type': 'application/json' } : {},
      }).then((res) => JSON.parse(res.responseText));
    }

    return fetch(url, {
      method,
      cache: 'no-store',
      headers: payload ? { 'Content-Type': 'application/json' } : {},
      body: payload,
    }).then((res) => res.json());
  }

  // ------------------------------------------------------------ доска: поиск

  function findBoardRoot() {
    const candidates = [
      'wc-chess-board',
      '#board-single',
      '.board-layout-main wc-chess-board',
      'cg-board',
      '.cg-wrap',
    ];
    for (const selector of candidates) {
      const el = document.querySelector(selector);
      if (el) {
        const rect = el.getBoundingClientRect();
        if (rect.width > 120 && rect.height > 120) return el;
      }
    }
    return null;
  }

  function pieceElements(root) {
    let nodes = root.querySelectorAll('.piece, piece');
    if (!nodes.length) nodes = document.querySelectorAll('.piece');
    return Array.prototype.slice.call(nodes).filter((el) => {
      const cls = el.className && el.className.baseVal !== undefined ? el.className.baseVal : el.className;
      if (typeof cls !== 'string') return true;
      return !/(dragging|ghost|premove|hidden|fading)/.test(cls);
    });
  }

  // ------------------------------------------------------- доска: фигура

  function pieceInfo(el) {
    const classes = Array.prototype.slice.call(el.classList || []);

    // вариант 1: классы "wp", "bn" (chess.com)
    for (const cls of classes) {
      const m = /^([wb])([pnbrqk])$/.exec(cls);
      if (m) return { color: m[1], type: m[2] };
    }

    // вариант 2: отдельные классы "white"/"black" + "pawn" (lichess)
    let color = null;
    let type = null;
    for (const cls of classes) {
      if (cls === 'white') color = 'w';
      else if (cls === 'black') color = 'b';
      else if (TYPE_BY_WORD[cls]) type = TYPE_BY_WORD[cls];
    }
    if (color && type) return { color, type };

    // вариант 3: data-атрибуты
    const dataPiece = (el.getAttribute('data-piece') || '').toLowerCase();
    const m3 = /^([wb])([pnbrqk])$/.exec(dataPiece);
    if (m3) return { color: m3[1], type: m3[2] };
    const dataColor = (el.getAttribute('data-color') || '').toLowerCase();
    const dataType = (el.getAttribute('data-type') || '').toLowerCase();
    if ((dataColor === 'white' || dataColor === 'w') && TYPE_BY_WORD[dataType]) {
      return { color: 'w', type: TYPE_BY_WORD[dataType] };
    }
    if ((dataColor === 'black' || dataColor === 'b') && TYPE_BY_WORD[dataType]) {
      return { color: 'b', type: TYPE_BY_WORD[dataType] };
    }

    // вариант 4: картинка вида .../wp.png или .../white-pawn.svg
    const url = imageUrl(el);
    if (url) {
      let m = /(?:^|[^a-z])([wb])[_-]?([pnbrqk])\.(?:png|svg|webp)(?:[?#]|$)/i.exec(url);
      if (m) return { color: m[1].toLowerCase(), type: m[2].toLowerCase() };
      m = /(?:^|[^a-z])(white|black)[_-]?(pawn|knight|bishop|rook|queen|king)\.(?:png|svg|webp)/i.exec(url);
      if (m) return { color: m[1].toLowerCase() === 'white' ? 'w' : 'b', type: TYPE_BY_WORD[m[2].toLowerCase()] };
    }

    return null;
  }

  function imageUrl(el) {
    const bg = getComputedStyle(el).backgroundImage || '';
    const m = /url\(["']?([^"')]+)["']?\)/.exec(bg);
    if (m) return m[1];
    const img = el.querySelector('img');
    if (img && img.src) return img.src;
    return '';
  }

  // ------------------------------------------------- доска: координаты

  function classSquare(el) {
    const attr = el.getAttribute('data-square');
    if (attr && /^[a-h][1-8]$/.test(attr)) {
      return { file: attr.charCodeAt(0) - 97, rank: parseInt(attr[1], 10) - 1 };
    }
    for (const cls of el.classList || []) {
      const m = /^square-(\d)(\d)$/.exec(cls);
      if (m) return { file: parseInt(m[1], 10) - 1, row: parseInt(m[2], 10) - 1 };
    }
    return null;
  }

  function geometrySquare(rect, board, orientation) {
    const cellW = board.width / 8;
    const cellH = board.height / 8;
    const cx = rect.left + rect.width / 2 - board.left;
    const cy = rect.top + rect.height / 2 - board.top;
    const col = Math.floor(cx / cellW);
    const row = Math.floor(cy / cellH);
    if (col < 0 || col > 7 || row < 0 || row > 7) return null;
    return orientation === 'white'
      ? { file: col, rank: 7 - row }
      : { file: 7 - col, rank: row };
  }

  function squareName(sq) {
    return String.fromCharCode(97 + sq.file) + (sq.rank + 1);
  }

  function sameSquare(a, b) {
    return a && b && a.file === b.file && a.rank === b.rank;
  }

  // ----------------------------------------------------- доска: ориентация

  function detectOrientation(root, pieces) {
    if (prefs.orientation === 'white' || prefs.orientation === 'black') {
      return prefs.orientation;
    }
    const cls = (root.className && root.className.baseVal !== undefined ? root.className.baseVal : root.className) || '';
    if (/(^|\s)flipped(\s|$)/.test(cls) || root.classList.contains('flipped')) return 'black';

    // подписи линий/рядов: ищем "1..8" у левого края
    const board = root.getBoundingClientRect();
    let best = null;
    root.querySelectorAll('[class*="coordinate"]').forEach((el) => {
      const text = (el.textContent || '').trim();
      if (!/^[1-8]$/.test(text)) return;
      const r = el.getBoundingClientRect();
      if (r.width === 0) return;
      const nearLeft = r.left - board.left < board.width * 0.12;
      if (!nearLeft) return;
      if (!best || r.top < best.top) best = { top: r.top, text: text };
    });
    if (best) return best.text === '8' ? 'white' : 'black';

    // по фигурам: чьи фигуры стоят в двух нижних рядах
    let bottomWhite = 0;
    let bottomBlack = 0;
    for (const piece of pieces) {
      const r = piece.rect;
      const rel = (r.top + r.height / 2 - board.top) / board.height;
      if (rel > 0.75) {
        if (piece.info.color === 'w') bottomWhite += 1;
        else bottomBlack += 1;
      }
    }
    if (bottomWhite !== bottomBlack) return bottomWhite > bottomBlack ? 'white' : 'black';

    return 'white';
  }

  // ----------------------------------------------------- доска: сборка FEN

  function readBoard() {
    const root = findBoardRoot();
    if (!root) return null;

    const board = root.getBoundingClientRect();
    const pieces = [];
    for (const el of pieceElements(root)) {
      const info = pieceInfo(el);
      if (!info) continue;
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) continue;
      pieces.push({ el, info, rect, cls: classSquare(el) });
    }
    if (pieces.length < 2) return null;

    let orientation = detectOrientation(root, pieces);

    // сверяем геометрию с классами square-NN: если они расходятся,
    // значит ориентация определена неверно (или сменилось соглашение chess.com)
    const withClass = pieces.filter((p) => p.cls);
    if (withClass.length >= 6 && prefs.orientation === 'auto') {
      const score = { white: 0, black: 0 };
      for (const piece of withClass) {
        const asWhite = { file: piece.cls.file, rank: 7 - piece.cls.row };
        const asBlack = { file: 7 - piece.cls.file, rank: piece.cls.row };
        if (sameSquare(geometrySquare(piece.rect, board, 'white'), asWhite)) score.white += 1;
        if (sameSquare(geometrySquare(piece.rect, board, 'black'), asBlack)) score.black += 1;
      }
      const total = withClass.length;
      if (score.white > score.black && score.white / total > 0.8) orientation = 'white';
      else if (score.black > score.white && score.black / total > 0.8) orientation = 'black';
    }

    const grid = [];
    for (let rank = 0; rank < 8; rank += 1) grid.push(new Array(8).fill(null));

    for (const piece of pieces) {
      const sq = geometrySquare(piece.rect, board, orientation);
      if (!sq) continue;
      if (grid[sq.rank][sq.file]) return { error: 'две фигуры на одном поле', orientation, root };
      grid[sq.rank][sq.file] = piece.info;
    }

    const placement = placementFromGrid(grid);
    if (!placement) return { error: 'нечитаемая расстановка', orientation, root };

    const turn = readTurn(orientation);
    return {
      root,
      board,
      orientation,
      placement,
      turn,
      key: placement + ' ' + turn,
      source: location.hostname.replace(/^www\./, ''),
    };
  }

  function placementFromGrid(grid) {
    let whiteKings = 0;
    let blackKings = 0;
    const rows = [];
    for (let rank = 7; rank >= 0; rank -= 1) {
      let row = '';
      let empty = 0;
      for (let file = 0; file < 8; file += 1) {
        const cell = grid[rank][file];
        if (!cell) {
          empty += 1;
          continue;
        }
        if (empty) {
          row += empty;
          empty = 0;
        }
        const letter = cell.type;
        if (letter < 'a') return null;
        row += cell.color === 'w' ? letter.toUpperCase() : letter;
        if (cell.type === 'k') {
          if (cell.color === 'w') whiteKings += 1;
          else blackKings += 1;
        }
        if (cell.type === 'p' && (rank === 0 || rank === 7)) return null;
      }
      if (empty) row += empty;
      rows.push(row);
    }
    if (whiteKings !== 1 || blackKings !== 1) return null;
    return rows.join('/');
  }

  function readTurn(orientation) {
    const bottom = orientation === 'white' ? 'w' : 'b';
    const opposite = bottom === 'w' ? 'b' : 'w';

    // chess.com: активные часы
    const bottomClock = document.querySelector('.clock-bottom');
    const topClock = document.querySelector('.clock-top');
    if (bottomClock && bottomClock.classList.contains('clock-player-turn')) return bottom;
    if (topClock && topClock.classList.contains('clock-player-turn')) return opposite;

    // lichess: rclock
    const lichessBottom = document.querySelector('.rclock-bottom');
    const lichessTop = document.querySelector('.rclock-top');
    if (lichessBottom && lichessBottom.classList.contains('active')) return bottom;
    if (lichessTop && lichessTop.classList.contains('active')) return opposite;

    // запасной вариант: чётность ходов в списке
    let maxPly = 0;
    document.querySelectorAll('[data-ply]').forEach((el) => {
      const ply = parseInt(el.getAttribute('data-ply'), 10);
      if (!Number.isNaN(ply) && ply > maxPly) maxPly = ply;
    });
    if (maxPly > 0) return maxPly % 2 === 0 ? 'b' : 'w';

    if (document.querySelector('.move-list, .vertical-move-list')) {
      const moves = document.querySelectorAll('.move-list .node, .vertical-move-list .node');
      if (moves.length) return moves.length % 2 === 0 ? 'w' : 'b';
    }

    return bottom === 'w' ? 'w' : 'b';
  }

  // ------------------------------------------------------------ оверлей

  const ui = { host: null, svg: null, chip: null, status: null, key: '' };

  function ensureUi(root) {
    if (ui.host && ui.host.isConnected) return;
    ui.host = document.createElement('div');
    ui.host.id = 'chess-coach-overlay';
    ui.host.style.cssText =
      'position:absolute;inset:0;pointer-events:none;z-index:2147483000;';

    ui.svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    ui.svg.setAttribute('viewBox', '0 0 100 100');
    ui.svg.setAttribute('preserveAspectRatio', 'none');
    ui.svg.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;overflow:visible;';
    ui.host.appendChild(ui.svg);

    ui.chip = document.createElement('div');
    ui.chip.style.cssText = [
      'position:absolute', 'left:6px', 'bottom:6px', 'display:flex', 'gap:8px',
      'align-items:center', 'padding:5px 10px', 'border-radius:8px',
      'background:rgba(18,20,24,.88)', 'color:#e8eaed',
      "font:600 14px/1.2 'Segoe UI',system-ui,sans-serif",
      'box-shadow:0 4px 16px rgba(0,0,0,.35)', 'pointer-events:auto',
      'backdrop-filter:blur(4px)', 'user-select:none', 'white-space:nowrap',
    ].join(';');
    ui.status = document.createElement('div');
    ui.status.style.cssText = "font:500 11px/1.2 'Segoe UI',system-ui,sans-serif;color:#8b95a3;";
    ui.chip.appendChild(ui.status);
    ui.chip.addEventListener('click', (event) => {
      if (event.shiftKey) {
        prefs.orientation = prefs.orientation === 'white' ? 'black' : 'white';
        savePref('orientation', prefs.orientation);
      } else {
        prefs.debug = !prefs.debug;
        savePref('debug', prefs.debug);
      }
      ui.key = '';
    });
    ui.host.appendChild(ui.chip);

    const computed = getComputedStyle(root).position;
    if (computed === 'static') root.style.position = 'relative';
    root.appendChild(ui.host);
  }

  function removeUi() {
    if (ui.host && ui.host.parentElement) ui.host.parentElement.removeChild(ui.host);
    ui.host = null;
    ui.svg = null;
    ui.chip = null;
    ui.key = '';
  }

  function svgEl(name, attrs) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', name);
    for (const key in attrs) node.setAttribute(key, attrs[key]);
    return node;
  }

  function cellCenter(square, orientation) {
    const file = square.charCodeAt(0) - 97;
    const rank = parseInt(square[1], 10) - 1;
    const col = orientation === 'white' ? file : 7 - file;
    const row = orientation === 'white' ? 7 - rank : rank;
    return { x: col * 12.5 + 6.25, y: row * 12.5 + 6.25 };
  }

  function drawArrow(best, orientation) {
    ui.svg.innerHTML = '';
    if (!best || !best.from || !best.to) return;
    const a = cellCenter(best.from, orientation);
    const b = cellCenter(best.to, orientation);

    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const len = Math.hypot(dx, dy) || 1;
    const ux = dx / len;
    const uy = dy / len;
    const px = -uy;
    const py = ux;

    const tip = { x: b.x + ux * 1.5, y: b.y + uy * 1.5 };
    const head = 7.5;
    const halfWidth = 4.2;
    const base = { x: tip.x - ux * head, y: tip.y - uy * head };

    ui.svg.appendChild(svgEl('line', {
      x1: a.x + ux * 3.2, y1: a.y + uy * 3.2, x2: base.x, y2: base.y,
      stroke: 'rgba(0,0,0,.45)', 'stroke-width': 3.4, 'stroke-linecap': 'round',
    }));
    ui.svg.appendChild(svgEl('line', {
      x1: a.x + ux * 3.2, y1: a.y + uy * 3.2, x2: base.x, y2: base.y,
      stroke: '#35d07f', 'stroke-width': 1.9, 'stroke-linecap': 'round',
    }));
    ui.svg.appendChild(svgEl('polygon', {
      points: `${tip.x},${tip.y} ${base.x + px * halfWidth},${base.y + py * halfWidth} ${base.x - px * halfWidth},${base.y - py * halfWidth}`,
      fill: '#35d07f', stroke: 'rgba(0,0,0,.45)', 'stroke-width': 0.5,
    }));
  }

  // ------------------------------------------------------------ основной цикл

  let current = null;      // последняя прочитанная позиция
  let sentKey = '';
  let stable = 0;
  let candidateKey = '';
  let serverState = null;
  let online = false;

  function tick() {
    const position = readBoard();

    if (!position) {
      removeUi();
      current = null;
      return;
    }
    if (position.error) {
      ensureUi(position.root);
      if (ui.status) ui.status.textContent = 'не читается: ' + position.error;
      return;
    }

    ensureUi(position.root);
    current = position;

    // ждём, пока позиция два раза подряд прочитается одинаково
    if (position.key === candidateKey) stable += 1;
    else {
      candidateKey = position.key;
      stable = 0;
    }

    if (stable >= 1 && position.key !== sentKey) {
      sentKey = position.key;
      api('POST', '/api/position', {
        placement: position.placement,
        turn: position.turn,
        orientation: position.orientation,
        source: position.source,
      }).catch(() => {
        sentKey = '';
      });
    }

    render();
  }

  function pollState() {
    api('GET', '/api/state')
      .then((data) => {
        serverState = data;
        if (!online) sentKey = ''; // сервер перезапустился — отправим позицию заново
        online = true;
      })
      .catch(() => {
        if (online) sentKey = '';
        online = false;
      });
  }

  function render() {
    if (!current || !ui.host) return;

    const analysis = serverState && serverState.position && serverState.position.fen
      ? serverState.analysis
      : null;
    const fresh = analysis && serverState.position.fen.split(' ')[0] === current.placement;

    const best = fresh ? analysis.best : null;
    const signed = fresh ? analysis.score.text : '';
    const depth = fresh ? analysis.depth : 0;

    const key = [current.key, best && best.uci, signed, prefs.debug, online].join('|');
    if (key !== ui.key) {
      ui.key = key;
      drawArrow(prefs.overlay ? best : null, current.orientation);

      const move = best ? best.ru : '';
      const text = prefs.debug
        ? `${current.source} · ${current.turn === 'w' ? 'белые' : 'чёрные'} · ${current.orientation} снизу · ${current.placement}`
        : (move ? `${move}` : (online ? 'думаю…' : 'нет связи с движком'));

      ui.status.textContent = text;
      const detail = !prefs.debug && best ? ` ${signed} · d${depth}` : '';
      if (detail) {
        ui.status.textContent = text + detail;
      }
      ui.status.style.color = best ? '#e8eaed' : '#8b95a3';
      ui.chip.style.borderLeft = online ? '3px solid #35d07f' : '3px solid #e5645e';
    }
  }

  window.addEventListener('resize', () => {
    ui.key = '';
  });

  document.addEventListener('keydown', (event) => {
    if (!event.altKey) return;
    if (event.code === 'KeyC') {
      prefs.overlay = !prefs.overlay;
      savePref('overlay', prefs.overlay);
      ui.key = '';
    } else if (event.code === 'KeyD') {
      prefs.debug = !prefs.debug;
      savePref('debug', prefs.debug);
      ui.key = '';
    }
  });

  setInterval(tick, TICK_MS);
  setInterval(pollState, STATE_MS);
  setTimeout(() => {
    tick();
    pollState();
  }, 600);

  // отладочная ручка: window.chessCoach.readBoard() в консоли браузера
  window.chessCoach = {
    readBoard,
    state: () => serverState,
    online: () => online,
    prefs,
    current: () => current,
  };
})();
