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

**Why this path can never reach the lights (measured 2026-08-16, engine running, `ATRV` 14.2 V).** The OBD-II port on this car exposes *only* the legislated emissions diagnostics. Proven by a discriminating test: `ATSH7E0` + `0100` answers `7E8064100BE3EB811` and `0902` returns the VIN over multi-frame ISO-TP, so custom headers and ISO-TP both work - but `1003`, `22F190` and `3E00` return `NO DATA` on *every* address tried, including the engine's own `7E0`, and functionally via `7DF`. Nothing answers UDS: not BSI `0x752`, not the stalk HDC `0x742`, not CMB `0x782`, CORPRO `0x6B7`, airbag `0x744`, ABS `0x6AD`, radio `0x760`. The BSI/HDC diagnostics live on PSA's DiagOnCan pair (OBD pins 3/11), which a standard ELM327 does not wire - it only connects pins 6/14. That is the root cause of every failure below, and the reason the Lexia works where the ELM cannot. Do not retry BSI-over-ELM; it is not a protocol or baud-rate problem.

Separately, this particular USB adapter (`0918:7104`, reports `ELM327 v1.5`) has a broken `ATMA`: it returns zero frames even with the engine running and the powertrain bus demonstrably busy.

What does not work: light control. Bus sniffing via `ATMA` never produced anything - `can_dump.log` is empty and `can_raw.log` holds a single junk line - so `03_analyze_dump.py` (which diffs a lights-off dump against a lights-on dump per CAN ID) has never had real input. `04_strobe.py` writes to CAN ID `0x128` and `try_lights2.py` tries UDS sessions against BSI (TX `0x752` / RX `0x652`), PROJECTEURS (`0x6B7`/`0x697`), the stalk (`0x742`/`0x642`) and the cluster (`0x75F`/`0x65F`). Note the caveat written into `04_strobe.py`: `0x128` is an *indicator* message (BSI telling the cluster what is lit), not a command, so replaying it would not switch a lamp even if the frames reached the right bus. This path was abandoned in favor of Path B.

### Path B - Lexia 3 over raw USB (`pyusb`) - the working one

Files: `lexia.py`, `police_mode.py`, `police2.py`, `blink_test.py`, plus `sniff_lexia_usb.py` for capture.

A Lexia 3 (PSA dealer interface, USB `103a:f008`) is driven directly through libusb: bulk EP OUT `0x06`, bulk EP IN `0x85`, 64-byte reads. Connecting means detaching the kernel driver and claiming interface 0, which needs root or a udev rule.

The protocol was reverse-engineered by running DiagBox in a VM, passing the Lexia through to it, and capturing the USB traffic with `sniff_lexia_usb.py`, which parses `/dev/usbmon1` binary packets directly (64-byte header struct, filtered to bulk transfers with data). That capture is checked in as `lexia_usb.log` - 5500 packets in `timestamp,type,direction,endpoint,device,length,hex_data` form. It is the ground truth for every magic hex string in the Lexia scripts; re-read it before changing them.

**The device handshake is mandatory and was the missing piece for years.** Sending the session frame `400915c000fe...39` cold makes the device answer with status byte `0x0C` (byte 18 of the reply) and nothing works afterwards. Before any vehicle traffic, DiagBox runs 11 device-level commands that read the interface's own firmware strings (`011113A`, `921815  C/t`, `BOOT1_PSA_XS__ P107441- V1.0.3 @ACTIA`, `APPLI_XS_Fuji_ P106138A V4.3.7 @ACTIA`). Only then does the `fe` frame return status `0x01` and the link to the car come up. That sequence is captured verbatim in `lexia_boot.DEVICE_BOOT` and replayed by `Lexia.device_boot()`; `lexia_proto.link_status()` decodes the status byte. The old capture never showed this because it began mid-session.

Two related traps. Every command - including the device-handshake ones - must go through the full poll/fetch cycle below; sending one and reading the reply directly returns the `064009` acknowledgement or a `1540090b` rejection, never the data. And byte 8 of a command frame is a per-session handle, not the constant `01` seen in the old dump: the fresh capture shows `d9`, `35`, `2b`, `aa`, `98`. The checksum rule is unaffected - the last byte still forces the frame's 8-bit sum to `0xFF`, which is the general rule; `(0xBA - id)` only held while every other byte was constant.

Every command follows the same four-step cycle that DiagBox uses:

