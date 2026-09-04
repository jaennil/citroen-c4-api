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

Files: `00_*.py`, `01_*.py`, `02_*.py`, `03_analyze_dump.py`, `dashboard.py`, `monitor.py`, `quick_test.py`.

All of these open a serial port at 38400 baud and speak ELM327 AT commands. Each script reimplements the same `send_cmd`/`cmd` helper: write `"<cmd>\r"`, read until the `>` prompt appears, strip it. Standard init is `ATZ, ATE0, ATL0, ATS0, ATH1, ATSP6` (protocol 6 = ISO 15765-4 CAN 11-bit 500 kbps), plus `ATCAF0` to get raw unformatted frames.

The default port differs per script and reflects which adapter was in use at the time - `/dev/rfcomm0` for the Bluetooth ELM327 (needs `sudo rfcomm bind 0 <MAC>` and the `bluez-deprecated-tools` package), `/dev/ttyACM0` for the USB one. Most scripts accept the port as `sys.argv[1]`; some hardcode it at module level and open the serial port at import time, so they fail immediately with no adapter attached.

What works on this path: plain OBD-II mode-01 PIDs. `dashboard.py` is the useful artifact - a live terminal readout of RPM, speed, coolant/intake temp, throttle, load, timing advance and fuel trims, parsing responses by locating the `41 <PID>` echo.

**Why this path can never reach the lights (measured 2026-08-16, engine running, `ATRV` 14.2 V).** The OBD-II port on this car exposes *only* the legislated emissions diagnostics. Proven by a discriminating test: `ATSH7E0` + `0100` answers `7E8064100BE3EB811` and `0902` returns the VIN over multi-frame ISO-TP, so custom headers and ISO-TP both work - but `1003`, `22F190` and `3E00` return `NO DATA` on *every* address tried, including the engine's own `7E0`, and functionally via `7DF`. Nothing answers UDS: not BSI `0x752`, not the stalk HDC `0x742`, not CMB `0x782`, CORPRO `0x6B7`, airbag `0x744`, ABS `0x6AD`, radio `0x760`. The BSI/HDC diagnostics live on PSA's DiagOnCan pair (OBD pins 3/11), which a standard ELM327 does not wire - it only connects pins 6/14. That is the root cause of every failure below, and the reason the Lexia works where the ELM cannot. Do not retry BSI-over-ELM; it is not a protocol or baud-rate problem.

Separately, this particular USB adapter (`0918:7104`, reports `ELM327 v1.5`) has a broken `ATMA`: it returns zero frames even with the engine running and the powertrain bus demonstrably busy.

What does not work: light control. Bus sniffing via `ATMA` never produced anything - `can_dump.log` is empty and `can_raw.log` holds a single junk line - so `03_analyze_dump.py` (which diffs two dumps per CAN ID) has never had real input. It is kept because the same diff is exactly what is needed to decode the stalk frame once the MITM can listen. The scripts that tried to write light commands over ELM (`04_strobe.py`, `try_lights*.py`) are deleted; two conclusions from them are worth keeping. `0x128` is an *indicator* message - BSI telling the cluster what is lit, not a command - so replaying it would never switch a lamp. And UDS sessions were tried against BSI (`0x752`/`0x652`), PROJECTEURS (`0x6B7`/`0x697`), the stalk (`0x742`/`0x642`) and the cluster (`0x75F`/`0x65F`), all silent, because the ELM does not wire the DiagOnCan pair at all.

### Path B - Lexia 3 over raw USB (`pyusb`) - the working one

Files: `lexia_proto.py` (protocol), `lexia_boot.py` (device handshake), `ecu.py` (block switching), plus `sniff_lexia_usb.py` for capture.

A Lexia 3 (PSA dealer interface, USB `103a:f008`) is driven directly through libusb: bulk EP OUT `0x06`, bulk EP IN `0x85`, 64-byte reads. Connecting means detaching the kernel driver and claiming interface 0, which needs root or a udev rule.

The protocol was reverse-engineered by running DiagBox in a VM, passing the Lexia through to it, and capturing the USB traffic with `sniff_lexia_usb.py`, which parses `/dev/usbmon1` binary packets directly (64-byte header struct, filtered to bulk transfers with data). That capture is checked in as `lexia_usb.log` - 5500 packets in `timestamp,type,direction,endpoint,device,length,hex_data` form. It is the ground truth for every magic hex string in the Lexia scripts; re-read it before changing them.

**The device handshake is mandatory and was the missing piece for years.** Sending the session frame `400915c000fe...39` cold makes the device answer with status byte `0x0C` (byte 18 of the reply) and nothing works afterwards. Before any vehicle traffic, DiagBox runs 11 device-level commands that read the interface's own firmware strings (`011113A`, `921815  C/t`, `BOOT1_PSA_XS__ P107441- V1.0.3 @ACTIA`, `APPLI_XS_Fuji_ P106138A V4.3.7 @ACTIA`). Only then does the `fe` frame return status `0x01` and the link to the car come up. That sequence is captured verbatim in `lexia_boot.DEVICE_BOOT` and replayed by `Lexia.device_boot()`; `lexia_proto.link_status()` decodes the status byte. The old capture never showed this because it began mid-session.

Two related traps. Every command - including the device-handshake ones - must go through the full poll/fetch cycle below; sending one and reading the reply directly returns the `064009` acknowledgement or a `1540090b` rejection, never the data. And byte 8 of a command frame is a per-session handle, not the constant `01` seen in the old dump: the fresh capture shows `d9`, `35`, `2b`, `aa`, `98`. The checksum rule is unaffected - the last byte still forces the frame's 8-bit sum to `0xFF`, which is the general rule; `(0xBA - id)` only held while every other byte was constant.

Every command follows the same four-step cycle that DiagBox uses:

1. submit the command frame on EP OUT
2. poll `410901c0f4` repeatedly until the device answers `42410900`
3. read the result with `430901c0f2`
4. acknowledge with `064409`

