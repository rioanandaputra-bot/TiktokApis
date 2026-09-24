"""TikTok Shop (Tokopedia, Indonesia) values: the one place they are defined.

Everything a client needs to look like TikTok's own web pages -- hosts, app ids, SDK and build
versions, signing modes, salts, the page profiles the in-process SDK runners boot -- lives
here, for this fork's own additions (signing/lucifer_bsid.py, reverse/tiktok_shop_bsid,
tiktok_shop) and for every consumer of the fork (GrowSeller imports
`tiktok_shop`). Nothing TikTok-specific is read from environment variables:
change a value here, commit, and move the submodule pin.

Values owned by upstream cv-cat/TiktokApis (the X-Bogus/X-Gnarly algorithm internals in
signing/pure.py, api/*) are not copied here, so upstream updates merge cleanly; update those
by pulling upstream.

How to read the "Source" / "Update" notes
-----------------------------------------
Most values are not documented by TikTok; they are what the live pages send. Each group says
where the value was read, when it was last verified, and how to read it again. The general
procedure, in Chrome logged in to a Tokopedia seller account:

  1. Open the page named in the note (DevTools > Network, "Preserve log" on).
  2. Find the request named in the note and read the value from its query string, headers
     or body -- or, for bundle constants, search the page's scripts:
       DevTools > Sources > Ctrl/Cmd+Shift+F, search the key (e.g. `sdkVersion:`).
  3. Change the value here, update the "verified" date, and run the consumer's tests plus one
     live call of the flow that uses it.

A version bump of a script TikTok serves (unisec loader/core, captcha SDK) also needs a new
immutable copy under reverse/ -- see the note on that value.
"""

# ============================================================================ region
# GrowSeller serves Indonesian sellers only. Source: the shop_region / oec_region query
# params and x-tt-oec-region header on affiliate-id.tokopedia.com (verified 24 Sep 2026).
REGION = "ID"
LANGUAGE = "id"
LOCALE = "id-ID"
TIMEZONE = "Asia/Jakarta"

# ============================================================================ hosts
# Source: the address bar and request hosts of each page (all in use on 24 Sep 2026).
# Update: they only change if TikTok migrates the Indonesian market; re-read the hosts in
# DevTools > Network on the seller center, affiliate center, login and chat pages.
SELLER = "https://seller-id.tokopedia.com"              # Seller Center + passport (activation)
AFFILIATE = "https://affiliate-id.tokopedia.com"        # Affiliate Center (creators, invitations)
AFFILIATE_API = f"{AFFILIATE}/api/v1"
SSO = "https://business-sso-id.tokopedia.com"           # login (account_login/v3, check_login)
IM_API = "https://oec-im-tt-sg.tiktokglobalshopv.com"   # chat REST (seller/im page, /v1/* and /api/*)
FRONTIER_WS = "wss://frontier.byteoversea.com/ws/v2"    # chat push websocket (seller/im page)
TTWID_HOST = "https://ttwid-sg.byteoversea.com"         # ttwid/union/register (login page)
STATIC_CDN = "https://sf16-website-login.neutral.ttwstatic.com/obj/tiktok_web_login_static"
# Cookie domains: session cookies (msToken, ttwid, odin_tt) are set on the parent domain,
# Affiliate Center ones (s_v_web_id, oec_lucifer, user_oec_info) on its host.
COOKIE_DOMAIN = ".tokopedia.com"
AFFILIATE_DOMAIN = "affiliate-id.tokopedia.com"
SELLER_DOMAIN = "seller-id.tokopedia.com"
SSO_DOMAIN = "business-sso-id.tokopedia.com"

# Pages GrowSeller presents as referer / boots SDKs on.
SELLER_LOGIN_PAGE = f"{SELLER}/account/login"
SELLER_ACTIVATE_PAGE = f"{SELLER}/profile/activate-page"
AFFILIATE_IM_PAGE = f"{AFFILIATE}/seller/im"
AFFILIATE_MARKETPLACE_PAGE = f"{AFFILIATE}/connection/creator-marketplace"
AFFILIATE_CREATOR_PAGE = f"{AFFILIATE}/connection/creator"
# The SSO login service the seller center redirects to (login page, `service` param).
SSO_SERVICE = f"{SELLER}/homepage?lng={LOCALE}&region_check=1"
SSO_LOGIN_PAGE = f"{SSO}/login/?service=https%3A%2F%2Fseller-id.tokopedia.com%2Fhomepage"

