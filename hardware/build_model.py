"""Generates the Teachify desk tripod as STL files. Units are millimetres.

Run: .venv/bin/python hardware/build_model.py
Parts ending in _ref are reference shapes for the 3D viewer, not for printing.

The tripod holds a camera about 30 cm above a sheet of paper. Two swappable
mounts attach to the head with a standard 1/4"-20 thumbscrew, so they also fit
any store-bought camera tripod:
  phone_clamp    grips a phone by its long edges, camera facing down
  camera_cradle  holds a Seeed XIAO ESP32S3 Sense, lens facing down

Hardware: 3 x M3x16 bolts and nuts (legs), 1 x M5x25 bolt and wing nut (tilt),
1 x 1/4"-20 thumbscrew and hex nut (mount), a 16-LED WS2812 ring (44.5 mm).
Assembled positions are exported; rotate each part flat in your slicer.
Measure your own phone and parts and test-fit first.
"""
from math import cos, radians, sin
from pathlib import Path

import manifold3d as m3d
import numpy as np
import trimesh

OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "models"
SEGMENTS = 96

# Layout
HUB_Z = 150.0                  # leg pivot height
PIVOT_R = 34.0                 # leg pivot distance from the centre
FOOT_R = 150.0                 # foot distance from the centre
LEG_ANGLES = (180.0, 55.0, -55.0)  # back leg, then two front legs either side of the paper
COLUMN_R, COLUMN_BORE = 11.0, 7.5  # hollow so the USB cable runs inside
COLUMN_Z0, COLUMN_TOP = 142.0, 320.0
LIGHT_Z = 250.0                # bottom of the light collar, below the phone
TILT_Z = 336.0                 # head tilt axis
REACH = 75.0                   # mount centre, forward of the column
MOUNT_Z = TILT_Z - 6.0         # underside of the head plate
PHONE_W = 78.0                 # phone width including case; jaws grip these edges
PHONE_T = 11.0                 # phone thickness including case
FIT = 0.25


def cyl(h, r, x=0.0, y=0.0, z=0.0, seg=SEGMENTS):
    return m3d.Manifold.cylinder(h, r, r, seg).translate([x, y, z])


def ycyl(length, r, x, z):
    """Cylinder whose axis runs along Y, centred on y = 0."""
    return m3d.Manifold.cylinder(length, r, r, SEGMENTS).translate([0, 0, -length / 2]).rotate([90, 0, 0]).translate([x, 0, z])


def box(x0, x1, y0, y1, z0, z1):
    return m3d.Manifold.cube([x1 - x0, y1 - y0, z1 - z0], False).translate([x0, y0, z0])


def hull(*parts):
    return m3d.Manifold.batch_hull(list(parts))


def union(parts):
    out = parts[0]
    for p in parts[1:]:
        out = out + p
    return out


# ---- Legs and hub -------------------------------------------------------

def leg():
    """One leg, at angle 0. Print three."""
    eye = ycyl(8.0, 7.0, PIVOT_R, HUB_Z)
    # Stay 8 mm thick until clear of the hinge plates, then widen toward the foot.
    dx, dz = FOOT_R - PIVOT_R, 6.5 - HUB_Z
    length = (dx * dx + dz * dz) ** 0.5
    neck = ycyl(8.0, 6.0, PIVOT_R + 18 * dx / length, HUB_Z + 18 * dz / length)
    foot = m3d.Manifold.sphere(6.5, SEGMENTS).translate([FOOT_R, 0, 6.5])
    pad = cyl(2.0, 9.0, FOOT_R, 0, 0)
    body = hull(eye, neck) + hull(neck, foot, pad)
    return body - ycyl(20, 1.7, PIVOT_R, HUB_Z)


def hub():
    body = cyl(32, 26, z=HUB_Z - 16)
    for angle in LEG_ANGLES:
        gap = 4.25
        plates = box(22, 42, gap, gap + 4, HUB_Z - 9, HUB_Z + 9) + box(22, 42, -gap - 4, -gap, HUB_Z - 9, HUB_Z + 9)
        plates = plates - ycyl(30, 1.7, PIVOT_R, HUB_Z)
        body = body + plates.rotate([0, 0, angle])
    socket = cyl(26, COLUMN_R + FIT, z=COLUMN_Z0)
    cable_room = cyl(10, 8.5, z=HUB_Z - 17)
    # The USB cable leaves between the back and left legs.
    cable_exit = cyl(40, 7.0).rotate([0, 90, 0]).rotate([0, 0, 120]).translate([0, 0, HUB_Z - 11])
    return body - socket - cable_room - cable_exit


def column():
    tube = cyl(COLUMN_TOP - COLUMN_Z0, COLUMN_R, z=COLUMN_Z0) - cyl(400, COLUMN_BORE, z=0)
    led_wires = cyl(12, 2.5).rotate([0, 90, 0]).translate([0, 0, LIGHT_Z + 9])
    return tube - led_wires


# ---- Status light -------------------------------------------------------

def collar():
    """Holds the LED ring around the column, where the teacher can see it."""
    z = LIGHT_Z
    ring = cyl(12, 27, z=z) - cyl(30, COLUMN_R + FIT, z=z - 10)
    recess = cyl(3, 23, z=z + 9.5)
    wire_groove = box(COLUMN_R - 1, 23, -2, 2, z + 7, z + 13)
    grub = cyl(30, 1.4).rotate([0, -90, 0]).translate([0, 0, z + 6])
    return ring - recess - wire_groove - grub


def diffuser():
    """Print in clear or white."""
    return cyl(3, 27, z=LIGHT_Z + 12) - cyl(10, COLUMN_R + 0.4, z=LIGHT_Z + 8)


