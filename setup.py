# coding=utf-8
from setuptools import setup, find_packages


def parse_requirements(filename):
    """ load requirements from a pip requirements file """
    lineiter = (line.strip() for line in open(filename))
    return [line for line in lineiter if line and not line.startswith("#")]


requirements = parse_requirements('requirements.txt')

setup(
    name="a816",
    version="0.0.5",
    license="BSD",
    url="https://github.com/manz/a816",
    packages=find_packages('.', exclude=['a816.tests']),
    include_package_data=True,
    classifiers=[
        "Operating System :: OS Independent",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3.6"
    ],
    scripts=[
        'x816'
    ],
    install_requires=['ply']
)
