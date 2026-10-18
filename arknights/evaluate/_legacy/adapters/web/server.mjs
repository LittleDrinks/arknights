// External replay/UI overlay; the upstream game submodule is never modified.
import http from 'node:http';
import net from 'node:net';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { execFileSync } from 'node:child_process';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const layout = JSON.parse(fs.readFileSync(path.join(root, 'configs/layout.json')));
const game = path.join(root, 'game');
if (execFileSync('git', ['-C', game, 'rev-parse', 'HEAD'], {encoding:'utf8'}).trim() !== layout.rules)
  throw Error('Pinned game revision mismatch');
if (execFileSync('git', ['-C', game, 'status', '--porcelain', '--untracked-files=all'], {encoding:'utf8'}).trim())
  throw Error('game must be pristine');
const args=process.argv.slice(2);
let port=38181, host='0.0.0.0';
for(let i=0;i<args.length;i+=2) {
  if(args[i]==='--port')port=Number(args[i+1]);
  else if(args[i]==='--host')host=args[i+1];
  else throw Error('Unknown option '+args[i]);
}
if(!Number.isInteger(port)||port<1||port>65535||!host)throw Error('Invalid listen address');
process.env.SP_ASSET_INDEX='0';
const { startServer }=await import(pathToFileURL(path.join(game,'server/index.js')));
const quiet={info(){},debug(){},warn(){},error(){}};
const upstream=await startServer({port:0,host:'127.0.0.1',quiet:true,log:quiet,assetIndex:false,studio:false});
const mounts=[
  ['/dev/',path.join(root,'results/replays/public/dev')],
  ['/assets/',path.join(root,'runtime/web/assets')],
  ['/vendor/',path.join(root,'runtime/web/vendor')],
  ['/fonts/',path.join(root,'runtime/web/fonts')],
  ['/',path.join(root,'adapters/web/overlay')],
];
const mime={'.js':'text/javascript; charset=utf-8','.mjs':'text/javascript; charset=utf-8','.json':'application/json; charset=utf-8','.css':'text/css; charset=utf-8','.html':'text/html; charset=utf-8','.png':'image/png','.jpg':'image/jpeg','.webp':'image/webp','.svg':'image/svg+xml','.gz':'application/gzip','.atlas':'text/plain','.skel':'application/octet-stream','.woff2':'font/woff2','.mp3':'audio/mpeg'};
const proxy=http.createServer((req,res)=>{
  let pathname;
  try { pathname=decodeURIComponent(new URL(req.url,'http://localhost').pathname); }
  catch {res.writeHead(400);res.end();return;}
  if(pathname.split('/').some(p=>p==='..'||p.startsWith('.'))){res.writeHead(403);res.end();return;}
  if(req.method==='GET'||req.method==='HEAD')for(const [prefix,base] of mounts){
    if(!pathname.startsWith(prefix))continue;
    const file=path.resolve(base,pathname.slice(prefix.length));
    if(!file.startsWith(base+path.sep))continue;
    let st;try {st=fs.statSync(file);}catch {continue;}
    if(!st.isFile())continue;
    res.writeHead(200,{'Content-Type':mime[path.extname(file)]||'application/octet-stream','Content-Length':st.size,'Cache-Control':'no-cache','X-Arknights-Overlay':'external'});
    if(req.method==='HEAD')res.end();else fs.createReadStream(file).on('error',()=>res.destroy()).pipe(res);
    return;
  }
  const forwarded=http.request({hostname:'127.0.0.1',port:upstream.port,path:req.url,method:req.method,headers:req.headers},response=>{
    res.writeHead(response.statusCode,response.headers);response.pipe(res);
  });
  forwarded.on('error',()=>{if(!res.headersSent)res.writeHead(502);res.end();});
  req.pipe(forwarded);
});
proxy.on('upgrade',(req,socket,head)=>{
  const backend=net.connect(upstream.port,'127.0.0.1',()=>{
    const lines=[`${req.method} ${req.url} HTTP/${req.httpVersion}`];
    for(let i=0;i<req.rawHeaders.length;i+=2)lines.push(`${req.rawHeaders[i]}: ${req.rawHeaders[i+1]}`);
    backend.write(lines.join('\r\n')+'\r\n\r\n');
    if(head.length)backend.write(head);
    backend.pipe(socket);socket.pipe(backend);
  });
  backend.on('error',()=>socket.destroy());socket.on('error',()=>backend.destroy());
  socket.on('close',()=>backend.destroy());
});
await new Promise((resolve,reject)=>{proxy.once('error',reject);proxy.listen(port,host,resolve);});
console.log(JSON.stringify({port,host,game:layout.rules,overlay:'external',replayIndex:'/dev/agent/index.json'}));
let stopping=false;
async function close(){if(stopping)return;stopping=true;proxy.close();proxy.closeAllConnections();await upstream.close();process.exit(0);}
for(const signal of ['SIGTERM','SIGINT'])process.on(signal,close);
