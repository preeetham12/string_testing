// Drives the real tester page in Chromium with a fake microphone that plays
// synthetic instrument recordings. Usage:
//   node tests/tester/browser.test.js [--shots <dir>]
// Needs playwright-core (npm i playwright-core) and a Chromium binary
// (PLAYWRIGHT_BROWSERS_PATH, or CHROMIUM_PATH).
const http = require("http"), fs = require("fs"), path = require("path"), os = require("os");
let chromium; try { ({ chromium } = require("playwright-core")); } catch (e) { ({ chromium } = require(process.env.PLAYWRIGHT_CORE || "playwright-core")); }
const D = require("../../tester/dsp.js"), S = require("./synth.js");

const ROOT = path.join(__dirname, "..", "..", "tester");
const shotsDir = process.argv.includes("--shots") ? process.argv[process.argv.indexOf("--shots") + 1] : null;
const SR = 48000;
const results = []; const check = (name, ok, extra) => { results.push([name, !!ok]); console.log((ok ? "PASS " : "FAIL ") + name + (extra ? "  " + extra : "")); };

function wavFile(samples, sr, file) {
  fs.writeFileSync(file, Buffer.from(D.encodeWav(samples, sr)));
}
const SAVED = { reports: [], audio: {} };
function server() {
  const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css" };
  return new Promise((res) => {
    const s = http.createServer((req, rs) => {
      let p = req.url.split("?")[0]; if (p === "/") p = "/index.html";
      if (p.startsWith("/tester-api/results")) {          // stand-in for the StringCoach results API
        const body = []; req.on("data", (c) => body.push(c));
        req.on("end", () => {
          const buf = Buffer.concat(body), m = /^\/tester-api\/results\/(\w+)\/audio$/.exec(p);
          if (req.method === "POST" && p === "/tester-api/results") {
            const id = "r" + SAVED.reports.length; SAVED.reports.push(JSON.parse(buf.toString()));
            rs.writeHead(200, { "content-type": "application/json" }); return rs.end(JSON.stringify({ id, upload_token: "tok-" + id }));
          }
          if (req.method === "PUT" && m && req.headers["x-upload-token"] === "tok-" + m[1]) { SAVED.audio[m[1]] = buf; rs.writeHead(200, { "content-type": "application/json" }); return rs.end("{}"); }
          rs.writeHead(403); rs.end();
        });
        return;
      }
      const f = path.join(ROOT, p);
      if (!f.startsWith(ROOT) || !fs.existsSync(f)) { rs.writeHead(404); return rs.end(); }
      rs.writeHead(200, { "content-type": types[path.extname(f)] || "application/octet-stream" }); rs.end(fs.readFileSync(f));
    }).listen(0, "127.0.0.1", () => res(s));
  });
}
const exe = process.env.CHROMIUM_PATH || (() => {
  const roots = [process.env.PLAYWRIGHT_BROWSERS_PATH, "/opt/pw-browsers", path.join(os.homedir(), ".cache", "ms-playwright")].filter((r) => r && fs.existsSync(r));
  for (const r of roots) {
    const d = fs.readdirSync(r).filter((x) => /^chromium-\d+/.test(x)).sort().pop();
    if (d) for (const sub of ["chrome-linux", "chrome-linux64"]) { const f = path.join(r, d, sub, "chrome"); if (fs.existsSync(f)) return f; }
  }
  throw new Error("No Chromium found: set CHROMIUM_PATH");
})();

// A take, optionally with talking and knocks mixed in, preceded by 3.7 s of silence for the room check.
function buildInput(kind) {
  let take;
  if (kind === "talk") {
    take = S.renderTake("guitar", SR, { seed: 11, count: 8 });
    // talking in the pause after the last note and a knock between notes
    // (talking over a ringing note cannot be separated from it by pitch)
    const last = take.truth[take.truth.length - 1], k5 = take.truth[5];
    S.mixAt(take.samples, S.speech(SR, 2.4, 5), last.t0 + last.dur + 0.8, SR, 0.5);
    S.mixAt(take.samples, S.knock(SR, S.rng(3)), k5.t0 + k5.dur + 0.05, SR, 0.8);
  } else take = S.renderTake(kind, SR, { seed: 21, count: 10, room: 0.3 });
  const lead = new Float32Array(Math.round(3.7 * SR));
  const all = new Float32Array(lead.length + take.samples.length); all.set(take.samples, lead.length);
  return { all, take };
}

