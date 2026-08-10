# Publishing to NPM

## Prerequisites

1. NPM account with 2FA enabled
2. Organization scope `@erii` created on NPM
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

# Install dependencies
npm install

# Run tests
npm test

# Build
npm run build

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

- [ ] All tests passing
- [ ] README.md up to date
- [ ] CHANGELOG.md updated
- [ ] Version bumped appropriately
- [ ] Build successful (`npm run build`)
- [ ] Dry-run checked (`npm pack --dry-run`)
- [ ] Published with correct tag
- [ ] Verified on npmjs.com
- [ ] Tested installation

## Automation (Future)

Consider setting up GitHub Actions for automated publishing:

```yaml
# .github/workflows/publish.yml
name: Publish to NPM
on:
  release:
    types: [created]
jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-node@v3
        with:
          node-version: '18'
          registry-url: 'https://registry.npmjs.org'
      - run: npm ci
      - run: npm test
      - run: npm publish --access public
        env:
          NODE_AUTH_TOKEN: ${{secrets.NPM_TOKEN}}
```
