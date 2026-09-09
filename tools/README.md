# GaN MPPT tools

Python 3.11 or newer and [uv](https://docs.astral.sh/uv/) are required.
Dependencies are declared in `pyproject.toml` and resolved in `uv.lock`.

Run from the repository root:

```powershell
uv sync --project tools --locked
uv run --project tools --locked python tools/gui/server.py
uv run --project tools --locked python tools/console.py --list
uv run --project tools --locked python tools/plotter.py --list
uv run --project tools --locked python tools/iv_curve.py <csv>
```

The GUI prints its local URL. Select a serial port and connect when the board
is ready. Captures are saved under the repository's ignored `captures/` folder.

To replay an existing capture without connecting to hardware:

```powershell
uv run --project tools --locked python tools/gui/server.py --replay <csv> --speed 4
```

The scripts retain their inline dependency metadata for standalone use.
Use the explicit `python` commands above to run with the project's locked
dependencies. Add shared dependencies with `uv add --project tools <package>`.

`gen_ntc_table.py` writes to `firmware/Inc/drivers/ntc_table.h` and requires
`.agents/NCU18XH103F6SRB.csv`, which is not currently included in the repository.
