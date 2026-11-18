import http.client
import json
import math
from pathlib import Path
import tempfile
import threading
import unittest

from rover_sim import (DOCK, Events, LEASE_S, Rejected, Rover, Server, centre, clear_body, free, route)
from scenario_report import run, until


class MotionTests(unittest.TestCase):
    def setUp(self):
        self.robot = Rover(Events(":memory:"))
        self.addCleanup(self.robot.events.conn.close)
        self.robot.command("phone", "claim")

    def test_exclusive_control_and_emergency_stop(self):
        with self.assertRaises(Rejected):
            self.robot.command("tablet", "claim")
        self.robot.command("phone", "drive", "forward")
        self.robot.command("tablet", "stop")
        position = self.robot.x, self.robot.y
        self.robot.tick(0.1)
        self.assertEqual((self.robot.x,self.robot.y), position)
        self.assertEqual(self.robot.mode, "stopped")
        for action in ("search", "home", "low"):
            with self.assertRaises(Rejected):
                self.robot.command("phone", action)
        self.robot.command("phone", "reset")
        self.assertEqual(self.robot.mode, "idle")
        self.assertEqual(self.robot.linear, 0)

    def test_expired_lease_cannot_move_and_new_client_can_claim(self):
        self.robot.command("phone", "drive", "forward")
        self.robot.tick(LEASE_S + 0.2)
        self.assertEqual(self.robot.mode, "idle")
        self.assertEqual(self.robot.distance, 0)
        with self.assertRaises(Rejected):
            self.robot.command("phone", "drive", "forward")
        self.robot.command("tablet","claim")
        self.assertEqual(self.robot.state("tablet")["control"], "mine")

    def test_drive_watchdog_is_not_extended_by_ping(self):
        self.robot.command("phone","drive","forward")
        self.robot.tick(0.1)
        for _ in range(4):
            self.robot.command("phone","ping")
            self.robot.tick(0.1)
        self.assertEqual(self.robot.mode,"idle")
        self.assertLess(self.robot.distance, 0.23)

    def test_body_hits_wall_and_stops(self):
        self.robot.heading = math.pi
        for _ in range(20):
            self.robot.command("phone", "drive", "forward")
            self.robot.tick(0.1)
            if self.robot.bumps:
                break
        self.assertGreater(self.robot.bumps, 0)
        self.assertEqual(self.robot.mode,"idle")
        self.assertTrue(clear_body(self.robot.x,self.robot.y))

    def test_route_neither_teleports_nor_crosses_walls(self):
        path = route(DOCK,(10,3))
        self.assertTrue(path)
        for former, latter in zip([DOCK] + path[:-1],path):
            self.assertTrue(free(latter))
            self.assertEqual(abs(former[0]-latter[0])+abs(former[1]-latter[1]), 1)
        self.assertIsNone(route(DOCK,(0,0)))
        self.assertEqual(route(DOCK,DOCK),[])

    def test_camera_occlusion_and_field_of_view(self):
        self.robot.x,self.robot.y = centre((4,2))
        self.robot.pet=(6,2)
        self.robot.heading=0
        self.assertFalse(self.robot.sees_pet())
        self.robot.pet=(4,3)
        self.assertFalse(self.robot.sees_pet())
        self.robot.heading=math.pi/2
        self.assertTrue(self.robot.sees_pet())

    def test_low_soc_overrides_existing_search(self):
        self.robot.command("phone","search")
        self.robot.battery = 24.9
        self.robot.tick(.05)
        self.assertEqual(self.robot.mode,"return")
        with self.assertRaises(Rejected):
            self.robot.command("phone","drive","forward")
        until(self.robot,"charging",limit=2)
        before = self.robot.battery
        self.robot.tick(.1)
        self.assertGreater(self.robot.battery,before)

    def test_depleted_outside_dock_is_not_charging(self):
        self.robot.x,self.robot.y=centre((3,3))
        self.robot.battery=0
        self.robot.tick(.05)
        self.assertEqual(self.robot.mode,"depleted")
        with self.assertRaises(Rejected):
            self.robot.command("phone","home")

    def test_events_persist_and_limit_retention(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp)/"rover.db"
            log = Events(db)
            try:
                for index in range(304):
                    log.add(index,"manual","南区","synthetic","<svg/>")
            finally:
                log.conn.close()
            log=Events(db)
            try:
                self.assertIsNone(log.image(4))
                self.assertEqual(log.image(5),"<svg/>")
                self.assertEqual(len(log.recent()),15)
                self.assertEqual(log.recent()[0]["id"],304)
            finally:
                log.conn.close()

    def test_reproducible_end_to_end_scenarios(self):
        report=run()
        self.assertEqual(run(), report)
        self.assertEqual(report["found"]["events"],1)
        self.assertEqual(report["not_found"]["events"],0)
        self.assertEqual(report["low_battery_return"]["final_dock_center_error_m"],0)


class InterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = Events(":memory:")
        cls.server=Server(("127.0.0.1",0),Rover(cls.store),"test-fixture")
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.store.conn.close()

    def request(self, path, method="GET", body=None, client="a"*32, authorized=True):
        con=http.client.HTTPConnection("127.0.0.1",self.server.server_port,timeout=3)
        headers={"X-Client":client}
        if authorized:
            headers["Authorization"]="Bearer test-fixture"
        if body is not None:
            body=json.dumps(body)
        try:
            con.request(method,path,body=body,headers=headers)
            response=con.getresponse()
            return response.status,response.getheader("Content-Type"),response.read()
        finally:
            con.close()

    def test_two_client_shared_state_and_event(self):
        self.assertEqual(self.request("/api/command","POST",{"action":"claim"})[0],200)
        self.assertEqual(self.request("/api/command","POST",{"action":"claim"},client="b"*32)[0],409)
        self.assertEqual(self.request("/api/command","POST",{"action":"capture"})[0],200)
        state=json.loads(self.request("/api/state",client="b"*32)[2])
        self.assertTrue(state["simulation"])
        self.assertEqual(state["control"],"busy")
        status,mime,image=self.request("/api/events/"+str(state["events"][0]["id"]),client="b"*32)
        self.assertEqual(status,200)
        self.assertIn("image/svg+xml",mime)
        self.assertIn(b"SIMULATED VIEW",image)
        self.assertEqual(self.request("/api/command","POST",{"action":"stop"},client="b"*32)[0],200)
        self.assertEqual(json.loads(self.request("/api/state")[2])["mode"],"stopped")

    def test_authentication_and_unspecified_assets(self):
        self.assertEqual(self.request("/api/state",authorized=False)[0],401)
        self.assertEqual(self.request("/api/preview",client="")[0],400)
        self.assertEqual(self.request("/api/events/999999")[0],404)
        self.assertEqual(self.request("/not-an-asset")[0],404)
        status,_,body=self.request("/",authorized=False)
        self.assertEqual(status,200)
        self.assertIn("仿真台".encode(),body)


if __name__ == "__main__":
    unittest.main()
