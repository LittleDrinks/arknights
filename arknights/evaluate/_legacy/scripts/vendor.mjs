// Reuse the upstream file list and rewrite rules, with an external output directory.
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {VENDOR_FILES,VENDOR_REWRITES,rewriteBare} from '../../../../game/tools/vendor.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../../../..');
const out=path.join(root,'runtime/web/vendor');fs.mkdirSync(out,{recursive:true});
let copied=0;
for(const [src,name,optional] of VENDOR_FILES){
 const source=path.join(root,src);
 if(optional&&!fs.existsSync(source))continue;
 const data=fs.readFileSync(source);
 fs.writeFileSync(path.join(out,name),VENDOR_REWRITES[name]?rewriteBare(data.toString(),VENDOR_REWRITES[name]):data);copied++;
}
console.log(JSON.stringify({copied,out}));