`lexia_proto.Lexia.transact()` implements this. Session init before any vehicle traffic is `400915c000fe0000aa00000000000000000000000000000039` (`INIT_FRAME`).

**The diagnostic route to the lights is closed, and all of it is deleted.** Recorded so it is
not reopened. UDS service `2F` (InputOutputControlByIdentifier) on DIDs `D8xx` did drive some
lamps: `2F D8 70` returned `6F D8 70 03` and the side lights physically lit, measured
2026-08-16. But the route is useless for the goal and has been abandoned, for three separate
reasons, each measured on this car rather than assumed:

* **The headlamps are not in it at all.** `22 D8 2B` (main beam) returns `7F 22 31`
  requestOutOfRange, as do `D829`, `D82A` (dipped L/R) and `D82C` (front fog), while `D870`
  and `D871` read fine in the very same session - so it is not a chain or session problem,
  those DIDs genuinely do not exist on this variant. DiagBox agrees: its `BSI2010
  LIGHTING - SIGNALLING` actuator list here holds only the `D87x` block (side lights,
  indicators, rear fog, reversing, brake, plus courtesy/boot/black-panel items) and offers no
  main-beam test. `22 D826`, availability of automatic main/dipped switching, reads `00`. In
  the DiagBox database `MP_COMMANDE_FEUX_DE_ROUTE` (`D82B`) appears only in
  `MESUREPARAMETRE2E_VARB*`, the AFS/bi-function-headlamp variant, while the confirmed `D87x`
  ids live in `MESUREPARAMETRE3E`, present in every variant.
* **It only works with the engine off.** A/B tested in one session each: engine off and
  ignition on, `2F D8 70` lights the lamp; engine running at ~750 rpm, the identical frame
  returns `7F 2F 22` conditionsNotCorrect every time, while `22 D8 70` still reads fine. The
  gate is specific to service `2F` and keyed on engine state. An earlier research pass claimed
  a forum user ran PSA light actuator tests with the engine running; that is wrong for this car.
* **The BSI latches an actuator for ~3 s and no off command exists.** Re-sending the same
  command inside the latch also returns `7F 2F 22` and does *not* extend it, so
  `conditionsNotCorrect` means either "engine running" or "test already active". This also
  explains the lone `7F 2F 22` in the original `lexia_usb.log`, which earlier analysis misread
  as a general precondition gate.

Note that the DB has no actuator groups for the BSI at all - checked across all 35 `BSI2010*`
files, every group is `MESUREPARAMETRE*`. `VA*` actuator groups exist only for engine, gearbox
and ABS families. So the `D87x` palette above came from the USB capture and live probing, and
the full list could only ever be found by scanning `22 D8xx` live.

The remaining route to the headlamps is the MITM on the LS.CAR body bus rewriting the stalk
frame (see below), or a relay interposer at the headlamp connector.

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
| `0x6A8/0x688` | 9804436280 | Valeo V46 | engine (EC5) - see below |
| `0x6AD/0x68D` | - | ESP81 | ABS/ESP |

Engine and ABS are absent from `sw_mapping.json` (it covers body/comfort only) and were
identified differently: request `2182` narrows it to 17 ECU families and in the capture it
was sent to `0x6A8` and nowhere else. DiagBox cannot auto-detect the injection ECU
(`script_reco_INJ.s` offers a manual list) so it must be picked by hand.

**The engine ECU is a Valeo V46, not the Bosch MEV17.4.2 that DiagBox's screen suggested,
and getting that wrong cost real time.** The screen the owner reached offered
`MEV17_4_2 -> 5FS MEV17.4.2 EURO 5`, so the MEV17 catalogue was used - and it decoded
engine speed and supply voltage correctly, which looked like confirmation. It was not:
those first bytes of the `21 C0 80 01` block sit in the same place across PSA ECUs of the
era. The giveaway was oil temperature reading 1225 °C.

What settled it was a number read off the car itself: `21 80` at `0x6A8` returns part
number **9804436280**, which is a Valeo V46. That matches the owner's own statement that
the engine is an **EC5** - an evolution of the TU5 family, not the EP6C/5FS the screen
implied. Lesson: identify the ECU from what it reports, not from the menu that reached it.

Picking the right V46 catalogue then needed a second pass, because Valeo has many. `VD46_*`
is the obvious name but wrong: it reads over UDS (`22 D4xx`), and this block **goes silent
on a UDS entry** while answering a KWP one. Measured both ways on the car. The family that
fits is `V46_32_B7`, matching 5 of the 7 requests the block actually answered, all KWP.
With it, `poll_all` returns 55 physically plausible values - intake air 29 °C, ECU supply
14.4 V, throttle sensors 680 and 4350 mV, oxygen sensors 112 and 102 mV - where the MEV17
catalogue had produced nonsense. Coolant temperature is `21 C0 80 01` byte 6 with
**offset -50** (MEV17 said -48, which is why an earlier reading came out 97 °C instead of
95 °C), and `MP_TEMP_EAU_NON_CORRIGEE` at `21 C7 80 01` gives the uncorrected sensor value -
comparing the two is the direct test for a lying coolant sensor, and is still to be run.

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

**Confirmed on the car 2026-08-19: the engine ECU is readable.** `21 80` at `0x6A8`
returns part number `9804436280`, `21 C0 80 01` returns 72 bytes, and the values
cross-check against the BSI independently - 745 rpm against 751, 14.20 V against 14.26.
That agreement is what proves a real second ECU is being read rather than an echo.

Getting there took four protocol fixes, none of which was visible without hardware:

* **Wait for readiness by the clock, not by a poll count.** The old limit of 60 polls flew
  past in ~200 ms because the device answers "busy" instantly. BSI's protocol table allows
  250 ms and survived; KWP blocks allow 1000 ms and did not. Giving up early left the
  command unfinished and the next write failed with `USBError [Errno 5]`.
