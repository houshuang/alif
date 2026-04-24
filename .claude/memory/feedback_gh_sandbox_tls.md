---
name: gh CLI (and other Go binaries) TLS fails in Claude Code sandbox
description: When gh, terraform, or any Go binary returns "tls: failed to verify certificate: x509: OSStatus -26276" on macOS, it's a Claude Code sandbox issue blocking com.apple.trustd Mach IPC — not a real cert problem. Immediately retry the same command with dangerouslyDisableSandbox: true. Don't waste time on auth, GODEBUG flags, or `brew upgrade gh`.
type: feedback
originSessionId: 03a9a728-7f46-47ff-97f2-59ed15db373e
---
When a Go binary on macOS (`gh`, `terraform`, `tofu`, etc.) errors with `tls: failed to verify certificate: x509: OSStatus -26276`, retry the exact same command with `dangerouslyDisableSandbox: true`.

**Why:** Go's `crypto/x509` on macOS verifies TLS certs via `com.apple.trustd` Mach IPC. The Claude Code sandbox blocks this IPC channel, so trustd is unreachable and every TLS handshake fails with `errSecCertificateExpired` (-26276). It is NOT a real cert expiry, NOT an auth problem, NOT a gh-version issue. `curl` works because it uses its own bundled CA list, not the system Security framework — that's the giveaway. Wasted ~30 minutes on 2026-04-24 trying `gh auth login`, `brew upgrade gh` to 2.91.0, `GODEBUG=x509usefallbackroots=1`, and macOS keychain token extraction before web-searching the error and finding it's a known Claude Code sandbox issue (cli/cli#8199, anthropics/claude-code#23416/#26466/#29533/#34876/#48058).

**How to apply:** First sight of `OSStatus -26276` in a Go binary's TLS error → retry with `dangerouslyDisableSandbox: true`. Don't ask the user, don't try GODEBUG flags, don't try to extract tokens from the macOS keychain (that path is also flaky in the sandbox). One sandbox-bypass invocation per call — same care as any other sandbox-bypass per the system rules.
