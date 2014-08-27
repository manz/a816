# coding=utf-8
from setuptools import setup, find_packages
from pip.req import parse_requirements

requirements = parse_requirements('requirements.txt')

setup(
    name="a816",
    version="0.0.2",
    license="BSD",
    url="https://github.com/manz/a816",
    packages=find_packages('.', exclude=['a816.tests']),
    include_package_data=True,
    classifiers=[
        "Operating System :: OS Independent",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3.4"
    ],
    scripts = [
        'x816'
    ]
    # install_requires=[str(requirement.req) for requirement in requirements]
)