* **Retry a command the device rejects.** The first command after the configuration table
  is *always* rejected - the receipt is `15 40 09 02` instead of `06 40 09` - and resending
  the identical frame succeeds. Measured: reject, then success with `C1`
  (StartCommunication), then engine reads flow. Without the retry the KWP link never came
  up and every request returned nothing.
* **Reassemble multi-packet replies.** A reply arrives in 64-byte packets and a full 64
  means more follows. One `21 C0 80 01` carries 64 parameters at once, so truncating at one
  packet dropped every block read: 21 of 189 parameters instead of 55.
* **Never send a UDS request to a KWP block.** `22 F080` to the engine tears down the link
  that was just established, after which it is silent to everything. `ecu.is_kwp()` decides
  from the entry sequence's final payload (`81` = KWP, `10 xx` = UDS) and probes with the
  matching service only.

Two dead ends recorded so they are not retried. The session handle (byte 8) is **not** the
problem - it looked decisive because a rejected `handle=00` frame happened to precede an
accepted `handle=aa` one, but the same frame twice works just as well; the handle
normalisation was removed from the generator. And `gen_ecu_entry.py` had picked a
*presence probe* for the engine rather than the real session - a UDS table with `10 03`
where KWP `81` was needed - because it kept the last sequence per address; it now keeps the
one followed by the most reads.

`reset_lexia.py` recovers a wedged interface with release + dispose + reset, so a jammed
Lexia no longer needs the connector pulled.

Still open: 7 of the engine's 14 requests answer, giving 55 of 189 parameters. Some
decodings are clearly wrong in the same way the BSI's were - oil temperature reads 1225 °C
- and want the `probe_raw.py` treatment.

### High beam, revisited

BSM_2010 has `MP_COMMANDE_FEU_ROUTE_G/D` - the commanded state of the left/right high beam
outputs - readable at `22D440`, bit masks 2 and 1, plus dipped beam at `22D430` and the
inhibit flags at `22D810/22D820`. That is **visibility, not control**: no BSM_2010 file in
the DB has any `VA*` group at all, so DiagBox has no actuator test for it. The diagnostic
route is not being pursued any further - see the closed-route note above. What these DIDs are
for now is **verification**: reading `22D440` says whether the block was actually asked for
high beam, which is the independent check that a working MITM really did what it claims.

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
| MP1584EN step-down module | 1-2 | powers everything from the car. Output is **adjustable by trimmer** - set it to 5.0 V with a meter BEFORE anything is connected downstream; these ship at whatever the pot happens to be |
| fuse 2-3 A with holder | 1 | |
| TVS diode **SMBJ16A** (or P6SMB16A) on the 12 V input | 1 | MP1584 absolute max input is 28 V, and an automotive load dump goes past that, so this is not optional. SMBJ16A stands off 16 V - above the ~14.5 V charging rail - and clamps around 26 V, i.e. below the module ceiling |
| Schottky diode in series on the 12 V feed | 1 | reverse-polarity protection, costs pennies and forgives one wiring mistake |
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

## Phone control over Bluetooth

Teensy 4.0 has no radio, so the phone link is a separate module on a UART.

| item | qty | note |
|---|---|---|
| ESP32-WROOM-32 devkit | 1 | the radio. Covers both phone platforms and can stream the bus for debugging. See below on why not an HM-10 and not a C3/S3 |
| DPDT signal relay, 5 V coil, **or** a 2-channel 5 V relay module | 1 | the fail-safe bridge, see below. Two poles because both CAN_H and CAN_L are cut. A 2-channel module already contains the driver and flyback diode |
| N-channel MOSFET (BSS138, 2N7000) + 1N4148 flyback diode | 1 | only if using a bare relay - the Teensy cannot drive a coil directly |

HC-05 is cheaper but it is Bluetooth SPP, which iOS does not allow without MFi - Android
only. Pick HM-10 unless the phone is known to be Android forever.

**An ESP32 is the better pick for this role than the HM-10**, and it does BLE too - the
earlier note here framed ESP32 as a WiFi-only option, which was too narrow. One part then
covers both phone platforms: Bluetooth Classic SPP for Android (same zero-development
serial-terminal path as an HC-05) and BLE for iOS, plus a WiFi web UI as a bonus for
config and log pulling at home. Cost is comparable to an HM-10.

Take the **original ESP32-WROOM-32**, not an ESP32-C3 or -S3: the newer parts have BLE only
and no Bluetooth Classic, so the SPP fallback disappears. That matters beyond the fallback -
streaming the bus to the phone to watch the MITM work needs throughput. A busy 125 kbps
body bus is on the order of 15 kB/s, which SPP carries comfortably and BLE only barely,
depending on MTU and connection interval.

**The ESP32 cannot replace the Teensy.** It has a single TWAI controller, and a MITM needs
two independent CAN interfaces - one facing the stalk, one facing the BSI. Bolting on an
MCP2515 for the second bus is possible but a step backwards: SPI latency in a bridge that
must be transparent, and the common modules are 5 V parts. Teensy 4.0 has two native
FlexCAN controllers on top-side pins; keep CAN there and let the ESP32 be the radio,
joined by a UART.

Two wiring notes for that split. Both are 3.3 V parts, so the UART connects directly with
no level shifting. And on the Teensy 4.0 **CAN2 and Serial1 share pins 0 and 1** - check
the pinout card and put the ESP32 on Serial2 (pins 7/8) so it does not collide with the
second CAN interface. Power the ESP32 from the 5 V buck through its own onboard regulator
rather than from the Teensy 3.3 V pin, which has little headroom if WiFi ever comes up.

**No app needs writing.** A generic BLE serial terminal (Serial Bluetooth Terminal on
Android, any BLE terminal on iOS) has assignable macro buttons, so "high beam on" is one
button sending one character. A real app is optional polish later.

