// Read-only playback of recorded bot observations using the original GameScreen and field renderer.
import { render } from '../vendor/preact.module.js';
import { html, UiHosts } from './ui/components.js';
import { GameScreen } from './screens/game.js';
import { ToastHost } from './ui/toasts.js';
import { GuideHost } from './ui/guide.js';
import { data } from './data.js';
import { GAME_FILES } from './ui/gameComponents.js';
import { store, emptyMatch } from './store.js';
import { net, NetError } from './net.js';
import { initLang } from './ui/lang.js';
import { settingsStore } from './ui/settings.js';
import { installDeviceSupport } from './ui/device.js';
import { t } from '../../shared/i18n.js';

const state = { manifest: null, episode: null, steps: [], index: 0, mode: 'actions', playing: false,
  speed: 1, frameIndex: 0, recording: null, elapsed: 0, lastAt: 0, loading: false, request: 0, error: '', selectedId: null };
let controls;
const $ = (name) => controls.querySelector(`[data-control="${name}"]`);
const clone = (x) => JSON.parse(JSON.stringify(x));
const json = async (url) => { const r = await fetch(url); if (!r.ok) throw new Error(`${url}: ${r.status}`); return r.json(); };
const label = (s) => ({ 'g.buy': '购买', 'g.sell': '出售', 'g.refresh': '刷新', 'g.move': '部署', 'g.equip': '配发装备',
  'g.levelUp': '升级', 'g.ready': '准备战斗', 'g.reward': '选择奖励', 'g.choice': '机变选择', 'g.band': '策略',
  'g.freeze': '冻结', 'g.art': '使用法术', 'g.destroy': '销毁' }[s.action.t] || s.action.t);

function showError(error) {
  state.playing = false; state.loading = false;
  state.error = `${t('回放载入失败')}：${String(error.message || error)}。${t('可重试或选择其他回放')}`;
  updateControls();
}
function updateControls() {
  $('play').textContent = t(state.playing ? '暂停' : '播放');
  const battle = state.mode === 'battle';
  $('scrub').max = String(Math.max(0, battle ? state.recording.frames.length - 1 : state.steps.length - 1));
  $('scrub').value = String(battle ? state.frameIndex : state.index);
  const s = state.steps[state.index];
  if (state.error) $('status').textContent = state.error;
  else if (state.loading) $('status').textContent = t('正在载入…');
  else if (s) {
    $('round').value = String(s.before.round);
    $('status').textContent = state.loading ? t('正在载入…') : battle
      ? `${t('作战')} R${state.recording.round} · ${state.elapsed.toFixed(1)} / ${state.recording.duration.toFixed(1)}s`
      : `R${s.before.round} · ${t(label(s))} · ${state.index + 1}/${state.steps.length}${s.accepted ? '' : ` · ${t('已拒绝')}`}`;
  }
  const unavailable = state.loading || !!state.error || !s;
  for (const name of ['play', 'prev', 'next', 'player', 'round', 'scrub', 'speed']) $(name).disabled = unavailable;
  $('retry').hidden = !state.error;
  $('battle').disabled = unavailable || !state.episode?.meta.battles.some(b => b.round === s?.before.round);
}

function publicOf(obs) {
  const pub = clone(obs.public || { ...obs, players: obs.teammates, modeId: obs.own.modeId,
    fields: [], bossRound: 14, hiddenRound: 15, lastRound: 14 });
  pub.combatMode = 'server'; pub.serverNow = Date.now(); pub.deadline = null; pub.paused = false;
  // A bot's autoplay flag is not a viewer's ownership or a request to show the away overlay.
  for (const p of pub.players || []) { p.autoplay = false; p.name = `AI ${p.seat + 1}`; }
  return pub;
}

function showStep(index, { pause = true } = {}) {
  if (pause) state.playing = false;
  state.index = Math.max(0, Math.min(state.steps.length - 1, index));
  state.mode = 'actions'; state.recording = null; state.elapsed = 0;
  const step = state.steps[state.index];
  if (!step) return;
  let obs = step.observation;
  // The last decision is labelled with the real terminal observation. Show its pre-combat formation first.
  if (obs.phase === 'RESULT' && step.action.t === 'g.ready' && state.index) obs = state.steps[state.index - 1].observation;
  const pub = publicOf(obs), own = clone(obs.own);
  own.ready = false;
  store.set({
    me: { playerId: step.playerId, name: `AI ${Number(step.playerId.split('_')[1]) + 1}`, token: null },
    match: { ...emptyMatch(), public: pub, private: own }, ticker: [], emotes: [],
  });
  updateControls();
}

