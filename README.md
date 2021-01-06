# JauriaCNC interface for linuxcnc

![](pics/tnc.png)

JauriaCNC is a QtPyVCP based interface for the LinuxCNC machine control.

## Quick install

install linuxcnc using the linuxcnc 2.80 iso

http://www.linuxcnc.org/
http://www.linuxcnc.org/downloads/


* Dependencies

```
$ sudo apt install python-pyqt5 python-pyqt5.qtquick python-dbus.mainloop.pyqt5 python-pyqt5.qtopengl python-pyqt5.qsci python-pyqt5.qtmultimedia qml-module-qtquick-controls gstreamer1.0-plugins-bad libqt5multimedia5-plugins pyqt5-dev-tools python-dev python-setuptools python-pip git
```

* jauriacnc pip package

```
$ pip install jcnc
```

## Customize

clone the jcnc repository

```
$ git clone https://github.com/TurBoss/jauriacnc.git
```

install the dev version using pip

```
$ cd jauriacnc
$ pip install -e .
```

now you can run editvcp to edit the interface

```
$ editvcp jcnc
```


## Documentation

QtPyVCP [documentation](https://kcjengr.github.io/qtpyvcp/).


## Resources

* [Development](https://github.com/TurBoss/jauriacnc/)
* [Documentation](https://kcjengr.github.io/qtpyvcp/)
* [Freenode IRC](http://webchat.freenode.net/?channels=%23hazzy) (#hazzy)
* [The Matrix](https://riot.im/app/#/room/#qtpyvcp:matrix.org) (#qtpyvcp:matrix.org)
* [Gitter](https://gitter.im/kcjengr/qtpyvcp)
* [Discord](https://discord.gg/463hMhd)
* [Issue Tracker](https://github.com/TurBoss/jauriacnc/issues)


## Dependancies

* [LinuxCNC](https://linuxcnc.org)
* Python 2.7
* PyQt5 or PySide2
* [QtPyVCP](https://qtpyvcp.kcjengr.com/)

JauriaCNC is developed and tested using the LinuxCNC Debian 10 (buster)
[Live ISO](http://www.linuxcnc.org/download/) It should run
on any system that can have PyQt5 installed, but Debian 10 is the only OS
that is officially supported.


## DISCLAIMER

THE AUTHORS OF THIS SOFTWARE ACCEPT ABSOLUTELY NO LIABILITY FOR
ANY HARM OR LOSS RESULTING FROM ITS USE.  IT IS _EXTREMELY_ UNWISE
TO RELY ON SOFTWARE ALONE FOR SAFETY.  Any machinery capable of
harming persons must have provisions for completely removing power
from all motors, etc, before persons enter any danger area.  All
machinery must be designed to comply with local and national safety
codes, and the authors of this software can not, and do not, take
any responsibility for such compliance.

This software is released under the GPLv2.