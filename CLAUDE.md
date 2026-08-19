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

**Actuator tests require ignition ON with the engine OFF - measured, not folklore.** A/B tested on 2026-08-16 in one session each. Engine off, ignition on: `2F D8 70` returns `6F D8 70 03` and the side lights physically light. Engine running at ~750 rpm (BSI supply 14.28 V, vehicle speed 0): the identical frame returns `7F 2F 22` conditionsNotCorrect on every attempt, three tries spaced 4 s apart, while `22 D8 70` and `22 D8 71` still read `62 ... 00` in the same session. So the gate is specific to service `2F` and keyed on engine state; the link and the DIDs are fine. An earlier research pass claimed a forum user ran PSA light actuator tests with the engine running and concluded the engine-off rule was an unfounded local convention - that conclusion is wrong for this car.

A second, separate meaning of the same NRC: while an actuator test is already latched, re-sending the same command also returns `7F 2F 22`, and it does **not** extend the latch. So `conditionsNotCorrect` here means either "engine running" or "test already active". To hold a lamp on, re-send just after the ~3 s latch expires (`lights.py` uses `LATCH + 0.25`); re-sending sooner is silently rejected and leaves a brief gap when the latch lapses. This also explains the lone `7F 2F 22` in the original `lexia_usb.log`, which earlier analysis misread as evidence of a general precondition gate.

Vehicle state is readable while the engine runs, which makes tests self-documenting - see `car_state.py`: `22DBA8` engine rpm (factor 0.125), `22DB61` vehicle speed (0.01 km/h), `22DA44` BSI supply voltage (0.001 V), `22DD18` key position, `22DD03` powertrain state. Data begins at index 3 of the `62 D8 xx ...` payload. `22DA46` battery voltage reads `FFFE`, i.e. not available.

## DBC files

`can_confort.dbc` (comfort bus, nodes BSI CMB RADIO_CD EMF CLIM BTE) and `can_is.dbc` (nodes BSI CMM DAE ABS CAV) are reference databases sourced from PSA CAN reverse-engineering work, not loaded by any script - nothing here depends on `cantools`. The relevant message is `BO_ 296 CDE_COMBINE_SIGNALISATION` (`0x128`) from BSI, whose light bits are 33 left indicator, 34 right indicator, 35 rear fog, 36 front fog, 37 high beam, 38 low beam, 39 daytime running lights, and bit 17 for hazards. Use them to decode captures; do not assume a signal listed there is writable.

## Conventions

Docstrings, log messages and menu text are in Russian - keep new code consistent with that. Every script configures `logging` at module top with the same format string and logs through `log = logging.getLogger(__name__)`, but interactive menus print with bare `print()`. Long-running modes loop until `KeyboardInterrupt`, which is caught to send a shutdown/all-off frame before returning to the menu rather than to exit.

## Import offer

An OpenAI Codex config exists at `~/.codex/config.toml`. Reply `/import` to scan and list what is importable (MCP servers, slash commands, subagents, skills, instructions), then `/import --yes=<digest>` with the digest from the scan output to apply the user-level items. If `/import` is unavailable on this surface, run `claude import` from a terminal.

## Телеметрия в Grafana

`telemetry.py` опрашивает BSI и пишет в локальный SQLite (`storage.py`), `sync.py`
идемпотентно досылает накопленное в Postgres домашнего кластера. Ноутбук - сборщик,
кластер - архив: поездки вдали от дома не теряются, а графики доступны и когда
ноутбук выключен.

Инфраструктура живёт в другом репозитории, `homelab-infra`, и уже развёрнута:
namespace `citroen`, кластер CloudNativePG (база `car`, владелец `car`), читающая
роль `grafana_reader` с тем же паролем, что у life-dashboard, поэтому переменная
`$GRAFANA_READER_PASSWORD` в деплое Grafana работает для обоих источников.
Источник данных в Grafana называется "Citroen C4". Оба секрета запечатаны
через SealedSecret, открытых паролей в репозиториях нет.

    export CAR_PG="postgresql://car:ПАРОЛЬ@<хост>:5432/car"
    ./.venv/bin/python telemetry.py --preset engine --hz 2 --sqlite car.db
    ./.venv/bin/python sync.py

Пароль владельца базы:

    kubectl get secret citroen-postgres-credentials -n citroen \
      -o jsonpath='{.data.password}' | base64 -d

Каталог параметров (`did_catalog.py`, 773 штуки) сгенерирован из дампа базы
DiagBox; `sweep.py` обходит их все и записывает в `live_dids.py` те 316, что
реально существуют на этой машине.

## ECU switching - solved (2026-08-18)

For years only the BSI was reachable, and two earlier attempts to switch ECUs produced
false positives (D400-D402, then D411/D415 - both are BSI *index* DIDs). The answer came
from a targeted capture of DiagBox actually switching blocks: `capture-switch.sh` (one
command; sets the collector's pause flag, loads usbmon, records, and clears the flag on
exit - do NOT use `exec` there, it destroys the trap), analysed by `analyze_switch.py`.
Raw capture is `switch.log.gz`, the ground truth for everything below.

