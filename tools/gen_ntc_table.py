#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = []
# ///
"""Generate firmware/Inc/drivers/ntc_table.h from Murata's characteristic CSV."""

import argparse
import csv
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CSV_PATH = REPO / ".agents" / "NCU18XH103F6SRB.csv"
OUT_PATH = REPO / "firmware" / "Inc" / "drivers" / "ntc_table.h"

# Murata CSV reference divider; --pullup specifies the board's divider.
REF_PULLUP_OHMS = 10000.0
REF_RAIL_V = 3.3


TYP_TEMP_COL, TYP_VOUT_COL = 2, 3


UNITS_PER_VOLT = 10000


def read_typ_curve(path):
    rows = []
    with path.open(newline="") as handle:
        for fields in csv.reader(handle):
            if not fields or fields[0].startswith("#"):
                continue
            try:
                temp = float(fields[TYP_TEMP_COL])
                vout = float(fields[TYP_VOUT_COL])
            except (IndexError, ValueError):
                continue
            rows.append((temp, vout))
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pullup", type=float, default=10000.0,
                    help="board's NTC pull-up resistance in ohms (default 10000)")
    ap.add_argument("--rail", type=float, default=3.3,
                    help="voltage the pull-up goes to (default 3.3)")
    ap.add_argument("--ntc25", type=float, default=10000.0,
                    help="fitted thermistor's nominal resistance at 25 degC in "
                         "ohms (default 10000, i.e. a '103' part). Use 1000 for "
                         "a '102'. Same curve shape, scaled - only the ratio to "
                         "--pullup affects the result.")
    args = ap.parse_args()

    if not CSV_PATH.exists():
        sys.exit(f"missing {CSV_PATH}")

    rows = read_typ_curve(CSV_PATH)
    if not rows:
        sys.exit(f"no data rows parsed from {CSV_PATH}")

    temps = [t for t, _ in rows]
    step = round(temps[1] - temps[0])
    if any(round(b - a) != step for a, b in zip(temps, temps[1:])):
        sys.exit("temperature column is not evenly spaced")


    ohms = [REF_PULLUP_OHMS * v / (REF_RAIL_V - v) for _, v in rows]

    at25 = ohms[temps.index(25.0)] if 25.0 in temps else None
    if at25 is not None and not (9800.0 < at25 < 10200.0):
        sys.exit(f"derived R(25 degC) = {at25:.0f} ohm, expected ~10000 - the CSV "
                 f"is not for a 10 kOhm part in a {REF_PULLUP_OHMS:.0f}/{REF_RAIL_V} V divider")


    scale = args.ntc25 / 10000.0
    ohms = [r * scale for r in ohms]
    volts = [args.rail * r / (args.pullup + r) for r in ohms]

    counts = [round(v * UNITS_PER_VOLT) for v in volts]
    if any(b >= a for a, b in zip(counts, counts[1:])):
        sys.exit("voltage column is not strictly decreasing")
    if max(counts) > 0xFFFF:
        sys.exit("voltage exceeds uint16 range")

    lines = [
        "// This software is licensed under terms that can be found in the LICENSE file",
        "// in the root directory of this software component.",
        "// If no LICENSE file comes with this software, it is provided AS-IS.",
        "#ifndef NTC_TABLE_H",
        "#define NTC_TABLE_H",
        "",
        "#include <stdint.h>",
        "",
        "// Source: .agents/NCU18XH103F6SRB.csv. Regenerate; do not edit by hand:",
        f"// uv run tools/gen_ntc_table.py --ntc25 {args.ntc25:g}"
        f" --pullup {args.pullup:g} --rail {args.rail:g}",
        "",
        f"#define NTC_TABLE_MIN_C   {int(temps[0])}",
        f"#define NTC_TABLE_STEP_C  {step}",
        f"#define NTC_TABLE_LEN     {len(counts)}U",
        "",
        "static const uint16_t ntc_table_dmv[NTC_TABLE_LEN] = {",
    ]

    for start in range(0, len(counts), 8):
        chunk = counts[start : start + 8]
        lines.append("    " + " ".join(f"{c}," for c in chunk))

    lines += [
        "};",
        "",
        "#endif",
        "",
    ]

    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(
        f"wrote {OUT_PATH.relative_to(REPO)}: {len(counts)} entries, "
        f"{int(temps[0])}..{int(temps[-1])} degC, "
        f"{max(counts)}..{min(counts)} tenths of a mV\n"
        f"  divider: {args.ntc25:.0f} ohm NTC, {args.pullup:.0f} ohm pull-up "
        f"to {args.rail} V\n"
        f"  a board at 25 degC should show {volts[temps.index(25.0)] * 1000:.0f} mV "
        f"at the pin - check with 'adc' over serial, or a meter"
    )


if __name__ == "__main__":
    main()
