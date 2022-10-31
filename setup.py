import os
import sys

from setuptools import setup

entry_points = {"console_scripts": ["moz-phab = mozphab.mozphab:run"]}
if "develop" in sys.argv:
    entry_points["console_scripts"].append("moz-phab-dev = mozphab.mozphab:run_dev")

VERSION = "1.2.1"

# Validate that `CIRCLE_TAG` matches the current version.
circle_tag = os.getenv("CIRCLE_TAG")
if circle_tag and circle_tag != VERSION:
    raise Exception("`CIRCLE_TAG` does not match `VERSION`.")


setup(
    author="Mozilla",
    author_email="conduit-team@mozilla.com",
    description="Phabricator review submission/management tool.",
    entry_points=entry_points,
    include_package_data=True,
    install_requires=[
        "distro",
        "glean-sdk>=50.0.1,==50.*",
        "packaging",
        "python-hglib>=2.6.2",
        "sentry-sdk>=0.14.3",
        "setuptools",
    ],
    license="Mozilla Public License 2.0",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    name="MozPhab",
    package_data={"mozphab": ["metrics.yaml", "pings.yaml"]},
    packages=["mozphab", "mozphab/commands"],
    # Note: Please change the `updater.py::check_for_updates` method if the format
    # would be different than >=X.Y
    python_requires=">=3.7",
    url="https://github.com/mozilla-conduit/review",
    version=VERSION,
    zip_safe=False,
)
