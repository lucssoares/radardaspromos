from PIL import Image, ImageDraw, ImageFont
import math

W, H = 1080, 1080
img = Image.new('RGBA', (W, H), (10, 10, 46, 255))
draw = ImageDraw.Draw(img)

cx, cy = W // 2, H // 2
safe_r = 490  # Instagram circle crop radius

# ---- Background glow ----
glow = Image.new('RGBA', (W, H), (0, 0, 0, 0))
glow_draw = ImageDraw.Draw(glow)
for r in range(safe_r, 0, -3):
    alpha = int(22 * (1 - r / safe_r))
    glow_draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                      fill=(0, 160, 90, alpha))
img = Image.alpha_composite(img, glow)
draw = ImageDraw.Draw(img)

# ---- Radar screen (centered, upper area) ----
radar_cx, radar_cy = cx, cy - 80
radar_r = 250

# Outer ring (bold)
for w in range(6):
    r = radar_r + w - 3
    draw.ellipse([radar_cx - r, radar_cy - r, radar_cx + r, radar_cy + r],
                 outline=(0, 255, 136, 200))

# Inner circles
for frac in [0.33, 0.66]:
    r = int(radar_r * frac)
    draw.ellipse([radar_cx - r, radar_cy - r, radar_cx + r, radar_cy + r],
                 outline=(0, 255, 136, 60), width=1)

# Cross lines
draw.line([(radar_cx - radar_r, radar_cy), (radar_cx + radar_r, radar_cy)],
          fill=(0, 255, 136, 50), width=1)
draw.line([(radar_cx, radar_cy - radar_r), (radar_cx, radar_cy + radar_r)],
          fill=(0, 255, 136, 50), width=1)

# ---- Sweep ----
sweep_layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
sweep_draw = ImageDraw.Draw(sweep_layer)
sweep_start = -85
sweep_end = -20
steps = 80
for i in range(steps):
    angle = sweep_start + i * (sweep_end - sweep_start) / steps
    alpha = int(3 + 60 * (i / steps) ** 2)
    a1 = angle
    a2 = angle + (sweep_end - sweep_start) / steps + 0.5
    sweep_draw.pieslice([radar_cx - radar_r, radar_cy - radar_r,
                         radar_cx + radar_r, radar_cy + radar_r],
                        start=a1, end=a2, fill=(0, 255, 136, alpha))
img = Image.alpha_composite(img, sweep_layer)
draw = ImageDraw.Draw(img)

# Sweep edge line
edge_angle = math.radians(sweep_end)
edge_x = radar_cx + int(radar_r * math.cos(edge_angle))
edge_y = radar_cy + int(radar_r * math.sin(edge_angle))
draw.line([(radar_cx, radar_cy), (edge_x, edge_y)],
          fill=(0, 255, 136, 240), width=3)

# ---- Blips ----
blips_layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
blips_draw = ImageDraw.Draw(blips_layer)

blip_data = [
    (0.38, -60, 13),
    (0.62, 20, 11),
    (0.78, 155, 12),
    (0.48, 225, 10),
    (0.82, -10, 9),
    (0.33, 130, 11),
    (0.65, 285, 10),
]

for dist_frac, angle_deg, size in blip_data:
    rad = math.radians(angle_deg)
    bx = radar_cx + int(radar_r * dist_frac * math.cos(rad))
    by = radar_cy + int(radar_r * dist_frac * math.sin(rad))
    for gs in range(size * 3, 0, -2):
        alpha = int(20 * (1 - gs / (size * 3)))
        blips_draw.ellipse([bx - gs, by - gs, bx + gs, by + gs],
                           fill=(0, 255, 136, alpha))
    blips_draw.ellipse([bx - size // 2, by - size // 2,
                        bx + size // 2, by + size // 2],
                       fill=(0, 255, 220, 240))

img = Image.alpha_composite(img, blips_layer)
draw = ImageDraw.Draw(img)

# ---- Center dot ----
draw.ellipse([radar_cx - 8, radar_cy - 8, radar_cx + 8, radar_cy + 8],
             fill=(0, 255, 136, 255))
draw.ellipse([radar_cx - 14, radar_cy - 14, radar_cx + 14, radar_cy + 14],
             outline=(0, 255, 136, 200), width=2)

# ---- Text ----
try:
    font_radar = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 90)
    font_promos = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 50)
except:
    font_radar = ImageFont.load_default()
    font_promos = font_radar

# "RADAR"
text1 = "RADAR"
bb1 = draw.textbbox((0, 0), text1, font=font_radar)
tw1 = bb1[2] - bb1[0]
th1 = bb1[3] - bb1[1]
text_y = radar_cy + radar_r + 35
draw.text((cx - tw1 // 2, text_y), text1,
          fill=(0, 255, 136, 255), font=font_radar)

# "DAS PROMOS"
text2 = "DAS PROMOS"
bb2 = draw.textbbox((0, 0), text2, font=font_promos)
tw2 = bb2[2] - bb2[0]
draw.text((cx - tw2 // 2, text_y + th1 + 10), text2,
          fill=(0, 255, 136, 200), font=font_promos)

# ---- Decorative outer ring ----
for w in range(3):
    r = safe_r - 5 + w
    draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                 outline=(0, 255, 136, 80))

# ---- Apply circular mask ----
mask = Image.new('L', (W, H), 0)
mask_draw = ImageDraw.Draw(mask)
mask_draw.ellipse([cx - safe_r, cy - safe_r, cx + safe_r, cy + safe_r], fill=255)

bg = Image.new('RGBA', (W, H), (10, 10, 46, 255))
final = Image.composite(img, bg, mask)

# Save with dark background
final.convert('RGB').save(
    '/home/ubuntu/repos/radardaspromos/logo_radar_das_promos.png', quality=95)

# Save with transparent background
bg_t = Image.new('RGBA', (W, H), (0, 0, 0, 0))
final_t = Image.composite(img, bg_t, mask)
final_t.save('/home/ubuntu/repos/radardaspromos/logo_radar_das_promos_transparent.png')

print("Logo salvo!")
