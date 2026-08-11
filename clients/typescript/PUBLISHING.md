# Publishing to npm

This document is a maintainer checklist, not an instruction for CI. The
TypeScript client is not published automatically. Do not publish it merely
because a repository commit or Python prerelease exists.

## Prerequisites

1. npm account with 2FA enabled
2. Organization scope `@erii` created on npm
3. Collaborator access to `@erii` scope

## First-time Setup

```bash
# Login to NPM
npm login

# Verify login
npm whoami
```

## Publishing Steps

### 1. Verify Package

```bash
cd clients/typescript

# Install the committed dependency graph
npm ci

# Verify against the live Python server contract from the repository root
cd ../..
python clients/typescript/scripts/verify_server_contract.py
cd clients/typescript

# Run static, build, and behavioral checks
npm run lint
npm run build
npm test -- --coverage

# Check what will be published
npm pack --dry-run
```

### 2. Update Version

```bash
# For alpha releases
npm version prerelease --preid=alpha

# For beta releases
npm version prerelease --preid=beta

# For stable releases
npm version patch  # or minor, or major
```

### 3. Publish

```bash
# For alpha/beta (with tag)
npm publish --tag alpha --access public

# For stable releases
npm publish --access public
```

### 4. Verify

```bash
# Check on NPM
npm view @erii/client

# Test installation
npm install @erii/client@alpha
```

## Version Strategy

- `0.5.0-alpha.x` - Alpha releases (current)
- `0.5.0-beta.x` - Beta releases
- `0.5.0` - Stable release

## Publishing Checklist

- [ ] `npm ci`, lint, build, and tests pass from a clean checkout
- [ ] Live FastAPI contract verification passes
- [ ] No owner API key appears in browser/frontend examples
- [ ] README.md up to date
- [ ] CHANGELOG.md updated
- [ ] Version bumped appropriately
- [ ] Dry-run checked (`npm pack --dry-run`)
- [ ] Published with correct tag
- [ ] Verified on npmjs.com
- [ ] Tested installation
