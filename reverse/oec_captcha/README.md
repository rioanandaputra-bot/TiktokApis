# OEC captcha SDK (immutable copies)

Official TikTok Shop / Affiliate captcha SDK ("oec-captcha-ttweb", bdsword, h5 SDK), copied
byte for byte from the CDN so a local Node/jsdom runner can decrypt `/captcha/get` challenges
and encrypt `/captcha/verifyV2` bodies without a browser. Nothing here is edited; each
directory mirrors the CDN path layout of one version:

```text
vendor/<h5_sdk_version>-<sdk_version>/captcha.js          <- .../captcha/sg/<h5>/<sdk>/captcha.js
vendor/<h5_sdk_version>-<sdk_version>/static/js/<n>.js    <- .../captcha/sg/<h5>/<sdk>/static/js/<n>.js
```

CDN base: `https://sf16-website-login.neutral.ttwstatic.com/obj/tiktok_web_login_static/oec-captcha-ttweb/captcha/sg/`

| version | captcha.js md5 | read |
| --- | --- | --- |
| 3.0.75-1.0.0.956 | 24ce4a0b128f3843f14864f27aeb1000 | loaded by affiliate-id.tokopedia.com, 24 Sep 2026 |

When TikTok ships a new version (its `setting_version` in the bdturing decision conf), add a
new directory from the CDN rather than editing an existing one, then point
`CAPTCHA_V2_SDK` in `tiktok_shop_constants.py` at it.
