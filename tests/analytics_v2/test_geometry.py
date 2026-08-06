import unittest

from app.analytics_v2.scene.geometry import (
    bbox_footpoint,
    border_proximity_score,
    movement_crosses_line_segment,
    point_in_polygon,
    point_side_of_line,
    point_side_of_line_with_deadband,
    size_plausibility_from_profile,
)


class GeometryTests(unittest.TestCase):
    def test_footpoint_uses_bbox_bottom_center(self):
        fp = bbox_footpoint([10, 20, 30, 60])
        self.assertEqual((fp.x, fp.y), (20.0, 60.0))

    def test_polygon_and_line_side(self):
        polygon = [(0, 0), (10, 0), (10, 10), (0, 10)]
        self.assertTrue(point_in_polygon((5, 5), polygon))
        self.assertFalse(point_in_polygon((15, 5), polygon))
        self.assertLess(point_side_of_line((1, 1), (0, 0), (10, 0)), 0.0)

    def test_line_deadband_suppresses_jitter(self):
        previous_side, current_side, crossed = point_side_of_line_with_deadband((49, 50), (51, 50), (0, 50), (100, 50), deadband=2.0)
        self.assertFalse(crossed)
        self.assertIsNotNone(previous_side)
        self.assertIsNotNone(current_side)

    def test_line_crossing_requires_drawn_segment(self):
        _, _, inside_segment = movement_crosses_line_segment((50, 40), (50, 60), (0, 50), (100, 50))
        _, _, outside_segment = movement_crosses_line_segment((150, 40), (150, 60), (0, 50), (100, 50))
        self.assertTrue(inside_segment)
        self.assertFalse(outside_segment)

    def test_size_plausibility_penalizes_tiny_objects_far_away(self):
        profile = [{"y_min": 0.0, "y_max": 1.0, "min_bbox_height": 30, "max_bbox_height": 800, "min_bbox_area": 800, "max_bbox_area": 500000}]
        good_score = size_plausibility_from_profile([10, 10, 40, 90], 0.8, profile)
        tiny_score = size_plausibility_from_profile([10, 10, 20, 25], 0.9, profile)
        self.assertGreater(good_score, tiny_score)

    def test_border_proximity_penalizes_edge_points(self):
        center_score = border_proximity_score((50, 50), 100, 100)
        edge_score = border_proximity_score((1, 50), 100, 100)
        self.assertGreater(center_score, edge_score)
        self.assertGreater(center_score, 0.9)
        self.assertLess(edge_score, 0.5)


if __name__ == "__main__":
    unittest.main()
