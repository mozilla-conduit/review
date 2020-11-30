import sys

from setuptools import setup

entry_points = {"console_scripts": ["moz-phab = mozphab.mozphab:run"]}
if "develop" in sys.argv:
    entry_points["console_scripts"].append("moz-phab-dev = mozphab.mozphab:run_dev")

setup(
    name="MozPhab",
    version="0.1.93",
    author="Mozilla",
    author_email="conduit-team@mozilla.com",
    packages=["mozphab", "mozphab/commands"],
    entry_points=entry_points,
    url="https://github.com/mozilla-conduit/review",
    license="Mozilla Public License 2.0",
    description="Phabricator review submission/management tool.",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    # Note: Please change the `updater.py::check_for_updates` method if the format
    # would be different than >=X.Y
    python_requires=">=3.6",
    include_package_data=True,
    package_data={"mozphab": ["metrics.yaml", "pings.yaml"]},
    install_requires=[
        "distro",
        "glean-sdk==33.0.4",
        "python-hglib==2.6.1",
        "sentry-sdk>=0.14.3",
        "setuptools",
    ],
)
