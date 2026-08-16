# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Ad-hoc reverse-engineering scripts for controlling the exterior lights of a Citroën C4 (PSA platform) from Linux. There is no package, no build system, and no tests - every `.py` file is a standalone script run directly with `python <script>.py`. Scripts share code by copy-paste, not by imports; that is deliberate, since each one is a self-contained experiment.

## Setup

No virtualenv exists in the tree and none of the dependencies are installed system-wide. The README's `cd /home/jaennil/projects/c4-can` path is stale - the project lives at `/home/jaennil/life/citroen/c4-can`.

```bash
python -m venv .venv
source .venv/bin/activate.fish      # shell is fish
pip install pyserial pyusb bleak    # serial=ELM327, usb=Lexia, bleak=BLE ELM clones
```

Not a git repository despite appearances: `/home/jaennil/life/citroen/.git` is an empty directory, so every `git` command fails with "not a repository". `.gitignore` here contains a bare `*` (venv-generated), so initializing a repo without fixing it would ignore the entire project.

## Two hardware paths

The single most important thing to know: there are two completely separate ways this project talks to the car, and only one of them can actually drive the lights.

### Path A - ELM327 over the OBD-II port (`pyserial`)

Files: `00_*.py`, `01_*.py`, `02_*.py`, `03_analyze_dump.py`, `04_strobe.py`, `dashboard.py`, `monitor.py`, `quick_test.py`, `try_lights*.py`.

All of these open a serial port at 38400 baud and speak ELM327 AT commands. Each script reimplements the same `send_cmd`/`cmd` helper: write `"<cmd>\r"`, read until the `>` prompt appears, strip it. Standard init is `ATZ, ATE0, ATL0, ATS0, ATH1, ATSP6` (protocol 6 = ISO 15765-4 CAN 11-bit 500 kbps), plus `ATCAF0` to get raw unformatted frames.

The default port differs per script and reflects which adapter was in use at the time - `/dev/rfcomm0` for the Bluetooth ELM327 (needs `sudo rfcomm bind 0 <MAC>` and the `bluez-deprecated-tools` package), `/dev/ttyACM0` for the USB one. Most scripts accept the port as `sys.argv[1]`; some hardcode it at module level and open the serial port at import time, so they fail immediately with no adapter attached.

What works on this path: plain OBD-II mode-01 PIDs. `dashboard.py` is the useful artifact - a live terminal readout of RPM, speed, coolant/intake temp, throttle, load, timing advance and fuel trims, parsing responses by locating the `41 <PID>` echo.

What does not work: light control. Bus sniffing via `ATMA` never produced anything - `can_dump.log` is empty and `can_raw.log` holds a single junk line - so `03_analyze_dump.py` (which diffs a lights-off dump against a lights-on dump per CAN ID) has never had real input. `04_strobe.py` writes to CAN ID `0x128` and `try_lights2.py` tries UDS sessions against BSI (TX `0x752` / RX `0x652`), PROJECTEURS (`0x6B7`/`0x697`), the stalk (`0x742`/`0x642`) and the cluster (`0x75F`/`0x65F`). Note the caveat written into `04_strobe.py`: `0x128` is an *indicator* message (BSI telling the cluster what is lit), not a command, so replaying it would not switch a lamp even if the frames reached the right bus. This path was abandoned in favor of Path B.

### Path B - Lexia 3 over raw USB (`pyusb`) - the working one

Files: `lexia.py`, `police_mode.py`, `police2.py`, `blink_test.py`, plus `sniff_lexia_usb.py` for capture.

A Lexia 3 (PSA dealer interface, USB `103a:f008`) is driven directly through libusb: bulk EP OUT `0x06`, bulk EP IN `0x85`, 64-byte reads. Connecting means detaching the kernel driver and claiming interface 0, which needs root or a udev rule.

The protocol was reverse-engineered by running DiagBox in a VM, passing the Lexia through to it, and capturing the USB traffic with `sniff_lexia_usb.py`, which parses `/dev/usbmon1` binary packets directly (64-byte header struct, filtered to bulk transfers with data). That capture is checked in as `lexia_usb.log` - 5500 packets in `timestamp,type,direction,endpoint,device,length,hex_data` form. It is the ground truth for every magic hex string in the Lexia scripts; re-read it before changing them.

Every command follows the same four-step cycle that DiagBox uses:

1. submit the command frame on EP OUT
2. poll `410901c0f4` repeatedly until the device answers `42410900`
3. read the result with `430901c0f2`
4. acknowledge with `064409`

`police_mode.py` implements this faithfully in `send_and_poll`; `police2.py` and `blink_test.py` use trimmed-down variants that skip waits for speed. Session init before any actuator is `400915c000fe0000aa00000000000000000000000000000039`.

An actuator command is the fixed prefix `40091bc0ff06060001` padded with zeros, followed by `2f d8 <actuator_id> 03 0a 01 <checksum>`, where `checksum = (0xBA - actuator_id) & 0xFF`. The `2f` is UDS InputOutputControlByIdentifier. Known actuator IDs:

| ID | Light |
|----|-------|
| `0x70` | side lights |
| `0x71` | right indicator |
| `0x72` | left indicator |
| `0x75` | brake light |

The key behavioral constraint that shapes all the "modes": the BSI latches an actuator on for roughly 3 seconds and no off command is known. Blinking is therefore done by re-sending init+actuate faster than that timeout expires, or by firing a different actuator to preempt the current one - that is the whole idea behind `police2.py`'s spam loop and the experiments in `blink_test.py` (which also probes an unconfirmed off variant: trailing byte `00` instead of `01`, checksum incremented).

Actuator tests require ignition ON with the engine OFF.

## DBC files

`can_confort.dbc` (comfort bus, nodes BSI CMB RADIO_CD EMF CLIM BTE) and `can_is.dbc` (nodes BSI CMM DAE ABS CAV) are reference databases sourced from PSA CAN reverse-engineering work, not loaded by any script - nothing here depends on `cantools`. The relevant message is `BO_ 296 CDE_COMBINE_SIGNALISATION` (`0x128`) from BSI, whose light bits are 33 left indicator, 34 right indicator, 35 rear fog, 36 front fog, 37 high beam, 38 low beam, 39 daytime running lights, and bit 17 for hazards. Use them to decode captures; do not assume a signal listed there is writable.

## Conventions

Docstrings, log messages and menu text are in Russian - keep new code consistent with that. Every script configures `logging` at module top with the same format string and logs through `log = logging.getLogger(__name__)`, but interactive menus print with bare `print()`. Long-running modes loop until `KeyboardInterrupt`, which is caught to send a shutdown/all-off frame before returning to the menu rather than to exit.

## Import offer

An OpenAI Codex config exists at `~/.codex/config.toml`. Reply `/import` to scan and list what is importable (MCP servers, slash commands, subagents, skills, instructions), then `/import --yes=<digest>` with the digest from the scan output to apply the user-level items. If `/import` is unavailable on this surface, run `claude import` from a terminal.
