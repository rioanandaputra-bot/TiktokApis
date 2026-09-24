// Headless (Node + jsdom, NO browser/chromium) minter for TikTok's `s_v_web_id` device fingerprint.
//
// Affiliate 'sensitive' endpoints (api_sens .../cmp/contact — creator phone/email) require a valid
// `s_v_web_id`, which is generated CLIENT-SIDE by TikTok's secsdk. A hand-made verify_ string is
// rejected; only the real secsdk output is accepted. curl_cffi can't run JS, so we load the real
// affiliate page in jsdom (which executes secsdk) and read the s_v_web_id it writes. Verified: the
// secsdk-generated value works from curl_cffi immediately (no /web/report registration needed).
//
// IO: stdin = {"cookies":[{name,value,domain,path}], "device" (or "ua"), "url", "waitMs"}; stdout = {"s_v_web_id":..,"msToken"?:..}

// Node 20 races IPv4 against IPv6 with a 250 ms budget per attempt. From the container the IPv4
// handshake to TikTok takes longer than that and the DNS64 IPv6 address is unreachable, so every
// page load died with AggregateError [ETIMEDOUT, ENETUNREACH] and the mint returned {} -- silently,
// on every re-mint. Measured 23 Sep 2026: 3/3 loads succeed once IPv4 is simply tried first.
require("net").setDefaultAutoSelectFamily(false);
require("dns").setDefaultResultOrder("ipv4first");

// The device secsdk fingerprints must be the one our requests claim to be: the shop's own
// device (tiktok_shop.device), passed in as `device`. Bare jsdom reports platform "", a
// 0x0 screen, UTC and vendor "Apple Computer, Inc.". The defaults below are a real Chrome on a
// seller's Mac, read 23 Sep 2026, for callers that pass no device. TZ is set before any
// Date/Intl use.
process.env.TZ = "Asia/Jakarta";
const CHROME = {
  platform: "MacIntel", vendor: "Google Inc.", hardwareConcurrency: 8, deviceMemory: 16, maxTouchPoints: 0,
  languages: ["en-US", "en", "id"],
  plugins: ["PDF Viewer", "Chrome PDF Viewer", "Chromium PDF Viewer", "Microsoft Edge PDF Viewer", "WebKit built-in PDF"],
  screen: { width: 1536, height: 960, availWidth: 1536, availHeight: 867, availLeft: 0, availTop: 30, colorDepth: 24, pixelDepth: 24 },
  dpr: 2, inner: [1536, 780], outer: [1536, 867],
};

// Overrides go on the prototypes, where a real Chrome keeps these getters, so an own-property
// check on navigator/screen sees nothing unusual.
// `device` is the shop's navigator (userAgent, platform, languages, hardwareConcurrency,
// deviceMemory, screen); anything it leaves out keeps CHROME's value.
function deviceProfile(device) {
  const d = device && typeof device === "object" ? device : {};
  const screen = Object.assign({}, CHROME.screen, d.screen || {});
  const win = d.platform === "Win32";
  return Object.assign({}, CHROME, {
    platform: d.platform || CHROME.platform,
    hardwareConcurrency: d.hardwareConcurrency || CHROME.hardwareConcurrency,
    deviceMemory: d.deviceMemory || CHROME.deviceMemory,
    languages: (d.languages && d.languages.length) ? d.languages : CHROME.languages,
    screen: Object.assign(screen, { availTop: win ? 0 : screen.availTop }),
    dpr: win ? 1 : CHROME.dpr,
    inner: [screen.width, screen.availHeight - 87], outer: [screen.width, screen.availHeight],
    uaPlatform: win ? "Windows" : (d.platform && d.platform !== "MacIntel" ? "Linux" : "macOS"),
  });
}

function presentAsChrome(w, ua, device) {
  const def = (o, k, v) => Object.defineProperty(o, k, { get: () => v, configurable: true, enumerable: true });
  const N = w.Navigator.prototype, S = w.Screen.prototype, E = deviceProfile(device);
  def(N, "platform", E.platform); def(N, "vendor", E.vendor);
  def(N, "hardwareConcurrency", E.hardwareConcurrency); def(N, "deviceMemory", E.deviceMemory);
  def(N, "webdriver", false); def(N, "maxTouchPoints", E.maxTouchPoints); def(N, "pdfViewerEnabled", true);
  def(N, "language", E.languages[0]); def(N, "languages", Object.freeze(E.languages.slice()));
  const plugins = E.plugins.map((name) => ({ name, filename: "internal-pdf-viewer", description: "Portable Document Format", length: 2 }));
  plugins.item = (i) => plugins[i] || null;
  plugins.namedItem = (n) => plugins.find((p) => p.name === n) || null;
  plugins.refresh = () => {};
  def(N, "plugins", plugins);
  const major = (ua.match(/Chrome\/(\d+)/) || [])[1] || "0";
  const uad = { brands: [{ brand: "Not=A?Brand", version: "8" }, { brand: "Chromium", version: major },
                         { brand: "Google Chrome", version: major }], mobile: false, platform: E.uaPlatform };
  def(N, "userAgentData", Object.assign({}, uad, {
    getHighEntropyValues: async () => Object.assign({}, uad, { architecture: "x86", bitness: "64", model: "",
                                                               platformVersion: "15.0.0", uaFullVersion: `${major}.0.0.0` }),
    toJSON: () => uad,
  }));
  for (const [k, v] of Object.entries(E.screen)) def(S, k, v);
  def(w, "devicePixelRatio", E.dpr);
  def(w, "innerWidth", E.inner[0]); def(w, "innerHeight", E.inner[1]);
  def(w, "outerWidth", E.outer[0]); def(w, "outerHeight", E.outer[1]);
  w.chrome = { runtime: {}, app: { isInstalled: false }, csi() {}, loadTimes() {} };
}