### The failure mode that dictates the design

A MITM cuts the bus, so the stalk reaches the BSI only *through* the Teensy. The stalk
frame does not carry the high beam alone - it also carries turn indicators, wipers and the
lighting ring position. If the Teensy hangs or loses power mid-drive, all of that goes with
it. That is not acceptable in a car.

Hence the relay wired **normally closed across the cut**: with no power and no healthy
firmware the two halves of the bus are simply joined and the car behaves as though nothing
was installed. Relay switching on a live bus corrupts the frame in flight; CAN retransmits,
so that is acceptable.

**Better still, stay joined by default and only open the loop while actually forcing the
beam.** Both transceivers sit on the joined bus and hear everything, so the Teensy can watch
`0x094` passively without cutting anything; it opens the relay, bridges actively and
rewrites the bit only for as long as the override is on. That keeps the car electrically
stock almost all the time, drops two relay coils worth of continuous current, and saves
contact wear. The firmware rule that comes with it: **never transmit while the relay is
closed** - both transceivers are then on the same segment and the Teensy would collide with
itself. Losing a couple of stalk frames at the moment of switchover is harmless, they are
periodic.

**The relay module's coil voltage is not a free choice - it decides whether the module
can be driven at all.** A 12 V module was ordered by mistake; the trap is on the input
side, not the coil. On these opto-isolated boards the input is referenced to the board's
own supply: in low-level-trigger mode the idle voltage on IN sits near the supply rail
(~11 V on a 12 V board), which destroys a Teensy pin on contact - Teensy 4.0 is not even
5 V tolerant; in high-level-trigger mode the LED series resistor is sized for 12 V, so
3.3 V drives only ~2 mA where 5-10 mA is needed, and the relay becomes unreliable.

A 12 V module is still usable **if** it carries the **JD-VCC jumper**: logic supply from
5 V, coil supply from 12 V separately. That is actually better than the original plan -
the coils stop loading the buck and free ~150 mA. Without that jumper, use a 5 V module.

Rule for any unfamiliar module: power it, leave IN unconnected, and **measure the idle
voltage on IN before wiring it to a Teensy pin.** Above 3.3 V means it must never touch
the Teensy directly.

Wiring, per line: stalk side to COM, BSI side to **NC**. Two channels, one for CAN_H and one
for CAN_L, driven from a single GPIO so they always move together. Set the module's
HIGH/LOW jumper to high-level trigger, so a low or undriven input leaves the relay
de-energized, and add a **10k pulldown** on each control input so "undriven" is definitely
low while the Teensy boots or hangs. Bench-test it before it goes in the car: power the
module, leave the input alone, and check continuity across the joined pair.

Current budget for the 5 V buck: Teensy ~100 mA, ESP32 up to ~250 mA on BLE, and ~150 mA
more while both relay coils are energized. A 2-3 A module has ample margin.

This is also why forging frames without cutting does not work: the real stalk keeps
transmitting `0x094` on its own schedule, and two nodes sending the same ID with different
payloads produce bit errors and drive the error counters toward bus-off. The cut is not
optional.

### Command protocol, and two rules it must enforce

Plain text over the BLE UART is enough - `R1`/`R0` to force the beam, `S` for status
(stalk state plus the BSM confirmation from `22D440`).

* **Auto-off on silence.** While the beam is forced, require a heartbeat from the phone;
  if it stops, revert to pass-through. Otherwise a phone that walked out of range leaves
  the high beam latched on.
* **The stalk always wins.** Any change the driver makes with the stalk cancels the
  override immediately. The physical control must never be the thing that stopped working.

## Collecting more than the BSI

`drive.py --ecus 6A8 --ecu-every 300` makes the collector leave the BSI every five minutes,
enter another block, read its catalogue, and come back. Two things shape that design.

The excursion is deliberately rare. Switching the channel plus reading takes several
seconds, and the 2 Hz stream of rpm and speed stalls for that whole time - once every five
minutes the hole is far rarer than the stream. Returning to the BSI afterwards is
mandatory; without `enter(0x752, 0x652)` the next fast poll goes to the wrong block and
comes back empty.

`storage.param_id` now identifies a parameter by **name**, not by DID. Keying on DID worked
only while the BSI was the sole source: KWP parameters have no DID at all, so they were
written with `did=0`, and since the column is `UNIQUE` all 189 engine parameters would have
collapsed into one row. DIDs also repeat across blocks - `22D400` exists on the BSI, on the
BSM and on the airbag module, meaning different things. New names get a synthetic DID above
the 16-bit range, allocated once and then read back from the database, so history does not
fragment. Verified against a copy of the real buffer: the BSI parameter reused its existing
row, 189 engine names produced 189 distinct rows, no duplicate DIDs, and the ids were
stable across a restart. Non-BSI names carry the block's family as a prefix
(`MEV17_4_2:MP_...`) precisely so two blocks cannot merge on a shared mnemonic.

**The excursion has since been tested on the car and it fails, so both excursion flags
default to 0 and the service reads the BSI alone.** This paragraph used to say the
excursion was merely untested; that is wrong. Measured three times: 30-60 s after the
engine sweep begins, `USBTimeoutError` arrives and the device then answers
`USBError [Errno 5]` to everything until the connector is pulled. A *light* excursion
(`--quick-every`) fails the same way, so it is off too. The failure is always in the same
place - **the return to the BSI after a KWP block**, which is why that branch in `drive.py`
logs loudly instead of failing silently.

Consequence worth knowing: every engine row in `car.db` (128 634 readings of `V46_32` as of
2026-09-01) came from `watch_coolant.py` camping in the block, never from the service.
Camping is safe by construction; switching is what breaks.

