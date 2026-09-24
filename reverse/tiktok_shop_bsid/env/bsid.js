"use strict";

// Generic Lucifer BSID runner: one SDK boot, any number of requests.
//
// stdin : {"cookie": "a=b; c=d", "requests": [{"method": "POST", "url": "<pre-sign URL>", "body": "..."}]}
// stdout: {"bsids": ["..."], "bs_token": "<oec_lucifer the SDK signed with>", "cookie": "<cookie jar after boot>"}
//
// The pre-sign URL must already carry msToken, X-Bogus and X-Gnarly/_signature in their
// final spelling: Lucifer signs the URL exactly as it will be sent, then the caller
// appends `&X-Tts-Oec-Bsid=<bsid>`. Pick the page with TIKTOK_BSID_PROFILE.
const fs = require("fs");
const nativeProcess = process;

function fail(message) {
    nativeProcess.stderr.write(`${message}\n`);
    nativeProcess.exit(1);
}

let input;
try {
    input = JSON.parse(fs.readFileSync(0, "utf8"));
} catch {
    fail("invalid signer input");
}
if (typeof input.cookie !== "string") fail("cookie is required");
if (!Array.isArray(input.requests) || !input.requests.length) fail("requests are required");

nativeProcess.env.TIKTOK_BSID_QUIET = nativeProcess.env.TIKTOK_BSID_QUIET || "1";
const runtime = require("./run");

(async () => {
    await runtime.boot({ cookie: input.cookie });
    const sign = runtime.fakeWindow.lucifer && runtime.fakeWindow.lucifer._s;
    if (typeof sign !== "function") throw new Error("Lucifer signer is unavailable");
    // /bs/rt (rt_passthrough) answers asynchronously; wait until the token has landed.
    const deadline = Date.now() + Number(input.token_wait_ms || 8000);
    while (!runtime.fakeWindow.localStorage.getItem("_lcf_v") && Date.now() < deadline) {
        await new Promise(resolve => setTimeout(resolve, 100));
    }
    const bsToken = runtime.fakeWindow.localStorage.getItem("_lcf_v") || "";
    if (!bsToken) throw new Error("no bs token: pass oec_lucifer or enable rt_passthrough");
    const bsids = input.requests.map(request => sign.call(runtime.fakeWindow, {
        method: String(request.method || "GET").toUpperCase(),
        body: request.body == null ? "" : String(request.body),
        url: String(request.url),
        flag: String(request.flag || "xhr"),
    }));
    for (const bsid of bsids) {
        if (!/^[0-9a-f]+$/.test(bsid)) throw new Error("BSID format does not match Chrome");
    }
    nativeProcess.stdout.write(JSON.stringify({ bsids, bs_token: bsToken, cookie: runtime.serializeCookies() }));
    nativeProcess.exit(0);
})().catch(error => fail(error && error.message ? error.message : "BSID generation failed"));
