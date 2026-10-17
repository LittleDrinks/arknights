// Explicit fixed deployment controller. Never invokes botPrep or performs shop economy.
export function control(env, { forced = false } = {}) {
  const { m, ps, bot } = env;
  if (m.phase !== 'PREP' || ps.ready || !ps.alive) return;
  env.source = 'fixed-controller';
  try {
    m.autoPickPersonalChoice(ps, 'bot');
    // arrange() can request a sale if the bench is full: prohibit it at the method boundary.
    const sell = ps.sell;
    ps.sell = () => { env.controllerBlockedSales++; return { ok: false, error: 'RL_CONTROLLER_NO_SALES' }; };
    try { bot.arrange(m, ps); } finally { ps.sell = sell; }
    // Conservative equipment subcontroller: regular equipment, first deployed carrier with room.
    // Consumables and Arts deliberately remain unused in v1 (effects may require strategic targeting).
    for (const item of [...ps.hand, ...ps.temp].filter(p => p?.kind === 'item')) {
      const rec = ps.gd.item(item.id);
      if (rec?.itemType !== 'EQUIP' || String(rec.kind).startsWith('consume_on_equip')) continue;
      const carrier = [...ps.board.values()].find(p => p.kind === 'chess' && (p.items || []).length < ps.gd.equipPerChess);
      if (carrier && ps.find(item.uid)) ps.equip(item.uid, carrier.uid);
    }
    // Only free reward offers, first valid displayed card; no selling to create room.
    for (let i = 0; i < 8 && ps.hand.includes(null); i++) {
      const offer = ps.privateView().shop.rewardOffer;
      if (!offer) break;
      const idx = offer.slots.findIndex(s => !s.sold && s.price <= 0 &&
        (s.kind === 'item' ? !!ps.gd.item(s.id) : !!ps.gd.chess(s.id)));
      if (idx < 0 || !ps.pickReward(idx)?.ok) break;
    }
    // Overflow items are outside the learned chess-sale action space. Destroy only temp items
    // that remain after placement/equipping; keep hand items and all chess for the policy.
    for (const item of ps.temp.filter(p => p?.kind === 'item')) ps.destroy(item.uid);
    if (forced) {
      // Original deadline disposal, explicitly audited; this is a safety budget, not a game rule.
      const before = ps.privateView().temp.filter(Boolean);
      ps.resolveTemp();
      env.history.actions.push({ source: 'budget-controller', type: 'resolveTemp', round: m.round,
        removed: before.filter(p => !ps.find(p.uid)).map(p => ({ uid: p.uid, id: p.id, kind: p.kind })) });
    }
  } finally { env.source = 'policy'; }
}
