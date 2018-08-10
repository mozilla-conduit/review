import imp
import os
import unittest

review = imp.load_source(
    "review", os.path.join(os.path.dirname(__file__), os.path.pardir, "review")
)


class Configuration(unittest.TestCase):
    def test_default_arc_command(self):
        review.IS_WINDOWS = False
        config = review.Config()
        self.assertEqual(config.arc_command, "arc")

        review.IS_WINDOWS = True
        config = review.Config()
        self.assertEqual(config.arc_command, "arc.bat")
