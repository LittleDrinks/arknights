// Pure projection of messages already delivered to ONE seat. No Match/data/Session access.
export const PROJECTION = 'human-visible.v1';
const pick = (o, keys) => Object.fromEntries(keys.filter(k => Object.hasOwn(o || {}, k)).map(k => [k, o[k]]));
const publicKeys = 't phase round lastRound deadline serverNow modeId difficulty stageId setupRevision rerollVote factions disabledBonds drawnDisabledBonds bannedChess bossId bossRound hiddenRound bossPairs spRound combatMode paused players fields teamLp overtimeAt bossHp draft sp unite uniteResult'.split(' ');
const ownKeys = 't playerId seat alive lp funds bandId ready canReady personalChoice shop hand temp board deployCap deployCount bonds effects nextEnemies loadout ops notOwned diy diyBanned standIns stats prepMapChars'.split(' ');
// Explicit nested field vocabulary: new upstream fields require a projection version change.
const nested = new Set(('t id uid playerId seat alive lp funds bandId ready canReady name kind price basePrice sold frozen level maxLevel upgradePrice refreshPrice freeRefreshes rewardOffer tier source label queued slots chessId baseId charId itemId itemUid targetUid replaceUid pos row col r c dir area index star stars elite golden count num layers activeLayers active enabled off max cap threshold bonds effects hand temp board items equipment equips skill module potential cultivate choiceId round sourceItemId cards picks order turn current options available remaining owners playerIds players fieldId status connected autoplay isBot left eliminated infoReady infoRemaining expires deadline setupRevision hp maxHp current total killed leaked damage bossDamage wins losses icon desc text title value color tags enemyKey gate fly boss source tag start at to from readyCount modeId won result victory reason roundsPassed hiddenReached perPlayer nameKey waiting seconds limit x y rows cols cells deployable blocked side positions turnIndex turnPlayerId pool votes agree disagree phase skipsLeft taken turnDeadline turnSeconds untimed family eventId soldOut shopLevel boardCount live progress resolved done max proposerId voters agreed ownerUid iconKind iconId counter counterText dmgDealt kills leaks gold refreshes merges skillIndex uniEquipId standIns stats prepMapChars thresholds countsHand activeThreshold tierCount next pendingLp uniteLeft through helpers leakers nameKey bondId harmony ').split(' '));
for (const key of [...publicKeys, ...ownKeys]) nested.add(key);
const maps = new Set(['entries', 'picks', 'skipsLeft', 'taken', 'loadout', 'ops', 'diy', 'losses']);
const omitted = new Set();
function visible(value, depth = 0, parent = '') {
  if (depth > 16) return null;
  if (value == null || ['boolean', 'number', 'string'].includes(typeof value)) return value;
  if (Array.isArray(value)) return value.map(v => visible(v, depth + 1, parent));
  return Object.fromEntries(Object.entries(value).filter(([k]) => {
    const allowed = nested.has(k) || (maps.has(parent) && /^[A-Za-z0-9_-]{1,160}$/.test(k) && !/seed|spec|hidden|token|secret|rng/i.test(k));
    if (!allowed) omitted.add(parent ? parent + '.' + k : k);
    return allowed;
  }).map(([k, v]) => [k, visible(v, depth + 1, k)]));
}
export function project(pub, own, playerId) {
  if (!pub || !own || own.playerId !== playerId) return null;
  omitted.clear();
  const publicView = visible(pick(pub, publicKeys)), privateView = visible(pick(own, ownKeys));
  const missingFields = [...omitted].sort();
  if (Array.isArray(publicView.players)) publicView.players = publicView.players.map(({ name, ...player }) => player);
  return { filteredFieldCount: missingFields.length, schema: PROJECTION, public: publicView, own: privateView,
    missing: ['unit-detail text/stats', 'scouted fields', 'map geometry', 'adapter descriptors/candidate shortlist'] };
}
export const ACTION_FIELDS = Object.freeze({
  'g.infoReady': ['setupRevision'], 'g.band': ['bandId'], 'g.bandSkip': [], 'g.bandFocus': ['bandId'],
  'g.buy': ['slot'], 'g.refresh': [], 'g.freeze': [], 'g.levelUp': [], 'g.sell': ['uid'],
  'g.move': ['uid', 'to', 'dir'], 'g.equip': ['itemUid', 'targetUid', 'replaceUid'],
  'g.art': ['itemUid', 'row', 'col', 'dir'], 'g.destroy': ['uid'], 'g.reward': ['idx'],
  'g.choice': ['idx', 'choiceId'], 'g.ready': ['ready'], 'g.rerollVote': ['voteId', 'agree'],
  'g.autoplay': ['on'], 'g.pause': ['on'], 'g.watch': ['fieldId', 'playerId'], 'g.emote': ['id'],
  'g.unitStats': ['seq'], 'g.leave': [], 'room.loadout': ['entries', 'ops'],
});
export function actionOf(msg) {
  if (!Object.hasOwn(ACTION_FIELDS, msg?.t)) return null;
  const action = { t: msg.t, ...pick(msg, ACTION_FIELDS[msg.t]) };
  if (action.to) action.to = pick(action.to, ['area', 'row', 'col', 'idx', 'dir']);
  for (const field of ['entries', 'ops']) if (action[field]) action[field] = visible(action[field], 0, field);
  return action;
}
// Credentials are never persisted, even in restricted evidence. Seed/spec are verification only.
export function redact(value) {
  if (Array.isArray(value)) return value.map(redact);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value)
    .filter(([k]) => !/token|password|secret|address|(^|_)ip$|authorization|cookie/i.test(k)).map(([k, v]) => [k, redact(v)]));
  return value;
}
