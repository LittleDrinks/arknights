import {PINS,sha256,transformSource} from './lib.mjs';
let settings;
export function initialize(data){settings=data;}
export async function load(url,context,nextLoad){
  const result=await nextLoad(url,context);
  if(url!==settings.botURL)return result;
  const source=Buffer.isBuffer(result.source)?result.source.toString('utf8'):String(result.source);
  if(sha256(source)!==PINS.botJs.sha256)throw new Error('Loader source hash mismatch');
  const {code}=transformSource(source,settings.spec.rules);
  return {...result,source:code};
}
