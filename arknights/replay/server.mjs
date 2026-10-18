// External native viewer overlay. The pinned game and existing saved indexes stay on disk.
import http from 'node:http';
import net from 'node:net';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { execFileSync, spawn } from 'node:child_process';
import readline from 'node:readline';
import { RULES } from './export.mjs';
import { Collector } from '../collect/server.mjs';

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const WEB = fileURLToPath(new URL('./web/', import.meta.url));
const mime = { '.js': 'text/javascript; charset=utf-8', '.mjs': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8', '.css': 'text/css; charset=utf-8', '.html': 'text/html; charset=utf-8',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.webp': 'image/webp', '.svg': 'image/svg+xml',
  '.gz': 'application/gzip', '.atlas': 'text/plain', '.skel': 'application/octet-stream',
  '.woff2': 'font/woff2', '.mp3': 'audio/mpeg' };

export function parseArgs(args) {
  const options = { port: 38181, host: '0.0.0.0' }, seen = new Set();
  for (let i = 0; i < args.length; i += 2) {
    const key = args[i], value = args[i + 1];
    if (!['--port', '--host', '--replay-dir'].includes(key) || !value || seen.has(key)) throw Error(`Invalid option ${key}`);
    seen.add(key);
    if (key === '--port') options.port = Number(value);
    else if (key === '--host') options.host = value;
    else options.replayDir = path.resolve(value);
  }
  if (!Number.isInteger(options.port) || options.port < 1 || options.port > 65535 || !options.host) throw Error('Invalid listen address');
  return options;
}

export function createPreviewServer({ upstream, root = ROOT, replayDir, webDir = WEB, evaluationsPort, collector }) {
  root = path.resolve(root);
  const live = path.join(root, 'results/replays/public/dev');
  const mounts = [
    ...(replayDir ? [['/dev/', path.resolve(replayDir)]] : []),
    ['/dev/', live], ['/assets/', path.join(root, 'runtime/web/assets')],
    ['/vendor/', path.join(root, 'runtime/web/vendor')], ['/fonts/', path.join(root, 'runtime/web/fonts')],
    ['/', webDir],
  ];
  const sockets = new Set();
  const track = socket => { sockets.add(socket); socket.once('close', () => sockets.delete(socket)); return socket; };
  const proxy = http.createServer(async (req, res) => {
    let pathname;
    try { pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname); }
    catch { res.writeHead(400); res.end(); return; }
    if (pathname.includes('\\') || pathname.includes('\0') || pathname.split('/').some(p => p === '..' || p.startsWith('.'))) {
      res.writeHead(403); res.end(); return;
    }
    if (collector && await collector.http(req, res, pathname)) return;
    if (collector && pathname === '/js/net.js') {
      res.writeHead(200, { 'Content-Type': mime['.js'], 'Cache-Control': 'no-store' });
      res.end(collector.netModule()); return;
    }
    if (pathname === '/healthz' && ['GET', 'HEAD'].includes(req.method)) {
      const bytes = Buffer.from(JSON.stringify({ arknightsPreview: 1, rules: RULES, root,
        replayDir: replayDir ? path.resolve(replayDir) : null, overlay: 'external', evaluations: !!evaluationsPort, collect: collector ? { supported: true, schema: 'arknights.human-collection.v1', combatMode: 'server', replayMode: 'recorded-events' } : { supported: false } }));
      res.writeHead(200, { 'Content-Type': mime['.json'], 'Content-Length': bytes.length, 'Cache-Control': 'no-store' });
      res.end(req.method === 'HEAD' ? undefined : bytes); return;
    }
    if (pathname === '/evaluations' || pathname === '/evaluations/') pathname = '/evaluations.html';
    if (pathname.startsWith('/api/evaluations')) {
      if (!evaluationsPort) { res.writeHead(503); res.end('Evaluation queue unavailable'); return; }
      if (!['GET', 'POST'].includes(req.method)) { res.writeHead(405); res.end(); return; }
      if (req.method === 'POST') {
        // Strategy execution belongs to this local research page, never a cross-site form.
        let foreign = req.headers['sec-fetch-site'] === 'cross-site';
        try { if (req.headers.origin) foreign ||= new URL(req.headers.origin).host !== req.headers.host; }
        catch { foreign = true; }
        if (foreign || !(req.headers['content-type'] || '').startsWith('application/json')) {
          res.writeHead(403); res.end(); return;
        }
      }
      const api = http.request({ hostname: '127.0.0.1', port: evaluationsPort,
        method: req.method, path: req.url, headers: req.headers }, response => {
        res.writeHead(response.statusCode, response.headers); response.pipe(res);
      });
      api.on('error', () => { if (!res.headersSent) res.writeHead(503); res.end(); });
      req.on('aborted', () => api.destroy());
      res.on('close', () => { if (!res.writableFinished) api.destroy(); });
      req.pipe(api); return;
    }
    if (req.method === 'GET' || req.method === 'HEAD') for (const [prefix, base] of mounts) {
      if (!pathname.startsWith(prefix)) continue;
      const file = path.resolve(base, pathname.slice(prefix.length));
      if (!file.startsWith(path.resolve(base) + path.sep)) continue;
      let st;
      try {
        // Resolve both sides: runtime mounts may themselves be symlinks, but a
        // request cannot follow an artifact symlink outside its mounted directory.
        const realBase = fs.realpathSync(base), realFile = fs.realpathSync(file);
        if (!realFile.startsWith(realBase + path.sep)) continue;
        st = fs.statSync(realFile);
      } catch { continue; }
      if (!st.isFile()) continue;
      res.writeHead(200, { 'Content-Type': mime[path.extname(file)] || 'application/octet-stream',
        'Content-Length': st.size, 'Cache-Control': 'no-cache', 'X-Arknights-Overlay': 'external' });
      if (req.method === 'HEAD') res.end();
      else fs.createReadStream(file).on('error', () => res.destroy()).pipe(res);
      return;
    }
    const forwarded = http.request({ hostname: '127.0.0.1', port: upstream.port,
      path: req.url, method: req.method, headers: req.headers }, response => {
      res.writeHead(response.statusCode, response.headers); response.pipe(res);
    });
    forwarded.on('error', () => { if (!res.headersSent) res.writeHead(502); res.end(); });
    req.on('aborted', () => forwarded.destroy());
    res.on('close', () => { if (!res.writableFinished) forwarded.destroy(); });
    req.pipe(forwarded);
  });
  proxy.on('connection', track);
  proxy.on('upgrade', (req, socket, head) => {
    if (collector?.upgrade(req, socket, head, upstream)) return;
    const backend = track(net.connect(upstream.port, '127.0.0.1', () => {
      const lines = [`${req.method} ${req.url} HTTP/${req.httpVersion}`];
      for (let i = 0; i < req.rawHeaders.length; i += 2) lines.push(`${req.rawHeaders[i]}: ${req.rawHeaders[i + 1]}`);
      backend.write(lines.join('\r\n') + '\r\n\r\n');
      if (head.length) backend.write(head);
      backend.pipe(socket); socket.pipe(backend);
    }));
    backend.on('error', () => socket.destroy()); socket.on('error', () => backend.destroy());
    backend.on('close', () => socket.destroy()); socket.on('close', () => backend.destroy());
  });
  proxy.destroyConnections = () => { for (const socket of sockets) socket.destroy(); };
  return proxy;
}

