/**
 * TikTok captcha v3 (bdsword, h5 SDK 3.x) crypto oracle — headless jsdom, NO chromium.
 *
 * The get-response `data` blob and the verifyV2 `captchaBody` are encrypted+protobuf inside a
 * bdsword VM; the key derivation is not extractable to Python. So we run the REAL secsdk headless
 * and use it as an oracle. Two ops, one per invocation (stdin JSON):
 *
 *   {"op":"decrypt","data":"<b64 captcha/get data>","config":{...captcha/get query params...}}
 *      -> stdout: the decrypted challenge JSON {id, mode, question:{url1,url2,tip_y,...}, ...}
 *      Drives verifySDK.renderCaptcha with XHR/fetch mocked to return the supplied get-response,
 *      and captures the challenge the SDK decrypts via a JSON.parse hook.
 *
 *   {"op":"encrypt","reply":{...VerifyRequest reply...}}
 *      -> stdout: the base64 `captchaBody` for POST /captcha/verifyV2
 *      Uses the self-contained crypto module (static/js/10.js). The base64 output
 *      is returned as-is (tc\x05 envelope) — no byte rewrite needed.
 *
 * Exit non-zero + stderr on failure so the Python caller can fall back to interactive re-auth.
 */
const { JSDOM } = require('jsdom');
const fs = require('fs');
const path = require('path');

// The SDK itself is not ours: unmodified CDN copies in the pinned TiktokApis submodule
// (reverse/oec_captcha/vendor/<version>). Every input names the version ("sdk", e.g.
// "3.0.75-1.0.0.956", from tiktok_shop/constants.py CAPTCHA_V2_SDK) and the page the SDK runs on
// ("page_url"); nothing TikTok-specific is defaulted here.
let SDK_DIR = '';
let JSONP = '';
function useSdk(version) {
  if (!/^[\w.]+-[\w.]+$/.test(version || '')) throw new Error('input.sdk must be "<h5_sdk_version>-<sdk_version>"');
  const [h5, sdk] = version.split('-');
  SDK_DIR = path.join(__dirname, '..', '..', 'reverse', 'oec_captcha', 'vendor', version);
  JSONP = `bytedance_secsdk_captcha_jsonp_${h5}_${sdk}`;
}
const readAsset = (f) => fs.readFileSync(path.join(SDK_DIR, f), 'utf8');

function makeWindow(url) {
  const dom = new JSDOM('<!DOCTYPE html><html><body><div id="captcha-container"></div></body></html>', {
    url, runScripts: 'dangerously', pretendToBeVisual: true,
  });
  const window = dom.window;
  window.crypto = require('crypto').webcrypto;
  // jsdom has no canvas; the SDK only needs it for device fingerprinting (non-fatal for crypto).
  const fakeCtx = new Proxy({}, { get: (t, p) => {
    if (p === 'canvas') return { width: 300, height: 150 };
    if (p === 'getImageData') return () => ({ data: new Uint8ClampedArray(4) });
    if (p === 'measureText') return () => ({ width: 10 });
    if (p === 'getParameter') return () => 'stub';
    if (p === 'getExtension') return () => null;
    if (p === 'getSupportedExtensions') return () => [];
    return () => {};
  }});
  window.HTMLCanvasElement.prototype.getContext = () => fakeCtx;
  window.HTMLCanvasElement.prototype.toDataURL = () => 'data:image/png;base64,';
  window.requestAnimationFrame = (cb) => setTimeout(() => cb(Date.now()), 0);
  return window;
}

function loadCrypto(window) {
  // static/js/10.js is a self-contained webpack; module 1131 -> {encrypt}. No SDK init needed.
  window.eval(readAsset('static/js/10.js'));
  const modules = window[JSONP].find((x) => String(x[0]) === '10')[1];
  const req = (id) => { const m = { exports: {} }; modules[id].call(window, m, m.exports, req); return m.exports; };
  return req(1131);
}

