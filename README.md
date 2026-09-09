# gan_mppt

Firmware, hardware designs, and host tools for the five-channel GaN MPPT controller.

```powershell
git clone https://github.com/a-m-a-c/gan_mppt.git
cd gan_mppt
```

| Folder | Contents |
| --- | --- |
| `firmware/` | STM32 firmware, CubeMX project, and CMake build files |
| `hardware/` | Altium schematics, PCB, output job, and schematic PDFs |
| `tools/` | Python bench GUI, serial console, capture and plotting tools; managed by uv |
| `docs/` | Project documents and plots |

Open the repository root in VS Code. Its CMake, flash, and debug settings target
`firmware/`. The firmware's CubeMX project and executable remain named
`gan_mppt_firmware`.

To compile with STM32CubeCLT's CMake, Ninja, and ARM GCC on PATH:

```powershell
cd firmware
cmake --preset Debug
cmake --build --preset Debug
```

To run the host GUI from the repository root with [uv](https://docs.astral.sh/uv/):

```powershell
uv sync --project tools --locked
uv run --project tools --locked python tools/gui/server.py
```

See [tools/README.md](tools/README.md) for the other commands. A serial connection
is selected in the GUI; opening it alone does not connect to the board.

Open `hardware/SR8_MPPT.PrjPcb` in Altium Designer. Local Altium history,
previews, logs, and generated outputs are ignored by Git.

The repository retains the original firmware Git history. Hardware was copied
from the existing Altium project without importing its Git history.
