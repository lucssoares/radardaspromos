import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np
from matplotlib.colors import LinearSegmentedColormap

fig, ax = plt.subplots(figsize=(10, 10), facecolor='#0a0a2e')
ax.set_facecolor('#0a0a2e')
ax.set_xlim(-1.3, 1.3)
ax.set_ylim(-1.3, 1.3)
ax.set_aspect('equal')
ax.axis('off')

# Radar circles (concentric rings)
for r in [0.25, 0.5, 0.75, 1.0]:
    circle = plt.Circle((0, 0), r, fill=False, color='#00ff88', linewidth=0.8, alpha=0.4)
    ax.add_patch(circle)

# Cross lines
for angle in [0, 45, 90, 135]:
    rad = np.radians(angle)
    ax.plot([-np.cos(rad), np.cos(rad)], [-np.sin(rad), np.sin(rad)],
            color='#00ff88', linewidth=0.5, alpha=0.3)

# Radar sweep (glowing wedge)
sweep_angle = 45
theta1, theta2 = 30, 30 + sweep_angle
wedge = patches.Wedge((0, 0), 1.0, theta1, theta2, facecolor='#00ff88', alpha=0.15)
ax.add_patch(wedge)

# Gradient sweep effect
for i in range(sweep_angle):
    angle = theta1 + i
    alpha = 0.02 + 0.18 * (i / sweep_angle)
    wedge_grad = patches.Wedge((0, 0), 1.0, angle, angle + 1, facecolor='#00ff88', alpha=alpha)
    ax.add_patch(wedge_grad)

# Sweep leading edge line
edge_rad = np.radians(theta2)
ax.plot([0, np.cos(edge_rad)], [0, np.sin(edge_rad)],
        color='#00ff88', linewidth=2, alpha=0.9)

# Blips (simulated "promos" detected on radar)
blips = [
    (0.3, 60, 8, 1.0),
    (0.55, 120, 6, 0.8),
    (0.7, 200, 10, 0.9),
    (0.85, 310, 5, 0.7),
    (0.4, 250, 7, 0.85),
    (0.6, 45, 9, 0.95),
    (0.9, 160, 4, 0.6),
    (0.35, 340, 6, 0.75),
    (0.75, 80, 8, 0.88),
]

for r, angle_deg, size, brightness in blips:
    rad = np.radians(angle_deg)
    x = r * np.cos(rad)
    y = r * np.sin(rad)
    # Glow effect
    for s in range(3, 0, -1):
        ax.plot(x, y, 'o', color='#00ff88', markersize=size * s * 0.7,
                alpha=brightness * 0.15)
    ax.plot(x, y, 'o', color='#00ff88', markersize=size * 0.5,
            alpha=brightness)

# Center dot
ax.plot(0, 0, 'o', color='#00ff88', markersize=6, alpha=0.9)
circle_center = plt.Circle((0, 0), 0.02, fill=False, color='#00ff88', linewidth=1.5, alpha=0.7)
ax.add_patch(circle_center)

# Title
ax.text(0, -1.18, 'RADAR DAS PROMOS', fontsize=22, fontweight='bold',
        color='#00ff88', ha='center', va='center', fontfamily='monospace',
        alpha=0.9)

# Outer ring decoration
outer = plt.Circle((0, 0), 1.05, fill=False, color='#00ff88', linewidth=1.5, alpha=0.6)
ax.add_patch(outer)

# Cardinal labels
labels = {'N': (0, 1.12), 'S': (0, -1.08), 'E': (1.12, 0), 'W': (-1.12, 0)}
for label, (x, y) in labels.items():
    ax.text(x, y, label, fontsize=12, color='#00ff88', ha='center', va='center',
            fontfamily='monospace', alpha=0.5)

plt.tight_layout()
plt.savefig('/home/ubuntu/repos/radardaspromos/radar.png', dpi=200,
            bbox_inches='tight', facecolor='#0a0a2e', pad_inches=0.3)
print("Imagem salva em radar.png")
