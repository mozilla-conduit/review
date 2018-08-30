import imp
import os
import unittest

review = imp.load_source(
    "review", os.path.join(os.path.dirname(__file__), os.path.pardir, "moz-phab")
)


class CommitParsing(unittest.TestCase):
    def test_bug_id(self):
        parse = review.parse_bugs

        self.assertEqual(parse("bug 1"), ["1"])
        self.assertEqual(parse("bug 123456"), ["123456"])
        self.assertEqual(parse("testb=1234x"), [])
        self.assertEqual(parse("ab4665521e2f"), [])
        self.assertEqual(parse("Aug 2008"), [])
        self.assertEqual(parse("GECKO_191a2_20080815_RELBRANCH"), [])
        self.assertEqual(parse("12345 is a bug"), [])
        self.assertEqual(parse(" bug   123456 whitespace!"), ["123456"])
        self.assertEqual(parse("bug 1 and bug 2"), ["1", "2"])

    def test_reviewers(self):
        parse = review.parse_reviewers

        # first with r? reviewer request syntax
        self.assertEqual(["romulus"], parse("stuff; r?romulus"))
        self.assertEqual(["romulus", "remus"], parse("stuff; r?romulus, r?remus"))
        self.assertEqual(["romulus", "remus"], parse("stuff; r?romulus,r?remus"))
        self.assertEqual(["romulus", "remus"], parse("stuff; r?romulus, remus"))
        self.assertEqual(["romulus", "remus"], parse("stuff; r?romulus,remus"))
        self.assertEqual(["romulus"], parse("stuff; (r?romulus)"))
        self.assertEqual(["romulus", "remus"], parse("stuff; (r?romulus,remus)"))
        self.assertEqual(["romulus"], parse("stuff; [r?romulus]"))
        self.assertEqual(["remus", "romulus"], parse(" stuff; [r?remus, r?romulus]"))
        self.assertEqual(["romulus"], parse("stuff; r?romulus, a=test-only"))
        self.assertEqual(["romulus"], parse("stuff; r?romulus, ux-r=test-only"))

        # now with r= review granted syntax
        self.assertEqual(["romulus"], parse("stuff; r=romulus"))
        self.assertEqual(["romulus", "remus"], parse("stuff; r=romulus, r=remus"))
        self.assertEqual(["romulus", "remus"], parse("stuff; r=romulus,r=remus"))
        self.assertEqual(["romulus", "remus"], parse("stuff; r=romulus, remus"))
        self.assertEqual(["romulus", "remus"], parse("stuff; r=romulus,remus"))
        self.assertEqual(["romulus"], parse("stuff; (r=romulus)"))
        self.assertEqual(["romulus", "remus"], parse("stuff; (r=romulus,remus)"))
        self.assertEqual(["romulus"], parse("stuff; [r=romulus]"))
        self.assertEqual(["remus", "romulus"], parse("stuff; [r=remus, r=romulus]"))
        self.assertEqual(["romulus"], parse("stuff; r=romulus, a=test-only"))

        # try some other separators than ;
        self.assertEqual(["romulus"], parse("stuff r=romulus"))
        self.assertEqual(["romulus", "remus"], parse("stuff. r=romulus, r=remus"))
        self.assertEqual(["romulus", "remus"], parse("stuff - r=romulus,r=remus"))
        self.assertEqual(["romulus", "remus"], parse("stuff, r=romulus, remus"))
        self.assertEqual(["romulus", "remus"], parse("stuff.. r=romulus,remus"))
        self.assertEqual(["romulus"], parse("stuff | (r=romulus)"))

        # make sure things work with different spacing
        self.assertEqual(["romulus", "remus"], parse("stuff;r=romulus,r=remus"))
        self.assertEqual(["romulus", "remus"], parse("stuff.r=romulus, r=remus"))
        self.assertEqual(["romulus", "remus"], parse("stuff,r=romulus, remus"))
        self.assertEqual(["gps"], parse("stuff; r=gps DONTBUILD (NPOTB)"))
        self.assertEqual(["gps"], parse("stuff; r=gps DONTBUILD"))
        self.assertEqual(["gps"], parse("stuff; r=gps (DONTBUILD)"))

        # bare r?
        self.assertEqual([], parse("stuff; r?"))
        self.assertEqual([], parse("stuff, r="))

        # oddball real-world examples
        self.assertEqual(
            ["roc", "ehsan"],
            parse(
                "Bug 1094764 - Implement AudioContext.suspend and friends.  r=roc,ehsan"
            ),
        )
        self.assertEqual(
            ["bsmedberg", "dbaron"],
            parse(
                "Bug 380783 - nsStringAPI.h: no equivalent of IsVoid (tell if "
                "string is null), patch by Mook <mook.moz+mozbz@gmail.com>, "
                "r=bsmedberg/dbaron, sr=dbaron, a1.9=bz"
            ),
        )
        self.assertEqual(
            ["hsinyi"],
            parse(
                "Bug 1181382: move declaration into namespace to resolve conflict. "
                "r=hsinyi. try: -b d -p all -u none -t none"
            ),
        )
        self.assertEqual(
            ["bsmedberg"],
            parse(
                "Bug 1024110 - Change Aurora's default profile behavior to use "
                "channel-specific profiles. r=bsmedberg f=gavin,markh"
            ),
        )
        self.assertEqual(
            ["gijs"],
            parse(
                "Bug 1199050 - Round off the corners of browser-extension-panel's "
                "content. ui-r=maritz, r=gijs"
            ),
        )
        self.assertEqual(
            ["billm"],
            parse(
                "Bug 1197422 - Part 2: [webext] Implement the pageAction API. "
                "r=billm ui-r=bwinton"
            ),
        )

    def test_arc_diff_rev(self):
        parse = review.parse_arc_diff_rev

        self.assertEqual("1", parse("Differential Revision: https://example.com/D1"))
        self.assertEqual("22", parse("Differential Revision: https://example.com/D22"))
        self.assertEqual("1", parse("Differential Revision:https://example.com/D1"))
        self.assertEqual("1", parse("Differential Revision:   https://example.com/D1"))
        self.assertEqual(
            "22", parse("   Differential Revision: https://example.com/D22")
        )
        self.assertEqual("1", parse("Differential Revision: https://example.com/D1  "))
        self.assertEqual(
            "1", parse("\n  Differential Revision: https://example.com/D1")
        )
        self.assertEqual("1", parse("Differential Revision: https://example.com/D1\n"))
        self.assertIsNone(parse("Differential Revision: https://example.com/D1 1"))
        self.assertIsNone(parse("Differential Revision: http://example.com/D1"))
        self.assertIsNone(parse("\n  Differential Revision: http://example.com/Q1"))

    def test_arc_reject(self):
        reject = review.has_arc_rejections

        self.assertTrue(reject("Summary: blah\nReviewers: blah\n"))
        self.assertTrue(reject("Reviewers: blah\nSummary: blah\n"))
        self.assertTrue(reject("blah\nSummary: blah\n\nReviewers: blah"))
        self.assertTrue(reject("Summary:\n\nReviewers:\n\n"))
        self.assertFalse(reject("Summary: blah"))
        self.assertFalse(reject("Reviewers: blah"))


if __name__ == "__main__":
    unittest.main()