async function opEncrypt(input) {
  const window = makeWindow(input.page_url);

  // Expose module table on window.__modules so we can access TransformClass (module 2465)
  let mainJs = readAsset('captcha.js');
  mainJs = mainJs.replace('n.B[2171]={v:function(){return un(3512,n,this,arguments,0,20)}}', 'window.__modules = n.B; n.B[2171]={v:function(){return un(3512,n,this,arguments,0,20)}}');
  mainJs = mainJs.replace('tn(A,r,on(A,t)[on(A,i)])', 'tn(A,r,(on(A,t)??{})[on(A,i)])');
  window.eval(mainJs);

  const enc = loadCrypto(window);

  const challenge = input.challenge || (input.reply && typeof input.reply === 'object' && !Array.isArray(input.reply) ? input.reply : {});
  const rawReply = Array.isArray(input.reply) ? input.reply : ((input.reply && input.reply.reply) || []);

  const now = Date.now();
  const init_time = challenge.uuid || input.uuid || (now - (input.duration || 850) - 1500);
  const totalMs = (rawReply && rawReply.length > 0 && rawReply[rawReply.length - 1].relative_time) || input.duration || 850;
  // finish_time is right now (user just finished slider drag ~40ms before verify request is dispatched)
  const finish_time = now - Math.floor(Math.random() * 20 + 30);
  const drag_start_time = finish_time - totalMs;
  const ugt = finish_time - init_time;

  // 1. Transform coordinates using ByteDance VM TransformClass (applies challenge.id salt/hash offset)
  let transformedReply = JSON.parse(JSON.stringify(rawReply));
  if (window.__modules && window.__modules[2465] && challenge.id) {
    const dummyProps = {
      challenge_id: challenge.id,
      mode: challenge.mode || 'slide',
      type: challenge.mode || 'slide',
      challenge_code: String(challenge.challenge_code || 99999),
      // The build the live page serves, passed down from the bdturing conf's
      // extension.setting_version ("<h5>-<sdk>"). Pinning it drifts: the affiliate page
      // loads .../captcha/sg/3.0.75/1.0.0.956/captcha.js, and signing a captchaBody with
      // a build the server no longer knows is answered 504 "unexpected error [5013]".
      h5_sdk_version: input.h5_sdk_version || input.sdk.split('-')[0],
      sdk_version: input.sdk_version || input.sdk.split('-')[1],
      start_time: init_time,
      drag_width: input.drag_width || 271,
      modified_img_width: input.modified_img_width || 340,
      update() {},
      setReply(arg) { transformedReply = arg; }
    };
    try {
      const TransformCls = window.__modules[2465].v;
      const inst = new TransformCls(dummyProps, transformedReply);
      inst.transform();
    } catch (e) {
      process.stderr.write('TransformClass failed: ' + (e && e.message) + '\n');
    }
  }

  // 2. Synthesize realistic dense mouse movements (mm, mp, mc, mu) and timing
  const originX = 560 + Math.floor(Math.random() * 15);
  const originY = 530 + Math.floor(Math.random() * 15);

  const mm = [];
  const mp = [];

  // Pre-drag approach to the slider button (~20 steps ending at drag_start_time)
  const preSteps = 20;
  const approachStart = Math.max(init_time + 200, drag_start_time - 350 - Math.floor(Math.random() * 150));
  const approachDuration = drag_start_time - approachStart;
  const startApproachX = originX - 80 + Math.floor(Math.random() * 30);
  const startApproachY = originY - 120 + Math.floor(Math.random() * 40);
  for (let i = 0; i < preSteps; i++) {
    const p = i / (preSteps - 1);
    const ease = p * p * (3 - 2 * p);
    const t = approachStart + Math.round(p * approachDuration);
    const px = Math.round(startApproachX + (originX - startApproachX) * ease + (Math.random() - 0.5) * 1.5);
    const py = Math.round(startApproachY + (originY - startApproachY) * ease + (Math.random() - 0.5) * 1.5);
    mm.push({ x: px, y: py, time: t });
    mp.push({ x: px, y: py, time: t });
  }

  // Mouse click at slider origin exactly when drag starts
  const mc = [{ x: originX, y: originY, time: drag_start_time }];

  // Dense drag movement corresponding to trajectory: sample every ~10ms
  const totalTrajectorySteps = rawReply.length;
  if (totalTrajectorySteps > 0) {
    const denseStepCount = Math.max(70, Math.round(totalMs / 10));
    for (let s = 0; s < denseStepCount; s++) {
      const curRelMs = Math.round((s / (denseStepCount - 1)) * totalMs);
      const t = drag_start_time + curRelMs;
      // Interpolate x and y from rawReply
      let ptBefore = rawReply[0];
      let ptAfter = rawReply[totalTrajectorySteps - 1];
      for (let j = 0; j < totalTrajectorySteps - 1; j++) {
        if (rawReply[j].relative_time <= curRelMs && rawReply[j + 1].relative_time >= curRelMs) {
          ptBefore = rawReply[j];
          ptAfter = rawReply[j + 1];
          break;
        }
      }
      const span = Math.max(1, (ptAfter.relative_time || 0) - (ptBefore.relative_time || 0));
      const ratio = Math.max(0, Math.min(1, (curRelMs - (ptBefore.relative_time || 0)) / span));
      const interpX = ptBefore.x + (ptAfter.x - ptBefore.x) * ratio;
      const interpY = (ptBefore.y || 0) + ((ptAfter.y || 0) - (ptBefore.y || 0)) * ratio;

      const mx = originX + Math.round(interpX);
      const my = originY + Math.round(interpY + Math.sin(s / 4) * 1.2);
      mm.push({ x: mx, y: my, time: t });
      mp.push({ x: mx, y: my, time: t });
    }
  }

  // Mouse up upon reaching destination exactly at finish_time
  const lastPt = rawReply[rawReply.length - 1] || { x: 0, y: 0 };
  const endX = originX + Math.round(lastPt.x);
  const endY = originY + Math.round(lastPt.y || 0);
  const mu = [{ x: endX, y: endY, time: finish_time }];

  // Post-drag settle movement (~5 steps within 25ms after finish_time)
  const postSteps = 5;
  for (let i = 1; i <= postSteps; i++) {
    const t = finish_time + i * 5;
    const px = endX + Math.round((Math.random() - 0.5) * 1.5);
    const py = endY + Math.round((Math.random() - 0.5) * 1.5);
    mm.push({ x: px, y: py, time: t });
    mp.push({ x: px, y: py, time: t });
  }

  // 3. Construct full payload matching ByteDance captcha.js
  const payload = {
    id: challenge.id || '',
    mode: challenge.mode || 'slide',
    drag_width: input.drag_width || 271,
    modified_img_width: input.modified_img_width || 340,
    reply: transformedReply,
    ir: challenge.uuid || input.uuid || init_time,
    hk: 0,
    lo: {
      referer: input.referer || input.page_url,
      location: input.location || input.page_url,
      topref: input.location || input.page_url,
      frame: 0
    },
    br: input.ua,
    bt: { status: 0 },
    si: {
      bodyClientHeight: 900,
      screenWidth: 1536,
      innerHeight: 900,
      screenHeight: 960,
      screenLeft: 0,
      outerWidth: 1536,
      clientHeight: 900,
      innerWidth: 1440,
      bodyClientWidth: 1440,
      availWidth: 1536,
      clientWidth: 1440,
      outerHeight: 900,
      docClientWidth: 1440,
      screenTop: 0,
      availHeight: 960,
      docClientHeight: 900
    },
    vci: {
      renderer: 'ANGLE (Apple, ANGLE Metal Renderer: Apple M1 Pro, Version 15.0 (Build 24A348))',
      vendor: 'Google Inc. (Apple)',
      isWebglSupport: 1
    },
    cc: { ndi: 0, debugInfo: '' },
    fi: [],
    mc: mc,
    mu: mu,
    tc: [],
    tu: [],
    mm: mm,
    ugt: ugt,
    tmv: [],
    mp: mp,
    gy: [],
    gy2: [],
    tql: -1,
    cp: { p: 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855', c: '' },
    hash: '',
    tk: '',
    tm: init_time,
    detRes: 512
  };

  const b64 = await enc.encrypt(payload, 'Base64');
  // The SDK sends enc.encrypt() output as-is (tc\x05 envelope). No byte rewrite needed.
  // captcha.js passes base64 directly into {captchaBody: a} without modification.
  // Previous tc\x05->tc\x06 rewrite was incorrect and caused verifyV2 504 [5013] rejection.
  return b64;
}

function opDecrypt(data, config, pageUrl) {
  return new Promise((resolve, reject) => {
    const window = makeWindow(pageUrl);
    const getRespBody = JSON.stringify({ code: 200, data, message: 'Verification complete' });

    const captured = [];
    const origParse = window.JSON.parse;
    window.JSON.parse = function (s) {
      const o = origParse.apply(this, arguments);
      try {
        if (o && typeof o === 'object' && (o.challenges || o.question || o.verify_id || (o.id && o.mode))) {
          if (!o.uuid) o.uuid = Date.now();
          captured.push(o);
        }
      } catch (e) { /* ignore */ }
      return o;
    };

    const isGet = (u) => /captcha\/get/.test(String(u));
    function MockXHR() { this._h = {}; }
    MockXHR.prototype.open = function (m, u) { this._m = m; this._u = u; };
    MockXHR.prototype.setRequestHeader = function (k, v) { this._h[k] = v; };
    MockXHR.prototype.getAllResponseHeaders = () => 'content-type: application/json';
    MockXHR.prototype.getResponseHeader = () => 'application/json';
    MockXHR.prototype.send = function () {
      const self = this;
      const resp = isGet(this._u) ? getRespBody : '{"code":200,"data":{},"message":"ok"}';
      setTimeout(() => {
        self.status = 200; self.readyState = 4; self.response = resp; self.responseText = resp;
        if (self.onreadystatechange) self.onreadystatechange();
        if (self.onload) self.onload({ target: self });
      }, 0);
    };
    window.XMLHttpRequest = MockXHR;
    window.fetch = (u) => isGet(u)
      ? Promise.resolve({ status: 200, ok: true, headers: { get: () => 'application/json' },
          json: () => Promise.resolve(JSON.parse(getRespBody)), text: () => Promise.resolve(getRespBody) })
      : Promise.resolve({ status: 200, ok: true, headers: { get: () => null },
          json: () => Promise.resolve({ code: 200, data: {} }), text: () => Promise.resolve('{}') });

    try {
      window.eval(readAsset('captcha.js'));
      ['static/js/6.js', 'static/js/7.js', 'static/js/8.js', 'static/js/9.js', 'static/js/10.js']
        .forEach((f) => window.eval(readAsset(f)));
      const cfg = Object.assign({
        iid: '0',
        did: '0',
        device_id: '0',
        ch: 'web_text',
      }, config, {
        aid: parseInt(config.aid, 10),
        os_type: parseInt(config.os_type || '2', 10),
        webdriver: false,
        mode: config.subtype || config.mode,
        challenge_code: parseInt(config.challenge_code, 10) || undefined,
        container: window.document.getElementById('captcha-container'),
        successCb: () => {},
        showMode: 'embed',
      });
      const rc = window.verifySDK.renderCaptcha(cfg);
      if (rc && rc.catch) rc.catch(() => {});
    } catch (e) { /* the challenge may already be captured before a later render error */ }

    setTimeout(() => {
      if (captured.length) resolve(captured[captured.length - 1]);
      else reject(new Error('no challenge captured (decrypt failed)'));
    }, 4000);
  });
}

async function main() {
  const input = JSON.parse(fs.readFileSync(0, 'utf-8'));
  useSdk(input.sdk);
  if (!input.page_url) throw new Error('input.page_url is required');
  let out;
  if (input.op === 'encrypt') out = await opEncrypt(input);
  else if (input.op === 'decrypt') out = JSON.stringify(await opDecrypt(input.data, input.config, input.page_url));
  else throw new Error('unknown op: ' + input.op);
  process.stdout.write(out);
}

main().then(() => process.exit(0)).catch((err) => {
  process.stderr.write('captcha_oracle error: ' + (err && err.message) + '\n');
  process.exit(1);
});
