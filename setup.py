from distutils.core import setup

setup(
    name="MozPhab",
    version="0.1.20",
    author="Mozilla",
    author_email="conduit-team@mozilla.com",
    scripts=["moz-phab"],
    url="https://github.com/mozilla-conduit/review",
    license="Mozilla Public License 2.0",
    description="Phabricator review submission/management tool.",
    long_description=open("README.md").read(),
)
