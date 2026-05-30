# Security

## Reporting a vulnerability

Please report suspected vulnerabilities privately to the maintainer rather
than opening a public issue or pull request. Include the smallest possible
reproduction. Do not submit real OAuth tokens, refresh tokens, client
secrets, Fernet keys, or session cookies in reports.

## Out of scope

The following are explicitly out of scope for this project and will not be
accepted as features, patches, or "bug fixes":

- DRM bypass or decryption of protected streams.
- Widevine, FairPlay, PlayReady, or license-server circumvention.
- Token extraction, cookie theft, credential theft, or session hijacking.
- Use of stolen, leaked, or extracted credentials.
- Rate-limit evasion or abusive scraping.

This project is designed to fail closed for DRM-protected or encrypted
streams. That behavior is intentional and is not a bug.