async function runCase(browser, base, kind, expect) {
  const { all, take } = buildInput(kind);
  const wav = path.join(os.tmpdir(), "nueriq-" + kind + ".wav"); wavFile(all, SR, wav);
  const ctx = await browser.newContext({ permissions: ["microphone"], viewport: { width: 1280, height: 630 }, deviceScaleFactor: 1.5 });  // a 1080p laptop at 150%, minus the browser's toolbars
  const page = await ctx.newPage();
  const errors = []; page.on("pageerror", (e) => errors.push(e.message)); page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  const shot = async (n) => { if (shotsDir) { fs.mkdirSync(shotsDir, { recursive: true }); await page.screenshot({ path: path.join(shotsDir, kind + "-" + n + ".png") }); } };
  console.log("\n== " + kind);
  await page.goto(base + "/index.html");
  await shot("1-details");
  check(kind + ": no main note (Sa) field on the first page", (await page.$("#fSa")) === null && !(await page.textContent("#s1")).includes("Main note"));
  check(kind + ": no keyboard pictures", (await page.$("#kbd")) === null && (await page.$("#kbd4")) === null);
  // consent must be required
  await page.fill("#fName", "Tester"); await page.fill("#fEmail", "t@example.com");
  await page.click("#btnStart");
  check(kind + ": cannot start without consent", await page.isVisible("#detailsErr") && await page.isVisible("#s1"));
  await page.check("#fConsent"); await page.selectOption("#fInst", "auto"); await page.click("#btnStart");
  await page.waitForSelector("#btnRec", { state: "visible" });
  await shot("2-ready");
  await page.click("#btnRec");
  await page.waitForSelector("#live:not([hidden])", { timeout: 15000 });
  // while playing: label, graph, chip
  let sawNote = false, sawString = false, sawSwara = false, sawKey = false, canvasInk = 0, chip = "";
  const ranges = new Set(), heights = new Set(); let stopOff = 0;
  const dur = take.samples.length / SR;
  const t0 = Date.now();
  let shotTaken = false;
  while ((Date.now() - t0) / 1000 < dur - 1.5) {
    await page.waitForTimeout(500);
    const txt = await page.textContent("#nowNote");
    if (txt && txt.trim() !== "—") sawNote = true;
    if (/^String \d$/.test((txt || "").trim())) sawString = true;
    if (/\b(Sa|Re|Ga|Ma|Pa|Dha|Ni)\b/i.test(txt + " " + await page.textContent("#nowWest"))) sawSwara = true;
    ranges.add(await page.evaluate(() => { const L = window.__nueriq.live; return L.yLo + ":" + L.yHi; }));
    heights.add(await page.evaluate(() => document.getElementById("graph").clientHeight));
    const stopBox = await page.evaluate(() => { const r = document.getElementById("btnStop").getBoundingClientRect(); return r.bottom <= window.innerHeight + 1 && r.top >= 0; });
    if (!stopBox) stopOff++;
    chip = await page.evaluate(() => document.getElementById("instChip") ? "shown" : "");
    if ((Date.now() - t0) / 1000 > dur * 0.6 && !shotTaken) { await shot("3-recording"); shotTaken = true; }
  }
  canvasInk = await page.evaluate(() => { const c = document.getElementById("graph"), g = c.getContext("2d"), d = g.getImageData(0, 0, c.width, c.height).data; let n = 0; for (let i = 0; i < d.length; i += 4) if (d[i + 1] > 150 || d[i] > 200) n++; return n; });
  check(kind + ": live note label showed notes", sawNote);
  if (expect.strings) check(kind + ": live label shows string numbers", sawString);
  check(kind + ": no sa-re-ga-ma shown while playing", !sawSwara);
  check(kind + ": graph range stays fixed (moves only left to right)", ranges.size === 1, [...ranges].join(" "));
  check(kind + ": graph keeps its size at 150% screen scaling", heights.size === 1 && [...heights][0] === 220, [...heights].join(","));
  check(kind + ": recording view fits one window (Stop always visible)", stopOff === 0, stopOff + " checks off-screen");
  check(kind + ": live graph drew something", canvasInk > 300, "ink=" + canvasInk);
  check(kind + ": no 'Sounds like' line while recording", chip === "" && !(await page.textContent("#live")).includes("Sounds like"));
  await page.waitForTimeout(1200);
  await page.click("#btnStop");
  await page.waitForSelector("#s3:not([hidden])", { timeout: 60000 });
  await shot("4-listen");
  const flags = await page.textContent("#s3flags");
  const sum = await page.textContent("#s3sum");
  if (expect.talking) check(kind + ": talking flagged on the Listen step", /Talking was heard/.test(flags), flags.replace(/\s+/g, " ").slice(0, 120));
  else check(kind + ": no false talking flag on a clean take", !/Talking/.test(flags), flags.replace(/\s+/g, " ").slice(0, 120));
  if (expect.knock) {
    check(kind + ": knocks are not listed to the tester", !/isn't a note/.test(flags));
    check(kind + ": knocks are kept in the data for the team", await page.evaluate(() => window.__nueriq.S.quality.flags.some((f) => f.type === "non_key")));
  }
  if (!expect.talking) check(kind + ": clean take shows no warnings", !/record again/i.test(flags) && /looks good/.test(flags), flags.replace(/\s+/g, " ").slice(0, 120));
  const liveKeys = await page.evaluate(() => window.__nueriq.S.liveKeys.length);
  check(kind + ": live keys roughly match the notes played", Math.abs(liveKeys - take.truth.length) <= Math.max(3, take.truth.length * 0.3), liveKeys + " vs " + take.truth.length);
  await page.click("#btnUse");
  await page.waitForSelector("#keysTable tbody tr");
  const checkedKeys = await page.evaluate(() => window.__nueriq.S.keys.length);
  check(kind + ": checked keys match notes played", Math.abs(checkedKeys - take.truth.filter((t) => !t.glide).length) <= Math.max(2, take.truth.length * 0.25), checkedKeys + " vs " + take.truth.length);
  await page.check('input[name="mode"][value="checked"]');
  await shot("5-form");
  // edit one key, answer why
  const before = await page.evaluate(() => window.__nueriq.S.keys[0].midi);
  const sel = page.locator("#keysTable tbody tr select").first();
  await sel.selectOption(String(before + 1));
  await page.waitForSelector("#why:not([hidden])");
  await page.click("#why .btn >> nth=0");
  check(kind + ": editing a key logs a correction with a reason", await page.evaluate(() => { const c = window.__nueriq.S.corrections; return c.length === 1 && c[0].why === "wrong_note"; }));
  // add + delete
  await page.click("#btnAddKey"); await page.click("#why .btn >> nth=0");
  await page.locator("#keysTable tbody tr").first().locator("text=Delete").click(); await page.click("#why .btn >> nth=0");
  check(kind + ": add and delete are logged", await page.evaluate(() => window.__nueriq.S.corrections.map((c) => c.type).join(",") === "change_note,add_key,delete_key"));
  // submit needs an instrument
  const det = await page.evaluate(() => window.__nueriq.S.result.instrument.best);
  await page.click("#btnSubmit");
  check(kind + ": submit asks for the instrument", await page.isVisible("#s4"));
  await page.selectOption("#instFinal", expect.instrument);
  await page.click("#btnSubmit");
  await page.waitForSelector("#s5:not([hidden])");
  await shot("6-report");
  const rep = await page.textContent("#report");
  check(kind + ": report compares the app with what was really played", rep.includes("App vs what you really played") && (await page.$$(".vstrip i")).length > 0);
  check(kind + ": report has intonation by " + (expect.strings ? "string" : "note"), rep.includes("Intonation by " + (expect.strings ? "string" : "note")));
  check(kind + ": instrument-confidence card removed", !rep.includes("App's confidence") && !rep.includes("How the instrument was decided"));
  check(kind + ": no swaras in the report", !/Swara|\bSa\b/.test(rep));
  await page.waitForFunction(() => /Saved to the results dashboard|Couldn't save/.test(document.getElementById("saveState").textContent), null, { timeout: 15000 });
  const saved = SAVED.reports[SAVED.reports.length - 1], audio = SAVED.audio["r" + (SAVED.reports.length - 1)];
  check(kind + ": test saved to the results dashboard", (await page.textContent("#saveState")).includes("Saved") && !!saved && saved.tester.name === "Tester");
  check(kind + ": recording uploaded with the test", !!audio && audio.slice(0, 4).toString() === "RIFF" && audio.length > 44100);
  check(kind + ": saved report has no e-mail", !JSON.stringify(saved).includes("t@example.com"));
  const R = await page.evaluate(() => window.__nueriq.S.submitted);
  check(kind + ": report JSON has no e-mail", !JSON.stringify(R).includes("t@example.com"));
  if (expect.family) check(kind + ": detected the right instrument family", D.INSTRUMENTS[det].family === D.INSTRUMENTS[expect.family].family, det);
  if (expect.exact) check(kind + ": detected the exact instrument", det === expect.exact, det);
  check(kind + ": no page errors", errors.length === 0, errors.join(" | ").slice(0, 200));
  await ctx.close();
}

(async () => {
  const srv = await server(), base = "http://127.0.0.1:" + srv.address().port;
  const cases = [
    ["violin", { instrument: "violin", family: "violin", strings: true }],
    ["guitar", { instrument: "guitar", family: "guitar", exact: "guitar", strings: true }],
    ["veena", { instrument: "veena", family: "veena", exact: "veena" }],
    ["talk", { instrument: "guitar", talking: true, knock: true, family: "guitar", strings: true }],
  ];
  const only = process.argv.find((a) => a.startsWith("--only="));
  let any = false;
  for (const [kind, expect] of cases) {
    if (only && only.slice(7) !== kind) continue; any = true;
    // a fresh browser per case so the fake-microphone file starts at its beginning
    const wav = path.join(os.tmpdir(), "nueriq-" + kind + ".wav"); const { all } = buildInput(kind); wavFile(all, SR, wav);
    const browser = await chromium.launch({ executablePath: exe, args: ["--no-sandbox", "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--use-file-for-fake-audio-capture=" + wav + "%noloop", "--autoplay-policy=no-user-gesture-required"] });
    try { await runCase(browser, base, kind, expect); } catch (e) { check(kind + ": test ran to the end", false, e.message.split("\n")[0]); }
    await browser.close();
  }
  srv.close();
  const failed = results.filter((r) => !r[1]).length;
  console.log("\n" + (results.length - failed) + " of " + results.length + " browser checks passed");
  process.exit(failed ? 1 : 0);
})();
