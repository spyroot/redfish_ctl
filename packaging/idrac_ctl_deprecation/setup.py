"""PyPI redirect shim for the renamed package.

`idrac_ctl` was renamed to `redfish_ctl`. This is a metadata-only distribution
published to the existing `idrac_ctl` PyPI project so that `pip install idrac_ctl`
transparently installs `redfish_ctl` (which provides the idrac_ctl alias command,
`import idrac_ctl`, and the IDRAC_* env vars). It ships NO modules of its own --
redfish_ctl owns the actual idrac_ctl import package and console script, so there
is no file collision when both are installed.

This lives in the redfish_ctl repo (packaging/idrac_ctl_deprecation/) so the
redirect is reproducible and re-publishable, but it is a SEPARATE distribution
from the main package and is never built by the root setup.py.
"""
from setuptools import setup

with open("README.md") as fh:
    long_description = fh.read()

setup(
    name="idrac_ctl",
    # Latest legacy idrac_ctl on PyPI is 1.0.13; 2.0.0 marks the rename boundary.
    version="2.0.0",
    description="Deprecated: idrac_ctl was renamed to redfish_ctl. Installing this installs redfish_ctl.",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Mustafa Bayramov",
    author_email="spyroot@gmail.com",
    url="https://github.com/spyroot/redfish_ctl",
    project_urls={
        "New package (redfish_ctl)": "https://pypi.org/project/redfish-ctl/",
        "Source": "https://github.com/spyroot/redfish_ctl",
    },
    license="MIT",
    python_requires=">=3.10",
    # The whole point: pull in the renamed package.
    install_requires=["redfish_ctl>=1.1.1"],
    # Metadata-only: no packages, no modules, no entry points.
    packages=[],
    py_modules=[],
    classifiers=[
        "Development Status :: 7 - Inactive",
        "Environment :: Console",
        "Intended Audience :: System Administrators",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Topic :: System :: Systems Administration",
    ],
)
