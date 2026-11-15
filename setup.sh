
#!/bin/bash
set -e

# Always run from the directory containing this script
cd "$(dirname "$0")"

echo "Updating boot configuration for camera and display"

CONFIG="/boot/firmware/config.txt"

# Disable camera auto detection
if grep -q '^camera_auto_detect=' "$CONFIG"; then
    sudo sed -i 's/^camera_auto_detect=.*/camera_auto_detect=0/' "$CONFIG"
else
    echo "camera_auto_detect=0" | sudo tee -a "$CONFIG" > /dev/null
fi

# Append overlays only if they don't already exist
for overlay in \
    "dtoverlay=vc4-kms-v3d" \
    "dtoverlay=vc4-kms-dsi-waveshare-panel-v2,10_1_inch_a" \
    "dtoverlay=imx219,cam0"
do
    if ! grep -qxF "$overlay" "$CONFIG"; then
        echo "$overlay" | sudo tee -a "$CONFIG" > /dev/null
    fi
done

echo "Boot configuration updated"

echo "Updating software"
sudo apt update
sudo apt upgrade -y

echo "Installing required system packages"
sudo apt install -y \
    python3-libcamera \
    libcamera-apps \
    libcamera-dev \
    libcamera-tools \
    python3-picamera2 \
    unclutter \
    libcap-dev

echo "System packages finished installing"

# Set up virtual environment
echo "Creating virtual environment"

if [ -d ".venv" ]; then
    rm -rf .venv
fi

python3 -m venv --system-site-packages .venv

source .venv/bin/activate

# Install dependencies
echo "Installing dependencies"
python3 -m pip install -r requirements.txt
pip install --no-deps pyrender==0.1.45
pip install --no-deps PyOpenGL==3.1.6

echo "Dependencies finished installing"

echo "applying pyrender fixes for pi"
sudo cp pyrender_fixes/egl.py .venv/lib/python3.13/site-packages/pyrender/platforms/
sudo cp pyrender_fixes/shader_program.py .venv/lib/python3.13/site-packages/pyrender/
sudo rm -rf .venv/Lib/pyrender/shaders
sudo cp -r pyrender_fixes/shaders .venv/lib/python3.13/site-packages/pyrender/

echo "fixes applied"

# Set up systemd service
echo "Creating multiplane service"

sudo cp run_multiplane.service /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable run_multiplane.service
sudo systemctl enable --now run_multiplane.service

echo "Installation complete!"
echo "A reboot is recommended for the boot configuration changes."

