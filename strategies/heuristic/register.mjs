import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {register} from 'node:module';
import {defaultSpec,parseProfileText,PINS,sha256} from './lib.mjs';

if(!process.env.ARK_GAME_ROOT)throw new Error('ARK_GAME_ROOT required');
const root=fs.realpathSync(process.env.ARK_GAME_ROOT);
const bot=fs.realpathSync(path.join(root,PINS.botJs.relPath));
if(sha256(fs.readFileSync(bot))!==PINS.botJs.sha256)throw new Error('Pinned bot source mismatch');
const spec=process.env.ARK_POLICY_PROFILE?parseProfileText(fs.readFileSync(process.env.ARK_POLICY_PROFILE,'utf8')):defaultSpec();
register('./loader.mjs',import.meta.url,{data:{botURL:pathToFileURL(bot).href,spec}});
