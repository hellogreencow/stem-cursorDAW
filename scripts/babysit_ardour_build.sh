#!/bin/bash
# Babysit the Ardour fork build: rebuild, and on compile errors hand the
# error context to a Cursor CLI agent (composer-2.5) to fix, then retry.
# Survives terminal close (run via nohup). Log: /tmp/ardour_babysit.log
set -u

ARDOUR=~/Desktop/ardour-ai
LOG=/tmp/ardour_babysit.log
BUILD_LOG=/tmp/ardour_build.log
MAX_ROUNDS=25
MODEL="${BABYSIT_MODEL:-composer-2.5}"

export PKG_CONFIG_PATH="/opt/homebrew/lib/pkgconfig:/opt/homebrew/opt/glibmm@2.66/lib/pkgconfig:/opt/homebrew/opt/libarchive/lib/pkgconfig"
export CFLAGS="-I/opt/homebrew/include -I/opt/homebrew/opt/libarchive/include -I/opt/homebrew/include/libusb-1.0 -DDISABLE_VISIBILITY"
export CXXFLAGS="$CFLAGS"
export LDFLAGS="-L/opt/homebrew/lib -L/opt/homebrew/opt/libarchive/lib"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:$PATH"

log() { echo "[$(date '+%H:%M:%S')] $*" >> "$LOG"; }

log "=== babysitter started (model: $MODEL) ==="

# wait for any in-flight waf to finish so we don't fight the lockfile
while pgrep -f "waf build" > /dev/null; do sleep 30; done
log "no waf running; taking over"

prev_error=""
same_error_count=0

for round in $(seq 1 $MAX_ROUNDS); do
    log "--- round $round: building ---"
    cd "$ARDOUR" || { log "FATAL: no ardour dir"; exit 1; }
    python3 ./waf build -j8 > "$BUILD_LOG" 2>&1
    status=$?

    if [ $status -eq 0 ]; then
        log "BUILD SUCCEEDED on round $round 🎉"
        echo "ARDOUR BUILD COMPLETE $(date)" > /tmp/ardour_build_success
        exit 0
    fi

    first_error=$(grep -m 1 " error:" "$BUILD_LOG")
    log "build failed; first error: $first_error"

    # stuck detection: same first error 3 rounds in a row -> give up
    if [ "$first_error" == "$prev_error" ]; then
        same_error_count=$((same_error_count + 1))
        if [ $same_error_count -ge 3 ]; then
            log "STUCK: same error 3 rounds; stopping for human review"
            exit 2
        fi
    else
        same_error_count=0
    fi
    prev_error="$first_error"

    # check agent auth
    if ! agent status 2>/dev/null | grep -qi "logged in\|authenticated"; then
        if agent status 2>&1 | grep -qi "not logged in"; then
            log "agent NOT authenticated; retrying build without AI fix in 10min"
            sleep 600
            continue
        fi
    fi

    # build error context: errors + a little surrounding output
    errors=$(grep -B 2 -A 4 " error:" "$BUILD_LOG" | head -150)

    log "invoking agent ($MODEL) to fix..."
    cd "$ARDOUR"
    agent -p --trust --model "$MODEL" --output-format text \
"You are fixing macOS arm64 (Apple Silicon, clang) build errors in the Ardour
DAW source tree at $ARDOUR. The build system is waf; do NOT run the build
yourself (the watcher script handles rebuilds) and do NOT reconfigure.

Fix ONLY what these compile errors require — minimal, surgical edits to the
named files. Typical fixes: missing includes, platform guards
(#ifdef __APPLE__ / __aarch64__), removing x86-only intrinsics/flags on arm64,
C++17 compatibility. Never delete features; prefer adding platform guards.
Do not touch build flags in wscript unless an error is clearly flag-caused.

Compile errors:
$errors" >> "$LOG" 2>&1
    log "agent round $round done; rebuilding"
done

log "exhausted $MAX_ROUNDS rounds without success"
exit 3
