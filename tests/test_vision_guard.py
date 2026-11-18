from dataclasses import replace
import math
import unittest

from vision_guard.depth_guard import classify,fixtures


class DepthTests(unittest.TestCase):
    def setUp(self):
        self.cal,self.frames=fixtures()

    def check(self,frame,age=10,camera="synthetic_depth"):
        result=classify(frame,self.cal,camera_id=camera,age_ms=age)
        self.assertFalse(result["motion_authorization"])
        return result

    def test_clear_confined_to_visible_roi(self):
        frame=self.frames["far"]
        frame[0][0]=150
        self.assertEqual(self.check(frame)["state"],"clear")

    def test_connected_close_cluster_projects_optical_depth(self):
        result=self.check(self.frames["close"])
        self.assertEqual(result["state"],"stop")
        self.assertEqual(result["largest_close_cluster_px"],4)
        self.assertEqual(result["cluster_sample_camera_xyz_mm"],[-9.38,-9.38,300.0])

    def test_cluster_outranks_partial_blindness(self):
        frame=self.frames["close"]
        for row in (2,3,4):
            for col in (2,3,4):
                frame[row][col]=math.nan
        self.assertEqual(self.check(frame)["state"],"stop")

    def test_local_hole_not_hidden_by_global_coverage(self):
        result=self.check(self.frames["blind_tile"])
        self.assertGreater(result["valid_fraction"],.9)
        self.assertEqual(result["state"],"unknown")
        self.assertEqual(result["worst_tile_fraction"],0)

    def test_invalid_zero_nan_range_and_subrange(self):
        for val in (0,None,math.nan,math.inf,-10,3100):
            frame=[[val]*16 for _ in range(12)]
            self.assertEqual(self.check(frame)["state"],"unknown")
        self.frames["far"][4][4]=60
        self.assertEqual(self.check(self.frames["far"])["reason"],"below_minimum_range")

    def test_disconnected_diagonal_dots_are_not_a_confirmed_cluster(self):
        frame=self.frames["far"]
        for index in range(4):
            frame[4+index][4+index]=400
        result=self.check(frame)
        self.assertEqual(result["state"],"unknown")
        self.assertEqual(result["largest_close_cluster_px"],1)

    def test_mismatches_staleness_and_invalid_calibration(self):
        frame=self.frames["close"]
        self.assertEqual(self.check(frame,camera="other")["state"],"unknown")
        self.assertEqual(self.check(frame[:-1])["state"],"unknown")
        for age in (-1,201,math.nan):
            self.assertEqual(self.check(frame,age)["state"],"unknown")
        with self.assertRaises(ValueError):
            classify(frame,replace(self.cal,fx=0),camera_id=self.cal.camera_id,age_ms=0)


if __name__=="__main__":
    unittest.main()
