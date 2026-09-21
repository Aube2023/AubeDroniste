#!/usr/bin/env python3
"""Balises simulées AubeBeacon (AUBE-BCN-SIM001..003) pour développer la carte
sans matériel. Crée les balises manquantes pour un compte pilote, régénère
leurs jetons et écrit le fichier devices.json que lit le simulateur
(AubeBeacon/simulator/drone-simulator/simulate.py).

    .venv/bin/python scripts/beacon_sim_devices.py --user nicolas \
        --out ../AubeBeacon/simulator/drone-simulator/devices.json

Options : --count 3, --server http://127.0.0.1:5034, --create-drones (ajoute
un drone de démonstration par balise si la flotte est vide ; jamais en prod).
Le fichier produit contient des jetons : ne le versionnez pas.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

HOMES = [(45.5122, -73.5301), (45.5368, -73.4939), (45.5605, -73.7124), (45.4700, -73.6300), (45.6100, -73.5500)]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user", required=True, help="username du compte pilote propriétaire")
    ap.add_argument("--count", type=int, default=3)
    ap.add_argument("--server", default="http://127.0.0.1:5034")
    ap.add_argument("--out", default=os.path.join(HERE, "..", "..", "AubeBeacon", "simulator", "drone-simulator", "devices.json"))
    ap.add_argument("--create-drones", action="store_true")
    args = ap.parse_args()

    from app import app
    import db
    import services
    from beacon import devices

    with app.app_context():
        user = db.fetchone("SELECT * FROM users WHERE username=?", (args.user,))
        if not user:
            raise SystemExit(f"compte {args.user!r} introuvable")
        if user["role"] not in ("pilot", "both"):
            raise SystemExit(f"{args.user} n'est pas un compte pilote (rôle {user['role']})")
        drones = services.list_drones(user["id"])
        if not drones and args.create_drones:
            for i in range(args.count):
                services.add_drone(user["id"], category="pro_camera", brand="DJI", model=f"Mavic 3 (sim {i + 1})")
            drones = services.list_drones(user["id"])
        out = []
        for i in range(args.count):
            uid = f"AUBE-BCN-SIM{i + 1:03d}"
            device = devices.get_by_uid(uid)
            drone_id = drones[i % len(drones)]["id"] if drones else None
            if device is None:
                device, token = devices.create_device(user["id"], label=f"Drone simulé {i + 1}",
                                                      drone_id=drone_id, device_uid=uid, user_id=user["id"])
                print(f"créée   {uid} (drone {drone_id})")
            else:
                if device["owner_user_id"] != user["id"]:
                    raise SystemExit(f"{uid} appartient à un autre compte")
                token = devices.rotate_token(device["id"], user["id"], user_id=user["id"])
                print(f"jeton régénéré {uid}")
            out.append({"device_id": uid, "token": token, "home": list(HOMES[i % len(HOMES)])})
    path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"server": args.server, "devices": out}, f, indent=1)
    os.chmod(path, 0o600)
    print(f"écrit {path} ({len(out)} balises, jetons inclus : ne pas versionner)")


if __name__ == "__main__":
    main()
