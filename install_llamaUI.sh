#!/usr/bin/env bash
# install.sh — install llamaUI on any system.
#   Linux:   pip install + XDG desktop entry + icon
#   macOS:   pip install + .app bundle stub
#   Windows: pip install (run install.bat instead)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=== llamaUI installer ==="

# --- 1. pip install (editable for devs, normal for users) ---
if [ -f ".git/HEAD" ]; then
    echo "[1/3] Installing in editable (dev) mode…"
    pip3 install -e . 2>/dev/null || pip install -e .
else
    echo "[1/3] Installing…"
    pip3 install . 2>/dev/null || pip install .
fi

# --- 2. Platform-specific launcher ---
OS="$(uname -s 2>/dev/null || echo unknown)"

case "$OS" in
    Linux*)
        echo "[2/3] Installing desktop entry & icon…"
        ICONS_DIR="$HOME/.local/share/icons/hicolor"
        APPS_DIR="$HOME/.local/share/applications"
        mkdir -p "$APPS_DIR"

        # Install icon sizes
        for size in 16 22 24 32 48 64 128 256 512; do
            src="qt_app/icons/llamaui-${size}x${size}.png"
            [ -f "$src" ] || continue
            d="$ICONS_DIR/${size}x${size}/apps"
            mkdir -p "$d"
            cp "$src" "$d/llamaui.png"
        done

        # Desktop entry — resolve the installed entry point path
        EXEC_PATH="$(command -v llamaui 2>/dev/null || echo "python3 -m qt_app")"
        cat > "$APPS_DIR/llamaui.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Version=1.0
Name=llamaUI
GenericName=Local LLM Manager
Comment=Run and manage llama-server models locally
Icon=llamaui
Exec=${EXEC_PATH}
Terminal=false
Categories=Development;Network;
Keywords=llm;llama;gguf;ai;models;
StartupNotify=true
StartupWMClass=llamaUI
DESKTOP

        update-desktop-database "$APPS_DIR" 2>/dev/null || true
        gtk-update-icon-cache -f "$ICONS_DIR" 2>/dev/null || true
        echo "  → Desktop entry installed"
        ;;

    Darwin*)
        echo "[2/3] Creating macOS .app stub…"
        APP_DIR="$HOME/Applications/llamaUI.app"
        mkdir -p "$APP_DIR/Contents/MacOS"
        mkdir -p "$APP_DIR/Contents/Resources"

        # Info.plist
        cat > "$APP_DIR/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>llamaUI</string>
    <key>CFBundleDisplayName</key><string>llamaUI</string>
    <key>CFBundleIdentifier</key><string>com.llamaui.app</string>
    <key>CFBundleVersion</key><string>0.1.0</string>
    <key>CFBundleExecutable</key><string>llamaui</string>
    <key>CFBundleIconFile</key><string>llamaui</string>
    <key>LSMinimumSystemVersion</key><string>11.0</string>
</dict>
</plist>
PLIST

        # Launcher script
        EXEC_PATH="$(command -v llamaui 2>/dev/null || echo "python3 -m qt_app")"
        cat > "$APP_DIR/Contents/MacOS/llamaui" <<LAUNCHER
#!/bin/bash
exec ${EXEC_PATH}
LAUNCHER
        chmod +x "$APP_DIR/Contents/MacOS/llamaui"

        # Icon (icns)
        if command -v sips >/dev/null 2>&1 && [ -f "qt_app/icons/llamaui-512x512.png" ]; then
            sips -s format icns qt_app/icons/llamaui-512x512.png --out "$APP_DIR/Contents/Resources/llamaui.icns" 2>/dev/null || true
        fi

        echo "  → App stub created at $APP_DIR"
        ;;

    *)
        echo "[2/3] No platform-specific launcher for $OS — use 'llamaui' command or ./llamaui.sh"
        ;;
esac

echo "[3/3] Done. Launch with: llamaui"
