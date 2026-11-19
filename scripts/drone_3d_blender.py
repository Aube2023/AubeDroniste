"""Quadricoptere AubePilot pour le bandeau d'accueil.
Blender 4.2 : blender -b -P drone.py -- <dossier_sortie>
Rend NFRAMES images (helices qui tournent) sur fond transparent."""
import bpy, math, sys, os
from mathutils import Vector

OUT = sys.argv[sys.argv.index("--") + 1]
NFRAMES = 6
os.makedirs(OUT, exist_ok=True)

bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene


def mat(name, color, metal=0.0, rough=0.4, emit=None, emit_strength=0.0, alpha=1.0, coat=0.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*color, 1)
    b.inputs["Metallic"].default_value = metal
    b.inputs["Roughness"].default_value = rough
    b.inputs["Coat Weight"].default_value = coat
    if emit:
        b.inputs["Emission Color"].default_value = (*emit, 1)
        b.inputs["Emission Strength"].default_value = emit_strength
    if alpha < 1:
        b.inputs["Alpha"].default_value = alpha
        m.blend_method = "BLEND" if hasattr(m, "blend_method") else None
        try:
            m.surface_render_method = "BLENDED"
        except Exception:
            pass
    return m


def srgb(h):
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(((x + 0.055) / 1.055) ** 2.4 if x > 0.04045 else x / 12.92 for x in c)


WHITE = mat("coque", srgb("#eef0f7"), rough=0.28, coat=0.6)
ACCENT = mat("accent", srgb("#5b6fd0"), metal=0.3, rough=0.3, coat=0.5)
CARBON = mat("carbone", srgb("#1b1e27"), rough=0.45, coat=0.3)
METAL = mat("moteur", srgb("#9aa1b5"), metal=1.0, rough=0.25)
DARKMETAL = mat("cloche", srgb("#3a3f50"), metal=1.0, rough=0.3)
PROP = mat("helice", srgb("#2a2e3a"), rough=0.35)
BLUR = mat("disque", srgb("#c8cfee"), rough=0.2, alpha=0.055)
GLASS = mat("objectif", srgb("#0b0d14"), metal=0.2, rough=0.05, coat=1.0)
LENSRING = mat("bague", srgb("#6b7fd0"), emit=srgb("#8b9cf0"), emit_strength=1.5)
RED = mat("feu_rouge", srgb("#ff3b47"), emit=srgb("#ff3b47"), emit_strength=18)
GREEN = mat("feu_vert", srgb("#2ee27a"), emit=srgb("#2ee27a"), emit_strength=18)
WLED = mat("feu_blanc", srgb("#ffffff"), emit=srgb("#dfe6ff"), emit_strength=10)


def add(obj, m):
    obj.data.materials.append(m)
    bpy.ops.object.shade_smooth()
    return obj


def bevel(obj, w=0.02, seg=4):
    md = obj.modifiers.new("bev", "BEVEL")
    md.width = w
    md.segments = seg
    md.limit_method = "NONE"


# --- Corps : coque profilee (nez vers +X) ----------------------------------
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0))
body = bpy.context.object
body.scale = (0.56, 0.30, 0.13)
bpy.ops.object.transform_apply(scale=True)
bevel(body, 0.06, 6)
add(body, WHITE)
# dos bombe
bpy.ops.mesh.primitive_uv_sphere_add(radius=1, location=(-0.02, 0, 0.055), segments=48, ring_count=24)
top = bpy.context.object
top.scale = (0.25, 0.135, 0.07)
add(top, WHITE)
# bande d'accent
bpy.ops.mesh.primitive_cube_add(size=1, location=(0.0, 0, 0.068))
stripe = bpy.context.object
stripe.scale = (0.36, 0.05, 0.03)
bevel(stripe, 0.012, 3)
add(stripe, ACCENT)
# batterie a l'arriere
bpy.ops.mesh.primitive_cube_add(size=1, location=(-0.24, 0, 0.02))
bat = bpy.context.object
bat.scale = (0.12, 0.24, 0.11)
bevel(bat, 0.02, 3)
add(bat, CARBON)
# feux avant (yeux) blancs
for y in (-0.09, 0.09):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.018, location=(0.282, y, 0.0))
    add(bpy.context.object, WLED)

# --- Bras, moteurs, helices -------------------------------------------------
ARM_R = 0.62
props = []
for i, ang in enumerate((45, 135, 225, 315)):
    a = math.radians(ang)
    end = Vector((ARM_R * math.cos(a), ARM_R * math.sin(a), 0.035))
    mid = end / 2
    bpy.ops.mesh.primitive_cylinder_add(radius=0.026, depth=ARM_R, location=mid, vertices=24)
    arm = bpy.context.object
    arm.rotation_euler = (0, math.radians(90), a)
    arm.scale = (1, 1.35, 1)
    add(arm, CARBON)
    # moteur : stator + cloche
    bpy.ops.mesh.primitive_cylinder_add(radius=0.058, depth=0.07, location=end + Vector((0, 0, 0.03)), vertices=40)
    bevel(bpy.context.object, 0.01, 3)
    add(bpy.context.object, METAL)
    bpy.ops.mesh.primitive_cylinder_add(radius=0.05, depth=0.03, location=end + Vector((0, 0, 0.078)), vertices=40)
    bevel(bpy.context.object, 0.008, 3)
    add(bpy.context.object, DARKMETAL)
    # feu de bout de bras : rouge a gauche (babord, +Y), vert a droite
    led = RED if math.sin(a) > 0 else GREEN
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.03, location=end + Vector((0, 0, -0.02)))
    add(bpy.context.object, led)
    # helice bipale effilee
    hub = end + Vector((0, 0, 0.1))
    bpy.ops.object.empty_add(location=hub)
    pivot = bpy.context.object
    for s in (1, -1):
        bpy.ops.mesh.primitive_cube_add(size=1, location=(s * 0.19, 0, 0))
        bl = bpy.context.object
        bl.scale = (0.36, 0.045, 0.006)
        bl.location = (s * 0.2, 0, 0)
        bpy.ops.object.transform_apply(location=True, scale=True)
        md = bl.modifiers.new("taper", "SIMPLE_DEFORM")
        md.deform_method = "TWIST"
        md.angle = math.radians(14)
        md.deform_axis = "X"
        bevel(bl, 0.004, 2)
        add(bl, PROP)
        bl.parent = pivot
        bl.location = (0, 0, 0)
    pivot.rotation_euler = (0, 0, math.radians(37 * i))
    pivot["sens"] = 1 if i % 2 == 0 else -1
    props.append(pivot)
    # disque de flou (helice en rotation)
    bpy.ops.mesh.primitive_cylinder_add(radius=0.385, depth=0.002, location=hub, vertices=64)
    add(bpy.context.object, BLUR)

