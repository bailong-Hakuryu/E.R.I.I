# TypeScript Client Development

The package is a server-side adapter to the Python reference REST service. The
FastAPI implementation in `erii/server/app.py` is the contract authority.

## Local checks

```bash
npm ci
npm run lint
npm run build
npm test -- --coverage
```

From the repository root, with E.R.I.I. server dependencies installed:

```bash
python clients/typescript/scripts/verify_server_contract.py
```

The contract verifier reads the live FastAPI schema and checks authentication
middleware with `TestClient`. When changing a route or model, update the Python
server, SDK implementation, SDK tests, verifier, and README in the same change.

## Layout

```text
clients/typescript/
|-- scripts/verify_server_contract.py
|-- src/index.ts
|-- src/index.test.ts
|-- package.json
|-- package-lock.json
|-- tsconfig.json
`-- README.md
```

`dist`, `coverage`, `node_modules`, and `.npm-cache` are generated locally and
must remain untracked. `package-lock.json` is committed so CI and contributors
resolve the same dependency graph.

## Security boundary

The reference server key represents the project owner, not an end user. Tests
and examples must not put it into frontend environment variables or browser
code. Use a trusted host process and keep its public-user authorization boundary
separate from E.R.I.I.'s owner-key boundary.

## Publishing

Publishing is an explicit maintainer decision. CI validates and packs nothing
to npm automatically. Follow `PUBLISHING.md` only after the package version and
Python server version have been reconciled.