async function loadEpisode(id) {
  const req = ++state.request;
  state.selectedId = id;
  state.playing = false; state.loading = true; state.error = ''; updateControls();
  try {
    const meta = state.manifest.episodes.find(e => e.id === id);
    if (!meta) throw new Error(t('索引中没有这条回放，请重新选择'));
    const episode = await json(`/dev/agent/${meta.file}`);
    if (req !== state.request) return;
    state.episode = episode; state.loading = false;
    $('player').replaceChildren(...Array.from({ length: meta.players }, (_, seat) => new Option(`AI ${seat + 1}`, `ai_${seat}`)));
    $('episode').value = id;
    selectPlayer();
    const u = new URL(location.href); u.searchParams.set('replay', id); history.replaceState(null, '', u);
  } catch (error) { if (req === state.request) throw error; }
}

function selectPlayer() {
  state.playing = false;
  state.steps = state.episode.steps.filter(s => s.playerId === $('player').value);
  const rounds = [...new Set(state.steps.map(s => s.before.round))];
  $('round').replaceChildren(...rounds.map(r => new Option(`R${r}`, String(r))));
  showStep(Math.max(0, state.steps.findIndex(s => s.before.round >= 1)));
}

function pushFrame(frame) {
  const fieldId = state.recording.field.fieldId;
  if (frame.ev.length) net._emit('b.ev', { t: 'b.ev', fieldId, gt: frame.snap.t, ev: frame.ev });
  net._emit('b.snap', { ...frame.snap, t: 'b.snap', gt: frame.snap.t, fieldId });
}

function enterRecording(rec) {
  const obs = state.steps[state.index].observation;
  const pub = publicOf(obs);
  pub.phase = rec.kind === 'boss' ? 'FINAL_ASSAULT' : rec.kind === 'hidden' ? 'HIDDEN_CORE' : rec.kind === 'unite' ? 'UNITE' : 'COMBAT';
  pub.round = rec.round;
  pub.fields = [{ fieldId: rec.field.fieldId, kind: rec.kind, players: [store.get().me.playerId], live: true }];
  for (const p of pub.players || []) if (p.playerId === store.get().me.playerId) p.fieldId = rec.field.fieldId;
  store.patch('match', { public: pub, field: clone(rec.field), result: null, battle: null });
  net._emit('m.field', { t: 'm.field', ...clone(rec.field) });
}

async function showBattle({ auto = false } = {}) {
  const round = state.steps[state.index]?.before.round;
  const meta = state.episode.meta.battles.find(b => b.round === round);
  if (!meta) return false;
  const req = ++state.request;
  const resume = auto || state.playing;
  state.playing = false; state.loading = true; updateControls();
  const rec = await json(`/dev/recordings/${meta.file}`);
  if (req !== state.request) return false;
  state.recording = rec; state.mode = 'battle'; state.elapsed = 0; state.frameIndex = 0; state.loading = false;
  enterRecording(rec); state.playing = resume;
  // Let GameScreen mount and subscribe; it buffers frames while the renderer is loading.
  state.lastAt = performance.now(); updateControls();
  return true;
}

function seekBattle(index) {
  state.playing = false;
  const rec = state.recording;
  state.frameIndex = Math.max(0, Math.min(rec.frames.length - 1, index));
  enterRecording(rec);
  const spawns = new Map(rec.field.units.map(u => [u.id, u]));
  for (let i = 0; i <= state.frameIndex; i++) for (const ev of rec.frames[i].ev) if (ev[0] === 'spawn') spawns.set(ev[1].id, ev[1]);
  const target = rec.frames[state.frameIndex];
  const ids = new Set(target.snap.units.map(u => u[0]));
  const ev = [...spawns.values()].filter(u => ids.has(u.id)).map(u => ['spawn', u]);
  requestAnimationFrame(() => { pushFrame({ snap: target.snap, ev }); });
  state.elapsed = target.snap.t;
  updateControls();
}

function nextStep(delta) {
  if (state.mode === 'battle') { const index = state.index; showStep(index); }
  showStep(state.index + delta);
}

async function tick(at) {
  const dt = Math.min(0.15, (at - (state.lastAt || at)) / 1000); state.lastAt = at;
  if (state.playing && !state.loading) {
    state.elapsed += dt * state.speed * (state.mode === 'battle' ? 2 : 1);
    if (state.mode === 'battle') {
      const frames = state.recording.frames;
      while (state.frameIndex < frames.length && frames[state.frameIndex].snap.t <= state.elapsed) pushFrame(frames[state.frameIndex++]);
      if (state.frameIndex >= frames.length) {
        if (state.index >= state.steps.length - 1) {
          state.playing = false;
          store.patch('match', { result: clone(state.episode.meta.summary) });
        } else showStep(state.index + 1, { pause: false });
      }
      updateControls();
    } else if (state.elapsed >= 0.65) {
      state.elapsed = 0;
      const step = state.steps[state.index];
      if (step.action.t === 'g.ready' && step.accepted && step.action.ready && state.episode.meta.battles.some(b => b.round === step.before.round)) {
        try { await showBattle({ auto: true }); } catch (e) { showError(e); }
      } else if (state.index < state.steps.length - 1) showStep(state.index + 1, { pause: false });
      else { state.playing = false; updateControls(); }
    }
  }
  requestAnimationFrame(tick);
}