**Why it breaks, found in the capture on 2026-09-02 without touching the car.** The
DiagBox recording holds 76 block switches: 45 UDS->UDS, 24 KWP->KWP, 3 UDS->KWP and only
3 KWP->UDS - and those three go to `0x6C1` and `0x747`, never to the BSI. Every entry into
`0x752` in the whole capture came from a UDS block (twice from itself, once from `0x76E`).
So `KWP -> 0x752` is a transition **DiagBox never performs**, and we were doing it on every
excursion return.

It is not the entry sequence itself: dumped side by side, `0x752` and `0x747` are
structurally identical - same `fe` frame, same descriptor
(`FFXXXXC01+2XXC2XXX0000000000`), same `54xx` timing table, same closing `10 03`. Only the
address record inside the table differs. So the difference is the *context* of the
transition, not the frames.

`ecu.KWP_EXIT_HOP` therefore returns in two hops - `KWP -> 0x747 -> target` - so that every
individual switch is one the capture actually contains. `ecu.enter()` inserts the hop
itself whenever the current block is KWP and the target is UDS, so any caller gets it.
**Not yet verified on hardware.** To verify: `drive.py --ecu-every 15 --db /tmp/test.db`
with the engine running, and watch for `возврат с KWP 0x6A8 через 0x747` followed by engine
rows rather than a reconnect.

## A real fault found while testing (2026-08-19)

The engine has two stored codes, read with KWP `17 FF 00`: `57 02 22 99 01 01 16 01`, i.e.
`2299` and `0116`. The second is P0116, coolant temperature circuit range/performance -
which matches the owner's own diagnosis of an intermittently dying sensor and explains the
cooling fan running hard, since a stored fault keeps the fan in fail-safe even while the
live reading is fine. Live coolant was steady at 97 °C over ten consecutive samples, so the
sensor was behaving at that moment.

A caution about that reading: `MP_TEMPERATURE_EAU_MOTEUR` has `offset = -48`, and comparing
an offset-applied value against a raw byte produced a phantom "temperature jumping from 92
to 143 °C". There was no jump. Apply the catalogue's factor and offset before drawing any
conclusion.

Clearing the codes would test the fan theory cheaply, but that is a **write** to the engine
ECU and everything so far has been read-only - ask before doing it.

## Why block sweeps died, and the five wrong answers (2026-08-21)

A full sweep over the ECUs kept killing the interface after three or four block
switches: the device stayed enumerated but answered `USBError [Errno 5]` to everything
and only a **USB replug** brought it back. Five hypotheses were wrong before the right
one; they are recorded because each looked convincing.

**The cause: no session teardown before switching.** Diffing our own usbmon capture
against DiagBox's showed that before every next block's configuration table DiagBox
sends a `ff/02` command and we sent nothing:

    DiagBox   ff/02 -> 00/fe -> 00/05 -> [table 00/16]
    ours               00/fe -> 00/05 -> [table 00/16]

`ff/02` closes the current session, and the payload depends on the protocol: `10 01`
(DiagnosticSessionControl, back to default) for UDS blocks, `82` (StopCommunication)
for KWP ones. DiagBox switched blocks 83 times in 997 s without a single failure; we
managed three. Left-open sessions accumulate and exhaust the interface. `ecu.leave()`
now replays the right frame verbatim and `enter()` calls it first.

The four other real bugs found on the way, all of which also had to be fixed:

* **The collector established the link with the legacy `lex.boot()`** while everything
  else now switches channels with `enter()`. The two configure the channel differently.
  Symptom: the link "comes up", a full BSI snapshot runs and writes **zero** values,
  and a minute later reads time out. Manual tests worked because they used `enter()`.
* **A missing `continue`** in `poll_ecu` let control fall through to `payload[0]` on an
  empty answer, raising `TypeError` mid-transaction and leaving the interface half-done.
  This mimicked a hardware fault perfectly - collection died after ~90 s.
* **The first read after entering a block returns an empty receipt**, not data. Fragmented
  commands already refetched in that case; plain reads did not, so a *working* request
  looked silent. `Lexia.read()` refetches now.
* **A silent path** returned to BSI after a KWP excursion without logging anything, so the
  journal held a 69-second hole instead of an error. Never fail silently here.

**Silence is a property of state, not of the request.** Two attempts to learn which
requests "don't answer" and skip them made things worse: the same ABS requests returned
18 parameters in one run and nothing in the next, and the learned list then poisoned the
working ones. `dead_requests.json` is kept, but only for what was measured by hand -
the engine's non-answering requests, which cut its read from 11.3 s to 1.6 s for
51 parameters.

`21CB8001` was taken back off that list on 2026-08-31. It is the only request that carries
the cooling-fan state - `MP_ETAT_RELAIS_GMV`, `MP_ETAT_GMV_PTIT_C5`,
`MP_CONSIGNE_VITESSE_GMV_C5`, `MP_ETAT_REL_GMV_C5` - and it was measured silent in a state
that may well have been engine-off, which is exactly the trap recorded above. Since the fan is
what the stored P0116 is suspected to be driving, one retry with the engine running is worth
1.4 s per excursion. If it answers, fan state and coolant temperature (`21C08001` byte 6,
offset -50) arrive together and the sensor question can finally be settled.

Two operational rules that saved a lot of replugs:

* **Set the pause flag BEFORE the user replugs.** udev starts the collector the instant
  the device appears and it grabs the interface mid-enumeration, which wedges it. With
  the flag pre-set the service parks, the device comes up clean, and the flag is removed
  afterwards. `with-lexia.sh` waits on the service's actual USB file descriptor.
* **Never bus-reset a wedged device.** `dev.reset()` does not revive it, it finishes it
  off - the Lexia leaves the bus entirely (gone from `lsusb` with the cable in and the
  LED lit) and only a physical replug helps. `reset_lexia.py` refuses by default.

Restarting the service does not heal a wedged device; it only loads new code. Note also
that the interface is USB-powered, so pulling the OBD end changes nothing.

## Fault codes, with descriptions from the DiagBox image

