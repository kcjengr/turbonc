from setuptools import setup, find_packages

with open("README.md", "r") as fh:
    long_description = fh.read()

setup(
    name="jcnc",
    version="0.0.2",
    author="Jauria Studios",
    author_email="",
    description="Jauria CNC - A QtPyVCP based Virtual Control Panel for LinuxCNC",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/TurBoss/jauriacnc",
    download_url="https://github.com/TurBoss/jauriacnc/tarball/master",
    packages=find_packages(),
    include_package_data=True,
    entry_points={
        'gui_scripts': [
            'jcnc=jcnc:main',
        ],
        'qtpyvcp.vcp': [
            'jcnc=jcnc',
        ],
    },
    install_requires=[
       'qtpyvcp',
    ],
)
