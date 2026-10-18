(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);
  const state = {
    jobs: [], selected: null, filter: 'all', loaded: false, refreshing: false,
    refreshAgain: false, timer: null, sequence: 0, cards: new Map(), games: new Map(),
    replayOverrides: new Map(), replayRequests: new Set(),
  };
  const labels = { queued: '等待中', running: '运行中', completed: '已完成', failed: '执行失败' };
  const partnerNames = { original: '原版', mixed: '混合', weak: '弱搭档' };
  const phaseNames = { PREP: '准备', PREPARATION: '准备', PLANNING: '部署', SHOP: '采购',
    COMBAT: '作战', BATTLE: '作战', RESULT: '结算', REWARD: '奖励',
    FINAL_ASSAULT: '最终突袭', HIDDEN_CORE: '隐藏核心', UNITE: '合战' };
  const number = (value) => Number.isFinite(Number(value)) ? Math.max(0, Number(value)) : 0;
  const display = (value) => value === null || value === undefined || value === '' ? '—' : String(value);
  const count = (value) => number(value).toLocaleString('zh-CN');
  const percent = (wins, total) => total > 0 ? `${Math.round(number(wins) / total * 100)}%` : '—';
  const filename = (path) => String(path || '').split(/[\\/]/).filter(Boolean).pop() || '未命名策略';
  const shortPath = (path) => {
    const parts = String(path || '').split(/[\\/]/).filter(Boolean);
    return parts.length > 3 ? `…/${parts.slice(-3).join('/')}` : display(path);
  };
  const date = (value) => {
    if (!value) return '—';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? display(value) : parsed.toLocaleString('zh-CN', {
      month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
    });
  };
  const setText = (node, value) => {
    const text = String(value);
    if (node.textContent !== text) node.textContent = text;
  };
  const element = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) setText(node, text);
    return node;
  };
  const notice = (node, message) => { setText(node, message || ''); node.hidden = !message; };
  const status = (value) => {
    const key = String(value || '').toLowerCase();
    if (['queued', 'pending', 'waiting'].includes(key)) return 'queued';
    if (['running', 'starting'].includes(key)) return 'running';
    if (['completed', 'complete', 'finished', 'done', 'success', 'succeeded'].includes(key)) return 'completed';
    if (['failed', 'error', 'interrupted', 'partial', 'cancelled', 'canceled', 'stopped', 'timeout'].includes(key)) return 'failed';
    return 'unknown';
  };
  const statusLabel = (value) => ({ interrupted: '已中断', partial: '部分完成', cancelled: '已取消', canceled: '已取消', stopped: '已停止', timeout: '已超时' })[String(value).toLowerCase()] || labels[status(value)] || display(value);
  const badge = (node, value) => { node.className = `badge ${status(value)}`; setText(node, statusLabel(value)); };
  const progress = (bar, data) => {
    const planned = number(data?.planned), complete = number(data?.complete);
    const max = planned || 1, value = Math.min(complete, max);
    if (bar.max !== max) bar.max = max;
    if (bar.value !== value) bar.value = value;
  };
  const errorMessage = (error) => error instanceof Error ? error.message : String(error);
  const apiPath = (id) => `/api/evaluations/${encodeURIComponent(id)}`;
  const replayKey = (jobId, gameId) => JSON.stringify([String(jobId), String(gameId)]);

  async function request(url, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(url, { ...options, signal: controller.signal, cache: 'no-store',
        headers: { Accept: 'application/json', ...options.headers } });
      let body;
      try { body = await response.json(); }
      catch { throw new Error(`服务端返回了非 JSON 内容（HTTP ${response.status}）。`); }
      if (!response.ok) {
        const reason = body?.error || body?.detail || body?.message;
        throw new Error(`HTTP ${response.status}：${typeof reason === 'string' ? reason : reason ? JSON.stringify(reason) : response.statusText || '请求失败'}`);
      }
      return body;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('请求超时，将自动重试。');
      if (error instanceof TypeError) throw new Error('无法连接评测服务，请检查网络或服务是否启动。');
      throw error;
    } finally { clearTimeout(timer); }
  }

  function renderMetrics(queue) {
    const jobs = state.jobs;
    const sum = (key) => jobs.reduce((total, job) => total + number(job.progress?.[key]), 0);
    const planned = sum('planned'), wins = sum('teamWins');
    const failedJobs = jobs.filter((job) => status(job.status) === 'failed').length;
    setText($('metric-queued'), count(jobs.filter((job) => status(job.status) === 'queued').length));
    setText($('metric-running'), count(jobs.filter((job) => status(job.status) === 'running').length));
    $('metric-queued').title = `持久队列等待 ${count(queue?.waiting)}`;
    $('metric-running').title = `持久队列运行 ${count(queue?.running)}；总数包含自动发现的评测`;
    setText($('metric-completed'), count(jobs.filter((job) => status(job.status) === 'completed').length));
    setText($('metric-wins'), percent(wins, planned));
    setText($('metric-win-note'), `${count(wins)} 胜 / ${count(planned)} 计划对局`);
    setText($('metric-failed'), count(sum('failed')));
    setText($('metric-fail-note'), failedJobs ? `${count(failedJobs)} 项评测异常 · 不含战败` : '执行错误，不含战败');
  }

  function createCard(id) {
    const button = element('button', 'job-card');
    button.type = 'button';
    const top = element('span', 'job-top'), title = element('span', 'job-title truncate');
    const badgeNode = element('span', 'badge'), script = element('span', 'job-script mono truncate');
    const bar = element('progress', 'job-progress');
    bar.setAttribute('aria-label', '对局完成进度');
    const bottom = element('span', 'job-bottom'), counts = element('span'), time = element('span');
    const footer = element('span', 'job-footer');
    top.append(title, badgeNode); bottom.append(counts, time); button.append(top, script, bar, bottom, footer);
    button.addEventListener('click', () => selectJob(id));
    return { button, title, badgeNode, script, bar, counts, time, footer };
  }

  function renderJobs() {
    const ids = new Set(state.jobs.map((job) => String(job.id)));
    for (const [id, card] of state.cards) {
      if (!ids.has(id)) { card.button.remove(); state.cards.delete(id); }
    }
    let visible = 0;
    for (const job of state.jobs) {
      const id = String(job.id);
      let card = state.cards.get(id);
      if (!card) {
        card = createCard(id); state.cards.set(id, card);
        const next = state.jobs.slice(state.jobs.indexOf(job) + 1).find((item) => state.cards.has(String(item.id)));
        $('job-list').insertBefore(card.button, next ? state.cards.get(String(next.id)).button : null);
      }
      card.button.hidden = state.filter !== 'all' && status(job.status) !== state.filter;
      if (!card.button.hidden) visible++;
      card.button.setAttribute('aria-pressed', String(state.selected === id));
      setText(card.title, job.label || filename(job.script));
      card.title.title = job.label || filename(job.script);
      badge(card.badgeNode, job.status);
      setText(card.script, display(job.script)); card.script.title = display(job.script);
      progress(card.bar, job.progress);
      setText(card.counts, `${count(job.progress?.complete)} / ${count(job.progress?.planned)} 局 · 团队 ${count(job.progress?.teamWins)} 胜`);
      setText(card.time, date(job.updatedAt || job.createdAt));
      setText(card.footer, `${job.source === 'discovered' ? '自动发现' : '持久队列 · FIFO'} · ${id}`);
    }
    setText($('job-count'), state.filter === 'all' ? count(state.jobs.length) : `${visible} / ${state.jobs.length}`);
    $('queue-empty').hidden = visible > 0;
    if (state.loaded) {
      setText($('queue-empty-title'), state.jobs.length ? '没有符合条件的评测' : '队列还很安静');
      setText($('queue-empty-note'), state.jobs.length ? '切换状态筛选，查看其他评测。当前详情会保留。' : '点击「新建评测」提交策略；已有结果也会显示在这里。');
    }
  }

  function selectJob(id) {
    id = String(id);
    if (state.selected === id) return;
    state.selected = id;
    const url = new URL(location.href);
    url.searchParams.set('job', id);
    history.replaceState(null, '', url);
    state.games.clear(); $('games').replaceChildren();
    $('detail-empty').hidden = true; $('detail-content').hidden = false;
    notice($('detail-error'), ''); notice($('detail-notice'), '正在加载逐局数据…');
    const job = state.jobs.find((item) => String(item.id) === id);
    renderDetail(job || { id }, false);
    renderJobs();
    scheduleRefresh();
  }

  function renderDetail(job, hasGames = true) {
    setText($('detail-title'), job.label || filename(job.script));
    badge($('detail-status'), job.status);
    setText($('detail-script'), filename(job.script)); $('detail-script').title = display(job.script);
    setText($('detail-root'), shortPath(job.root)); $('detail-root').title = display(job.root);
    setText($('detail-run'), display(job.runId));
    setText($('detail-updated'), date(job.updatedAt || job.createdAt));
    notice($('job-error'), job.error ? display(job.error) : '');
    const p = job.progress || {}, complete = number(p.complete), planned = number(p.planned);
    progress($('detail-progress'), p);
    setText($('detail-progress-text'), `${count(complete)} / ${count(p.planned)} 局 · ${percent(complete, number(p.planned))}`);
    setText($('detail-progress-counts'), `运行 ${count(p.running)} · 等待 ${count(p.pending)} · 执行失败 ${count(p.failed)}`);
    setText($('detail-team'), `${count(p.teamWins)} / ${count(planned)} · ${percent(p.teamWins, planned)}`);
    setText($('detail-learner'), `${count(p.learnerWins)} / ${count(planned)} · ${percent(p.learnerWins, planned)}`);
    setText($('detail-failed'), count(p.failed));
    if (hasGames) {
      notice($('detail-notice'), '');
      renderGames(Array.isArray(job.games) ? job.games : [], String(job.id));
    } else {
      setText($('game-count'), '—'); $('games-empty').hidden = false;
      setText($('games-empty'), '正在加载逐局数据…');
    }
  }

  function createGame(jobId, gameId) {
    const container = element('article', 'game');
    const summary = element('button', 'game-summary'); summary.type = 'button';
    summary.setAttribute('aria-expanded', 'false');
    const top = element('span', 'game-top'), name = element('span', 'game-name');
    const badgeNode = element('span', 'badge'), chevron = element('span', 'game-chevron', '›');
    chevron.setAttribute('aria-hidden', 'true'); top.append(name, badgeNode, chevron);
    const stats = element('span', 'game-stats'), outcome = element('span', 'game-outcome');
    const bondsPreview = element('span', 'bond-preview'); summary.append(top, stats, outcome, bondsPreview);
    const expanded = element('div', 'game-expanded'); expanded.hidden = true;
    expanded.id = `game-observation-${++state.sequence}`; summary.setAttribute('aria-controls', expanded.id);
    const bondHeading = element('h4', '', '羁绊观测'), bonds = element('div', 'bond-list');
    bondHeading.style.margin = '0';
    const error = element('p', 'notice error error-text'); error.hidden = true;
    const gameMeta = element('p', 'game-id mono'); expanded.append(bondHeading, bonds, error, gameMeta);
    const footer = element('div', 'game-footer'), replayNote = element('span', 'replay-note');
    const replayButton = element('button', 'button secondary', '回放准备中'); replayButton.type = 'button';
    footer.append(replayNote, replayButton); container.append(summary, expanded, footer);
    const record = { container, summary, name, badgeNode, stats, outcome, bondsPreview, expanded, bonds,
      error, gameMeta, replayNote, replayButton, data: null, bondSignature: null };
    summary.addEventListener('click', () => {
      expanded.hidden = !expanded.hidden;
      summary.setAttribute('aria-expanded', String(!expanded.hidden));
    });
    replayButton.addEventListener('click', () => launchReplay(jobId, gameId, record));
    return record;
  }

  function activeBond(bond) { return bond.active === true || bond.active === 1; }
  function bondText(bond) { return `${display(bond.bondId)} · ${display(bond.layers)} 层`; }
  function outcomeText(value) { return value === true ? '胜利' : value === false ? '未胜' : '待定'; }
  function replayState(jobId, game) {
    const remote = game.replay || { status: 'absent' };
    const local = state.replayOverrides.get(replayKey(jobId, game.id));
    return local || remote;
  }

  function renderGames(games, jobId) {
    const ids = new Set(games.map((game) => String(game.id)));
    for (const [id, record] of state.games) {
      if (!ids.has(id)) { record.container.remove(); state.games.delete(id); }
    }
    for (const game of games) {
      const id = String(game.id);
      let record = state.games.get(id);
      if (!record) { record = createGame(jobId, id); state.games.set(id, record); $('games').append(record.container); }
      record.data = game;
      setText(record.name, `种子 ${display(game.seed)} · ${partnerNames[game.partner] || display(game.partner)}`);
      badge(record.badgeNode, game.status);
      setText(record.stats, `回合 ${display(game.round)}　 LP ${display(game.lp)}　 资金 ${display(game.funds)}　 ${phaseNames[game.phase] || display(game.phase)}　 ${game.alive === true ? '存活' : game.alive === false ? '已退场' : '存活状态 —'}`);
      setText(record.outcome, `团队：${outcomeText(game.teamVictory)}　学习者：${outcomeText(game.learnerIndividualVictory)}`);
      const bonds = Array.isArray(game.bonds) ? game.bonds : [];
      const active = bonds.filter(activeBond);
      setText(record.bondsPreview, active.length ? `已激活：${active.map(bondText).join(' / ')}` : bonds.some(b => number(b.layers) > 0) ? `已累积（当前未激活）：${bonds.filter(b => number(b.layers) > 0).slice(0, 4).map(bondText).join(' / ')}` : '暂无激活羁绊记录');
      const signature = JSON.stringify(bonds);
      if (record.bondSignature !== signature) {
        record.bondSignature = signature;
        record.bonds.replaceChildren(...(bonds.length ? bonds.map((bond) =>
          element('span', `bond-chip${activeBond(bond) ? '' : ' inactive'}`, `${bondText(bond)} · ${activeBond(bond) ? '已激活' : '未激活'}`))
          : [element('span', 'muted', '尚无已保存的羁绊观测。')]));
      }
      notice(record.error, game.error ? display(game.error) : '');
      setText(record.gameMeta, `对局 ${id} · ${status(game.status) === 'running' ? '已保存的最新观测' : '已保存观测'}`);
      updateReplay(record, jobId);
    }
    setText($('game-count'), count(games.length));
    $('games-empty').hidden = games.length > 0;
    setText($('games-empty'), '暂无逐局记录。任务启动并保存观测后，会自动显示在这里。');
  }

  function safeReplayUrl(value) {
    if (typeof value !== 'string' || !value) throw new Error('回放已就绪，但服务端尚未提供有效链接。');
    const url = new URL(value, location.origin);
    if (url.origin !== location.origin || url.pathname !== '/' || !url.searchParams.get('replay') || url.username || url.password) {
      throw new Error('服务端返回的原生回放链接无效，需为同源 /?replay=… 链接。');
    }
    return url.href;
  }

  function updateReplay(record, jobId) {
    const game = record.data, replay = replayState(jobId, game);
    const key = replayKey(jobId, game.id), posting = state.replayRequests.has(key);
    const completed = status(game.status) === 'completed';
    let text = completed ? '回放准备中' : '等待对局完成', note = '对局完成后自动准备回放', disabled = true;
    if (completed) note = '自动准备 · 状态每 3 秒同步';
    if (posting || ['pending', 'queued', 'running'].includes(replay.status)) {
      text = '回放准备中';
      note = '自动准备 · 状态每 3 秒同步';
    } else if (replay.status === 'ready') {
      text = '播放回放'; disabled = false;
      try { safeReplayUrl(replay.url); note = '已就绪 · 点击在新标签页打开'; }
      catch (error) { note = errorMessage(error); disabled = true; }
    } else if (replay.status === 'failed') {
      text = '重试回放'; note = `回放准备失败：${display(replay.error)}`; disabled = !completed;
    }
    setText(record.replayNote, note); setText(record.replayButton, text);
    record.replayButton.disabled = disabled;
    record.replayButton.setAttribute('aria-label', `${text}，种子 ${display(game.seed)}，对局 ${game.id}`);
  }

  async function launchReplay(jobId, gameId, record) {
    const key = replayKey(jobId, gameId);
    if (state.replayRequests.has(key)) return;
    const replay = replayState(jobId, record.data);
    if (replay.status === 'ready') {
      try {
        const url = safeReplayUrl(replay.url), viewer = window.open('about:blank', '_blank');
        if (viewer) { viewer.opener = null; viewer.location.replace(url); }
        else location.assign(url);
      } catch (error) { setText(record.replayNote, errorMessage(error)); }
      return;
    }
    if (replay.status !== 'failed' || status(record.data.status) !== 'completed') return;
    state.replayRequests.add(key); updateReplay(record, jobId);
    try {
      const result = await request(`${apiPath(jobId)}/replay`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ episode: record.data.id }),
      });
      const next = result?.replay || result;
      if (!next || !['absent', 'pending', 'queued', 'running', 'ready', 'failed'].includes(next.status)) throw new Error('服务端返回了无效的回放状态。');
      state.replayOverrides.set(key, next);
      setText($('announcer'), next.status === 'failed' ? `回放准备失败：${display(next.error)}` : next.status === 'ready' ? '回放已就绪，请点击播放回放。' : '回放重试已提交，准备就绪后可播放。');
      scheduleRefresh();
    } catch (error) {
      const failed = { status: 'failed', error: errorMessage(error) };
      state.replayOverrides.set(key, failed);
      setText($('announcer'), `回放重试失败：${failed.error}`);
    } finally {
      state.replayRequests.delete(key);
      if (state.selected === jobId && state.games.get(gameId) === record) updateReplay(record, jobId);
    }
  }

  function processReplays(job) {
    for (const game of Array.isArray(job.games) ? job.games : []) {
      const key = replayKey(job.id, game.id);
      if (state.replayRequests.has(key)) continue;
      if (game.replay?.status && game.replay.status !== 'absent') state.replayOverrides.delete(key);
    }
  }

  function scheduleRefresh(delay = 0) {
    clearTimeout(state.timer);
    if (state.refreshing) { state.refreshAgain = true; return; }
    state.timer = setTimeout(refresh, delay);
  }

  // All polling GETs share this one serial refresh lane.
  async function refresh() {
    if (state.refreshing) { state.refreshAgain = true; return; }
    state.refreshing = true; $('refresh').disabled = true;
    try {
      try {
        const response = await request('/api/evaluations');
        if (!Array.isArray(response?.jobs)) throw new Error('服务端返回的评测列表格式无效。');
        state.jobs = response.jobs.filter((job) => job && job.id !== undefined && job.id !== null);
        state.loaded = true;
        renderMetrics(response.queue); renderJobs();
        notice($('network-error'), '');
        $('connection').className = 'connection online'; setText($('connection'), '已连接 · 每 3 秒同步');
        setText($('sync-note'), `最近同步 ${new Date().toLocaleTimeString('zh-CN', { hour12: false })}`);
        if (state.selected === null && state.jobs.length) selectJob(String(state.jobs[0].id));
      } catch (error) {
        notice($('network-error'), `${errorMessage(error)} 每 3 秒重试；已加载的数据保留。`);
        $('connection').className = 'connection offline'; setText($('connection'), '连接异常 · 自动重试');
        if (!state.loaded) {
          setText($('queue-empty-title'), '暂时无法读取队列');
          setText($('queue-empty-note'), '请检查评测服务；恢复连接后会自动加载。');
        }
      }
      const detailIds = new Set(state.selected === null ? [] : [state.selected]);
      for (const id of detailIds) {
        try {
          const job = await request(apiPath(id));
          if (!job || typeof job !== 'object' || String(job.id) !== id || !Array.isArray(job.games)) throw new Error('服务端返回的评测详情格式无效。');
          processReplays(job);
          if (state.selected === id) { notice($('detail-error'), ''); renderDetail(job); }
        } catch (error) {
          if (state.selected === id) {
            notice($('detail-notice'), '');
            notice($('detail-error'), `详情同步失败：${errorMessage(error)} 自动重试中；已有观测可能不是最新数据。`);
            if (!state.games.size) setText($('games-empty'), '暂时无法读取对局，正在等待服务恢复。');
          }
        }
      }
    } finally {
      state.refreshing = false; $('refresh').disabled = false;
      const delay = state.refreshAgain ? 0 : 3000;
      state.refreshAgain = false; scheduleRefresh(delay);
    }
  }

  function toggleSubmit(open) {
    $('submit-panel').hidden = !open;
    $('new-job').setAttribute('aria-expanded', String(open));
    if (open) $('script').focus(); else $('new-job').focus();
  }

  $('new-job').addEventListener('click', () => toggleSubmit($('submit-panel').hidden));
  $('close-submit').addEventListener('click', () => toggleSubmit(false));
  $('refresh').addEventListener('click', () => scheduleRefresh());
  $('status-filter').addEventListener('change', (event) => { state.filter = event.target.value; renderJobs(); });
  window.addEventListener('online', () => scheduleRefresh());

  $('submit-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if ($('submit-job').disabled) return;
    const data = new FormData(event.currentTarget);
    const payload = { script: String(data.get('script') || '').trim(), label: String(data.get('label') || '').trim(),
      seeds: String(data.get('seeds') || '').trim(), partners: data.getAll('partners'), mode: data.get('mode'),
      difficulty: 'ABYSS', workers: 2, timeout: 120 };
    const feedback = $('submit-feedback');
    feedback.className = 'form-feedback error';
    if (!payload.script || !payload.seeds || (payload.mode === 'coop' && !payload.partners.length)) {
      notice(feedback, '请填写策略路径和种子范围；合作模式至少选择一种搭档。'); return;
    }
    $('submit-job').disabled = true; setText($('submit-job'), '正在提交…');
    notice(feedback, '');
    try {
      const result = await request('/api/evaluations', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      feedback.className = 'form-feedback';
      notice(feedback, '评测已加入队列，结果会自动更新。');
      const job = result?.job || result;
      if (job?.id !== undefined && job?.id !== null) {
        state.filter = 'all'; $('status-filter').value = 'all';
        if (!state.jobs.some((item) => String(item.id) === String(job.id))) state.jobs.unshift({ ...payload, ...job });
        selectJob(String(job.id));
      }
      scheduleRefresh();
    } catch (error) { notice(feedback, `提交失败：${errorMessage(error)}`); }
    finally { $('submit-job').disabled = false; setText($('submit-job'), '加入评测队列 →'); }
  });

  const initialJob = new URLSearchParams(location.search).get('job');
  if (initialJob) selectJob(initialJob);
  else scheduleRefresh();
})();
