#!/usr/bin/env bash
#
# One-command install for the BJJ dashboard.
#
#   ./setup.sh
#
# Creates the GitHub repo, logs in to Garmin, uploads the secrets, switches on
# Pages, and kicks off the first sync. Safe to re-run: every step checks whether
# it has already been done.

set -euo pipefail
cd "$(dirname "$0")"

BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'
RED=$'\033[31m'; OFF=$'\033[0m'

step()  { printf "\n%s▸ %s%s\n" "$BOLD" "$1" "$OFF"; }
ok()    { printf "  %s✓%s %s\n" "$GREEN" "$OFF" "$1"; }
info()  { printf "  %s%s%s\n" "$DIM" "$1" "$OFF"; }
warn()  { printf "  %s!%s %s\n" "$YELLOW" "$OFF" "$1"; }
die()   { printf "\n%s✗ %s%s\n\n" "$RED" "$1" "$OFF" >&2; exit 1; }

SECRETS_DIR=""
cleanup() { [ -n "$SECRETS_DIR" ] && rm -rf "$SECRETS_DIR"; return 0; }
trap cleanup EXIT

printf "\n%sMat time — BJJ dashboard installer%s\n" "$BOLD" "$OFF"
info "Takes a few minutes. You'll be asked for your Garmin login once."

# ─────────────────────────────────────────────────────── prerequisites ──
step "Checking prerequisites"

command -v python3 >/dev/null || die "python3 not found. Install it from python.org, then re-run."
ok "python3 $(python3 -V 2>&1 | cut -d' ' -f2)"

command -v git >/dev/null || die "git not found. Run: xcode-select --install"
ok "git"

# The GitHub CLI does all the GitHub-side setup. If it isn't installed we fetch
# the official binary into .tools/ inside this folder — no Homebrew, no sudo,
# nothing added to the rest of the system. Delete the folder and it's gone.
GH_FALLBACK_VERSION="2.97.0"

install_gh() {
  case "$(uname -m)" in
    arm64)  GH_ARCH=arm64 ;;
    x86_64) GH_ARCH=amd64 ;;
    *) return 1 ;;
  esac

  # The `|| true` matters: GitHub's API is rate-limited per IP, and under
  # `set -o pipefail` a failed lookup would otherwise abort the whole installer
  # instead of quietly falling back to the pinned version.
  GH_VERSION=$( { curl -fsSL --max-time 15 \
      https://api.github.com/repos/cli/cli/releases/latest 2>/dev/null || true; } \
    | sed -n 's/.*"tag_name": *"v\([^"]*\)".*/\1/p' | head -1 )
  [ -z "$GH_VERSION" ] && GH_VERSION="$GH_FALLBACK_VERSION"

  NAME="gh_${GH_VERSION}_macOS_${GH_ARCH}"
  rm -rf .tools/extract
  mkdir -p .tools/extract
  curl -fsSL --max-time 180 \
    "https://github.com/cli/cli/releases/download/v${GH_VERSION}/${NAME}.zip" \
    -o .tools/gh.zip || return 1
  unzip -oq .tools/gh.zip -d .tools/extract || return 1

  FOUND=$(find .tools/extract -type f -name gh | head -1)
  [ -n "$FOUND" ] || return 1
  mv "$FOUND" .tools/gh
  chmod +x .tools/gh
  xattr -d com.apple.quarantine .tools/gh 2>/dev/null || true
  rm -rf .tools/gh.zip .tools/extract
  return 0
}

if command -v gh >/dev/null; then
  GH="gh"
elif [ -x .tools/gh ]; then
  GH=".tools/gh"
else
  info "The GitHub CLI isn't installed — fetching it into this folder (about 15 MB)."
  install_gh || die "Couldn't download the GitHub CLI. Install it from cli.github.com, then re-run this script."
  GH=".tools/gh"
fi
ok "gh $("$GH" --version | head -1 | cut -d' ' -f3)$([ "$GH" = ".tools/gh" ] && printf ' (local to this folder)')"

if ! "$GH" auth status >/dev/null 2>&1; then
  info "Signing you in to GitHub. Choose: GitHub.com → HTTPS → yes → login with a browser."
  "$GH" auth login
fi
GH_USER=$("$GH" api user --jq .login)
ok "signed in to GitHub as $GH_USER"

# ─────────────────────────────────────────────────────── python deps ──
step "Installing Python dependencies"
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
PY=".venv/bin/python"
"$PY" -m pip install --quiet --upgrade pip
"$PY" -m pip install --quiet -r requirements.txt
ok "installed into .venv"

# ─────────────────────────────────────────────────────── the repo ──
step "Setting up the GitHub repository"

if [ ! -d .git ]; then
  git init -q -b main 2>/dev/null || { git init -q && git checkout -q -b main; }
fi
git add -A
git diff --staged --quiet || git commit -q -m "BJJ dashboard"

push_to() {  # $1 = owner/name
  git remote get-url origin >/dev/null 2>&1 \
    && git remote set-url origin "https://github.com/$1.git" \
    || git remote add origin "https://github.com/$1.git"
  if git push -q -u origin main 2>/dev/null; then return 0; fi
  warn "That repository already holds different content."
  printf "  Replace its contents with the dashboard? This erases what's there. [y/N] "
  read -r overwrite
  case "$overwrite" in
    [Yy]*) git push -q -u --force origin main ;;
    *) die "Nothing pushed. Re-run and choose a different repository name." ;;
  esac
}

