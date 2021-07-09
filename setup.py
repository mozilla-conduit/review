import sys

from setuptools import setup

entry_points = {"console_scripts": ["moz-phab = mozphab.mozphab:run"]}
if "develop" in sys.argv:
    entry_points["console_scripts"].append("moz-phab-dev = mozphab.mozphab:run_dev")

setup(
    author="Mozilla",
    author_email="conduit-team@mozilla.com",
    description="Phabricator review submission/management tool.",
    entry_points=entry_points,
    include_package_data=True,
    install_requires=[
        "distro",
        "glean-sdk>=36.0.0",
        "python-hglib==2.6.1",
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
    python_requires=">=3.6",
    url="https://github.com/mozilla-conduit/review",
    version="1.0.0rc1",
    zip_safe=False,
)
