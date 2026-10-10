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
  await page.goto('file://' + path.join(__dirname, 'infernis-the-gate.html') + '?render');
  await page.evaluate(() => window.__ready);
  const cues = await page.evaluate(() => {
    const S = Object.fromEntries(SCENES.map(s => [s.id, s]));
    return {
      duration: DUR,
      chart: S.g1.S.map((_, i) => +(S.g1.t0 + .3 + i * .05).toFixed(4)),
      reroute: S.g2.nodes.map(n => +(S.g2.t0 + n.t).toFixed(4)).sort((a, b) => a - b),
      reveal: Array.from({ length: 17 }, (_, j) => +(S.g9.t0 + .15 + j * .06).toFixed(4)),
      scenes: Object.fromEntries(SCENES.map(s => [s.id, [s.t0, s.t1]])),
    };
  });
  fs.mkdirSync(path.join(__dirname, 'build'), { recursive: true });
  fs.writeFileSync(path.join(__dirname, 'build', 'cues.json'), JSON.stringify(cues, null, 1));
  console.log(`cues: ${cues.chart.length} chart candles, ${cues.reroute.length} reroutes, ${cues.reveal.length} reveal candles`);
  await browser.close();
})();
