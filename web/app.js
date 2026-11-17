/* ===== 学考打卡 · 应用逻辑 ===== */
'use strict';

/* ---------- 存储 ---------- */
const DB_KEY = 'xuekao_v1';
const DEFAULTS = { newPerDay: 10, reviewPerDay: 5, instantExplain: true };

function loadDB() {
  try {
    const raw = localStorage.getItem(DB_KEY);
    if (!raw) return { records: {}, checkins: {}, settings: { ...DEFAULTS } };
    const db = JSON.parse(raw);
    db.records = db.records || {};
    db.checkins = db.checkins || {};
    db.settings = Object.assign({}, DEFAULTS, db.settings || {});
    return db;
  } catch (e) {
    console.warn('读取本地数据失败，使用空库', e);
    return { records: {}, checkins: {}, settings: { ...DEFAULTS } };
  }
}

let DB = loadDB();

function saveDB() {
  try {
    localStorage.setItem(DB_KEY, JSON.stringify(DB));
  } catch (e) {
    toast('保存失败：浏览器存储空间不足');
    console.error(e);
  }
}

/* ---------- 日期工具 ---------- */
function todayStr(d = new Date()) {
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
function addDays(dateStr, n) {
  const [y, m, d] = dateStr.split('-').map(Number);
  const dt = new Date(y, m - 1, d + n);
  return todayStr(dt);
}
function daysBetween(a, b) {
  const [y1, m1, d1] = a.split('-').map(Number);
  const [y2, m2, d2] = b.split('-').map(Number);
  return Math.round((new Date(y2, m2 - 1, d2) - new Date(y1, m1 - 1, d1)) / 86400000);
}

/* ---------- 题库 ---------- */
let BANK = [];
let BANK_BY_ID = {};

async function loadBank() {
  const res = await fetch('../data/questions.json');
  if (!res.ok) throw new Error('题库文件加载失败：' + res.status);
  const data = await res.json();
  BANK = data.questions || [];
  BANK_BY_ID = {};
  BANK.forEach(q => { BANK_BY_ID[q.id] = q; });
  return BANK;
}

/* ---------- 间隔重复 ----------
   reps：连续答对次数。答对按 1/3/7/15/30/60 天递进，答错归零次日重现。 */
const INTERVALS = [1, 3, 7, 15, 30, 60];

function recordOf(qid) {
  return DB.records[qid] || null;
}

function applyAnswer(qid, isCorrect, chosen) {
  const today = todayStr();
  let r = DB.records[qid];
  if (!r) {
    r = DB.records[qid] = {
      reps: 0, wrongCount: 0, rightCount: 0,
      nextReview: today, lastSeen: today, lastWrong: null, interval: 0,
    };
  }
  r.lastSeen = today;
  if (isCorrect) {
    r.rightCount = (r.rightCount || 0) + 1;
    r.reps = (r.reps || 0) + 1;
    r.interval = INTERVALS[Math.min(r.reps - 1, INTERVALS.length - 1)];
    r.nextReview = addDays(today, r.interval);
  } else {
    r.wrongCount = (r.wrongCount || 0) + 1;
    r.reps = 0;
    r.interval = 1;
    r.nextReview = addDays(today, 1);   // 明天再来
    r.lastWrong = today;
  }
  r.lastAnswer = chosen;
  return r;
}

/* ---------- 抽题 ---------- */
function buildTodayTask(opts = {}) {
  const today = todayStr();
  const settings = DB.settings;
  const wantNew = settings.newPerDay;
  // 复习量：默认取设置值；「只练错题」时才放开上限
  const wantReview = opts.noReview ? 0
    : (opts.onlyWrong ? 999 : settings.reviewPerDay);

  const seen = Object.keys(DB.records);

  // ① 到期的错题（做过且答错过），越久没复习越靠前
  const due = seen
    .map(id => ({ id, r: DB.records[id] }))
    .filter(x => x.r.wrongCount > 0 && (x.r.nextReview || today) <= today)
    .sort((a, b) => (a.r.nextReview || '').localeCompare(b.r.nextReview || ''))
    .slice(0, wantReview)
    .map(x => x.id);

  // ② 从没做过的新题
  const doneSet = new Set(seen);
  const fresh = BANK.filter(q => !doneSet.has(q.id)).slice(0, wantNew);

  const task = [...due, ...fresh.map(q => q.id)];

  // ③ 新题不够（题库刷完）时，把「已到期但没进错题本」的题补进来
  if (task.length < wantNew && !opts.onlyWrong) {
    const extra = seen
      .filter(id => !task.includes(id) && (DB.records[id].nextReview || today) <= today)
      .slice(0, wantNew - task.length);
    task.push(...extra);
  }

  return task.map(id => BANK_BY_ID[id]).filter(Boolean);
}

/* ---------- 打卡与统计 ---------- */
function markCheckinIfDone() {
  const today = todayStr();
  const dayRecords = Object.entries(DB.records)
    .filter(([, r]) => r.lastSeen === today);
  if (dayRecords.length > 0) {
    DB.checkins[today] = Math.max(DB.checkins[today] || 0, dayRecords.length);
    saveDB();
  }
}

function streakCount() {
  let n = 0;
  let d = todayStr();
  // 今天没打卡就从昨天开始数，保证"昨天打了卡"仍显示连续
  if (!DB.checkins[d]) d = addDays(d, -1);
  while (DB.checkins[d]) {
    n++;
    d = addDays(d, -1);
  }
  return n;
}

/* ---------- 判分 ---------- */
function judge(q, chosen) {
  if (!chosen) return false;
  return chosen === q.answer;
}

/* ---------- DOM 助手 ---------- */
const $ = sel => document.querySelector(sel);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

let toastTimer = null;
function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 2200);
}