def led_ring_ref():
    return cyl(1.6, 22.25, z=LIGHT_Z + 9.6) - cyl(4, 15.75, z=LIGHT_Z + 8)


# ---- Head ---------------------------------------------------------------

def yoke():
    """Caps the column and carries the tilt hinge."""
    cap = cyl(19, 15, z=305) - cyl(15, COLUMN_R + FIT, z=305)
    plates = []
    for y0 in (4.4, -8.4):
        plate = box(-9, 9, y0, y0 + 4, 322, TILT_Z) + ycyl(4, 9, 0, TILT_Z).translate([0, y0 + 2, 0])
        plates.append(plate)
    return union([cap] + plates) - ycyl(30, 2.65, 0, TILT_Z)


def head():
    """Tilting arm that reaches over the paper. Mounts screw into its plate."""
    eye = ycyl(8.2, 8, 0, TILT_Z)
    neck = box(0, 20, -4.1, 4.1, TILT_Z - 6, TILT_Z + 6)
    arm = hull(box(16, 20, -4.1, 4.1, TILT_Z - 6, TILT_Z + 6), box(52, 60, -11, 11, TILT_Z - 6, TILT_Z + 6))
    plate = box(REACH - 20, REACH + 20, -22, 22, MOUNT_Z, TILT_Z) + box(52, REACH, -11, 11, MOUNT_Z, TILT_Z + 6)
    body = union([eye, neck, arm, plate])
    pins = cyl(3, 2, REACH - 14, 0, MOUNT_Z - 3) + cyl(3, 2, REACH + 14, 0, MOUNT_Z - 3)
    body = body + pins
    holes = ycyl(30, 2.65, 0, TILT_Z) + cyl(20, 3.4, REACH, 0, MOUNT_Z - 1) + cyl(4, 7, REACH, 0, TILT_Z + 3)
    return body - holes


def mount_top():
    """Shared top of both mounts: a captive 1/4"-20 nut and two locating holes."""
    block = box(-18, 18, -18, 18, -10, 0)
    nut = cyl(6.01, 6.6, z=-6, seg=6)
    holes = cyl(11, 3.4, z=-10.5) + cyl(4.1, 2.25, -14, 0, -4) + cyl(4.1, 2.25, 14, 0, -4)
    return block, nut + holes


def at_mount(part):
    return part.translate([REACH, 0, MOUNT_Z])


def phone_clamp():
    block, cut = mount_top()
    half = PHONE_W / 2
    bar = box(-half - 6, half + 6, -10, 10, -16, -10)
    jaws = []
    for side in (1, -1):
        outer = side * (half + 4 + FIT)
        inner = side * (half + FIT)
        jaw = box(min(inner, outer), max(inner, outer), -12, 12, -16 - PHONE_T - 3, -10)
        lip_inner = side * (half - 4)
        lip = box(min(lip_inner, inner), max(lip_inner, inner), -12, 12, -16 - PHONE_T - 3, -16 - PHONE_T)
        jaws += [jaw, lip]
    return at_mount(union([block, bar] + jaws) - cut)


def phone_ref():
    return at_mount(box(-PHONE_W / 2 + 1, PHONE_W / 2 - 1, -78, 78, -16 - PHONE_T, -16))


def camera_cradle():
    block, cut = mount_top()
    shell = box(-16, 16, -13, 13, -30, -10)
    pocket = box(-11, 17, -9.4, 9.4, -27, -12)  # board slides in from the open end
    lens = cyl(4, 5.5, z=-31)
    return at_mount(union([block, shell]) - cut - pocket - lens)


def board_ref():
    pcb = box(-10.5, 10.5, -8.9, 8.9, -14, -12.8)
    sense = box(-10.5, 10.5, -8.9, 8.9, -20, -14)
    lens = cyl(6, 4, z=-26)
    usb = box(10.5, 13.5, -4.5, 4.5, -14.5, -12)
    return at_mount(union([pcb, sense, lens, usb]))


def paper_ref():
    return box(REACH - 139.5, REACH + 139.5, -108, 108, 0, 0.6)


PARTS = {
    "hub": (hub, True),
    "leg": (leg, True),
    "column": (column, True),
    "collar": (collar, True),
    "diffuser": (diffuser, True),
    "yoke": (yoke, True),
    "head": (head, True),
    "phone_clamp": (phone_clamp, True),
    "camera_cradle": (camera_cradle, True),
    "led_ring_ref": (led_ring_ref, False),
    "phone_ref": (phone_ref, False),
    "board_ref": (board_ref, False),
    "paper_ref": (paper_ref, False),
}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (build, printable) in PARTS.items():
        mesh = build().to_mesh()
        tm = trimesh.Trimesh(
            vertices=np.asarray(mesh.vert_properties)[:, :3],
            faces=np.asarray(mesh.tri_verts),
        )
        if printable and not tm.is_watertight:
            raise SystemExit(f"{name} is not watertight")
        tm.export(OUT / f"{name}.stl")
        size = tm.bounds[1] - tm.bounds[0]
        print(
            f"{name:14s} {size[0]:6.1f} x {size[1]:6.1f} x {size[2]:6.1f} mm  "
            f"{tm.volume / 1000:6.1f} cm3  watertight={tm.is_watertight}"
        )
    # Stability: the loaded centre of mass must sit inside the feet.
    front_x = FOOT_R * cos(radians(LEG_ANGLES[1]))
    print(f"front feet at x = {front_x:.0f} mm, {2 * FOOT_R * sin(radians(LEG_ANGLES[1])):.0f} mm apart; mount at x = {REACH:.0f} mm")


if __name__ == "__main__":
    main()