# ============================================================================ app ids
# Source: `aid` / `app_name` query params. Seller center + passport send 4068, the
# Affiliate Center 4331. x-mcs-appkey: header on Affiliate Center API calls. All in use and
# accepted on 24 Sep 2026 (not re-read that day). Update: DevTools > Network on the page,
# any /api/ request.
AID_SELLER = "4068"
AID_AFFILIATE = "4331"
APP_NAME_SELLER = "i18n_ecom_shop"
APP_NAME_AFFILIATE = "i18n_ecom_alliance"
MCS_APP_KEY = "566f58151b0ed37e"

# ============================================================================ passport SDK
# `sdk_version` the passport SDK adds to its requests. Source: the page bundles, searched for
# `account_sdk_source` (verified in Chrome 24 Sep 2026):
#   activate-page: account.<hash>.js sends "2.1.3-tiktokbeta.1" on invitation/check|accept;
#   login page:    passport SDK sends "2.1.3-tiktokbeta.2" on account/info/v2.
# Update: open SELLER_ACTIVATE_PAGE / SELLER_LOGIN_PAGE, search the scripts for
# `sdk_version:"` next to `account_sdk_source`.
PASSPORT_SDK_ACTIVATE = "2.1.3-tiktokbeta.1"
PASSPORT_SDK_LOGIN = "2.1.3-tiktokbeta.2"
# Login form `ect_type` (account_login/v3 on the login page; accepted 24 Sep 2026).
LOGIN_ECT_TYPE = "13"

# ============================================================================ signing
# Endpoint-class byte X-Bogus (version[3]) and X-Gnarly (field 2) carry. Source: decoding the
# X-Bogus/X-Gnarly of an Affiliate Center request with signing/pure.py (Chrome sent 12 on
# 24 Sep 2026; 14 was accepted by the same endpoints). Update: decode a fresh browser
# signature; only change if 14 starts being refused.
SIGN_MODE = 14

# ============================================================================ BSID (unisec / Lucifer)
# The page profile reverse/tiktok_shop_bsid/env/run.js boots TikTok's unisec SDK with, to
# sign X-Tts-Oec-Bsid. Source: on the page, `lucifer.getConfig()` in the console and the
# unisec loader/core script URLs in DevTools > Network (verified 24 Sep 2026).
# Update when TikTok ships new unisec versions (the core URL's version changes):
#   1. copy the new loader/core byte for byte into reverse/tiktok_shop_bsid/env/vendor/<profile>/
#      (new file names if you want to keep the old ones);
#   2. change loader_url/core_url/loader_file/core_file and the `lucifer` block below;
#   3. verify one signed Affiliate Center call (e.g. invitation_group/detail) is accepted.
BSID_PROFILE = "affiliate-id"
BSID_PROFILES = {
    "affiliate-id": {
        "page_url": f"{AFFILIATE}/affiliate/collaboration/target-invitation?shop_region={REGION}&tab=1",
        "loader_url": f"{STATIC_CDN}/oec/unisec/web/loader/1.0.0.58/sg/index.js",
        "core_url": f"{STATIC_CDN}/oec/unisec/web/1.0.0.85/sg/index.js",
        "loader_file": "vendor/affiliate-id/loader.js",
        "core_file": "vendor/affiliate-id/core.js",
        "webmssdk": False,        # X-Bogus/X-Gnarly come from signing/pure.py
        "rt_passthrough": True,   # mint the bs token (oec_lucifer) from TikTok's /bs/rt
        "aid": int(AID_AFFILIATE),
        "lucifer": {
            "region": "sg",
            "mode": 513,
            "initialPathList": ["^/captcha/get", "^/captcha/verifyV2", "^/captcha/feedbackV2",
                                "^/api/.*", "^/api_sens/.*", "^/widget/api/.*"],
        },
        "navigator": {
            "userAgent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36",
            "platform": "MacIntel", "language": "en-US", "languages": ["en-US", "en", "id"],
            "hardwareConcurrency": 8, "deviceMemory": 8, "maxTouchPoints": 0,
        },
        "screen": {"width": 1536, "height": 960, "availWidth": 1536, "availHeight": 867},
    },
}