**The target ECU is not a command - it is a field in the protocol configuration table.**
The `b5=16` frame carries records `50 <index> <lo> <hi>` (16-bit, low byte first):

    50 01 52 07  ->  P01 = 0x0752   request address on CAN
    50 02 52 06  ->  P02 = 0x0652   reply address

`0x752/0x652` is the PSA DiagOnCan pair for the BSI, which is what proves the field was
read correctly. Two traps: the table does not fit one USB frame, so it is sent as two
(byte 3 is `0x80` on the first, `0x41` on the last, `0xc0` when a command is single -
bit `0x40` means "last", `0x80` means "first"); and continuation frames carry only a
4-byte header, not the usual 24. `Lexia.transact_frames()` handles this: write every
fragment, poll once after the last.

Entry into a block is five frames - session reset, descriptor, table (two frames),
StartCommunication. They are replayed **verbatim** from `ecu_entry.py` (generated by
`gen_ecu_entry.py`), not rebuilt: hand-built frames are exactly what produced the two
earlier false positives. 39 addresses have a recorded entry sequence.

**`extract_payload` had a bug that hid every non-BSI reply.** It required a literal
marker `12 <len> 00 05`; byte 25 is not a constant - BSI sends `0x12`, the engine sends
`0x08`. Responses are now located by the `00 05` pair and the length byte, at the fixed
offset first and by search as a fallback.

### The ECU map of this car

Read out of the capture: every block answers `22F080` or `2180` with its PSA part number
(5 bytes BCD, then a 2-byte supplier code), and `sw_mapping.json` in the DiagBox DB clone
maps that number to a model.

| TX/RX | part no. | ECU | what it is |
|---|---|---|---|
| `0x752/0x652` | 9664992380 | BSI2010 | body computer (also returns the VIN) |
| `0x747/0x647` | 9664998880 | BSM_2010 | engine-bay relay/fuse box |
| `0x742/0x642` | 9665666777 | COM2008P | steering-column stalk |
| `0x75F/0x65F` | 9665731480 | COMBINE_UDS | instrument cluster |
| `0x744/0x644` | 9806788680 | RBG_UDS | airbag |
| `0x765/0x665` | 9804496980 | EMF_C_UDS | multifunction display |
| `0x75D/0x65D` | 9800409680 | AAS_UDS | parking sensors |
| `0x730/0x710` | 9665925480 | CDPL_UDS_HELLA | rain/light sensor |
| `0x731/0x711` | 9665232380 | BPGA2010 | door module |
| `0x77B/0x67B` | 9804078277 | FMUX | control panel |
| `0x6B5/0x695` | 9803319180 | GEP | electric pump |
| `0x6C8/0x628` | 9672044777 | VCI | - |
| `0x760/0x660` | 9808620980 | RD5 | radio |
| `0x6A8/0x688` | 9804436280 | MEV17.4.2 | engine - see below |
| `0x6AD/0x68D` | - | ESP81 | ABS/ESP |

Engine and ABS are absent from `sw_mapping.json` (it covers body/comfort only) and were
identified differently: request `2182` exists in only 17 ECU families - MEV17 among them,
no ABS family - and in the capture it was sent to `0x6A8` and nowhere else, alongside the
actuator requests `218700/218701` that match the MEV17 catalog's `VA*` groups. The engine
is a 1.6 VTi (EP6C, code 5FS, family EC5), Euro 5, Bosch MEV17.4.2; DiagBox cannot
auto-detect it (`script_reco_INJ.s` offers a manual list) so it must be picked by hand.

Identification by DB request-fingerprint is implemented in `id_ecus.py` but is only
trustworthy when a whole request set is exclusive to one family - that is how BSM was
found independently before the part numbers confirmed it. Ranking by rarity of individual
requests and by coverage fraction were both tried and are useless: on the *known* `0x752`
neither returns BSI. Generic services (`2221xx` identification, `22F0xx/22F1xx`, `19xxxx`
DTCs) must be filtered out first - they are what made `0x747` look like an IVI head unit.

### Two access models

`ecu_catalog.py` (generated by `gen_catalog.py`, 1393 parameters over 14 blocks) keeps
both, and `poll_all.py` polls them:

* **UDS blocks** - `22 <DID>`, one DID per parameter, batched (measured limits: 10 DIDs
  and 30 response bytes, over which the batch is refused *whole*). Parameter data sits at
  `sb-4` inside its DID's data.
* **KWP blocks** - `21 <LID> [args]`, and one request returns a whole record from which
  parameters are taken at `sb-1` of the reply. The engine yields 189 parameters from 14
  requests - far denser than the BSI's per-DID reads.

The offset rule was verified on a real captured reply: at `0x6A8`, `2180` answered
`61 80 98 04 43 62 80 00 06 ...`, and the catalog field with `sb=3 ln=5` lands exactly on
`98 04 43 62 80` - the PSA part number. As with the BSI, the `endian` field from the DB is
not carried over: it lies, big-endian is what is on the wire.

