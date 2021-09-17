# JauriaCNC interface for linuxcnc

![](pics/tnc.png)

JauriaCNC is a QtPyVCP based interface for the LinuxCNC machine control.

## Quick install

Install linuxcnc using linuxcnc 2.90~pre

http://www.linuxcnc.org/


* Dependencies

```
$ sudo apt install python3-pyqt5 python3-pyqt5.qtquick python3-dbus.mainloop.pyqt5 python3-pyqt5.qtopengl python3-pyqt5.qsci python3-pyqt5.qtmultimedia qml-module-qtquick-controls gstreamer1.0-plugins-bad libqt5multimedia5-plugins pyqt5-dev-tools python3-dev python3-setuptools python3-pip git
```

* jauriacnc pip package

```
$ python3 -m pip install jcnc
```

## Custom Install (Optional)

Clone the jcnc repository

```
$ git clone https://github.com/TurBoss/jauriacnc.git
```

Install JauriaCNC using pip

```
$ cd jauriacnc
$ python3 -m pip install -e .
```

Now you can run editvcp to edit the interface

```
$ editvcp tcnc
```


## Documentation

QtPyVCP [documentation](https://kcjengr.github.io/qtpyvcp/).


## Resources

* [Development](https://github.com/TurBoss/jauriacnc/)
* [Documentation](https://qtpyvcp.com/)
* [Libera Chat](http://web.libera.chat/) (#qtpyvcp)
* [The Matrix](https://riot.im/app/#/room/#qtpyvcp:matrix.org) (#qtpyvcp:matrix.org)


## Dependencies

* [LinuxCNC](https://linuxcnc.org)
* [QtPyVCP](https://qtpyvcp.com/)
* Python 3
* PyQt5 or PySide2

JauriaCNC is developed and tested using the LinuxCNC Debian 10 (buster) and 11 (bullseye)
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
