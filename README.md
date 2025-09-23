# TurBo NC interface for linuxcnc


![](pics/tnc.png)

TurBo NC is a QtPyVCP based interface for the LinuxCNC machine control.

## Quick install

Install linuxcnc 2.9.4^
http://www.linuxcnc.org/

## APT install


```commandline
sudo apt update

sudo apt upgrade

sudo apt install curl

echo 'deb [arch=amd64] https://repository.qtpyvcp.com/apt stable main' | sudo tee /etc/apt/sources.list.d/kcjengr.list

curl -sS https://repository.qtpyvcp.com/repo/kcjengr.key | gpg --dearmor | sudo tee /etc/apt/trusted.gpg.d/kcjengr.gpg

gpg --keyserver keys.openpgp.org --recv-key 2DEC041F290DF85A

sudo apt update

sudo apt install python3-qtpyvcp

sudo apt install python3-turbonc
```



## Manual install steps (Optional)

Clone the tnc repository

```
$ git clone https://github.com/KCJengr/turbonc.git
```

Install TurBoNC using pip

```
$ cd tnc
$ python3 -m venv venv
$ source venv/bin/activate
$ python3 -m pip install -e .
$ qcompile .
```

Now you can run editvcp to edit the interface

```
$ editvcp tcnc
```


## Documentation

QtPyVCP [documentation](https://qtpyvcp.com)


## Resources

* [Development](https://github.com/TurBoss/jauriacnc/)
* [Documentation](https://qtpyvcp.com/)
* [Libera Chat](http://web.libera.chat/) (#qtpyvcp)
* [The Matrix](https://riot.im/app/#/room/#qtpyvcp:matrix.org) (#qtpyvcp:matrix.org)


## Dependencies

* [Debian](https://debian.org) 13
* [LinuxCNC](https://linuxcnc.org) 2.9.4^
* [QtPyVCP](https://qtpyvcp.com/)
* Python 3.13**
* PySide6

TurBoNC is developed and tested using the LinuxCNC Debian 13 (trixie)


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