`gen_catalog.py` picks a catalog file per family preferring the B7 platform and avoiding
`DEGRAD`/`MESS`/country builds. `VA*` (actuator-test) groups are deliberately excluded -
this is a read-only path.

**Not yet run against the car.** Everything above was derived from the capture and the DB;
`ecu.py`, `poll_all.py` and the fixed `extract_payload` have never had hardware in front
of them. First run should be `./.venv/bin/python ecu.py --all` (identification only), then
`poll_all.py --tx 6A8`.

### High beam, revisited

BSM_2010 has `MP_COMMANDE_FEU_ROUTE_G/D` - the commanded state of the left/right high beam
outputs - readable at `22D440`, bit masks 2 and 1, plus dipped beam at `22D430` and the
inhibit flags at `22D810/22D820`. That is **visibility, not control**: no BSM_2010 file in
the DB has any `VA*` group at all, so DiagBox has no actuator test for it. Still worth one
cheap live experiment (`2F D440`), since the BSI's DB entry also under-reported what the
block actually accepts. The MITM conclusion stands; the BSM readouts give a way to verify
a MITM works.

## Local DiagBox DB clone

`/home/jaennil/life/citroen/diag-server` - shallow clone of `jyseojys/diag-server`, 798 MB:
`ecu_groups_jsons/` (4305 ECU catalogs), `dtc_groups_lightweight/`, `sw_mapping.json`
(33203 rows, part number -> ECU model; only ~9910 keys are real 10-digit part numbers, the
rest are row-index artefacts). No more network round-trips to look up parameters.

## MITM hardware for the high beam - shopping list and order of work

The diagnostic route to the main beam is closed for good (see above: `2F D82B` does not
exist on this BSI, and BSM_2010 has no actuator groups at all). What is left is a
man-in-the-middle on the body bus between the stalk (COM2008P, `0x742`) and the BSI:
intercept the stalk frame, set the high-beam bit, pass it on. The BSI then drives the
relays itself, so the telltale lights and the stalk can still override.

Teensy 4.0 is ordered. What else is needed:

| item | qty | why |
|---|---|---|
| SN65HVD230 CAN transceiver breakout | **2** | one per side of the cut. A MITM is not a tap - the bus is cut and the Teensy sits in the middle, so it needs a transceiver facing the stalk and another facing the BSI. Buying one is the classic mistake. |
| 12 V -> 5 V step-down (MP1584 or similar) | 1 | powers the Teensy from the car |
| fuse 2-3 A + TVS or zener on the 12 V input | 1 | automotive 12 V has load-dump spikes |
| micro-USB cable | 1 | programming the Teensy 4.0 |
| inline connector pair / spare pigtail, crimps, heat-shrink | 1 | so the cut is reversible and the car can be put back |
| perfboard, pin headers, small enclosure | - | not strictly needed but the thing lives in a car |

Two traps worth knowing before soldering:

* **The 120 Ohm terminator on the breakout.** Waveshare-style SN65HVD230 boards carry a
  120 Ohm terminating resistor, jumper-selectable on some revisions, hard-wired on others.
  The bus is already terminated at its ends; adding two more terminators mid-bus will
  disturb it. Remove or un-jumper both.
* **3.3 V logic.** SN65HVD230 is a 3.3 V transceiver, which is why it suits the Teensy
  directly. MCP2551 and TJA1050 are 5 V parts and would need level shifting on RX -
  do not substitute them casually.

CAN1 and CAN2 on a Teensy 4.0 are both on top-side pins, which is enough for a two-bus
MITM; CAN3 sits on the underside pads and is the reason 4.1 was considered. Library is
FlexCAN_T4.

### Measure before cutting

The transceiver choice is **not yet verified on this car** - it rests on PSA AEE2010 using
a standard differential physical layer at 125 kbps on the body bus. Two measurements at
the stalk connector settle it, ignition on:

1. CAN_H and CAN_L to ground at idle. Both near 2.5 V means standard differential and
   SN65HVD230 is correct. If one line sits near 0 V and the other near 5 V, the bus is the
   fault-tolerant low-speed variant and the part must be TJA1055 instead - a different
   chip, not a drop-in.
2. Bit rate, confirmed by sniffing rather than assumed.

### Order of work

Do not cut anything first. The Teensy with **one** transceiver can listen passively on a
tap:

1. Listen-only. Confirm the stalk frame `0x094` and that bit 4 is the high-beam inverter
   and bit 3 the flash - the bit map comes from the COM2008P description, not from
   measurement on this car.
2. Cross-check against the BSM: `22D440` on `0x747` returns `MP_COMMANDE_FEU_ROUTE_G/D`,
   the commanded state of the left and right high-beam outputs. That is an independent
   readout of whether the lamps were actually asked for, and it is how a working MITM will
   be proved.
3. Only then cut the pair and go in-line with both transceivers.