const { JSDOM, CookieJar, VirtualConsole, ResourceLoader } = require("jsdom");

// Only fetch what secsdk needs. Block images, stylesheets/fonts, and media — the bulk of a heavy
// affiliate SPA's bytes, all irrelevant to s_v_web_id. Scripts, iframes, the main document, and
// secsdk's own window.fetch/XHR calls (which bypass ResourceLoader) are untouched, so minting stays
// reliable; this only trims dead weight so secsdk runs sooner and the poll below exits earlier.
// Return null = the per-document loader's documented no-op skip.
const BLOCK_TAGS = new Set(["img", "link", "audio", "video", "track", "source", "picture"]);
class LeanLoader extends ResourceLoader {
  fetch(url, options) {
    const el = options && options.element;
    if (el && BLOCK_TAGS.has(el.localName)) return null;
    return super.fetch(url, options);
  }
}

// The device, page and wait come from the caller (tiktok_shop.device_mint.mint_device_cookies, values in
// tiktok_shop/constants.py SECSDK_MINT_URL / SECSDK_MINT_WAIT_MS): a mint must claim the same
// device every signed request of the shop carries.
// No oec_lucifer: the bs token is minted per session by the BSID runner (signing/lucifer_bsid.py).
const WANT = ["s_v_web_id", "msToken", "ttwid", "odin_tt"];

function readStdin() {
  return new Promise((res) => {
    let b = ""; process.stdin.setEncoding("utf8");
    process.stdin.on("data", (d) => (b += d));
    process.stdin.on("end", () => res(b));
  });
}

(async () => {
  let out = {};
  try {
    const input = JSON.parse((await readStdin()) || "{}");
    const UA = (input.device && input.device.userAgent) || input.ua;
    if (!UA || !input.url || !input.waitMs) throw new Error("device/ua, url and waitMs are required");
    if (input.device && input.device.timezone) process.env.TZ = String(input.device.timezone);
    const url = input.url;
    const waitMs = input.waitMs;
    const origin = new URL(url).origin;

    const jar = new CookieJar();
    for (const c of input.cookies || []) {
      if (!c.value) continue;
      const dom = c.domain || ".tokopedia.com";
      try { jar.setCookieSync(`${c.name}=${c.value}; Domain=${dom}; Path=${c.path || "/"}`, "https://" + dom.replace(/^\./, "") + "/"); } catch (e) {}
    }

    // Silent console: the page's own console.log must NOT pollute stdout (stdout = result JSON only).
    const vc = new VirtualConsole();  // no listeners => swallowed
    const dom = await JSDOM.fromURL(url, {
      cookieJar: jar, runScripts: "dangerously", resources: new LeanLoader({ userAgent: UA }),
      pretendToBeVisual: true, userAgent: UA, virtualConsole: vc,
      beforeParse(w) {
        // Minimal browser-API shims so secsdk runs. Canvas is stubbed (fingerprint not required for
        // a valid s_v_web_id) to avoid the native node-canvas/cairo dependency.
        w.CanvasRenderingContext2D = function () {};
        w.OffscreenCanvas = function () {};
        if (w.HTMLCanvasElement) w.HTMLCanvasElement.prototype.getContext = () => ({
          getImageData: () => ({ data: [] }), fillRect() {}, fillText() {},
          measureText: () => ({ width: 0 }), getContextAttributes: () => ({}), canvas: {},
        });
        w.WebGLRenderingContext = function () {};
        if (!w.performance) w.performance = {};
        ["mark", "measure", "clearMarks", "clearMeasures"].forEach((k) => (w.performance[k] = () => {}));
        w.performance.getEntriesByName = () => [];
        w.performance.getEntriesByType = () => [];
        w.performance.timing = new Proxy({}, { get: () => Date.now() });
        w.performance.now = () => Date.now();
        w.Request = global.Request; w.Response = global.Response; w.Headers = global.Headers;
        const rf = global.fetch;
        w.fetch = (u, o) => { let x = (u && u.url) || u; x = String(x); if (x.startsWith("/")) x = origin + x; return rf(x, o); };
        w.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} });
        w.IntersectionObserver = class { observe() {} unobserve() {} disconnect() {} };
        w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
        w.scrollTo = () => {};
        presentAsChrome(w, UA, input.device);
      },
    });
    // swallow page-script errors (unsupported APIs) — they don't block secsdk's s_v_web_id write
    dom.window.addEventListener("error", () => {});

    // Early-exit poll: secsdk writes s_v_web_id within a few seconds — return as soon as it (and
    // ideally ttwid) is present instead of always waiting the full timeout. Big add/re-auth speedup.
    const deadline = Date.now() + waitMs;
    const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
    while (Date.now() < deadline) {
      out = {};
      for (const c of jar.getCookiesSync(url)) if (WANT.includes(c.key) && c.value) out[c.key] = c.value;
      // s_v_web_id is the only JS-generated cookie curl can't make; exit once it lands (a short grace
      // lets ttwid/msToken piggyback). Missing ttwid is cheaply covered by the curl fallback.
      if (out.s_v_web_id) { await sleep(600); for (const c of jar.getCookiesSync(url)) if (WANT.includes(c.key) && c.value) out[c.key] = c.value; break; }
      await sleep(350);
    }
    try { dom.window.close(); } catch (e) {}
  } catch (e) {
    process.stderr.write("mint error: " + String((e && e.message) || e).slice(0, 200) + "\n");
  }
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
})();
