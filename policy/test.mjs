import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {PINS,parseProfile,defaultSpec,sha256,transformSource} from './lib.mjs';
const root=process.argv[2];if(!root)throw new Error('Pass game root');
const source=fs.readFileSync(path.join(root,PINS.botJs.relPath),'utf8');
assert.equal(sha256(source),PINS.botJs.sha256);
assert.equal(transformSource(source,defaultSpec().rules).code,source);
const base={name:'test',description:'test',target:PINS.botJs.relPath,sourceSha256:PINS.botJs.sha256};
for(const overrides of [{MAX_REFRESHES:-1},{MAX_PAIRS:2.5},{UNKNOWN:1},{constructor:1},{LEVEL_TARGET:[1,2]},{FOCUS_MEMBER:Infinity}]){
  assert.throws(()=>parseProfile({...base,overrides}));
}
assert.throws(()=>parseProfile({...base,target:'server/sim/Battle.js'}));
assert.throws(()=>parseProfile({...base,sourceSha256:'wrong'}));
assert.throws(()=>parseProfile({...base,ablations:{constructor:true}}));
const spec=parseProfile({...base,overrides:{MAX_REFRESHES:40}});
const transformed=transformSource(source,spec.rules);
assert.match(transformed.code,/const MAX_REFRESHES = 40;/);
assert.equal(transformed.code.replace('const MAX_REFRESHES = 40;','const MAX_REFRESHES = 16;'),source);
assert.throws(()=>transformSource(source+'\nconst MAX_REFRESHES = 16;',spec.rules));
assert.throws(()=>transformSource(source.replace('const MAX_REFRESHES = 16;','const MAX_REFRESHES = 15;'),spec.rules));
for(const file of fs.readdirSync(new URL('./profiles/',import.meta.url))){
  const profile=parseProfile(JSON.parse(fs.readFileSync(new URL('./profiles/'+file,import.meta.url),'utf8')));
  const result=transformSource(source,profile.rules);
  assert.equal(result.applied.length,profile.rules.length);
}
assert.equal(sha256(fs.readFileSync(path.join(root,PINS.botJs.relPath))),PINS.botJs.sha256);
console.log('POLICY_CHECKS_OK: no-op, pinned source, twelve profiles, exact edits, invalid parameters and rule-file targets rejected');
