"""Linux-native hardware observation.

Deliberately independent of the control plane: `signage-controller device
inventory` prints what this package finds without any network access. Collection
reads sysfs only and needs no X11, no Wayland, no mpv, no television, and no
Internet. Partial results are normal on VMs, WSL, headless servers, and unusual
graphics drivers; no DRM devices at all is a valid inventory.
"""
