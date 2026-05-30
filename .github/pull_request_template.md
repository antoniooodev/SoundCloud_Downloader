## Summary


## Type of change

- [ ] docs
- [ ] test
- [ ] fix
- [ ] feat
- [ ] refactor
- [ ] ci

## Scope guard

- [ ] No DRM bypass behavior was added.
- [ ] No protected-stream decryption was added.
- [ ] No token scraping or credential extraction was added.
- [ ] No raw tokens, secrets, stream URLs, manifest URLs, or segment URLs are logged or printed.
- [ ] New network behavior, if any, is gated and tested with mocks.
- [ ] New filesystem writes, if any, are gated and tested inside tmp_path.
- [ ] New ffmpeg behavior, if any, uses the existing runner abstraction and mocked tests.

## Tests

- [ ] `PYTHON=.venv/bin/python make check`
- [ ] `PYTHON=.venv/bin/python scripts/check.sh`

## Security and safety checklist

- [ ] No access tokens, refresh tokens, client secrets, authorization codes, encryption keys, cookies, stream URLs, manifest URLs, or segment URLs are committed.
- [ ] Safety-relevant failures remain explicit and classified.
- [ ] Ambiguous authorization, entitlement, protection, or rights states fail closed.

## Documentation

- [ ] Documentation was updated or no documentation change is needed.