1. submit the command frame on EP OUT
2. poll `410901c0f4` repeatedly until the device answers `42410900`
3. read the result with `430901c0f2`
4. acknowledge with `064409`

`police_mode.py` implements this faithfully in `send_and_poll`; `police2.py` and `blink_test.py` use trimmed-down variants that skip waits for speed. Session init before any actuator is `400915c000fe0000aa00000000000000000000000000000039`.

An actuator command is the fixed prefix `40091bc0ff06060001` padded with zeros, followed by `2f d8 <actuator_id> 03 0a 01 <checksum>`, where `checksum = (0xBA - actuator_id) & 0xFF`. The `2f` is UDS InputOutputControlByIdentifier. Known actuator IDs:

| ID | Light | Status on this car |
|----|-------|--------------------|
| `0x70` | side lights | reads OK (`62 D870`) |
| `0x71` | right indicator | reads OK (`62 D871`) |
| `0x72` | left indicator | reads OK |
| `0x73` | rear fog | in DiagBox actuator menu |
| `0x74` | reversing lamps | in DiagBox actuator menu |
| `0x75` | brake light | reads OK |
| `0x29`/`0x2A` | dipped beam L/R | **absent** - `7F 22 31` |
| `0x2B` | **main beam** | **absent** - `7F 22 31` |
| `0x2C` | front fog | **absent** - `7F 22 31` |

**Settled: this BSI cannot drive the headlamps at all.** Measured with `probe_bsi.py` on 2026-08-16 with a working link: `22 D8 2B` returns `7F 22 31` requestOutOfRange, as do `D829`, `D82A` and `D82C`, while `D870`/`D871` read fine in the very same session - so it is not a chain or session problem, the DIDs genuinely do not exist here. DiagBox agrees: its `BSI2010 LIGHTING - SIGNALLING` actuator list on this car contains only the `D87x` block (side lights, indicators, rear fog, reversing, brake, plus courtesy/boot/black-panel items) and offers no main-beam test. `22 D826` (availability of automatic main/dipped switching) reads `00`, i.e. not available. This matches the DiagBox database, where `MP_COMMANDE_FEUX_DE_ROUTE` (`D82B`) appears only in `MESUREPARAMETRE2E_VARB*` - the AFS/bi-function-headlamp variant - while the confirmed `D87x` ids live in `MESUREPARAMETRE3E`, present in every variant. Do not spend more time on main beam via UDS `2F`; the remaining routes are a MITM on the LS.CAR body bus rewriting the stalk frame, or a relay interposer at the headlamp connector.

The key behavioral constraint that shapes all the "modes": the BSI latches an actuator on for roughly 3 seconds and no off command is known. Blinking is therefore done by re-sending init+actuate faster than that timeout expires, or by firing a different actuator to preempt the current one - that is the whole idea behind `police2.py`'s spam loop and the experiments in `blink_test.py` (which also probes an unconfirmed off variant: trailing byte `00` instead of `01`, checksum incremented).

Actuator tests require ignition ON with the engine OFF.

## DBC files

`can_confort.dbc` (comfort bus, nodes BSI CMB RADIO_CD EMF CLIM BTE) and `can_is.dbc` (nodes BSI CMM DAE ABS CAV) are reference databases sourced from PSA CAN reverse-engineering work, not loaded by any script - nothing here depends on `cantools`. The relevant message is `BO_ 296 CDE_COMBINE_SIGNALISATION` (`0x128`) from BSI, whose light bits are 33 left indicator, 34 right indicator, 35 rear fog, 36 front fog, 37 high beam, 38 low beam, 39 daytime running lights, and bit 17 for hazards. Use them to decode captures; do not assume a signal listed there is writable.

## Conventions

Docstrings, log messages and menu text are in Russian - keep new code consistent with that. Every script configures `logging` at module top with the same format string and logs through `log = logging.getLogger(__name__)`, but interactive menus print with bare `print()`. Long-running modes loop until `KeyboardInterrupt`, which is caught to send a shutdown/all-off frame before returning to the menu rather than to exit.

## Import offer

An OpenAI Codex config exists at `~/.codex/config.toml`. Reply `/import` to scan and list what is importable (MCP servers, slash commands, subagents, skills, instructions), then `/import --yes=<digest>` with the digest from the scan output to apply the user-level items. If `/import` is unavailable on this surface, run `claude import` from a terminal.
