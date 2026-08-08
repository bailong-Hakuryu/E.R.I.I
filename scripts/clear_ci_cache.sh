#!/bin/bash
# Clear GitHub Actions cache for E.R.I.I repository
# Requires: GitHub CLI (gh) to be installed and authenticated

echo "Listing all caches..."
gh cache list --repo bailong-Hakuryu/E.R.I.I

echo ""
echo "Deleting all caches..."
gh cache delete --all --repo bailong-Hakuryu/E.R.I.I

echo ""
echo "Cache cleared! Remaining caches:"
gh cache list --repo bailong-Hakuryu/E.R.I.I