function showScreen(id) {
  ['screen-home', 'screen-quiz', 'screen-result', 'screen-wrong', 'screen-settings']
    .forEach(s => { $('#' + s).hidden = (s !== id); });
  window.scrollTo(0, 0);
}

/* ---------- 图片放大 ---------- */
function ensureImgModal() {
  if ($('#imgModal')) return;
  const m = el('div');
  m.id = 'imgModal';
  const img = el('img');
  m.appendChild(img);
  m.addEventListener('click', () => m.classList.remove('show'));
  document.body.appendChild(m);
}
function zoomImage(src) {
  ensureImgModal();
  const m = $('#imgModal');
  m.querySelector('img').src = src;
  m.classList.add('show');
}

/* ---------- 进度预测 ----------
   两个独立的目标：
   ① 新题清空：剩余未做题数 ÷ 每天新题量
   ② 错题消化：错题本里还没「连对 3 次」的题，每天能清掉多少

   错题侧的口径：
   - 每道错题要连续答对 3 次才算「掌握」（REPS_TO_MASTER）
   - 每天复习题名额 = reviewPerDay
   - 但每做一道错题，答对后不是立刻消失，而是进入下一次复习
     所以「每天净减少」= 当天复习题里答对且达到 3 次的比例
   为了不画虚假的饼，正确率取「历史实测正确率」，没有历史数据时按 70% 估算并标注。
   ---------------------------------------------------------------- */
const REPS_TO_MASTER = 3;

function historyAccuracy() {
  // 用最近答过的记录算正确率；样本太少返回 null
  const recs = Object.values(DB.records).filter(r => r.lastAnswer);
  const attempts = recs.reduce((s, r) => s + (r.rightCount || 0) + (r.wrongCount || 0), 0);
  if (attempts < 10) return null;
  const right = recs.reduce((s, r) => s + (r.rightCount || 0), 0);
  return right / attempts;
}

function computeForecast() {
  const s = DB.settings;
  const seen = new Set(Object.keys(DB.records));
  const remainingNew = BANK.filter(q => !seen.has(q.id)).length;

  // 错题：还没掌握、且仍需复习的
  const openWrong = Object.entries(DB.records)
    .filter(([, r]) => r.wrongCount > 0 && (r.reps || 0) < REPS_TO_MASTER)
    .length;

  const acc = historyAccuracy();
  const accUsed = acc === null ? 0.7 : acc;
  const accIsEstimate = acc === null;

  // 新题清空所需天数
  const newDays = s.newPerDay > 0 ? Math.ceil(remainingNew / s.newPerDay) : Infinity;

  // 错题消化：每天复习 reviewPerDay 道。
  // 一道错题「连对 3 次」平均需要 1/acc^3 次作答机会（答错会归零重来）。
  // 每天能提供的作答机会 = reviewPerDay，故每日净消化量 = reviewPerDay * acc^3
  const perDayClear = s.reviewPerDay * Math.pow(accUsed, REPS_TO_MASTER);
  const wrongDays = perDayClear > 0 ? Math.ceil(openWrong / perDayClear) : Infinity;

  // 整体完成 = 两者都完成的较晚那天
  const totalDays = Math.max(
    Number.isFinite(newDays) ? newDays : 0,
    Number.isFinite(wrongDays) ? wrongDays : 0
  );

  return {
    remainingNew, openWrong, newDays, wrongDays, totalDays,
    acc: Math.round(accUsed * 100), accIsEstimate,
    newPerDay: s.newPerDay, reviewPerDay: s.reviewPerDay,
    totalDone: seen.size, bankTotal: BANK.length,
  };
}

