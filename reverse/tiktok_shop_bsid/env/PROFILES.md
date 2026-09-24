# Page profiles for the Lucifer (unisec) BSID runner

`run.js` boots TikTok's official unisec loader/core in a Node shim. Everything that depends
on the page -- URL, SDK versions, `lucifer.init` config, navigator, screen -- comes from a
profile. With no profile the runner behaves exactly as before (TikTok Shop PDP).

```bash
TIKTOK_BSID_PROFILE=affiliate-id.json node bsid.js < request.json
```

`TIKTOK_BSID_PROFILE` is a file name under `profiles/` or an absolute path.
`TIKTOK_BSID_UA` overrides the profile's user agent (it must equal the `User-Agent`
header, and the UA X-Bogus/X-Gnarly were computed with).

| key | meaning |
| --- | --- |
| `page_url` | page the SDK believes it runs on (`location`, `document.URL`, bs/rt referer) |
| `loader_url`, `core_url` | script URLs the page loads; `core_url` is also served as `js_url_v2` |
| `loader_file`, `core_file` | immutable copies of those scripts, relative to this directory |
| `webmssdk` | also boot the bundled webmssdk (Shop) -- off when the caller signs X-Bogus itself |
| `rt_passthrough` | send the SDK's `/api/v1/bs/rt` to TikTok for a real bs token instead of mocking it |
| `aid`, `lucifer` | `lucifer.init({aid, region, mode, initialPathList})`, read from `lucifer.getConfig()` |
| `navigator`, `screen` | device the SDK fingerprints |

## bsid.js

stdin `{"cookie", "requests": [{"method", "url", "body"}]}` -> stdout
`{"bsids", "bs_token", "cookie"}`. `url` is the final pre-sign URL (msToken, X-Bogus,
X-Gnarly already in place); append `&X-Tts-Oec-Bsid=<bsid>` and send the request with
`oec_lucifer=<bs_token>`. One boot signs any number of requests.

## affiliate-id

`profiles/affiliate-id.json` + `vendor/affiliate-id/` (loader 1.0.0.58, unisec core
1.0.0.85), read from affiliate-id.tokopedia.com on 24 Sep 2026. Verified in Chrome that
affiliate endpoints such as `invitation_group/detail` accept X-Bogus/X-Gnarly from the
pure encoders plus a BSID from `lucifer._s({method, body, url, flag})` over that URL, and
reject any BSID from a different signing. When TikTok ships new unisec versions, update the
two vendor files and the URLs in the profile together.