# ============================================================================ captcha
# Login (seller center account_login/v3): the 2.x SDK on verify-sg -- plain JSON
# /captcha/get + /captcha/verify, no msToken or X-Bogus/X-Gnarly on either call.
# Source: a real sub-account login in Chrome on 24 Sep 2026 (3d challenge, "Verification
# complete"): the query keys and their order, h5_sdk_version / h5_check_version, and the
# verify body's modified_img_width (the width the image was drawn at).
# Update: log in to SELLER_LOGIN_PAGE with a sub-account until the captcha appears; in
# DevTools > Network read the /captcha/get and /captcha/verify requests.
CAPTCHA_LOGIN_HOST = "https://verify-sg.byteoversea.com"
CAPTCHA_LOGIN_H5_SDK = "2.33.7"
CAPTCHA_LOGIN_CHECK_VERSION = "3.8.21-alpha.2"
CAPTCHA_LOGIN_APP_NAME = "TikTokAds_SSO"
CAPTCHA_LOGIN_IMG_W = 348
#
# The Affiliate Center answers its own bdturing challenges through the 3.x h5 SDK
# ("oec-captcha-ttweb", encrypted /captcha/verifyV2). tiktok_shop has no solver for it: every
# such challenge traced so far came from a request we had built wrong (a frozen BSID, a
# signature the page does not send), and the headless slide solver never once passed. The
# SDK copies and the oracle live in history (fork commit 1753ee1, reverse/oec_captcha) for
# when a challenge is shown to the user to solve instead.
#
# challenge_code the SDK sends per subtype before a challenge names its own.
CAPTCHA_CHALLENGE_CODE = {"slide": "99999", "3d": "99997"}

# ============================================================================ chat (IM SDK)
# Source: the seller/im page's IM web SDK bundle (929.<hash>.js), searched for
# `sdkVersion:` -> {sdkVersion:"1.2.16", buildNumber:`6eae7a1:master`,
# wsProtocols:["binary","base64","pbbp2"]} (verified in Chrome 24 Sep 2026).
# The Frontier access-key salt is the constant in the same SDK's
# md5(fp_id + app_key + device_id + salt); version_code is the handshake's. Both accepted
# on 24 Sep 2026 (not re-read that day).
# Update: open AFFILIATE_IM_PAGE, search the scripts for `sdkVersion:"`, `buildNumber` and
# the access-key md5 (salt), and the websocket URL in DevTools > Network > WS (version_code).
IM_SDK_VERSION = "1.2.16"
IM_BUILD_NUMBER = "6eae7a1:master"
FRONTIER_SUBPROTOCOLS = ["binary", "base64", "pbbp2"]
FRONTIER_ACCESS_KEY_SALT = "f8a69f1719916z"
FRONTIER_VERSION_CODE = "10000"
FRONTIER_ORIGIN = AFFILIATE
# IM app id / fp id the web SDK sends when an IM token names none (seller/im HAR; the token
# from /seller/im/get/token normally carries both).
IM_APP_ID = "380360"
IM_FP_ID = 448

# ============================================================================ device
# The identity every GrowSeller shop presented before per-shop devices (Mac, Chrome 131) --
# shops bound before then keep it, so these never change. Source: GrowSeller history.
LEGACY_USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
LEGACY_SEC_CH_UA = '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"'
LEGACY_IMPERSONATE = "chrome131"

# ============================================================================ device cookie mint
# Page the headless secsdk device minter (s_v_web_id, msToken) loads, and how long it waits
# for the SDK to set them. Any Affiliate Center page that loads webmssdk (the s_v_web_id
# generator) works; /seller/im loads ~11 scripts against the creator-marketplace SPA's ~42, so
# secsdk runs sooner (GrowSeller production: ~3.1-3.6 s per mint with s_v_web_id set).
# Update: if mints start timing out, try another Affiliate Center page and compare the
# minter's logged duration ("[device_mint]" lines in the API/worker logs).
SECSDK_MINT_URL = f"{AFFILIATE_IM_PAGE}?shop_region={REGION}"
SECSDK_MINT_WAIT_MS = 14000