export async function startPreview({ port = 38181, host = '0.0.0.0', replayDir, root = ROOT } = {}) {
  root = fs.realpathSync(root);
  const game = path.join(root, 'game');
  if (execFileSync('git', ['-C', game, 'rev-parse', 'HEAD'], { encoding: 'utf8' }).trim() !== RULES) throw Error('Pinned game revision mismatch');
  if (execFileSync('git', ['-C', game, 'status', '--porcelain', '--untracked-files=all'], { encoding: 'utf8' }).trim()) throw Error('game must be pristine');
  if (replayDir) for (const index of ['agent/index.json', 'recordings/index.json'])
    JSON.parse(fs.readFileSync(path.join(replayDir, index), 'utf8'));
  process.env.SP_ASSET_INDEX = '0';
  const { startServer } = await import(pathToFileURL(path.join(game, 'server/index.js')));
  const quiet = { info() {}, debug() {}, warn() {}, error() {} };
  const collector = new Collector(root);
  const [{ Match }, { Battle }] = await Promise.all([
    import(pathToFileURL(path.join(game, 'server/match/Match.js'))),
    import(pathToFileURL(path.join(game, 'server/sim/Battle.js'))),
  ]);
  const upstream = await startServer({ port: 0, host: '127.0.0.1', quiet: true, log: quiet, assetIndex: false, studio: false,
    MatchClass: collector.matchClass(Match, Battle) });
  const python = process.env.ARK_DASHBOARD_PYTHON || 'python3';
  const queue = spawn(python, ['-m', 'arknights.evaluate.dashboard', 'serve', '--root', root], {
    cwd: root, env: { ...process.env, PYTHONPATH: root }, stdio: ['ignore', 'pipe', 'inherit'],
  });
  const stopQueue = async () => {
    if (!queue.pid || queue.exitCode !== null || queue.signalCode !== null) return;
    await new Promise(resolve => {
      const timer = setTimeout(() => queue.kill('SIGKILL'), 10000);
      queue.once('exit', () => { clearTimeout(timer); resolve(); });
      queue.kill('SIGTERM');
    });
  };
  let evaluationsPort;
  try {
    evaluationsPort = await new Promise((resolve, reject) => {
      const lines = readline.createInterface({ input: queue.stdout });
      const timer = setTimeout(() => reject(Error('Evaluation service startup timeout')), 10000);
      queue.once('error', reject);
      queue.once('exit', () => reject(Error('Evaluation service exited during startup')));
      lines.once('line', line => {
        clearTimeout(timer); lines.close();
        try { const value = JSON.parse(line).evaluationsPort;
          if (!Number.isInteger(value) || value < 1) throw Error('Invalid queue port'); resolve(value);
        } catch (e) { reject(e); }
      });
    });
  } catch (error) { await stopQueue(); await upstream.close(); throw error; }
  const proxy = createPreviewServer({ upstream, root, replayDir, evaluationsPort, collector });
  try {
    await new Promise((resolve, reject) => { proxy.once('error', reject); proxy.listen(port, host, resolve); });
  } catch (error) { proxy.destroyConnections(); await stopQueue(); await upstream.close(); throw error; }
  let closing;
  const close = () => closing ||= (async () => {
    const stopped = new Promise(resolve => proxy.close(resolve));
    collector.close(); proxy.destroyConnections(); proxy.closeAllConnections();
    await Promise.all([stopped, upstream.close(), stopQueue()]);
  })();
  return { server: proxy, port: proxy.address().port, host, root, rules: RULES, close };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const preview = await startPreview(parseArgs(process.argv.slice(2)));
  console.log(JSON.stringify({ arknightsPreview: 1, port: preview.port, host: preview.host,
    rules: RULES, root: preview.root, replayIndex: '/dev/agent/index.json' }));
  for (const signal of ['SIGTERM', 'SIGINT']) process.once(signal, () => preview.close().then(() => process.exit(0), error => {
    console.error(error); process.exit(1);
  }));
}
