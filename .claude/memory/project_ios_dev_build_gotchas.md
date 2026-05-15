---
name: project-ios-dev-build-gotchas
description: "Non-obvious gotchas when rebuilding the Alif iOS EAS dev client and connecting it to the Hetzner dev server — ATS exception, icon source HTMLs, CgBI PNGs, Apple PLA blocks."
metadata: 
  node_type: memory
  type: project
  originSessionId: 1e44f379-78ca-46c7-8a87-654b233124b2
---

The 2026-05-14 rebuild surfaced four issues, three of them silent. Keep these together — they all live on the path "user needs a new dev build for a native module" and all need to be checked in advance.

## 1. iOS ATS blocks plain-HTTP loads to the dev server

**Fact:** The Hetzner Expo dev server at `alifstian.duckdns.org:8081` is plain HTTP. iOS dev clients require `NSAllowsArbitraryLoads: true` in `Info.plist` or the dev client cannot fetch the JS bundle. Symptom: "Failed to connect to http://alifstian.duckdns.org:8081" in the dev launcher. Chrome on the same iPhone *can* reach the URL (Chrome ships its own ATS exception), which is misleading.

**Why:** It's the standard public-internet HTTP block. Expo Go has its own ATS exception built in, so users on Expo Go don't hit this — only custom dev clients.

**How to apply:** `frontend/app.json` → `ios.infoPlist.NSAppTransportSecurity.NSAllowsArbitraryLoads = true` (committed in `6b63ded`). If a future build is missing it and connection fails, that's the first thing to check.

## 2. Icon source HTMLs must be committed, not just generated

**Fact:** Variant H "Framed" (`ألف` in Scheherazade New on `#0f0f1a`, thin rounded inner border) was generated locally on 2026-03-04 via the `icon-generation` skill but the resulting PNGs were never `git add`'d. Every iOS build since the initial commit shipped the gray-with-3-circles Expo placeholder.

**Why:** `git log frontend/assets/icon.png` only shows the initial Feb 8 commit (placeholder) and my 2026-05-14 restoration. The intermediate "working" build referenced the locally-generated file via the user's working tree.

**How to apply:** Source HTMLs are now committed at `frontend/assets/sources/{icon,adaptive-icon}.html` so they can be regenerated identically. If the user reports "icon is missing on home screen", first check whether `frontend/assets/icon.png` is currently a real icon or the placeholder, *before* hypothesising about iOS-side caching or build-pipeline bugs.

## 3. CgBI PNGs in the .ipa look broken to non-Apple tooling

**Fact:** `file /tmp/.../AppIcon60x60@2x.png` reports `PNG image data (CgBI), 120 x 120, 8-bit/color RGBA`. PIL fails to load it with "broken data stream when reading image file". That's not a corruption bug — CgBI is Apple's proprietary BGRA-byteorder PNG variant with non-standard zlib compression, readable by iOS only.

**Why:** Spent meaningful time today thinking the EAS build pipeline was producing corrupt icons and shipped a no-op "fix" before noticing `(CgBI)` in `file` output.

**How to apply:** Don't try to verify iOS app icons with PIL or non-Apple tooling. Use `sips -g pixelWidth ...` or extract via `xcrun assetutil --info Assets.car` instead. If you must look at the icon, render the *source* PNG that went into the build, not the embedded one.

## 4. Apple Program License Agreement (PLA) blocks EAS builds periodically

**Fact:** EAS dev builds fail with `Apple 403 detected - Access forbidden. PLA Update available — You currently don't have access to this membership resource. To resolve this issue, agree to the latest Program License Agreement in your developer account.` This is not an EAS or credential bug — Apple periodically pushes a new agreement that must be accepted at developer.apple.com/account/.

**Why:** Happened 2026-05-14. The first build attempt that session failed with this error; second attempt (after the user accepted the PLA) succeeded.

**How to apply:** If `./scripts/build.sh alif development` fails with "PLA Update available" or "Access forbidden", tell the user to sign in to developer.apple.com/account, accept the banner, and re-run the build. No code/config change required.

See also [[feedback-ats-arbitrary-loads]] and [[feedback-icon-source-control]] if those memories exist; otherwise this file is the primary record.
