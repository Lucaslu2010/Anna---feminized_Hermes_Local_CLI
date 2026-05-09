#!/bin/zsh

set -e

APP_NAME="HermesGirl"
APP_DIR="$APP_NAME.app"

rm -rf "$APP_DIR"

mkdir -p "$APP_DIR/Contents/MacOS"
mkdir -p "$APP_DIR/Contents/Resources"

cp -R HermesGirl "$APP_DIR/Contents/Resources/"

cat > "$APP_DIR/Contents/Resources/start_HermesGirl.sh" <<'EOF'
#!/bin/zsh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR/HermesGirl"

cd "$PROJECT_DIR"

export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/bin:$PATH"

PYTHON="$PROJECT_DIR/venv/bin/python"

if [ ! -f "$PYTHON" ]; then
    PYTHON="$PROJECT_DIR/.venv/bin/python"
fi

if [ ! -f "$PYTHON" ]; then
    osascript -e 'display dialog "Cannot find Python venv inside app." buttons {"OK"} default button "OK"'
    exit 1
fi

MAIN_FILE=""

if [ -f "$PROJECT_DIR/main_xterm.py" ]; then
    MAIN_FILE="$PROJECT_DIR/main_xterm.py"
elif [ -f "$PROJECT_DIR/main.py" ]; then
    MAIN_FILE="$PROJECT_DIR/main.py"
elif [ -f "$PROJECT_DIR/main_test.py" ]; then
    MAIN_FILE="$PROJECT_DIR/main_test.py"
fi

if [ -z "$MAIN_FILE" ]; then
    osascript -e 'display dialog "Cannot find main Python file inside HermesGirl." buttons {"OK"} default button "OK"'
    exit 1
fi

"$PYTHON" "$MAIN_FILE"
EOF

chmod +x "$APP_DIR/Contents/Resources/start_HermesGirl.sh"

cat > "$APP_DIR/Contents/MacOS/launcher" <<'EOF'
#!/bin/zsh

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$APP_DIR/Resources/start_HermesGirl.sh"

if [ ! -f "$SCRIPT" ]; then
    osascript -e 'display dialog "Cannot find start_HermesGirl.sh inside app." buttons {"OK"} default button "OK"'
    exit 1
fi

exec "$SCRIPT" >/tmp/HermesGirl.log 2>&1
EOF

chmod +x "$APP_DIR/Contents/MacOS/launcher"

cat > "$APP_DIR/Contents/Info.plist" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>HermesGirl</string>

    <key>CFBundleDisplayName</key>
    <string>HermesGirl</string>

    <key>CFBundleIdentifier</key>
    <string>com.local.hermesgirl</string>

    <key>CFBundleVersion</key>
    <string>1.0</string>

    <key>CFBundleShortVersionString</key>
    <string>1.0</string>

    <key>CFBundleExecutable</key>
    <string>launcher</string>

    <key>CFBundlePackageType</key>
    <string>APPL</string>

    <key>NSHighResolutionCapable</key>
    <true/>

    <key>LSUIElement</key>
    <false/>
</dict>
</plist>
EOF

echo "Built $APP_DIR"
echo "Logs will be written to /tmp/HermesGirl.log"
