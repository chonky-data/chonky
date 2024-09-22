#!/usr/bin/env python

from setuptools import setup

setup(name="chonky",
  packages=["chonky"],
  python_requires='>=3.9',
  entry_points={
    "console_scripts": ["chonky = chonky.__main__:main"]
  },
  install_requires=["platformdirs>=4.1", "boto3>=1.33.9", "tqdm>=4.66.5"]
)