function fmtDays(n) {
  if (!Number.isFinite(n)) return '—';
  if (n <= 0) return '已完成';
  return n + ' 天';
}

function renderForecast() {
  const f = computeForecast();
  const box = $('#forecastBox');
  if (!box) return;
  box.innerHTML = '';

  // 一句话结论
  const head = el('div', 'fc-head');
  if (f.remainingNew === 0 && f.openWrong === 0) {
    head.appendChild(el('div', 'fc-big', '🎉 全部完成'));
    head.appendChild(el('div', 'fc-sub', `${f.bankTotal} 题已全部掌握`));
  } else {
    head.appendChild(el('div', 'fc-big', `预计还需 ${fmtDays(f.totalDays)}`));
    head.appendChild(el('div', 'fc-sub',
      `当前 ${f.newPerDay} 新 / ${f.reviewPerDay} 复习 · 按历史正确率 ${f.acc}% 估算`));
  }
  box.appendChild(head);

  // 两条进度线
  const rows = [
    {
      label: '新题',
      left: f.remainingNew,
      total: f.bankTotal,
      days: f.newDays,
      hint: f.remainingNew === 0 ? '已全部做过' : `剩 ${f.remainingNew} 题`,
    },
    {
      label: '错题消化',
      left: f.openWrong,
      total: null,
      days: f.wrongDays,
      // 复习名额为 0 时，错题永远不会减少 —— 必须明确提示，不能显示「已清空」
      hint: f.openWrong === 0 ? '错题已清空'
        : (f.reviewPerDay === 0
            ? `待巩固 ${f.openWrong} 题 · 复习名额为 0，不会减少`
            : `待巩固 ${f.openWrong} 题`),
      raw: true,
    },
  ];

  rows.forEach(r => {
    const row = el('div', 'fc-row');
    const top = el('div', 'fc-row-top');
    top.appendChild(el('span', 'fc-label', r.label));
    top.appendChild(el('span', 'fc-days', fmtDays(r.days)));
    row.appendChild(top);

    const bar = el('div', 'fc-bar');
    const fill = el('i');
    let pct;
    if (r.raw) {
      // 错题没有固定总量：清空才满格，否则按剩余量给个可视化的占比
      if (r.left === 0) pct = 100;
      else pct = Math.max(4, Math.min(92, Math.round(100 - Math.min(r.left, 100))));
      fill.style.background = 'var(--warn)';
    } else {
      pct = r.total ? Math.round((r.total - r.left) / r.total * 100) : 100;
    }
    fill.style.width = pct + '%';
    bar.appendChild(fill);
    row.appendChild(bar);

    row.appendChild(el('div', 'fc-hint', r.hint));
    box.appendChild(row);
  });

  // 提示：正确率是估的还是实测的
  const note = el('div', 'fc-note');
  note.textContent = f.accIsEstimate
    ? '※ 做题记录还太少，正确率暂按 70% 估算；做过 10 题后按你的实际数据算。'
    : '※ 基于你的历史正确率推算，坚持复习就会越来越准。';
  box.appendChild(note);
}

