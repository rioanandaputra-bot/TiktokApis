"use strict";

// Generic Lucifer BSID runner.
//
// One-shot (default): one SDK boot for one cookie session, any number of requests.
//   stdin : {"cookie": "a=b; c=d", "requests": [{"method": "POST", "url": "<pre-sign URL>", "body": "..."}]}
//   stdout: {"bsids": ["..."], "bs_token": "<oec_lucifer the SDK signed with>", "cookie": "<cookie jar after boot>"}
//   With no oec_lucifer in the cookie and rt_passthrough on, the SDK fetches one from /bs/rt.
//
// --serve: boot once, then sign for any session, one JSON object per line.
//   in : {"id": 1, "cookie": "...; oec_lucifer=<token>", "navigator": {...}?, "ua": "<optional>", "requests": [...]}
//   out: {"id": 1, "bsids": [...]}  or  {"id": 1, "error": "..."}
//   The token must be in the cookie (get it with a one-shot run); ~2 ms per BSID.
//
// "navigator" (both modes) overrides the profile's device for that session: userAgent,
// platform, language, languages, hardwareConcurrency, deviceMemory, maxTouchPoints and
// "screen": {width, height, availWidth, availHeight}. Omitted keys keep the profile's.
//
// The pre-sign URL must already carry msToken, X-Bogus and X-Gnarly/_signature in their
// final spelling: Lucifer signs the URL exactly as it will be sent, then the caller
// appends `&X-Tts-Oec-Bsid=<bsid>`. Pick the page with TIKTOK_BSID_PROFILE.
const fs = require("fs");
const readline = require("readline");
const nativeProcess = process;
const serve = nativeProcess.argv.includes("--serve");

function fail(message) {
    nativeProcess.stderr.write(`${message}\n`);
    nativeProcess.exit(1);
}

nativeProcess.env.TIKTOK_BSID_QUIET = nativeProcess.env.TIKTOK_BSID_QUIET || "1";
if (serve) nativeProcess.env.TIKTOK_BSID_RT_PASSTHROUGH = "0";

let input;
if (!serve) {
    try {
        input = JSON.parse(fs.readFileSync(0, "utf8"));
    } catch {
        fail("invalid signer input");
    }
    if (typeof input.cookie !== "string") fail("cookie is required");
    if (!Array.isArray(input.requests) || !input.requests.length) fail("requests are required");
}

const runtime = require("./run");

function signAll(requests) {
    const sign = runtime.fakeWindow.lucifer && runtime.fakeWindow.lucifer._s;
    if (typeof sign !== "function") throw new Error("Lucifer signer is unavailable");
    return requests.map(request => {
        const bsid = sign.call(runtime.fakeWindow, {
            method: String(request.method || "GET").toUpperCase(),
            body: request.body == null ? "" : String(request.body),
            url: String(request.url),
            flag: String(request.flag || "xhr"),
        });
        if (!/^[0-9a-f]+$/.test(bsid)) throw new Error("BSID format does not match Chrome");
        return bsid;
    });
}

const baseNavigator = { ...runtime.PROFILE.navigator };
const baseScreen = { ...runtime.PROFILE.screen };

// The runtime's getters read PROFILE on every access, so swapping it per session is enough.
function useDevice(navigator, ua) {
    const { screen, ...fields } = (navigator && typeof navigator === "object") ? navigator : {};
    runtime.PROFILE.navigator = { ...baseNavigator, ...fields };
    if (ua) runtime.PROFILE.navigator.userAgent = String(ua);
    runtime.PROFILE.screen = { ...baseScreen, ...((screen && typeof screen === "object") ? screen : {}) };
}

function cookieValue(cookie, name) {
    const match = String(cookie || "").match(new RegExp(`(?:^|;\\s*)${name}=([^;]+)`));
    return match ? match[1] : "";
}

async function oneShot() {
    useDevice(input.navigator);
    await runtime.boot({ cookie: input.cookie });
    // /bs/rt (rt_passthrough) answers asynchronously; wait until the token has landed.
    const deadline = Date.now() + Number(input.token_wait_ms || 8000);
    while (!runtime.fakeWindow.localStorage.getItem("_lcf_v") && Date.now() < deadline) {
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    const bsToken = runtime.fakeWindow.localStorage.getItem("_lcf_v") || "";
    if (!bsToken) throw new Error("no bs token: pass oec_lucifer or enable rt_passthrough");
    const bsids = signAll(input.requests);
    nativeProcess.stdout.write(JSON.stringify({ bsids, bs_token: bsToken, cookie: runtime.serializeCookies() }));
    nativeProcess.exit(0);
}

async function serveLines() {
    await runtime.boot({ cookie: "" });
    const lines = readline.createInterface({ input: nativeProcess.stdin });
    nativeProcess.stdout.write(JSON.stringify({ ready: true }) + "\n");
    for await (const line of lines) {
        if (!line.trim()) continue;
        let message = {};
        try {
            message = JSON.parse(line);
            const token = cookieValue(message.cookie, "oec_lucifer");
            if (!/^[0-9a-f]{160,}$/i.test(token)) throw new Error("oec_lucifer token missing from cookie");
            runtime.seedCookies(message.cookie);
            runtime.fakeWindow.localStorage.setItem("_lcf_v", token);
            useDevice(message.navigator, message.ua);
            nativeProcess.stdout.write(JSON.stringify({ id: message.id, bsids: signAll(message.requests || []) }) + "\n");
        } catch (error) {
            nativeProcess.stdout.write(JSON.stringify({ id: message.id, error: String(error && error.message || error) }) + "\n");
        }
    }
    nativeProcess.exit(0);
}

(serve ? serveLines() : oneShot()).catch(error => fail(error && error.message ? error.message : "BSID generation failed"));