export async function bootReplay() {
  installDeviceSupport();
  // Controls are application code, independent of generated/retained replay artifacts.
  const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = '/css/replay.css'; document.head.append(css);
  document.body.classList.add('agent-native-replay');
  await initLang();
  await data.loadAll(...GAME_FILES, 'assets', 'local');
  settingsStore.set({ ...settingsStore.get(), quality: 'medium', officialFx: 'off' });
  // This mode never connects a socket, sends an intent, or writes a result to game history.
  net.request = () => Promise.reject(new NetError('REPLAY_READ_ONLY', t('回放中无法操作')));
  net.status = 'online';
  store.set({ connection: { status: 'online', ping: 0, everOnline: true }, session: { entered: true },
    room: { code: 'REPLAY', inMatch: true, mode: 'solo', spectators: [], seats: [] },
    ui: { pendingJoin: null, restoring: false, buildStale: false } });
  controls = document.createElement('div'); controls.className = 'agent-replay-dock';
  const option = (tag, name, text) => { const n = document.createElement(tag); n.dataset.control = name; if (text) n.textContent = t(text); return n; };
  const heading = option('strong', 'heading', '轨迹回放');
  const episode = option('select', 'episode'), player = option('select', 'player'), round = option('select', 'round');
  const prev = option('button', 'prev', '上一步'), play = option('button', 'play', '播放'), next = option('button', 'next', '下一步');
  const battle = option('button', 'battle', '本回合作战'), back = option('a', 'back', '返回首页'); back.href = '/';
  const retry = option('button', 'retry', '重试'); retry.hidden = true;
  const scrub = option('input', 'scrub'); scrub.type = 'range'; scrub.min = '0';
  const speed = option('select', 'speed'); for (const v of [0.5, 1, 2, 4, 8]) speed.add(new Option(`${v}×`, String(v))); speed.value = '1';
  const status = option('span', 'status');
  status.setAttribute('role', 'status');
  controls.append(heading, episode, player, round, prev, play, next, battle, speed, retry, back, scrub, status);
  document.body.append(controls);
  render(html`<div class="app-root"><${GameScreen} /><${UiHosts} /><${ToastHost} /><${GuideHost} /></div>`, document.getElementById('app'));
  episode.onchange = () => loadEpisode(episode.value).catch(showError); player.onchange = selectPlayer;
  round.onchange = () => showStep(state.steps.findIndex(s => s.before.round === Number(round.value)));
  prev.onclick = () => nextStep(-1); next.onclick = () => nextStep(1);
  play.onclick = () => { state.playing = !state.playing; updateControls(); };
  battle.onclick = () => showBattle({ auto: true }).catch(showError);
  speed.onchange = () => { state.speed = Number(speed.value); };
  scrub.oninput = () => { if (state.mode === 'battle') seekBattle(Number(scrub.value)); else showStep(Number(scrub.value)); };
  async function openReplay() {
    state.error = ''; state.loading = true; updateControls();
    try {
      const manifest = await json('/dev/agent/index.json');
      if (!Array.isArray(manifest.episodes) || !manifest.episodes.length) throw new Error(t('当前没有可用回放'));
      state.manifest = manifest;
      const id = state.selectedId || new URLSearchParams(location.search).get('replay') || manifest.episodes[0].id;
      episode.replaceChildren(...manifest.episodes.map(e => new Option(`${e.policyLabel ? e.policyLabel + ' · ' : ''}${e.config} · seed ${e.seed} · ${e.victory ? t('胜利') : t('失败')}`, e.id)));
      if (!manifest.episodes.some(e => e.id === id)) {
        const missing = new Option(t('链接中的回放不存在，请重新选择'), id, true, true);
        missing.disabled = true; episode.prepend(missing);
      } else episode.value = id;
      await loadEpisode(id);
    } catch (error) { showError(error); }
  }
  retry.onclick = () => openReplay();
  await openReplay();
  document.getElementById('boot')?.remove();
  globalThis.__SP__ = { store, net, data, replay: true, version: 1 };
  globalThis.__nativeReplay = { state };
  requestAnimationFrame(tick);
}
