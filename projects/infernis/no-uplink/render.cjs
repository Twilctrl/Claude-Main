// Render the built edit to an MP4, frame by frame, with the soundtrack.
//
//   npm i playwright        (or NODE_PATH pointing at a global install)
//   node render.cjs                         -> infernis-no-uplink.mp4 (1080p60)
//   node render.cjs --fps 15 --scale 0.5 --out build/preview.mp4
//   node render.cjs --from 47.5 --to 58     (a section)
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');
const { chromium } = require('playwright');

const arg = (name, def) => { const i = process.argv.indexOf('--' + name); return i > 0 ? process.argv[i + 1] : def; };
const fps = +arg('fps', 60);
const scale = +arg('scale', 1);
const out = path.resolve(__dirname, arg('out', 'infernis-no-uplink.mp4'));
const audio = path.join(__dirname, 'build', 'soundtrack.wav');

(async () => {
  const browser = await chromium.launch({ args: ['--force-color-profile=srgb'] });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  page.on('pageerror', e => console.error('page error:', e.message));
  await page.goto('file://' + path.join(__dirname, 'infernis-no-uplink.html') + '?render');
  await page.evaluate(() => window.__ready);
  const dur = await page.evaluate(() => window.__duration);
  const from = +arg('from', 0), to = Math.min(+arg('to', dur), dur);
  const frames = Math.round((to - from) * fps);
  const cdp = await page.context().newCDPSession(page);

  const w = Math.round(1920 * scale / 2) * 2, h = Math.round(1080 * scale / 2) * 2;
  const ff = ['-v', 'error', '-y', '-f', 'image2pipe', '-framerate', String(fps), '-c:v', 'mjpeg', '-i', '-'];
  const withAudio = fs.existsSync(audio);
  if (withAudio) ff.push('-ss', String(from), '-t', String(to - from), '-i', audio);
  ff.push('-vf', `scale=${w}:${h}:flags=lanczos,format=yuv420p`, '-c:v', 'libx264', '-preset', scale < 1 ? 'veryfast' : 'slow',
    '-crf', scale < 1 ? '26' : '22', '-r', String(fps));
  if (withAudio) ff.push('-c:a', 'aac', '-b:a', '192k', '-shortest');
  ff.push('-movflags', '+faststart', out);
  const enc = spawn('ffmpeg', ff, { stdio: ['pipe', 'inherit', 'inherit'] });

  const started = Date.now();
  for (let i = 0; i < frames; i++) {
    await page.evaluate(t => window.__frame(t), from + i / fps);
    const { data } = await cdp.send('Page.captureScreenshot', { format: 'jpeg', quality: 94, optimizeForSpeed: true });
    if (!enc.stdin.write(Buffer.from(data, 'base64'))) await new Promise(r => enc.stdin.once('drain', r));
    if (i % (fps * 5) === 0) process.stdout.write(`  ${(from + i / fps).toFixed(1)}s / ${to.toFixed(1)}s  (${((Date.now() - started) / 1000).toFixed(0)}s elapsed)\n`);
  }
  enc.stdin.end();
  await new Promise(r => enc.on('close', r));
  await browser.close();
  console.log(`wrote ${path.relative(process.cwd(), out)}: ${frames} frames at ${fps} fps, ${w}x${h}`);
})();