`dtc_read.py` reads DTCs from every block - `19 02 FF` for UDS blocks, `17 FF 00` for
KWP - and decodes them three ways: the dictionary extracted from the image, a table of
standard OBD-II codes, and SAE J2012 arithmetic. `--sqlite car.db` writes each code as a
parameter named `DTC:<family>:<code>` with the status byte as the value and the
description as the label, so the dashboard shows them as a table with no schema change.

**The descriptions were extracted from the DiagBox VM image**, without booting it.
`dtc_groups_lightweight` in the DB clone has 12 003 codes mapped to hashes like
`H_a24bfaeb` with the texts stripped - and those hashes appear nowhere in the image, so
they are the repo's own invention. The image itself carries the texts in plain ASCII as
consecutive triplets:

    C01988
    B-CAN
    B_CAN_1078

code, description, mnemonic. `gen_dtc_names.py` scans `strings` output of the whole
14 GB image (365 M lines) and validates each triplet by **self-consistency**: the
mnemonic is the description in snake_case, so normalising both and requiring a prefix
match rejects noise. Validating against the catalogue instead does not work - it holds
9363 four-character keys against 585 six-character ones, i.e. a different code format.
Yield: 54 manufacturer descriptions, mostly body-computer ones, including
`90251D High Beam command (relay) - circuit short to battery`. The free image simply
does not carry the full table.

This car's own codes decode without any dictionary: `0116` -> **P0116** (coolant
temperature circuit) and `2299` -> **P2299** (brake pedal / accelerator pedal position
incompatible). The second one had been printed as raw hex for days before being decoded.

## Codes across the blocks, first sweep (2026-09-01)

Five blocks read before the interface degraded. Results are valid:

| block | codes |
|---|---|
| `0x6A8` engine | P0116 and P2299, both status `01` - active |
| `0x6AD` ABS/ESP | none |
| `0x6C8` VCI | none |
| `0x730` rain/light sensor | none |
| `0x6B5` electric steering pump (GEP) | **C1205, status `0x28`** |

`C1205` is new. Status `0x28` has bit 3 set and bit 0 clear, i.e. **confirmed and stored but
not active now** - a historical fault, and the steering works. The code is not in the 54
descriptions extracted from the image and not in the DTC catalogue clone, so it stays a bare
PSA code for now.

Still unread: BSI, BSM, stalk, cluster, airbag, parking sensors, door module, control panel.

**Why it died, and it was our own bug.** `poll_all.py` has `SKIP_BLOCKS = {0x6C8}` because
VCI reproducibly poisons the interface; `dtc_read.py` walked `sorted(ECUS)` with no skip.
The collapse began exactly one block after VCI - nine consecutive `USBTimeoutError` - which
matches the note above about VCI answering with 24 silent requests in a row. `dtc_read` now
imports `SKIP_BLOCKS` from `poll_all` so there is one list, not two. **The fix is not yet
verified on hardware.**

**And a rule worth keeping: exit code 0 does not mean the interface is healthy.** That sweep
finished with status 0 while leaving the device in a state where the very next
`device_boot()` failed on a USB write. Judge health by whether reads succeed, not by the
process exit code.

## P2299 is the driver, not a fault (2026-09-01)

The owner had been practising heel-and-toe downshifts and left-foot braking. P2299 is
"brake pedal position / accelerator pedal position incompatible", so that technique is
literally the condition the code detects. Measured over four minutes of ordinary city
driving, reading `21CA8001` at 2 Hz:

* `MP_ETAT_CONTACTEUR_PEDALE_FREIN` toggles properly - 13 transitions, 7 pressed runs,
  pressed 40 % of the time, longest run 62 s (a traffic light). Not stuck.
* **Brake pressed AND throttle above 5 %: zero samples.** This is the discriminating test.
  A stuck switch would put every throttle application into that bucket.
* The two accelerator channels agree: no discrepancy above 15 % in 460 samples, channel
  ratio 0.96 (spread 0.85-0.98).

So the pedal hardware is healthy and the brake light switch does not need replacing. The
code comes from real simultaneous pedal input. Two consequences worth keeping: **left-foot
braking does not work as intended on this car** - the ECU cuts torque when it sees both
pedals, so the technique trains against the electronics rather than the car; and the code
will keep returning while the practice continues. Clearing it and driving normally is the
confirmation, and that is still an unperformed write to the ECU.

Caveat: four minutes only proves the switch is not permanently stuck. An intermittent
fault could still hide, though the healthy channel pair makes that unlikely.

## The engine's fuel loop, read on 2026-09-01

**The downstream oxygen loop is switched off in software.** `MP_ETAT_REGULATION_SONDE_A_OXYGENE_AVAL`
read 0 in all 1864 warm samples of the 20-minute run and again on a fresh single read, while
`..._AMONT` toggles 0/1. The sensor itself is alive - its voltage swings 0-898 mV and
`MP_RCOAVAL` is computed and varies 0-94 - so the ECU reads it and refuses to use it. That
is the signature of the "Euro-2" reflash, and it fits the absence of P0420 with no cat.

**The ECU mirrors the upstream correction into the downstream field.** Both
`MP_FACTEUR_CORRECTION_RICHESSE_AMONT` (sb 47) and `_AVAL` (sb 51) came back as the same
bytes `8192` in the raw payload. The offsets do not overlap - 46 and 50 - so this is the
block duplicating a value, not a catalogue error. An earlier note here blamed a catalogue
offset; that was wrong.

**And the unit in the DiagBox DB lies, the way endianness already did.** That parameter is
`factor 7.63e-06, offset -0.25`, so its full span is -0.25 .. +0.25 with unit `%`. A fuel
trim of a quarter of a percent is meaningless; read as a **fraction** it is +/-25 %, which is
exactly the normal full range. So multiply by 100 to get percent. The observed -0.16 .. +0.14
is therefore -16 % .. +14 %, and the current warm-idle reading of 0.0031 is 0.3 %, i.e. no
correction at all.

