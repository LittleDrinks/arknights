import assert from 'node:assert/strict';
import { PROFILE, KIND, CONTROLLER, LABEL, NOTE } from '../frozen-replay/profile.mjs';

export const TRAINING_PROFILE = 'training/v1';
const training = Object.freeze({ id: TRAINING_PROFILE,
  kind: 'actual-training-trajectory-not-frozen-model-evaluation',
  controller: 'coop/move-preflight-v3+upstream-drafts+no-sale-arrange+regular-equipment+free-rewards/v1',
  idPrefix: 'coop-training', label: '合作训练真实轨迹', battleLabel: '合作训练轨迹（跨 PPO 行为版本）',
  note: '真实训练轨迹跨多个 PPO 行为版本；不是冻结 checkpoint 成功。四席私有视图仅用于离线终局回放。' });
const frozen = Object.freeze({ id: PROFILE, kind: KIND, controller: CONTROLLER,
  idPrefix: 'frozen-bc-eval', label: LABEL, battleLabel: LABEL, note: NOTE });

// Only named, fixed profiles. Caller metadata can never replace kind/controller.
export function profileFor(id = TRAINING_PROFILE) {
  assert(id === TRAINING_PROFILE || id === PROFILE, 'Unknown replay profile');
  return id === TRAINING_PROFILE ? training : frozen;
}
