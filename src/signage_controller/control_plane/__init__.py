"""Optional control-plane identity, enrollment, and observed-state reporting.

This package is outbound-only and entirely optional. Nothing here runs unless a
`control_plane:` section exists in the configuration, and nothing here can stop
TV convergence or mpv playback: those are separate processes holding separate
runtime locks. A control plane that is unreachable, or absent altogether, leaves
the signage appliance fully functional.
"""