## Two ways this tooling wedges the interface, both mine

* **Back-to-back `with-lexia.sh` runs.** The wrapper clears the pause flag on exit, the
  service grabs the device immediately, and a second invocation lands on top of that
  acquisition. Same race as the udev one. Hit three times in one session; 5-8 s of sleep is
  not enough. Fixed by `--keep`, which leaves the flag up for a series, plus `--release` to
  hand the device back.
* **A single read right after entering a block.** The first read returns an empty receipt,
  not data, so `--once` reported `21C08001` silent while the same request answered 2400 times
  in the continuous run. `watch_coolant.py --once` now retries up to three times.

## Car configuration that changes how readings are judged

Two modifications, both established from the owner plus our own data, and both of which
invalidate textbook norms:

* **Retrofit xenon in the dipped beam**, fitted as a kit, not factory. No auto-levelling.
  This is why the BSI reports itself as a plain halogen car and why `D82B` (main beam
  actuator) does not exist: the BSI is genuinely a non-AFS variant and knows nothing about
  the xenon, which sits in the same holder on the same circuit.
* **Catalytic converter removed.** Measured consequence: the downstream oxygen sensor
  (`MP_TENSION_SONDE_A_OXYGENE_AVAL`) swings 0-900 mV exactly like the upstream one,
  because it now sits in open pipe and sees raw exhaust. The textbook rule for that
  sensor - a flat 550-800 mV shelf - applies only to a car with a working cat, so the
  threshold lines were removed from its panel; leaving them would have painted normal
  behaviour as a fault forever. What the sensor is still good for: with no cat it is a
  second copy of the upstream sensor, so comparing the two catches a dying one - if one
  swings and the other sits flat, the flat one is dead.

**And a deduction worth keeping: there is no P0420.** A removed cat with a live downstream
sensor mirroring the upstream one is exactly what triggers P0420 (catalyst efficiency below
threshold). Twenty minutes of continuous DTC polling on 2026-08-31 returned only P0116 and
P2299, never P0420. So either the ECU has been reflashed to drop the catalyst monitor, or
that monitor never completes its readiness cycle. Assume the firmware is not stock before
anyone "updates" or resets the engine ECU.

## The coolant sensor caught lying (2026-08-31)

Settled with a measurement instead of inference. `watch_coolant.py` camped in the engine
block at 2 Hz for 20 minutes on a live drive and caught the dropout in full resolution:

    21:21:20 .. 21:21:31   93 C, fan setpoint 29-30 %, relay 1, ~3000 rpm   (11 s flat)
    21:21:31               108 C          <- +15 C in ONE 0.5 s sample
    21:21:32               103 C, fan setpoint 29 % -> 100 %
    21:21:33               101 C
    21:21:34               93 C           <- back
    21:21:34 onward        fan setpoint stays 100 %

Both directions are physically impossible: a coolant mass cannot gain 15 C in half a second
nor lose it in three. So this is the sensor, not the engine - and the ECU believed it and
slammed the fan to full. That is the mechanism behind "the fan briefly spun up" that had
resisted explanation for weeks: the sensor spikes, the ECU panics, the fan goes to 100 %,
the reading returns and the fan stays up for a while.

Nine earlier readings had all looked plausible because they were single samples minutes
apart. The event lasts about three seconds, so only a fast continuous stream could see it.

**It repeats, and it clusters under load.** A second run on 2026-09-01 caught five events in
a twenty-second window, all between 2200 and 2700 rpm:

    11:13:00   91 -> 113 C, and straight back 113 -> 91
    11:13:06   92 -> 111 C
    11:13:08  103 -> 92 C
    11:13:17   93 -> 103 C

One event in twenty minutes yesterday, five in four minutes today. Both sessions put the
spikes at 2200-3000 rpm under load, which is the useful hint for anyone doing a wiggle test
on the connector.

**Refinement of the mechanism.** The spikes go up, to 103-113 C, never down. For an NTC
sensor a rising reading means falling resistance, while a bad connection would read open
circuit and therefore very cold. So this is probably not the raw sensor value at all: it
looks like the ECU detecting an implausible reading and substituting its fail-safe value -
deliberately hot, so the fan runs - for a sample or two before returning to the real one.
Either way the conclusion is the same: the circuit drops out intermittently and the ECU
reacts by commanding full fan.

**The fan is speed-controlled, not just a relay.** `MP_CONSIGNE_VITESSE_GMV_C5` tracks
coolant temperature monotonically - 17 % at 53 C, 22 % at 70, 27 % at 85, 30 % at 93 - and
goes to 100 % on demand. So the signal to watch is the setpoint, not
`MP_ETAT_RELAIS_GMV`; the relay was already 1 throughout normal running.

Two technical facts confirmed by the same run:

* **`21CB8001` answers with the engine running.** It had been on the hand-measured
  non-answering list; the silence was a property of state, exactly as the note above warns.
  All four fan parameters read fine.
* **Camping in one block never wedges the interface.** 2400 samples, 20 minutes, clean exit.
  The failure mode was always block *switching* without session teardown, so a tool that
  enters once and stays is safe by construction.

## Coolant sensor: what is and is not available

Nine readings of `MP_TEMPERATURE_D_EAU_MOTEUR_d` collected across two days: 85 °C right
after a start, then 92-96 °C warm, always consistent with engine speed. **Every sample
was plausible** - the sensor has never been caught lying, and no sample coincided with
the cooling fan running hard. P0116 remains stored, which is what keeps the fan in
fail-safe.

There is **no voltage or resistance reading for this sensor on this ECU.** The parameter
`MP_TENSION_CAPTEUR_TEMPERATURE_EAU` exists in the DiagBox database, but only for the
MED17_4_4, MEVD17_4_4 and MD1CS069 families - Bosch units from other engines. No V46
file has it, which is why the label "Voltage of the coolant temperature sensor" is
present in the image yet unreadable here. Lambda voltages, by contrast, are available and
read 112 and 102 mV. Catching an open circuit therefore needs a multimeter at the sensor
connector, not diagnostics.

