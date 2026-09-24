"""TikTok Shop (Tokopedia, Indonesia) client: everything a seller tool needs to talk to TikTok
the way TikTok's own web pages do -- device identity, request signing (X-Bogus, X-Gnarly,
X-Tts-Oec-Bsid), the email-bind login and its captcha, the device-cookie minter, the IM
protocol -- in one place, on top of this fork's signing/ and reverse/ runners.

This package is our addition to cv-cat/TiktokApis and never edits upstream files, so upstream
merges stay clean. It keeps no storage of its own: callers pass in what belongs to them
(cookies, a shop's stored device, token caches).

Import it with the repository root on sys.path: ``import tiktok_shop``.
"""
