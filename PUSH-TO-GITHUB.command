#!/bin/bash
# Double-click or run: push FaithMap main → agamkram/faithmap-app
set -e
cd "$(dirname "$0")"
echo "Remote:"
git remote -v
echo
echo "Pushing main…"
git push -u origin main
echo
echo "Done."
read -r -p "Press Return to close… " _
