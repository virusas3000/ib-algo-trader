#!/bin/bash
# TWS Watchdog — keeps Trader Workstation logged in 24/7
# - Detects API port 7497 disconnect
# - Relaunches TWS if not running
# - Auto-fills login form from macOS Keychain
# - Dismisses ALL dialogs: warnings, errors, restarts, logouts, OK popups
# Credentials stored in Keychain:
#   security add-generic-password -a "$USER" -s tws-paper-user -w 'YOUR_USER' -U
#   security add-generic-password -a "$USER" -s tws-paper      -w 'YOUR_PASS' -U

set -u
LOG=~/Desktop/ib_algo_trader/logs/tws_watchdog.log
mkdir -p "$(dirname "$LOG")"

log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

TWS_APP="/Users/$USER/Applications/Trader Workstation/Trader Workstation.app"
[ -d "$TWS_APP" ] || TWS_APP="/Applications/Trader Workstation.app"

get_cred() {
  security find-generic-password -a "$USER" -s "$1" -w 2>/dev/null
}

is_api_up() {
  /usr/bin/nc -z -G 2 127.0.0.1 7497 2>/dev/null
}

is_tws_running() {
  pgrep -f "JavaApplicationStub" >/dev/null
}

launch_tws() {
  log "Launching TWS..."
  open -a "$TWS_APP"
  sleep 30
}

# Dismiss ALL known dialogs — warnings, errors, OK popups, restart prompts
dismiss_dialogs() {
  osascript <<'ASEOF' 2>/dev/null
tell application "System Events"
  set twsProc to ""
  repeat with p in every process
    if name of p contains "JavaApplicationStub" or name of p contains "Trader Workstation" then
      set twsProc to p
      exit repeat
    end if
  end repeat
  if twsProc is "" then return

  tell twsProc
    -- NO set frontmost — don't steal focus when bot is trading
    set dismissButtons to {"OK", "Yes", "Continue", "Accept", "I understand and accept", "Restart", "Close Daily Lineup", "Close", "Dismiss", "Confirm", "Got it", "Proceed"}
    repeat with w in every window
      try
        repeat with btnName in dismissButtons
          try
            click button btnName of w
            delay 0.3
          end try
        end repeat
      end try
    end repeat
  end tell
end tell
ASEOF
}

login_tws() {
  local user pass
  user="$(get_cred tws-paper-user)"
  pass="$(get_cred tws-paper)"
  if [ -z "$user" ] || [ -z "$pass" ]; then
    log "ERROR: Keychain creds missing."
    return 1
  fi

  log "Waiting for Login window..."
  # Wait up to 45s for login window
  for i in $(seq 1 45); do
    local win
    win=$(osascript -e 'tell application "System Events" to tell process "JavaApplicationStub" to get name of every window' 2>/dev/null)
    if echo "$win" | grep -qi "login\|IBKR\|Interactive"; then
      break
    fi
    sleep 1
  done

  log "Auto-filling login form (user: $user)..."
  USER_ENV="$user" PASS_ENV="$pass" osascript <<'ASEOF' 2>>"$LOG"
set u to (system attribute "USER_ENV")
set p to (system attribute "PASS_ENV")
tell application "System Events"
  tell process "JavaApplicationStub"
    set frontmost to true
    delay 1
    -- Use Tab navigation — Java UI not indexable via AppleScript
    -- Click window to focus, then Cmd+Tab to cycle to username field
    set frontmost to true
    delay 0.5
    -- Select all in username, clear, type username
    keystroke tab
    delay 0.3
    -- Go to beginning: Shift+Tab back to username field
    keystroke tab using {shift down}
    delay 0.3
    keystroke tab using {shift down}
    delay 0.3
    keystroke "a" using {command down}
    key code 51
    delay 0.2
    keystroke u
    delay 0.5
    -- Tab to password field
    keystroke tab
    delay 0.3
    keystroke "a" using {command down}
    key code 51
    delay 0.2
    keystroke p
    delay 0.5
    -- Try clicking "Paper Log In" button first, fallback to Return
    try
      click button "Paper Log In" of window 1
    on error
      try
        click button "Log In" of window 1
      on error
        keystroke return
      end try
    end try
  end tell
end tell
ASEOF
  log "Login submitted — waiting 35s for TWS to load..."
  sleep 35

  # Dismiss any post-login dialogs (warnings, ToS, etc.)
  log "Dismissing post-login dialogs..."
  for i in 1 2 3; do
    dismiss_dialogs
    sleep 3
  done
}

log "=== TWS Watchdog started ==="

while true; do
  if ! is_tws_running; then
    log "TWS not running — launching"
    launch_tws
    login_tws
  elif ! is_api_up; then
    log "API port 7497 down — dismissing dialogs"
    dismiss_dialogs
    sleep 5
    if ! is_api_up; then
      log "Still down after dialog dismiss — checking for bad login"
      # Check if stuck on login screen (bad creds already tried)
      WIN=$(osascript -e 'tell application "System Events" to tell process "JavaApplicationStub" to get name of every window' 2>/dev/null)
      if echo "$WIN" | grep -qi "login\|IBKR"; then
        log "Login screen detected — dismissing any error dialogs first"
        # Dismiss "Unrecognized Username or Password" or any error popup
        osascript <<'ASEOF' 2>/dev/null
tell application "System Events"
  tell process "JavaApplicationStub"
    set frontmost to true
    repeat with w in every window
      try
        set wname to name of w as text
        if wname contains "Unrecognized" or wname contains "Error" or wname contains "Warning" or wname contains "Password" then
          try
            click button "OK" of w
          end try
          delay 0.5
        end if
      end try
    end repeat
  end tell
end tell
ASEOF
        sleep 2
        log "Re-filling credentials after error dismiss"
        login_tws
      else
        log "Restarting TWS"
        pkill -f "JavaApplicationStub" 2>/dev/null
        sleep 10
        launch_tws
        login_tws
      fi
    fi
  else
    dismiss_dialogs
  fi
  sleep 30
done
