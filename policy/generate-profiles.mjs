import fs from 'node:fs';
import {fileURLToPath} from 'node:url';
import {PINS,parseProfile} from './lib.mjs';
const early=[1,1,2,3,3,4,4,5,5,6,6,6,6,6,6,6];
const slow=[1,1,1,1,2,2,2,3,3,3,4,4,5,5,6,6];
const low=[1,1,1,2,2,2,2,2,2,2,2,3,4,4,5,6];
const variants=[
 ['default',{},{}],['early-level',{LEVEL_TARGET:early},{}],['slow-level',{LEVEL_TARGET:slow},{}],
 ['low-level-target',{LEVEL_TARGET:low},{}],['pairs-six',{MAX_PAIRS:6,BENCH_BUDGET:8},{}],
 ['refresh-forty',{MAX_REFRESHES:40},{}],['buy-more',{BUY_FULL_MIN:0},{}],
 ['merge-priority',{MERGE_HIT:72,PAIR_HIT:16},{}],['focus-more',{FOCUS_MEMBER:8},{}],
 ['without-layer-prior',{}, {ablateLayerPrior:true}],['without-econ-prior',{}, {ablateEconPrior:true}],
 ['early-pairs',{LEVEL_TARGET:early,MAX_PAIRS:6,BENCH_BUDGET:8},{}],
];
const dir=fileURLToPath(new URL('./profiles/',import.meta.url));fs.mkdirSync(dir,{recursive:true});
for(const [name,overrides,ablations] of variants){
  const raw={name,description:'Experimental hypothesis; '+name+'. No game rule changes. Low-level target is a preference, not a hard shop cap.',target:PINS.botJs.relPath,sourceSha256:PINS.botJs.sha256,overrides,ablations};
  parseProfile(raw);fs.writeFileSync(dir+name+'.json',JSON.stringify(raw,null,2)+'\n');
}
console.log('Generated '+variants.length+' validated profiles');
