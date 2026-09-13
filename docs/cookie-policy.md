---
status: DRAFT — requires review before publication.
last_updated: 2026-09-13
---

# Cookie Policy — Reconcile

> **⚠ DRAFT NOTICE:** This document requires review before publication. No legal advice has been given in its preparation.

---

## 1. Summary

**Reconcile does not use tracking cookies or third-party analytics cookies.**

This policy explains what does and does not load when you use Reconcile.

---

## 2. What we actually load

### 2a. Session state (not cookies)

Reconcile uses **Streamlit's server-side session state**, not browser cookies, to maintain your login session. The JWT access token issued when you sign in is held in server memory for your browser tab session only. It is not written to a cookie, localStorage, or sessionStorage. It is discarded when you close the browser tab or click "Log out".

Streamlit telemetry has been explicitly disabled (`gatherUsageStats = false` in the server configuration).

### 2b. Google Fonts

The application loads two typefaces — **DM Sans** and **Space Mono** — via a CSS `@import` request to `fonts.googleapis.com`. This is a standard HTTP request and results in your IP address being visible to Google's font-serving infrastructure. Google may log this request in accordance with [Google's Privacy Policy](https://policies.google.com/privacy).

**No cookie is set by this font request.** However, IP address is personal data under the DPDP Act.

> The operator is considering self-hosting these fonts in a future version to eliminate this data transfer entirely.

### 2c. Nothing else

There is no:
- Google Analytics, Mixpanel, Hotjar, or other analytics service
- Facebook Pixel or advertising network
- Third-party chat widget
- Third-party embeds or iframes of any kind

---

## 3. Do you need to consent to cookies?

No. Because Reconcile does not set any cookies, no cookie consent banner is required under India's DPDP Act, the EU's ePrivacy Directive, or any other major jurisdiction's cookie rules.

However, if you are deploying Reconcile in a jurisdiction where *any* third-party network request (including font loading) requires consent, you may wish to either:
1. Self-host the fonts, or
2. Add a notice at first login describing the Google Fonts IP log.

---

## 4. Future changes

If analytics, support chat, or advertising functionality is added in future, this policy will be updated and appropriate consent mechanisms will be implemented before deployment.

---

## 5. Contact

For questions about data handling, see the [Privacy Policy](./privacy-policy.md) or contact:

**[OPERATOR NAME]**  
**[CONTACT EMAIL]**

> **[PLACEHOLDER — fill in before publication]**

---

*Effective from the date first published. Draft pending review.*