if git remote get-url origin >/dev/null 2>&1; then
  REPO=$("$GH" repo view --json nameWithOwner --jq .nameWithOwner)
  push_to "$REPO"
  ok "using existing repo $REPO"
else
  REPO_NAME="bjj-dashboard"
  while :; do
    printf "  Repository name [%s]: " "$REPO_NAME"
    read -r answer
    [ -n "$answer" ] && REPO_NAME="$answer"
    REPO="$GH_USER/$REPO_NAME"

    if ! "$GH" repo view "$REPO" >/dev/null 2>&1; then
      info "Creating a public repo — public is what makes GitHub Pages free."
      "$GH" repo create "$REPO_NAME" --public --source=. --remote=origin --push
      ok "created $REPO"
      break
    fi

    # The name is taken. An empty repo is almost certainly one the user made for
    # exactly this, so adopt it silently; anything with content gets a question.
    BRANCH=$("$GH" repo view "$REPO" --json defaultBranchRef --jq '.defaultBranchRef.name // ""' 2>/dev/null || echo "")
    if [ -n "$BRANCH" ]; then
      warn "You already have a repository called '$REPO_NAME', and it isn't empty."
      printf "  [u]se it anyway, or pick another [n]ame? [u/n] "
      read -r choice
      case "$choice" in [Nn]*) REPO_NAME="${REPO_NAME}-2"; continue ;; esac
    else
      info "Found your empty '$REPO_NAME' repository — using that."
    fi

    VISIBILITY=$("$GH" repo view "$REPO" --json visibility --jq .visibility 2>/dev/null || echo "")
    if [ "$VISIBILITY" = "PRIVATE" ]; then
      warn "It's private, and GitHub Pages needs a paid plan for private repos."
      printf "  Make it public? [Y/n] "
      read -r makepublic
      case "$makepublic" in
        [Nn]*) warn "Leaving it private — the dashboard won't publish to Pages." ;;
        *) "$GH" repo edit "$REPO" --visibility public \
             --accept-visibility-change-consequences >/dev/null && ok "now public" ;;
      esac
    fi

    push_to "$REPO"
    ok "using $REPO"
    break
  done
fi

# ─────────────────────────────────────────────────────── garmin login ──
step "Connecting to Garmin Connect"
info "Your password is used once, here on your Mac, and is never stored or uploaded."
SECRETS_DIR=$(mktemp -d)
"$PY" scripts/login_local.py --secrets-to "$SECRETS_DIR"
[ -s "$SECRETS_DIR/garmin_tokens.txt" ] || die "Garmin login did not complete."
ok "logged in, session token minted"

# The login may have corrected bjj_type_keys in config.json — ship that too.
if ! git diff --quiet -- config.json; then
  git add config.json
  git commit -q -m "Set BJJ activity type"
  git push -q origin main
  info "pushed the activity-type correction"
fi

# ─────────────────────────────────────────────────────── secrets ──
step "Uploading secrets"
"$GH" secret set GARMIN_TOKENS --repo "$REPO" < "$SECRETS_DIR/garmin_tokens.txt"
"$GH" secret set TOKEN_KEY     --repo "$REPO" < "$SECRETS_DIR/token_key.txt"
rm -rf "$SECRETS_DIR"; SECRETS_DIR=""
ok "GARMIN_TOKENS and TOKEN_KEY stored (encrypted, write-only)"

# ─────────────────────────────────────────────────────── permissions ──
step "Allowing the daily job to commit its results"
printf '{"default_workflow_permissions":"write","can_approve_pull_request_reviews":false}' \
  | "$GH" api --method PUT "/repos/$REPO/actions/permissions/workflow" --input - >/dev/null
ok "workflow write permission enabled"

# ─────────────────────────────────────────────────────── pages ──
step "Publishing to GitHub Pages"
if "$GH" api "/repos/$REPO/pages" >/dev/null 2>&1; then
  ok "Pages already enabled"
else
  printf '{"source":{"branch":"main","path":"/docs"}}' \
    | "$GH" api --method POST "/repos/$REPO/pages" --input - >/dev/null \
    && ok "Pages enabled from main /docs" \
    || warn "Couldn't enable Pages automatically — Settings → Pages → main / /docs"
fi
PAGES_URL=$("$GH" api "/repos/$REPO/pages" --jq .html_url 2>/dev/null || echo "")
[ -z "$PAGES_URL" ] && PAGES_URL="https://${GH_USER}.github.io/${REPO#*/}/"

# ─────────────────────────────────────────────────────── first run ──
step "Starting the first sync"
QUEUED=""
for attempt in 1 2 3 4 5; do
  if "$GH" workflow run sync.yml --repo "$REPO" >/dev/null 2>&1; then QUEUED=yes; break; fi
  info "workflow not registered yet, retrying in 10s ($attempt/5)"
  sleep 10
done
if [ -n "$QUEUED" ]; then
  ok "first run queued — it backfills your whole history, so give it a few minutes"
else
  warn "Couldn't start it automatically. Open the repo's Actions tab and press"
  warn "\"Run workflow\" — everything else is already set up."
fi

printf "\n%sDone.%s\n\n" "$BOLD" "$OFF"
printf "  Your dashboard   %s\n" "$PAGES_URL"
printf "  Watch first run  %s%s run watch --repo %s%s\n" "$DIM" "$GH" "$REPO" "$OFF"
printf "  Runs daily at    05:15 Oslo time, by itself\n\n"
info "Heart-rate zones and VO2 max fill in over the next week or so."
info "The dashboard shows how many are still queued."
printf "\n"
