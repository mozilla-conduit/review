from setuptools import setup

setup(
    name="MozPhab",
    version="0.1.74",
    author="Mozilla",
    author_email="conduit-team@mozilla.com",
    packages=["mozphab"],
    entry_points={"console_scripts": ["moz-phab = mozphab.mozphab:run"]},
    url="https://github.com/mozilla-conduit/review",
    license="Mozilla Public License 2.0",
    description="Phabricator review submission/management tool.",
    long_description=open("README.md").read(),
    long_description_content_type="text/markdown",
    python_requires=">=3.5",
    install_requires=["setuptools"],
)
