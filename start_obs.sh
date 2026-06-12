#!/bin/bash

# Define the OBS configuration directory
OBS_CONFIG_DIR="$HOME/.config/obs-studio"

# Remove the sentinel directory if it exists to clear the crash flag
if [ -d "$OBS_CONFIG_DIR/.sentinel" ]; then
    rm -rf "$OBS_CONFIG_DIR/.sentinel"
fi

# Launch OBS Studio (supports both Flatpak and standard installations)
if command -v flatpak &> /dev/null && flatpak list | grep -q "com.obsproject.Studio"; then
    flatpak run com.obsproject.Studio --disable-shutdown-check "$@" &
else
    obs --disable-shutdown-check "$@" &
fi

# Disown the process so the terminal can close safely
disown
