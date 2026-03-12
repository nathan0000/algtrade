# setup.py
from setuptools import setup, find_packages

setup(
    name="deepseek",
    version="1.0.0",
    packages=find_packages(),
    install_requires=[
        "ibapi>=10.19.1",
        "pandas>=1.5.0",
        "numpy>=1.24.0",
        "pytz>=2023.3",
    ],
    python_requires=">=3.8",
)