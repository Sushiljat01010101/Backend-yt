#!/bin/bash
# Usage: ./push_to_github.sh <GITHUB_REPO_URL_OR_TOKEN>
# Example: ./push_to_github.sh https://github.com/username/tinycrap-backend.git

if [ -z "$1" ]; then
    echo "Error: Please provide your GitHub repository URL."
    echo "Usage: ./push_to_github.sh https://github.com/USERNAME/REPO_NAME.git"
    exit 1
fi

REPO_URL="$1"

git remote remove origin 2>/dev/null || true
git remote add origin "$REPO_URL"
git branch -M main

echo "Pushing backend files to GitHub..."
git push -u origin main
echo "Done! Successfully pushed to GitHub."
