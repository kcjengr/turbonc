# TurboNC

TurboNC is a QtPyVCP virtual control panel (VCP) for LinuxCNC. It provides a
machine-control interface built with PySide6 and integrates with LinuxCNC
through QtPyVCP's status, position, tool-table, offset-table, notification,
backplot, and action APIs.

The repository contains both the installable `turbonc` Python package and
example LinuxCNC configurations for testing the interface without connected
hardware.

## Requirements

TurboNC is developed and packaged for:

- Debian 13 (Trixie)
- LinuxCNC 2.9.x or newer, with a QtPyVCP-compatible installation
- Python 3.11 or newer
- QtPyVCP with PySide6

The Debian package declares the remaining Qt, VTK, PyQtGraph, YAML, SQLAlchemy,
serial, and related runtime dependencies. Real machines also require a
working LinuxCNC configuration, HAL setup, and appropriate hardware safety
systems.

## Installation

### Debian package

The project publishes `python3-turbonc` packages for amd64 and arm64 through
the KCJengr APT repository. Configure the appropriate repository suite, then
install the package:

```bash
sudo apt update
sudo apt install python3-qtpyvcp python3-turbonc
```

Stable releases use the `trixie` suite. Development builds use
`trixie-dev`. Follow the repository's package-source instructions when adding
the KCJengr APT repository; do not mix the two suites on a production machine.

### Development checkout

Install LinuxCNC and QtPyVCP from the Debian packages first, then install this
repository in editable mode:

```bash
git clone https://github.com/TurBoss/turbonc.git
cd turbonc
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

The package installs the `tnc` command and registers TurboNC as a QtPyVCP VCP
named `tnc`.

To inspect the available launcher options:

```bash
tnc --help
```

For packaging work, the Debian build uses `dpkg-buildpackage` and the
`debian/rules` file. It also runs `qcompile` during package installation so
QtPyVCP can discover the installed VCP resources.

## Running with LinuxCNC

In a LinuxCNC INI file, select TurboNC as the display program:

```ini
[DISPLAY]
DISPLAY = qtpyvcp tnc
```

Then start LinuxCNC with that INI file using the normal LinuxCNC launcher. A
direct launch is also supported by the `tnc` entry point, for example:

```bash
tnc --ini=/path/to/machine.ini
```

Use `--develop` while working on the UI. In development mode, TurboNC watches
the active theme and view-style files and reloads their stylesheets when they
change.

Machine-specific QtPyVCP settings can be layered on top of the package
configuration with a machine `tnc_config.yml`. The repository includes
examples of this in `linuxcnc/configs/sim.qtpyvcp/` and
`linuxcnc/configs/sim.robot_thor/`.

## Interface features

The main window includes the following functional areas:

- Program loading, G-code editing, syntax highlighting, run/step/pause/resume/abort controls, and a VTK backplot
- Machine power, E-stop, homing, coolant, override-limit, and jog controls
- Relative and absolute DROs with per-axis offset actions
- A dynamically populated `SET TO ZERO` panel for the machine's configured axes
- Tool-table support, tool changes, optional `G43 H<n>` tool-length compensation, and tool-related dialogs
- Work-offset and G52/G92 displays
- Probe controls, calibration workflows, probe parameters, and probe-result handling
- Cells controls for subroutine calls and kinematics mode indication
- HAL Meter, HAL Scope, HAL Show, LinuxCNC status, calibration, ClassicLadder, and simulated-probe tools
- Optional on-screen keyboard and persisted application settings

## Themes and settings

TurboNC includes persisted UI customization through **Settings**:

- 14 dark/light accent themes: Cyan, Magenta, Green, Amber, Blue, Orange, White, and Black variants
- Seven view styles: Neon, Flat, Sharp, Rounded, Glow, Retro, and Ink
- Optional background textures, accent tint, custom tint color, and panel opacity
- DRO units and lathe radius/diameter mode
- Backplot grid, bounds, machine display, motion colors, camera gizmo, perspective, and view angle
- Confirm-before-exit and on-screen keyboard settings

## Included LinuxCNC simulations

The `linuxcnc/configs/sim.qtpyvcp/` directory contains QtPyVCP simulation
configurations, including:

- `xyz-metric.ini`: three-axis metric simulation
- `xyz.ini`: three-axis imperial simulation
- `xyz3s.ini`: three-axis, three-spindle simulation
- `xyzb.ini`: four-axis simulation
- `xyzcw.ini`: five-axis simulation
- `xyzy-gantry.ini`: three-axis gantry simulation with dual Y drives

The `linuxcnc/configs/sim.robot_thor/` directory contains a six-axis Thor
robot simulation with robot models, kinematics/HAL files, tool data, sample
G-code, and a TurboNC-specific configuration overlay.

Select the desired INI file from the LinuxCNC configuration chooser, or run it
using the standard LinuxCNC command-line tools. The simulation configurations
are intended for UI and integration testing; they are not machine-specific
motion-control configurations.

## Repository layout

```text
src/tnc/                         TurboNC Python package and Qt resources
src/tnc/ui/window.ui             Main Qt Designer interface
src/tnc/config.yml               Base QtPyVCP configuration
src/tnc/menubar.yml              File, Machine, Settings, View, and Tools menus
src/tnc/themes/                  QSS themes, view styles, and backgrounds
src/tnc/dialogs/                 Settings, probing, homing, offsets, and robot dialogs
linuxcnc/configs/                LinuxCNC simulation configurations
linuxcnc/nc_files/               Example G-code and probing subroutines
debian/                          Debian package metadata and build rules
.github/workflows/               Stable and development package publishing
```

## Development notes

The main Python entry points are `tnc.main:MainWindow` and `tnc:main`. The
QtPyVCP configuration loads `src/tnc/ui/window.ui`, while theme resources are
loaded from `src/tnc/themes/`.

When changing the Qt Designer UI, keep the generated Python resource/UI files
in sync with the project workflow. When changing QSS, use `tnc --develop` for
live stylesheet reloads. Validate changes against one of the included LinuxCNC
simulation configurations before using them on a real machine.

Stable packages are built from `v*` tags. Development packages are built from
pushes to the `pyside6` branch and are published to the `trixie-dev` suite.

## Related documentation

- [LinuxCNC](https://linuxcnc.org/)
- [QtPyVCP](https://qtpyvcp.com/)
- [TurboNC development repository](https://github.com/TurBoss/turbonc)

## Safety disclaimer

TurboNC is machine-control software. Do not rely on software alone for
safety. Any machinery capable of harming people must have suitable emergency
stops, power-removal provisions, guarding, interlocks, and procedures that
comply with applicable local and national requirements.

The authors accept no liability for harm or loss resulting from the use of
this software. This project is released under the GPLv3.
