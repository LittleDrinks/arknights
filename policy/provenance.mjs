import fs from 'node:fs';
import path from 'node:path';
import {sha256,RULES_COMMIT} from './lib.mjs';
const expected=JSON.parse(fs.readFileSync(new URL('./pins/rules-manifest.json',import.meta.url),'utf8'));
if(expected.rulesCommit!==RULES_COMMIT)throw new Error('Rule manifest commit mismatch');
function files(root,dir){
  return fs.readdirSync(path.join(root,dir),{withFileTypes:true}).flatMap(e=>{
    const rel=dir+'/'+e.name;
    if(e.isDirectory())return files(root,rel);
    if(e.isFile()&&/\.(js|mjs|json)$/.test(e.name))return [rel];
    return [];
  });
}
export function verifyRules(root){
  const actual=[...files(root,'server'),...files(root,'data'),...files(root,'shared'),'package.json','package-lock.json'].sort();
  const expectedNames=Object.keys(expected.files).sort();
  if(JSON.stringify(actual)!==JSON.stringify(expectedNames))throw new Error('Rule file set differs from pinned version');
  const hashes={};
  for(const rel of actual){
    const digest=sha256(fs.readFileSync(path.join(root,rel)));
    if(digest!==expected.files[rel])throw new Error('Rule file changed: '+rel);
    hashes[rel]=digest;
  }
  const digest=sha256(actual.map(rel=>rel+'\0'+hashes[rel]+'\n').join(''));
  if(digest!==expected.treeSha256)throw new Error('Rule tree digest mismatch');
  return {rulesCommit:RULES_COMMIT,fileCount:actual.length,treeSha256:digest};
}