# --- Train d'atterrissage ---------------------------------------------------
for y in (-0.16, 0.16):
    bpy.ops.mesh.primitive_cylinder_add(radius=0.013, depth=0.52, location=(0, y, -0.2), vertices=16)
    sk = bpy.context.object
    sk.rotation_euler = (0, math.radians(90), 0)
    add(sk, CARBON)
    for x in (-0.12, 0.12):
        bpy.ops.mesh.primitive_cylinder_add(radius=0.011, depth=0.17, location=(x, y * 0.82, -0.12), vertices=16)
        lg = bpy.context.object
        lg.rotation_euler = (math.radians(14 if y > 0 else -14), 0, 0)
        add(lg, CARBON)

# --- Nacelle et camera ------------------------------------------------------
bpy.ops.mesh.primitive_cylinder_add(radius=0.018, depth=0.06, location=(0.2, 0, -0.08))
add(bpy.context.object, DARKMETAL)
bpy.ops.mesh.primitive_uv_sphere_add(radius=0.075, location=(0.22, 0, -0.14), segments=48, ring_count=24)
cam_body = bpy.context.object
cam_body.scale = (1.05, 1, 0.95)
add(cam_body, CARBON)
bpy.ops.mesh.primitive_cylinder_add(radius=0.045, depth=0.03, location=(0.29, 0, -0.145), vertices=48)
ring = bpy.context.object
ring.rotation_euler = (0, math.radians(90), 0)
add(ring, LENSRING)
bpy.ops.mesh.primitive_cylinder_add(radius=0.036, depth=0.034, location=(0.293, 0, -0.145), vertices=48)
lens = bpy.context.object
lens.rotation_euler = (0, math.radians(90), 0)
add(lens, GLASS)

# Tout le drone legerement penche vers l'avant (il avance)
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.empty_add(location=(0, 0, 0))
root = bpy.context.object
for o in bpy.data.objects:
    if o is not root and o.parent is None and o.type in ("MESH", "EMPTY"):
        o.parent = root
root.rotation_euler = (0, math.radians(7), 0)

# --- Lumieres : cle chaude douce, contre-jour bleu de la marque -------------
def light(kind, loc, energy, color, size=1.0):
    d = bpy.data.lights.new(kind + str(loc), kind)
    d.energy = energy
    d.color = color
    if kind == "AREA":
        d.size = size
    o = bpy.data.objects.new(d.name, d)
    o.location = loc
    sc.collection.objects.link(o)
    o.rotation_euler = (Vector((0, 0, 0)) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    return o

light("AREA", (1.6, -2.2, 2.6), 320, (1.0, 0.96, 0.9), 1.2)
light("AREA", (-2.0, 1.8, 1.2), 420, srgb("#7d8ff0"), 2.0)
light("AREA", (0.0, 0.0, -2.0), 35, srgb("#5b6fd0"), 3.0)
light("AREA", (-0.5, -2.5, 0.2), 45, (0.9, 0.92, 1.0), 2.0)

world = bpy.data.worlds.new("w")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.02, 0.025, 0.05, 1)
world.node_tree.nodes["Background"].inputs[1].default_value = 0.6
sc.world = world

# --- Camera : trois-quarts avant, un peu au-dessus --------------------------
cd = bpy.data.cameras.new("cam")
cd.type = "ORTHO"
cd.ortho_scale = 2.55
cam = bpy.data.objects.new("cam", cd)
sc.collection.objects.link(cam)
cam.location = (1.3, -3.4, 1.35)
cam.rotation_euler = (Vector((0, 0, -0.05)) - cam.location).to_track_quat("-Z", "Y").to_euler()
sc.camera = cam

r = sc.render
r.engine = "BLENDER_EEVEE_NEXT"
r.resolution_x, r.resolution_y = 640, 360
r.film_transparent = True
r.image_settings.file_format = "PNG"
r.image_settings.color_mode = "RGBA"
sc.eevee.taa_render_samples = 48
try:
    sc.view_settings.view_transform = "AgX"
    sc.view_settings.look = "AgX - Medium High Contrast"
except Exception:
    pass

# --- Images : l'helice bipale revient a l'identique tous les 180 degres -----
base = [p.rotation_euler.z for p in props]
for f in range(NFRAMES):
    for p, b0 in zip(props, base):
        p.rotation_euler.z = b0 + p["sens"] * math.radians(180 / NFRAMES * f)
    r.filepath = os.path.join(OUT, f"f{f}.png")
    bpy.ops.render.render(write_still=True)
print("RENDU OK", OUT)
