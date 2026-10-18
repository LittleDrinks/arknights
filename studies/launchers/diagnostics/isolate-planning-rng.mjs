// Register an explicitly experimental policy transform: layout tie-breaking gets its own cloned stream.
import {register} from 'node:module';
import {pathToFileURL} from 'node:url';
import fs from 'node:fs';
import path from 'node:path';
if(!process.env.ARK_GAME_ROOT)throw new Error('ARK_GAME_ROOT required');
const target=pathToFileURL(fs.realpathSync(path.join(process.env.ARK_GAME_ROOT,'server/match/bot.js'))).href;
register('./planning-rng-loader.mjs',import.meta.url,{data:{target}});