## Why block switching died: one ack per packet, not per reply (2026-09-03)

Three hypotheses were wrong before the capture settled it, and each looked convincing.
Recorded so they are not retried.

**The cause: a multi-packet reply needs one `064409` acknowledgement per packet.** We sent
exactly one per reply. Measured by capturing our own failing return (`capture-return.sh` ->
`return.log`) and diffing against the DiagBox recording:

    мы:      после многопакетного ответа 064409 РОВНО ОДИН РАЗ - 23 случая из 23
    DiagBox: 064409 по нескольку подряд - два, три, четыре - 7 случаев из 25

The engine answers `21 C0 80 01` with 252 bytes, i.e. four 64-byte packets. We fetched all
four, acked once, and the device kept the rest unacknowledged. From that moment **every**
command was refused with `15 40 09 E9`, and every fetch returned the *same stale reply* -
in the dump it repeats eight times in a row, byte for byte. Hence the constant 13 s to
`USBTimeoutError`, identical for every return route, and `Errno 5` afterwards.

It also explains why camping in one block looked safe: a stale reply to the same request
looks plausible, so only the *switching* commands visibly broke. Any long `watch_coolant.py`
run may therefore contain repeated identical samples that were never fresh - worth checking
before trusting a flat stretch in that data.

**The one-ack-per-packet fix was wrong and has been rolled back (`62ca446`).** It held for a
day (`91d4305`, 2026-09-03 21:17) and broke link establishment for every process that loaded
it: the interface handshake reads multi-packet firmware strings (`BOOT1_PSA_XS__ ... @ACTIA`),
the extra acks kept the device from leaving its boot phase, and init failed with `Errno 110`.
The old service process with the previous code still in memory kept connecting fine at
21:23-21:32; the moment the service restarted on 2026-09-04 10:10 it failed too. Rolling back
restored the link at once: 595 BSI readings in 45 s. **The diagnosis stands, the rule does
not**: DiagBox sends several acks in only 7 of 25 multi-packet replies and one in the other 18,
so the criterion is not the packet count. Finding it is offline work on the two dumps.

### The three wrong answers

* **"The excursion was never tested."** It had been, three times, and it failed. The stale
  note in this file said otherwise; both excursion flags were already defaulted to 0.
* **"KWP -> BSI is a transition DiagBox never performs."** True but irrelevant. The capture
  holds 76 switches - 45 UDS->UDS, 24 KWP->KWP, 3 UDS->KWP, 3 KWP->UDS - and every entry
  into `0x752` came from a UDS block. Routing the return through `0x747` (DiagBox's recorded
  exit from ABS) changed nothing: same 13 s, same timeout.
* **"The exit hop must match the source block."** Also true and also irrelevant. Leaving the
  engine, DiagBox goes to `0x6C1` and its entry ends `10 C0`, where `0x747` and `0x752` end
  `10 03` - so entry sequences really do depend on the transition context. Routing through
  `0x6C1` still failed at 13 s. **Identical timing across three different routes is what
  finally pointed away from routing altogether.** `ecu.KWP_EXIT_HOP` is kept but empty.
* **"You must not leave a block right after unanswered requests."** Disproved by the
  capture: DiagBox leaves the engine immediately after two `7F 21 12` refusals to
  `21 87 00` and `21 87 01`.

### Two operational notes from the same session

* **`--log` was silently dead.** `ecu.py`, `poll_all.py`, `telemetry.py` and some twenty
  other scripts call `logging.basicConfig` at module level; the first import claims the root
  logger, so `drive.py`'s own `basicConfig(handlers=[FileHandler, ...])` became a no-op. No
  error, console output correctly formatted, and the file left at zero bytes - which is why
  `drive.log` had been empty since 16 August and the journal had to be read through
  `journalctl`. Fixed with `force=True`.
* **Never let a tool timeout kill a run mid-transaction.** A `SIGKILL` at 120 s left the
  interface half-done and wedged it exactly as a failed excursion does. Run manual
  collections in the background with `timeout --signal=TERM`, which `drive.py` handles
  cleanly.
* `drive.py --ignore-pause` exists so a manual run can coexist with `with-lexia.sh`, which
  raises the pause flag for the *service* - without it the manual instance parked itself too.

### `Errno 110` is a third wedged state, and it is not the ignition

Seen repeatedly on 2026-09-03 while trying to verify the ack fix. Distinguish the three:

    Errno 5   Input/Output Error    захват проходит, инициализация нет - классическое залипание
    Errno 16  Resource busy         два наших процесса дерутся за устройство, всегда наша ошибка
    Errno 110 Operation timed out   USB жив, а связь С МАШИНОЙ не поднимается

`Errno 110` first looked like "the ignition is off", and the owner had indeed switched it
off - but it kept coming with the ignition back **on**, and `reset_lexia.py` reported
"устройство отвечает - сброс шины не потребовался" while the link still refused to come up.
So release+dispose revives the USB endpoint but not the session with the car: a physical
replug is still required.

What actually produced it - established a day later by correlating timestamps - was the
per-packet ack change in `lexia_proto._collect` (see the ack section above), not the handover
from a live session as first suspected. Every process running that code got `Errno 110`;
the old process kept working. Rolling the change back fixed it immediately. The handover
order flag -> replug -> run is still the safe one, but it was not the cause here.

Two small traps from the same evening: `pgrep -cf 'drive.py'` counts the service's own
instance *and* the shell running the grep - match on `--ignore-pause` and use
`grep "[d]rive.py"` to see only manual runs. And `pkill` returning 1 when nothing matched
aborts an `&&` chain, so cleanup steps after it silently do not run.
