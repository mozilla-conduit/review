import imp
import os
import unittest

mozphab = imp.load_source(
    "mozphab", os.path.join(os.path.dirname(__file__), os.path.pardir, "moz-phab")
)


class Configuration(unittest.TestCase):
    def test_default_arc_command(self):
        _is_windows = mozphab.IS_WINDOWS

        mozphab.IS_WINDOWS = False
        config = mozphab.Config(should_access_file=False)
        self.assertEqual(config.arc_command, "arc")

        mozphab.IS_WINDOWS = True
        config = mozphab.Config(should_access_file=False)
        self.assertEqual(config.arc_command, "arc.bat")

        mozphab.IS_WINDOWS = _is_windows


if __name__ == "__main__":
    unittest.main()
