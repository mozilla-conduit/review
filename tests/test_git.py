import imp
import mock
import os
import unittest

review = imp.load_source(
    "review", os.path.join(os.path.dirname(__file__), os.path.pardir, "moz-phab")
)

git_config = ["user.email=email"]


@mock.patch("review.Git.git_out")
@mock.patch("review.Git._get_current_head")
@mock.patch("review.Config")
@mock.patch("review.os.path")
@mock.patch("review.which")
@mock.patch("review.Repository._phab_url")
class TestGit(unittest.TestCase):
    def test_cherry(
        self,
        m_repository_phab_url,
        m_which,
        m_os_path,
        m_config,
        m_git_get_current_head,
        m_git_git_out,
    ):
        m_os_path.join = os.path.join
        m_os_path.exists.return_value = True
        m_which.return_value = True
        m_os_path.isfile.return_value = False
        m_git_get_current_head.return_value = "branch"

        m_git_git_out.side_effect = (git_config, review.CommandError, ["output"])
        git = review.Git("x")
        self.assertEqual(["output"], git._cherry(["cherry"], ["one", "two"]))
        m_git_git_out.assert_has_calls(
            [mock.call(["cherry", "one"]), mock.call(["cherry", "two"])]
        )

    @mock.patch("review.Git._cherry")
    def test_first_unpublished(
        self,
        m_git_cherry,
        m_repository_phab_url,
        m_which,
        m_os_path,
        m_config,
        m_git_get_current_head,
        m_git_git_out,
    ):
        m_os_path.join = os.path.join
        m_os_path.exists.return_value = True
        m_which.return_value = True
        m_os_path.isfile.return_value = False
        m_git_get_current_head.return_value = "branch"

        class Args:
            def __init__(self, upstream=None, start_rev="(auto)"):
                self.upstream = upstream
                self.start_rev = start_rev

        m_git_git_out.side_effect = (git_config, ["a", "b"], ["c"], ["d"])
        m_git_cherry.side_effect = (["- sha1", "+ sha2"], [], None, [])
        git = review.Git("x")
        git.args = Args()
        first = git._get_first_unpublished_node
        self.assertEqual("sha2", first())
        m_git_cherry.assert_called_with(["cherry", "--abbrev=12"], ["a", "b"])
        self.assertIsNone(first())
        with self.assertRaises(review.Error):
            first()

        git.args = Args(upstream=["upstream"])
        first()
        m_git_cherry.assert_called_with(["cherry", "--abbrev=12", "upstream"], [])

    def test_get_dirsect_children(
        self,
        m_repository_phab_url,
        m_which,
        m_os_path,
        m_config,
        m_git_get_current_head,
        m_git_git_out,
    ):
        m_os_path.join = os.path.join
        m_os_path.exists.return_value = True
        m_which.return_value = True
        m_os_path.isfile.return_value = False
        m_git_get_current_head.return_value = "branch"
        m_git_git_out.return_value = git_config
        git = review.Git("x")

        get_children = git._get_direct_children
        rev_list = ["aaa bbb ccc", "bbb", "ccc ddd"]

        self.assertEqual(["bbb", "ccc"], get_children("aaa", rev_list))
        self.assertEqual([], get_children("bbb", rev_list))
        self.assertEqual(["ddd"], get_children("ccc", rev_list))
        self.assertEqual([], get_children("xxx", rev_list))


    def test_is_child(
        self,
        m_repository_phab_url,
        m_which,
        m_os_path,
        m_config,
        m_git_get_current_head,
        m_git_git_out,
    ):
        m_os_path.join = os.path.join
        m_os_path.exists.return_value = True
        m_which.return_value = True
        m_os_path.isfile.return_value = False
        m_git_get_current_head.return_value = "branch"
        m_git_git_out.return_value = git_config
        git = review.Git("x")

        is_child = git._is_child
        # * ccc
        # * bbb
        # * aaa
        nodes = ["ccc", "bbb ccc", "aaa bbb"]
        self.assertTrue(is_child("aaa", "bbb", nodes))
        self.assertTrue(is_child("aaa", "ccc", nodes))
        self.assertTrue(is_child("bbb", "ccc", nodes))
        self.assertFalse(is_child("bbb", "aaa", nodes))
        self.assertFalse(is_child("aaa", "aaa", nodes))
        self.assertFalse(is_child("bbb", "bbb", nodes))
        self.assertFalse(is_child("ccc", "ccc", nodes))

        # * ddd
        # | * ccc
        # | | * eee
        # | |/
        # | * bbb
        # |/
        # * aaa
        nodes = ["ddd", "ccc", "eee", "bbb ccc eee", "aaa bbb ddd"]
        self.assertTrue(is_child("aaa", "bbb", nodes))
        self.assertTrue(is_child("aaa", "ccc", nodes))
        self.assertTrue(is_child("aaa", "ddd", nodes))
        self.assertTrue(is_child("aaa", "eee", nodes))
        self.assertTrue(is_child("bbb", "ccc", nodes))
        self.assertTrue(is_child("bbb", "eee", nodes))
        self.assertFalse(is_child("bbb", "ddd", nodes))
        self.assertFalse(is_child("ccc", "ddd", nodes))
