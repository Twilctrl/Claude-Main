// Read sound cue times (every typed character, the heartbeat) out of the built
// trailer, so the soundtrack lands on the same frames as the picture.
//
//   npm i playwright        (or NODE_PATH pointing at a global install)
//   node cues.cjs           -> build/cues.json
const path = require('path');
const fs = require('fs');
const { chromium } = require('playwright');

(async () => {
  const file = path.join(__dirname, 'infernis-trailer.html');
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  await page.goto('file://' + file + '?render');
  await page.evaluate(() => window.__ready);
  const cues = await page.evaluate(() => {
    const at = (ty, t0) => ty.times.map(x => +(t0 + x).toFixed(4));
    const S = Object.fromEntries(SCENES.map(s => [s.id, s]));
    const s6 = S.s6;
    return {
      duration: DUR,
      typing: [
        ...at(S.s2.ty, S.s2.t0 + 1.0),
        ...at(S.s4.ty, S.s4.t0 + .35),
        ...at(S.s5.ty, S.s5.t0 + .15),
        ...s6.tys.flatMap((ty, k) => at(ty, s6.t0 + k * 2.5 + .12)),
        ...at(s6.pfT1, s6.t0 + .25),
        ...at(s6.pfT2, s6.t0 + 1.5),
      ].sort((a, b) => a - b),
      laptop: at(S.s4.ty2, S.s4.t0 + 3.55),
      heartbeat: BEATS.map(b => +(S.s5.t0 + b).toFixed(4)),
      scenes: Object.fromEntries(SCENES.map(s => [s.id, [s.t0, s.t1]])),
    };
  });
  fs.mkdirSync(path.join(__dirname, 'build'), { recursive: true });
  fs.writeFileSync(path.join(__dirname, 'build', 'cues.json'), JSON.stringify(cues, null, 1));
  console.log(`cues: ${cues.typing.length} keystrokes, ${cues.laptop.length} laptop keys, ${cues.heartbeat.length} beats`);
  await browser.close();
})();