/* ---------- 首页渲染 ---------- */
function renderHome() {
  const today = todayStr();

  // 连续打卡
  $('#streakDays').textContent = streakCount();

  // 今日任务预览
  const task = buildTodayTask();
  const dueCount = task.filter(q => {
    const r = DB.records[q.id];
    return r && r.wrongCount > 0;
  }).length;

  $('#numNew').textContent = task.length - dueCount;
  $('#numReview').textContent = dueCount;

  const doneToday = Object.values(DB.records).filter(r => r.lastSeen === today).length;
  $('#numDone').textContent = doneToday;

  if (!BANK.length) {
    $('#todaySub').textContent = '题库未加载';
  } else if (task.length === 0) {
    $('#todaySub').textContent = '今天的题都练完了，休息一下 🎉';
  } else {
    $('#todaySub').textContent = `共 ${task.length} 题 · 题库 ${BANK.length} 题`;
  }
  $('#btnStart').disabled = task.length === 0;
  $('#btnStart').textContent = task.length ? '开始打卡' : '今日已完成';

  // 科目进度
  const box = $('#subjectProgress');
  box.innerHTML = '';
  ['生物', '历史', '政治', '地理'].forEach(sub => {
    const all = BANK.filter(q => q.subject === sub).length;
    if (!all) return;
    const done = BANK.filter(q => q.subject === sub && DB.records[q.id]).length;
    const pct = all ? Math.round(done / all * 100) : 0;
    const item = el('div', 'sp-item');
    const top = el('div', 'sp-top');
    top.appendChild(el('span', 'sp-name', sub));
    top.appendChild(el('span', 'sp-count', `${done}/${all}`));
    item.appendChild(top);
    const bar = el('div', 'sp-bar');
    const fill = el('i');
    fill.style.width = pct + '%';
    bar.appendChild(fill);
    item.appendChild(bar);
    box.appendChild(item);
  });

  renderCalendar();
  renderForecast();
  renderStats();
}

