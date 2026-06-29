import matplotlib.pyplot as plt
import matplotlib.patches as patches
import numpy as np

fig, ax = plt.subplots(figsize=(10, 10), facecolor='#0a0a2e')
ax.set_facecolor('#0a0a2e')
ax.set_xlim(-1.5, 1.5)
ax.set_ylim(-1.5, 1.5)
ax.set_aspect('equal')
ax.axis('off')

# ---- Background glow ----
for r in np.linspace(1.4, 0.1, 30):
    glow = plt.Circle((0, 0), r, fill=True, color='#003322', alpha=0.03)
    ax.add_patch(glow)

# ---- Radar circles ----
for r in [0.25, 0.5, 0.75, 1.0]:
    circle = plt.Circle((0, 0), r, fill=False, color='#00ff88', linewidth=1.2, alpha=0.35)
    ax.add_patch(circle)

# ---- Cross lines ----
for angle in [0, 45, 90, 135]:
    rad = np.radians(angle)
    ax.plot([-1.0 * np.cos(rad), 1.0 * np.cos(rad)],
            [-1.0 * np.sin(rad), 1.0 * np.sin(rad)],
            color='#00ff88', linewidth=0.6, alpha=0.25)

# ---- Radar sweep ----
sweep_start = 50
sweep_angle = 55
for i in range(sweep_angle):
    angle = sweep_start + i
    alpha = 0.01 + 0.22 * (i / sweep_angle) ** 1.5
    wedge = patches.Wedge((0, 0), 1.0, angle, angle + 1.5,
                          facecolor='#00ff88', alpha=alpha)
    ax.add_patch(wedge)

# Sweep leading edge
edge_rad = np.radians(sweep_start + sweep_angle)
ax.plot([0, 1.0 * np.cos(edge_rad)], [0, 1.0 * np.sin(edge_rad)],
        color='#00ff88', linewidth=2.5, alpha=0.95)

# ---- Blips (promos) ----
blips = [
    (0.35, 70, 12, 1.0),
    (0.6, 135, 9, 0.85),
    (0.78, 220, 11, 0.9),
    (0.5, 300, 8, 0.8),
    (0.88, 50, 7, 0.75),
    (0.42, 260, 10, 0.92),
    (0.7, 350, 6, 0.7),
]

for r, angle_deg, size, brightness in blips:
    rad = np.radians(angle_deg)
    x = r * np.cos(rad)
    y = r * np.sin(rad)
    # Glow layers
    for s in [4, 3, 2]:
        ax.plot(x, y, 'o', color='#00ff88', markersize=size * s * 0.6,
                alpha=brightness * 0.08)
    ax.plot(x, y, 'o', color='#00ffaa', markersize=size * 0.55,
            alpha=brightness * 0.95)

# ---- Center dot ----
ax.plot(0, 0, 'o', color='#00ff88', markersize=8, alpha=1.0)
center_ring = plt.Circle((0, 0), 0.03, fill=False, color='#00ff88',
                          linewidth=2, alpha=0.8)
ax.add_patch(center_ring)

# ---- Outer decorative ring ----
outer = plt.Circle((0, 0), 1.08, fill=False, color='#00ff88',
                    linewidth=2.5, alpha=0.7)
ax.add_patch(outer)

# ---- Circular clipping border (Instagram-friendly) ----
clip_circle = plt.Circle((0, 0), 1.12, fill=False, color='#00cc77',
                          linewidth=4, alpha=0.5)
ax.add_patch(clip_circle)

# ---- Text: "RADAR" at top ----
ax.text(0, 1.28, 'RADAR', fontsize=30, fontweight='bold',
        color='#00ff88', ha='center', va='center',
        fontfamily='monospace', alpha=0.95,
        bbox=dict(boxstyle='round,pad=0.15', facecolor='#0a0a2e',
                  edgecolor='none', alpha=0.8))

# ---- Text: "DAS PROMOS" at bottom ----
ax.text(0, -1.28, 'DAS PROMOS', fontsize=26, fontweight='bold',
        color='#00ff88', ha='center', va='center',
        fontfamily='monospace', alpha=0.95,
        bbox=dict(boxstyle='round,pad=0.15', facecolor='#0a0a2e',
                  edgecolor='none', alpha=0.8))

# ---- Small tagline ----
ax.text(0, -1.42, 'As melhores ofertas do ML', fontsize=11,
        color='#00ff88', ha='center', va='center',
        fontfamily='monospace', alpha=0.5, style='italic')

plt.tight_layout()
plt.savefig('/home/ubuntu/repos/radardaspromos/radar_profile.png', dpi=200,
            bbox_inches='tight', facecolor='#0a0a2e', pad_inches=0.2)
print("Imagem de perfil salva em radar_profile.png")
