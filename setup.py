# coding=utf-8
from setuptools import setup, find_packages

setup(
    name="a816",
    version="0.1.0",
    license="MIT",
    url="https://github.com/manz/a816",
    packages=find_packages(".", exclude=["a816.tests"]),
    package_data={"a816": ["py.typed"]},
    include_package_data=True,
    classifiers=[
        "Operating System :: OS Independent",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3.6",
    ],
    entry_points={
        "console_scripts": [
            "x816 = a816.cli:cli_main",
        ]
    },
)