function renderCalendar() {
  const box = $('#calendar');
  box.innerHTML = '';
  const now = new Date();
  const year = now.getFullYear();
  const month = now.getMonth();
  const first = new Date(year, month, 1);
  const startPad = (first.getDay() + 6) % 7;   // 周一为第一列
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const today = todayStr();

  ['一', '二', '三', '四', '五', '六', '日'].forEach(d => {
    box.appendChild(el('div', 'cal-head', d));
  });
  for (let i = 0; i < startPad; i++) {
    box.appendChild(el('div', 'cal-cell blank'));
  }
  for (let d = 1; d <= daysInMonth; d++) {
    const ds = `${year}-${String(month + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
    const cell = el('div', 'cal-cell', String(d));
    if (DB.checkins[ds]) cell.classList.add('dot-on');
    if (ds === today) cell.classList.add('dot-today');
    box.appendChild(cell);
  }
}

function renderStats() {
  const recs = Object.values(DB.records);
  const total = recs.length;
  const right = recs.reduce((s, r) => s + (r.rightCount || 0), 0);
  const wrong = recs.reduce((s, r) => s + (r.wrongCount || 0), 0);
  const attempts = right + wrong;
  const acc = attempts ? Math.round(right / attempts * 100) : 0;
  const wrongBook = recs.filter(r => r.wrongCount > 0 && r.reps < 3).length;
  const checkinDays = Object.keys(DB.checkins).length;

  const box = $('#statsBox');
  box.innerHTML = '';
  const items = [
    ['已练题数', total],
    ['累计正确率', acc + '%'],
    ['打卡天数', checkinDays],
    ['待巩固错题', wrongBook],
  ];
  items.forEach(([label, val]) => {
    const it = el('div', 'stat-item');
    it.appendChild(el('b', '', String(val)));
    it.appendChild(el('span', '', label));
    box.appendChild(it);
  });
}

/* ---------- 做题 ---------- */
let QUIZ = { list: [], idx: 0, answers: {}, revealed: {} };

function startQuiz(list) {
  if (!list.length) { toast('没有可练的题目'); return; }
  QUIZ = { list, idx: 0, answers: {}, revealed: {} };
  showScreen('screen-quiz');
  renderQuestion();
}

function renderQuestion() {
  const q = QUIZ.list[QUIZ.idx];
  const total = QUIZ.list.length;
  const chosen = QUIZ.answers[q.id] || null;
  const revealed = QUIZ.revealed[q.id] || false;

  $('#progressFill').style.width = ((QUIZ.idx + 1) / total * 100) + '%';
  $('#progressText').textContent = `${QUIZ.idx + 1} / ${total}`;
  $('#quizSubject').textContent = q.subject;

  const body = $('#quizBody');
  body.innerHTML = '';

  const card = el('div', 'q-card');
  card.appendChild(el('div', 'q-meta',
    `第 ${q.no} 题 · 来源 PDF 第 ${q.page} 页`));
  card.appendChild(el('div', 'q-stem', q.stem));

  if (q.img) {
    const img = el('img', 'q-img');
    img.src = '../data/' + q.img;
    img.alt = '原题配图';
    img.loading = 'lazy';
    img.addEventListener('click', () => zoomImage(img.src));
    card.appendChild(img);
  }

  const optKeys = Object.keys(q.options || {});
  if (optKeys.length === 0) {
    // 选项本身就是图片（如地理 64），提示看图作答
    const tip = el('p', 'muted', '本题选项为图片，请对照上方原题图片作答。');
    card.appendChild(tip);
    ['A', 'B', 'C', 'D'].forEach(k => {
      card.appendChild(makeOption(q, k, '（见图）', chosen, revealed));
    });
  } else {
    optKeys.forEach(k => {
      card.appendChild(makeOption(q, k, q.options[k], chosen, revealed));
    });
  }

  if (revealed) {
    card.appendChild(buildExplain(q, chosen));
  }

  body.appendChild(card);

  $('#btnPrev').disabled = QUIZ.idx === 0;
  const last = QUIZ.idx === total - 1;
  $('#btnNext').textContent = last ? '交卷' : (revealed ? '下一题' : '提交本题');
}

function makeOption(q, key, text, chosen, revealed) {
  const o = el('div', 'opt');
  o.appendChild(el('div', 'opt-key', key));
  o.appendChild(el('div', 'opt-text', text));

  if (chosen === key) o.classList.add('selected');
  if (revealed) {
    if (key === q.answer) o.classList.add('correct');
    else if (chosen === key) o.classList.add('wrong');
  }

  if (!revealed) {
    o.addEventListener('click', () => {
      QUIZ.answers[q.id] = key;
      renderQuestion();
    });
  }
  return o;
}

function buildExplain(q, chosen) {
  const wrap = el('div', 'explain');
  wrap.appendChild(el('div', 'explain-title',
    chosen === q.answer ? '✅ 回答正确' : `❌ 回答错误（你选 ${chosen || '未选'}，正确 ${q.answer}）`));
  if (q.explanation && q.explanation.trim()) {
    wrap.appendChild(el('div', 'explain-body', q.explanation));
  } else {
    wrap.appendChild(el('div', 'explain-empty',
      '本题原卷未提供文字解析，请参考上方原题图片自行核对。'));
  }
  return wrap;
}

function submitCurrent() {
  const q = QUIZ.list[QUIZ.idx];
  const chosen = QUIZ.answers[q.id];
  if (!chosen) { toast('请先选择一个答案'); return; }

  const isCorrect = judge(q, chosen);
  applyAnswer(q.id, isCorrect, chosen);
  QUIZ.revealed[q.id] = true;
  saveDB();
  markCheckinIfDone();

  if (DB.settings.instantExplain) {
    renderQuestion();
    toast(isCorrect ? '✅ 正确' : '❌ 已记入错题本');
  } else {
    nextQuestion();
  }
}

function nextQuestion() {
  if (QUIZ.idx < QUIZ.list.length - 1) {
    QUIZ.idx++;
    renderQuestion();
  } else {
    finishQuiz();
  }
}

function finishQuiz() {
  // 未作答的题也算一遍（防止漏算打卡）
  QUIZ.list.forEach(q => {
    if (!QUIZ.answers[q.id]) {
      applyAnswer(q.id, false, null);
    }
  });
  saveDB();
  markCheckinIfDone();
  renderResult();
  showScreen('screen-result');
}

/* ---------- 结果页 ---------- */
function renderResult() {
  const list = QUIZ.list;
  const right = list.filter(q => judge(q, QUIZ.answers[q.id])).length;
  const wrong = list.length - right;
  const acc = list.length ? Math.round(right / list.length * 100) : 0;

  const sum = $('#resultSummary');
  sum.innerHTML = '';
  sum.appendChild(el('div', 'rs-score', acc + '%'));
  sum.appendChild(el('div', 'rs-label', '本次正确率'));
  const det = el('div', 'rs-detail');
  [['总题数', list.length], ['答对', right], ['答错', wrong]].forEach(([l, v]) => {
    const d = el('div');
    d.appendChild(el('b', '', String(v)));
    d.appendChild(document.createTextNode(l));
    det.appendChild(d);
  });
  sum.appendChild(det);

  const box = $('#resultList');
  box.innerHTML = '';
  list.forEach(q => {
    box.appendChild(buildResultCard(q, QUIZ.answers[q.id], true));
  });

  $('#btnRedoWrong').style.display = wrong ? '' : 'none';
}
function buildResultCard(q, chosen, withImage) {
  const ok = judge(q, chosen);
  const card = el('div', 'rcard' + (ok ? '' : ' bad'));

  const head = el('div', 'rcard-head');
  head.appendChild(el('span', 'rcard-id', `${q.subject} · 第${q.no}题`));
  head.appendChild(el('span', 'rcard-badge ' + (ok ? 'ok' : 'bad'),
    ok ? '✔ 正确' : '✘ 错误'));
  card.appendChild(head);

  card.appendChild(el('div', 'rcard-stem', q.stem));

  const ans = el('div', 'rcard-ans');
  ans.appendChild(document.createTextNode('你的答案：'));
  ans.appendChild(el('span', chosen === q.answer ? 'right' : 'wrong', chosen || '未作答'));
  ans.appendChild(document.createTextNode('　正确答案：'));
  ans.appendChild(el('span', 'right', q.answer));
  card.appendChild(ans);

  if (withImage && q.img) {
    const img = el('img', 'q-img');
    img.src = '../data/' + q.img;
    img.alt = '原题配图';
    img.loading = 'lazy';
    img.addEventListener('click', () => zoomImage(img.src));
    card.appendChild(img);
  }

  if (!ok) {
    card.appendChild(buildExplain(q, chosen));
  }
  return card;
}

/* ---------- 错题本 ---------- */
let WRONG_FILTER = '全部';

function renderWrongbook() {
  const chips = $('#wrongFilter');
  chips.innerHTML = '';
  ['全部', '生物', '历史', '政治', '地理'].forEach(s => {
    const c = el('button', 'chip' + (WRONG_FILTER === s ? ' active' : ''), s);
    c.addEventListener('click', () => { WRONG_FILTER = s; renderWrongbook(); });
    chips.appendChild(c);
  });

  let items = Object.entries(DB.records)
    .filter(([, r]) => r.wrongCount > 0)
    .map(([id, r]) => ({ q: BANK_BY_ID[id], r }))
    .filter(x => x.q);

  if (WRONG_FILTER !== '全部') {
    items = items.filter(x => x.q.subject === WRONG_FILTER);
  }
  // 错得越多、越久没碰的排前面
  items.sort((a, b) => (b.r.wrongCount - a.r.wrongCount)
    || (a.r.lastSeen || '').localeCompare(b.r.lastSeen || ''));

  const box = $('#wrongList');
  box.innerHTML = '';
  if (!items.length) {
    box.appendChild(el('div', 'empty', '错题本是空的，继续保持 👍'));
  }
  items.forEach(({ q, r }) => {
    const card = el('div', 'rcard bad');
    const head = el('div', 'rcard-head');
    head.appendChild(el('span', 'rcard-id', `${q.subject} · 第${q.no}题`));
    head.appendChild(el('span', 'rcard-badge bad', `错 ${r.wrongCount} 次`));
    card.appendChild(head);
    card.appendChild(el('div', 'rcard-stem', q.stem));
    const ans = el('div', 'rcard-ans');
    ans.appendChild(document.createTextNode('正确答案：'));
    ans.appendChild(el('span', 'right', q.answer));
    ans.appendChild(document.createTextNode(
      `　下次复习：${r.nextReview || '—'}`));
    card.appendChild(ans);
    card.appendChild(buildExplain(q, r.lastAnswer));
    box.appendChild(card);
  });

  const acts = $('#wrongActions');
  acts.innerHTML = '';
  if (items.length) {
    const b = el('button', 'btn btn-primary btn-lg',
      `重练这 ${items.length} 道错题`);
    b.addEventListener('click', () => {
      startQuiz(items.map(x => x.q));
    });
    acts.appendChild(b);
  }
}

/* ---------- 设置 ---------- */
function renderSettings() {
  const s = DB.settings;
  $('#setNew').value = s.newPerDay;
  $('#setNewVal').textContent = s.newPerDay;
  $('#setReview').value = s.reviewPerDay;
  $('#setReviewVal').textContent = s.reviewPerDay;
  $('#setInstant').checked = !!s.instantExplain;

  const subjects = {};
  BANK.forEach(q => { subjects[q.subject] = (subjects[q.subject] || 0) + 1; });
  const info = Object.entries(subjects)
    .map(([k, v]) => `${k} ${v} 题`).join(' · ');
  $('#bankInfo').textContent = `题库共 ${BANK.length} 题：${info}。` +
    '题目与解析均取自原 PDF，未做任何编造。';
}

/* ---------- 绑定事件 ---------- */
function bindEvents() {
  $('#btnStart').addEventListener('click', () => startQuiz(buildTodayTask()));

  $('#btnWrongQuick').addEventListener('click', () => {
    const today = todayStr();
    const ids = Object.entries(DB.records)
      .filter(([, r]) => r.wrongCount > 0 && (r.nextReview || today) <= today)
      .map(([id]) => id);
    if (!ids.length) { toast('今天没有到期的错题'); return; }
    startQuiz(ids.map(id => BANK_BY_ID[id]).filter(Boolean));
  });

  $('#btnQuit').addEventListener('click', () => {
    if (confirm('退出后本次未提交的作答不会保存，确定退出？')) {
      renderHome();
      showScreen('screen-home');
    }
  });

  $('#btnPrev').addEventListener('click', () => {
    if (QUIZ.idx > 0) { QUIZ.idx--; renderQuestion(); }
  });

  $('#btnNext').addEventListener('click', () => {
    const q = QUIZ.list[QUIZ.idx];
    if (!QUIZ.revealed[q.id]) {
      submitCurrent();
    } else {
      nextQuestion();
    }
  });

  $('#btnResultClose').addEventListener('click', () => {
    renderHome(); showScreen('screen-home');
  });
  $('#btnBackHome').addEventListener('click', () => {
    renderHome(); showScreen('screen-home');
  });
  $('#btnRedoWrong').addEventListener('click', () => {
    const wrong = QUIZ.list.filter(q => !judge(q, QUIZ.answers[q.id]));
    if (wrong.length) startQuiz(wrong);
  });

  $('#btnWrongbook').addEventListener('click', () => {
    renderWrongbook(); showScreen('screen-wrong');
  });
  $('#btnWrongBack').addEventListener('click', () => {
    renderHome(); showScreen('screen-home');
  });

  $('#btnSettings').addEventListener('click', () => {
    renderSettings(); showScreen('screen-settings');
  });
  $('#btnSettingsBack').addEventListener('click', () => {
    renderHome(); showScreen('screen-home');
  });

  $('#setNew').addEventListener('input', e => {
    DB.settings.newPerDay = +e.target.value;
    $('#setNewVal').textContent = e.target.value;
    saveDB(); renderHome();
  });
  $('#setReview').addEventListener('input', e => {
    DB.settings.reviewPerDay = +e.target.value;
    $('#setReviewVal').textContent = e.target.value;
    saveDB(); renderHome();
  });
  $('#setInstant').addEventListener('change', e => {
    DB.settings.instantExplain = e.target.checked;
    saveDB();
  });

  $('#btnExport').addEventListener('click', () => {
    const blob = new Blob([JSON.stringify(DB, null, 1)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `xuekao-records-${todayStr()}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  });
  $('#btnImport').addEventListener('click', () => $('#fileImport').click());
  $('#fileImport').addEventListener('change', e => {
    const f = e.target.files[0];
    if (!f) return;
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const data = JSON.parse(reader.result);
        if (!data.records) throw new Error('格式不对');
        DB = Object.assign({ records: {}, checkins: {}, settings: { ...DEFAULTS } }, data);
        DB.records = DB.records || {};
        DB.checkins = DB.checkins || {};
        DB.settings = Object.assign({}, DEFAULTS, DB.settings || {});
        saveDB(); renderHome(); renderSettings();
        toast('导入成功');
      } catch (err) {
        toast('导入失败：文件格式不正确');
      }
    };
    reader.readAsText(f);
    e.target.value = '';
  });
  $('#btnReset').addEventListener('click', () => {
    if (!confirm('确定清空全部做题记录和打卡记录？此操作不可恢复。')) return;
    DB = { records: {}, checkins: {}, settings: { ...DB.settings } };
    saveDB(); renderHome(); renderSettings();
    toast('已清空');
  });
}

/* ---------- 启动 ---------- */
(async function init() {
  bindEvents();
  try {
    await loadBank();
  } catch (e) {
    console.error(e);
    $('#todaySub').textContent = '题库加载失败：请确认 data/questions.json 存在';
    toast('题库加载失败');
  }
  renderHome();
  showScreen('screen-home');
})();
