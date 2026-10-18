import {createHash} from 'node:crypto';
let target;
export function initialize(data){target=data.target;}
export async function load(url,context,nextLoad){
  const result=await nextLoad(url,context);if(url!==target)return result;
  let source=Buffer.from(result.source).toString('utf8');
  if(createHash('sha256').update(source).digest('hex')!=='bbdf346145416275e59d2bcd047189184769af3a99e51afad54af33424b4009b')throw new Error('Pinned bot source mismatch');
  const signature='export function* planLayoutSteps(m, ps, pieces, params = LAYOUT_PARAMS, { occupied = new Set(), recOf = null } = {}) {';
  const draw='const noise = m.rngBots() * 1e-6;';
  for(const anchor of [signature,draw])if(source.split(anchor).length!==2)throw new Error('Planning RNG anchor mismatch');
  source=source.replace(signature,signature+'\n  const planningRng = m.rngBots.clone();');
  source=source.replace(draw,'const noise = planningRng() * 1e-6;');
  return {...result,source};
}
