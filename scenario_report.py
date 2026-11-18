"""Offline synthetic scenario evidence; emits JSON to stdout."""
import json
import math

from rover_sim import CELL_M, DEADMAN_S, DOCK, PET, Events, Rover, centre, clear_body


def until(robot, mode, limit=180):
    segments = []
    previous_cell, entry = None, None
    ticks = 0
    while robot.mode != mode and ticks < limit * 20:
        robot.tick(0.05)
        if not clear_body(robot.x, robot.y):
            raise RuntimeError("body overlaps synthetic wall")
        here = (math.floor(robot.x), math.floor(robot.y))
        if here != previous_cell:
            entry = {"cell": list(here), "entered_s": round(robot.now, 2)}
            segments.append(entry)
            previous_cell = here
        ticks += 1
    if robot.mode != mode:
        raise RuntimeError(f"{mode} not reached within {limit} simulated seconds: {robot.mode}")
    return segments


def run():
    robot = Rover(Events(":memory:"))
    robot.command("operator", "claim")
    robot.command("operator", "search")
    found_trace = until(robot, "found")
    search = {"time_s": round(robot.now, 2), "model_distance_m": round(robot.distance, 3),
              "events": len(robot.events.recent()), "trace": found_trace}
    robot.command("operator", "claim")
    robot.command("operator", "low")
    start_time, start_distance = robot.now, robot.distance
    home_trace = until(robot, "charging")
    docking = {"start_soc_percent": 23, "end_soc_percent": round(robot.battery, 2),
               "time_s": round(robot.now - start_time, 2),
               "model_distance_m": round(robot.distance - start_distance, 3),
               "final_dock_center_error_m": round(math.dist((robot.x, robot.y), centre(DOCK)) * CELL_M, 6),
               "trace": home_trace}
    missing = Rover(Events(":memory:"), pet=None)
    missing.command("operator", "claim")
    missing.command("operator", "search")
    until(missing, "not_found")
    manual = Rover(Events(":memory:"))
    manual.command("operator", "claim")
    manual.command("operator", "drive", "forward")
    until(manual, "idle", limit=2)
    result = {
        "schema": 1, "evidence_type": "deterministic software simulation; NOT hardware measurements",
        "conditions": {"dt_s": 0.05, "cell_m": CELL_M, "map": "synthetic 13 x 9 fixed grid",
                       "pet_cell": PET, "battery": "linear demo model; no real capacity or charging feedback",
                       "geometry": "2D circular footprint; noiseless map/pose; instantaneous waypoint turns"},
        "found": search, "low_battery_return": docking,
        "not_found": {"end_mode": missing.mode, "events": len(missing.events.recent()),
                      "time_s": round(missing.now, 2)},
        "lost_command": {"configured_deadline_s": DEADMAN_S, "observed_idle_at_s": round(manual.now, 2),
                         "simulated_model_travel_m": round(manual.distance, 3)},
        "limits": ["no sensor noise, real pet, wheels, charging contacts, voltage, IR or measured LAN latency",
                   "zero center error is imposed by the grid model, not physical docking accuracy"]}
    for device in (robot, missing, manual):
        device.events.conn.close()
    return result


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
