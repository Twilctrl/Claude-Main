// Read sound cue times out of the built edit, so the score lands on the same
// frames as the picture.
//
//   npm i playwright        (or NODE_PATH pointing at a global install)
//   node cues.cjs           -> build/cues.json
const path = require('path');
const fs = require('fs');
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  await page.goto('file://' + path.join(__dirname, 'infernis-no-uplink.html') + '?render');
  await page.evaluate(() => window.__ready);
  const cues = await page.evaluate(() => {
    const S = Object.fromEntries(SCENES.map(s => [s.id, s]));
    return {
      duration: DUR,
      cuts: S.d1.arcs.map(a => +(S.d1.t0 + a._cut).toFixed(4)).sort((a, b) => a - b),
      laptop: S.d5.ty.times.map(x => +(S.d5.t0 + 3.35 + x).toFixed(4)),
      scenes: Object.fromEntries(SCENES.map(s => [s.id, [s.t0, s.t1]])),
    };
  });
  fs.mkdirSync(path.join(__dirname, 'build'), { recursive: true });
  fs.writeFileSync(path.join(__dirname, 'build', 'cues.json'), JSON.stringify(cues, null, 1));
  console.log(`cues: ${cues.cuts.length} link cuts, ${cues.laptop.length} keystrokes`);
  await browser.close();
})();
