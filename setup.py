import sys

from setuptools import setup

entry_points = {"console_scripts": ["moz-phab = mozphab.mozphab:run"]}
if "develop" in sys.argv:
    entry_points["console_scripts"].append("moz-phab-dev = mozphab.mozphab:run_dev")

setup(
    name="MozPhab",
    version="0.1.88",
    author="Mozilla",
    author_email="conduit-team@mozilla.com",
    packages=["mozphab", "mozphab/commands"],
    entry_points=entry_points,
    url="https://github.com/mozilla-conduit/review",
    license="Mozilla Public License 2.0",
    description="Phabricator review submission/management tool.",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    python_requires=">=3.5",
    install_requires=["sentry-sdk", "setuptools"],
)
