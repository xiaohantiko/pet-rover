from pathlib import Path
import subprocess
import sys
import unittest

from navigation.mission import Grid, Inputs, Mission, demonstration


class MissionTests(unittest.TestCase):
    def setUp(self):
        self.inputs=Inputs(grid=Grid(2,2,1,0,0,0,[0,0,100,-1]),xy=(.2,.2),fraction=.8)
        self.policy=Mission([(1.2,.2,0)],"dock")

    def tick(self,now):
        self.inputs.clock_at=self.inputs.battery_at=self.inputs.pose_at=now
        return self.policy.tick(self.inputs,now)

    def test_wait_for_map_pose_and_power(self):
        self.policy.tick(Inputs(),0)
        self.assertEqual(self.policy.mode,"wait_inputs")
        self.policy.tick(Inputs(),31)
        self.assertEqual(self.policy.mode,"fault")

    def test_no_goals_to_unknown_blocked_or_outside(self):
        for target in ((.4,1.3,0),(1.5,1.2,0),(-.1,0,0)):
            policy=Mission([target],"dock")
            self.inputs.clock_at=self.inputs.battery_at=self.inputs.pose_at=0
            self.assertIsNone(policy.tick(self.inputs,0))
            self.assertEqual(policy.mode,"fault")

    def test_low_battery_cancels_before_sending_dock(self):
        self.assertEqual(self.tick(0)[0],"navigate")
        self.inputs.fraction=.22
        self.assertEqual(self.tick(1)[0],"cancel")
        self.assertIsNone(self.tick(2))
        self.policy.result("canceled",2)
        self.assertEqual(self.tick(3),("dock","dock"))
        self.assertEqual(self.policy.mode,"to_dock")

    def test_stale_or_abort_never_dispatches_next_goal(self):
        self.tick(0)
        self.assertEqual(self.policy.tick(self.inputs,8)[0],"cancel")
        self.policy.result("canceled",9)
        self.assertEqual(self.policy.mode,"fault")
        self.policy=Mission([(1.5,.2,0)],"dock")
        self.tick(10)
        self.inputs.abort=True
        self.assertEqual(self.tick(11)[0],"cancel")
        self.policy.result("ok",12)
        self.assertEqual(self.policy.mode,"aborted")
        self.assertIsNone(self.tick(13))

    def test_cancel_timeout_does_not_send_new_goal(self):
        self.tick(0)
        self.inputs.fraction=.2
        self.tick(1)
        self.assertIsNone(self.tick(7))
        self.assertEqual(self.policy.mode,"fault")
        self.policy.result("canceled",8)
        self.assertIsNone(self.tick(9))

    def test_dock_success_requires_new_charging_messages(self):
        self.inputs.fraction=.2
        self.assertEqual(self.tick(0)[0],"dock")
        self.policy.result("ok",1)
        self.inputs.charging=True
        self.tick(1)
        self.policy.tick(self.inputs,2)
        self.assertEqual(self.policy.mode,"verify")
        self.tick(3)
        self.policy.tick(self.inputs,5)
        self.assertEqual(self.policy.mode,"verify")
        self.tick(5.1)
        self.assertEqual(self.policy.mode,"charging")

    def test_found_cancels_without_another_waypoint(self):
        self.tick(0)
        self.inputs.found=True
        self.assertEqual(self.tick(1)[0],"cancel")
        self.policy.result("canceled",2)
        self.assertEqual(self.policy.mode,"found")

    def test_unseen_area_is_not_reported_empty(self):
        self.tick(0)
        self.policy.result("ok",1)
        self.assertIsNone(self.tick(2))
        self.assertEqual(self.policy.mode,"watch")
        self.tick(4)
        self.assertEqual(self.policy.mode,"not_found")

    def test_demo_replay_and_action_mode_requires_opt_in(self):
        trace=demonstration()
        self.assertEqual(trace[-1]["mode"],"charging")
        cmds=[step["intent"][0] for step in trace if step["intent"]]
        self.assertEqual(cmds,["navigate","cancel","dock"])
        command=Path(__file__).resolve().parents[1]/"navigation"/"nav2_adapter.py"
        outcome=subprocess.run([sys.executable,str(command),"nav2-sim"],capture_output=True)
        self.assertNotEqual(outcome.returncode,0)
        self.assertIn(b"ack-isolated-sim",outcome.stderr)


if __name__=="__main__":
    unittest.main()
